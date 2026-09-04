import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from pathlib import Path
import tempfile
import unittest

from llm_harness.config import CloudModelEndpoint, HarnessConfig, WorkerConfig, load_config
from llm_harness.doctor import run_doctor
from llm_harness.runtime import RuntimeConfig
from llm_harness.model_providers import (
    ModelProviderError,
    ModelRequest,
    create_model_provider,
    model_endpoint_preflight,
)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": json.loads(self.rfile.read(length)),
            }
        )
        status, headers, payload = self.server.responses.pop(0)  # type: ignore[attr-defined]
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def log_message(self, format, *args):
        return


class _Server:
    def __init__(self, responses):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.responses = list(responses)
        self.httpd.requests = []
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_port}"

    @property
    def requests(self):
        return self.httpd.requests


def _request():
    return ModelRequest(
        instructions="Return JSON.",
        input_text="health",
        output_schema_name="health",
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["status"],
            "properties": {"status": {"type": "string"}},
        },
    )


class ModelProviderTests(unittest.TestCase):
    def test_openai_responses_contract_and_redacted_evidence(self):
        response = {
            "id": "resp_123",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"status":"ok"}'}],
                }
            ],
            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        }
        with _Server([(200, {"x-request-id": "req_123"}, response)]) as server:
            endpoint = CloudModelEndpoint(
                provider="openai",
                model="test-openai",
                base_url=server.url,
                api_key_env="TEST_OPENAI_KEY",
            )
            result = create_model_provider(
                endpoint,
                environment={"TEST_OPENAI_KEY": "top-secret"},
            ).invoke(_request())

        sent = server.requests[0]
        self.assertEqual(sent["path"], "/responses")
        self.assertEqual(sent["headers"]["Authorization"], "Bearer top-secret")
        self.assertEqual(sent["body"]["model"], "test-openai")
        self.assertEqual(sent["body"]["text"]["format"]["type"], "json_schema")
        self.assertEqual(result.output, {"status": "ok"})
        self.assertEqual(result.evidence.request_id, "req_123")
        self.assertEqual(result.evidence.total_tokens, 5)
        self.assertNotIn("top-secret", repr(result.evidence))
        self.assertEqual(result.evidence.endpoint, "/responses")

    def test_deepseek_chat_contract_retries_429_and_records_attempts(self):
        responses = [
            (429, {}, {"error": {"message": "slow down"}}),
            (
                200,
                {"x-request-id": "deepseek-1"},
                {
                    "id": "chat-1",
                    "choices": [{"message": {"content": '{"status":"ok"}'}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
                },
            ),
        ]
        sleeps = []
        with _Server(responses) as server:
            endpoint = CloudModelEndpoint(
                provider="deepseek",
                model="test-deepseek",
                base_url=server.url,
                max_retries=1,
            )
            result = create_model_provider(
                endpoint,
                environment={"DEEPSEEK_API_KEY": "secret"},
                sleeper=sleeps.append,
                random_source=lambda: 0,
            ).invoke(_request())

        self.assertEqual(len(server.requests), 2)
        self.assertEqual(server.requests[0]["path"], "/chat/completions")
        body = server.requests[0]["body"]
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertFalse(body["stream"])
        self.assertIn("JSON", body["messages"][1]["content"])
        self.assertEqual(result.evidence.attempts, 2)
        self.assertEqual(sleeps, [0.5])

    def test_openai_compatible_provider_uses_generic_chat_contract(self):
        response = {
            "id": "compatible-1",
            "choices": [{"message": {"content": '{"status":"ok"}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        with _Server([(200, {}, response)]) as server:
            endpoint = CloudModelEndpoint(
                provider="openai_compatible",
                model="vendor-model",
                base_url=server.url,
                api_key_env="VENDOR_KEY",
            )
            result = create_model_provider(endpoint, environment={"VENDOR_KEY": "secret"}).invoke(_request())

        self.assertEqual(server.requests[0]["path"], "/chat/completions")
        self.assertEqual(result.output, {"status": "ok"})
        self.assertEqual(result.evidence.provider, "openai_compatible")

    def test_authentication_and_quota_errors_are_not_retried(self):
        for status, kind in ((401, "authentication"), (402, "quota")):
            with self.subTest(status=status):
                with _Server([(status, {}, {"error": {"message": "denied"}})]) as server:
                    endpoint = CloudModelEndpoint(
                        provider="deepseek",
                        model="test",
                        base_url=server.url,
                        max_retries=3,
                    )
                    with self.assertRaises(ModelProviderError) as raised:
                        create_model_provider(
                            endpoint,
                            environment={"DEEPSEEK_API_KEY": "secret"},
                            sleeper=lambda _: self.fail("must not retry"),
                        ).invoke(_request())
                self.assertEqual(raised.exception.kind, kind)
                self.assertFalse(raised.exception.retryable)
                self.assertEqual(len(server.requests), 1)
                self.assertNotIn("secret", str(raised.exception))

    def test_openai_insufficient_quota_429_is_not_retried(self):
        with _Server(
            [
                (
                    429,
                    {},
                    {"error": {"code": "insufficient_quota", "message": "quota exhausted"}},
                )
            ]
        ) as server:
            endpoint = CloudModelEndpoint(
                provider="openai",
                model="test",
                base_url=server.url,
                max_retries=3,
            )
            with self.assertRaises(ModelProviderError) as raised:
                create_model_provider(
                    endpoint,
                    environment={"OPENAI_API_KEY": "secret"},
                    sleeper=lambda _: self.fail("must not retry quota"),
                ).invoke(_request())
        self.assertEqual(raised.exception.kind, "quota")
        self.assertFalse(raised.exception.retryable)

    def test_transport_timeout_retries_to_configured_limit(self):
        attempts = []
        sleeps = []

        def timeout_sender(url, headers, body, timeout):
            attempts.append(timeout)
            raise TimeoutError()

        endpoint = CloudModelEndpoint(
            provider="openai",
            model="test",
            max_retries=2,
            timeout_seconds=7,
        )
        with self.assertRaises(ModelProviderError) as raised:
            create_model_provider(
                endpoint,
                environment={"OPENAI_API_KEY": "secret"},
                http_sender=timeout_sender,
                sleeper=sleeps.append,
                random_source=lambda: 0,
            ).invoke(_request())
        self.assertEqual(raised.exception.kind, "transport")
        self.assertEqual(attempts, [7, 7, 7])
        self.assertEqual(sleeps, [0.5, 1.0])

    def test_preflight_requires_secret_and_https_outside_loopback(self):
        endpoint = CloudModelEndpoint(provider="openai", model="test")
        self.assertFalse(model_endpoint_preflight(endpoint, {})[0])
        insecure = CloudModelEndpoint(
            provider="openai",
            model="test",
            base_url="http://example.com/v1",
        )
        self.assertFalse(model_endpoint_preflight(insecure, {"OPENAI_API_KEY": "secret"})[0])

    def test_non_object_provider_response_is_classified_as_invalid(self):
        with _Server([(200, {}, "not-an-object")]) as server:
            endpoint = CloudModelEndpoint(
                provider="openai",
                model="test",
                base_url=server.url,
            )
            with self.assertRaises(ModelProviderError) as raised:
                create_model_provider(
                    endpoint,
                    environment={"OPENAI_API_KEY": "secret"},
                ).invoke(_request())
        self.assertEqual(raised.exception.kind, "invalid_response")

    def test_endpoint_validates_provider_timeout_retry_and_output_limit(self):
        for kwargs in (
            {"provider": "unknown", "model": "test"},
            {"provider": "openai_compatible", "model": "test", "api_key_env": "KEY"},
            {"provider": "openai_compatible", "model": "test", "base_url": "https://example.test/v1"},
            {"provider": "openai", "model": "test", "timeout_seconds": 0},
            {"provider": "openai", "model": "test", "max_retries": -1},
            {"provider": "openai", "model": "test", "max_output_tokens": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CloudModelEndpoint(**kwargs)

    def test_config_parses_provider_runtime_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "harness.toml"
            path.write_text(
                """
[supervisor_model]
provider = "openai"
model = "planner"
base_url = "https://example.test/v1"
api_key_env = "CUSTOM_KEY"
timeout_seconds = 12.5
max_retries = 4
max_output_tokens = 2048
""",
                encoding="utf-8",
            )
            endpoint = load_config(path).supervisor_model
        self.assertEqual(endpoint.provider, "openai")
        self.assertEqual(endpoint.timeout_seconds, 12.5)
        self.assertEqual(endpoint.max_retries, 4)
        self.assertEqual(endpoint.max_output_tokens, 2048)

    def test_doctor_reports_each_model_role_without_network_access(self):
        config = HarnessConfig(
            supervisor_model=CloudModelEndpoint("openai", "planner"),
            verifier_model=CloudModelEndpoint("deepseek", "reviewer"),
            runtime=RuntimeConfig(require_embedded_python=False),
            worker=WorkerConfig(type="stub", command="stub"),
        )
        report = run_doctor(
            Path.cwd(),
            config,
            env={"OPENAI_API_KEY": "present"},
            agent_resolver=lambda _: "available",
        )
        checks = {item.name: item for item in report.checks}
        self.assertTrue(checks["supervisor_model"].ok)
        self.assertFalse(checks["verifier_model"].ok)
        self.assertIn("DEEPSEEK_API_KEY", checks["verifier_model"].detail)


if __name__ == "__main__":
    unittest.main()
