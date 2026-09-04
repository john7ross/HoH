import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import socket
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from llm_harness.a2a import (
    A2AAuthenticationError,
    A2AClient,
    A2AConnection,
    A2AError,
)
from llm_harness.a2a_push import A2APushInbox


def _card(security=None, *, streaming=False):
    payload = {
        "name": "Remote",
        "version": "2.3.4",
        "supportedInterfaces": [
            {
                "url": "https://agent.example/a2a/v1",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }
        ],
        "capabilities": {"streaming": streaming},
        "skills": [{"id": "code"}],
    }
    if security is not None:
        payload["security"] = security
    return payload


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, method, headers, body, timeout, limit):
        self.calls.append((url, method, dict(headers), body, timeout, limit))
        status, payload = self.responses.pop(0)
        encoded = json.dumps(payload).encode("utf-8")
        return status, {}, encoded, url


class A2AClientTests(unittest.TestCase):
    def test_real_loopback_sse_stream_reaches_terminal_state(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = _card(streaming=True)
                payload["supportedInterfaces"][0]["url"] = self.server.endpoint
                self._json(payload)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length))
                self.server.methods.append(request["method"])
                rpc_id = request["id"]
                if request["method"] == "SendStreamingMessage":
                    results = [
                        {"task": {"id": "task-live", "status": {"state": "TASK_STATE_WORKING"}}},
                    ]
                else:
                    results = [
                        {
                            "statusUpdate": {
                                "taskId": "task-live",
                                "status": {
                                    "state": "TASK_STATE_COMPLETED",
                                    "message": {"parts": [{"text": "stream-live-ok"}]},
                                },
                            }
                        }
                    ]
                body = b"".join(
                    b"data: "
                    + json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": result}).encode("utf-8")
                    + b"\n\n"
                    for result in results
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *args):
                del args

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.endpoint = f"http://127.0.0.1:{server.server_address[1]}/a2a"
        server.methods = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = A2AClient(
                A2AConnection(f"http://127.0.0.1:{server.server_address[1]}", prefer_streaming=True)
            ).send_message("ping")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(result.text, "stream-live-ok")
        self.assertEqual(result.state, "COMPLETED")
        self.assertEqual(server.methods, ["SendStreamingMessage", "SubscribeToTask"])

    def test_real_loopback_http_round_trip_uses_v1_wire(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = _card()
                payload["supportedInterfaces"][0]["url"] = self.server.endpoint
                self._reply(payload)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.server.request = json.loads(self.rfile.read(length))
                self._reply(
                    {
                        "jsonrpc": "2.0",
                        "id": self.server.request["id"],
                        "result": {"message": {"role": "ROLE_AGENT", "parts": [{"text": "live-ok"}]}},
                    }
                )

            def _reply(self, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *args):
                del args

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.endpoint = f"http://127.0.0.1:{server.server_address[1]}/a2a"
        server.request = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = A2AClient(A2AConnection(f"http://127.0.0.1:{server.server_address[1]}" )).send_message("ping")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(result.text, "live-ok")
        self.assertEqual(server.request["method"], "SendMessage")

    def test_discovers_v1_jsonrpc_and_returns_immediate_message(self):
        transport = FakeTransport(
            [
                (200, _card()),
                (
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {"message": {"role": "ROLE_AGENT", "parts": [{"text": "hello"}]}},
                    },
                ),
            ]
        )
        result = A2AClient(A2AConnection("https://agent.example"), transport=transport).send_message("hi")

        self.assertEqual(result.text, "hello")
        self.assertEqual(result.state, "COMPLETED")
        self.assertEqual(transport.calls[0][0], "https://agent.example/.well-known/agent-card.json")
        request = json.loads(transport.calls[1][3])
        self.assertEqual(request["method"], "SendMessage")
        self.assertEqual(request["params"]["message"]["role"], "ROLE_USER")
        self.assertEqual(transport.calls[1][2]["A2A-Version"], "1.0")

    def test_polls_task_until_completed_and_collects_artifact(self):
        transport = FakeTransport(
            [
                (200, _card()),
                (
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {"task": {"id": "task-1", "status": {"state": "TASK_STATE_WORKING"}}},
                    },
                ),
                (
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "result": {
                            "task": {
                                "id": "task-1",
                                "status": {"state": "TASK_STATE_COMPLETED"},
                                "artifacts": [{"parts": [{"data": {"patch": "diff --git a/a b/a"}}]}],
                            }
                        },
                    },
                ),
            ]
        )
        client = A2AClient(
            A2AConnection("https://agent.example/card.json", poll_interval_seconds=0.001),
            transport=transport,
        )

        result = client.send_message("work")

        self.assertEqual(result.task_id, "task-1")
        self.assertEqual(result.state, "COMPLETED")
        self.assertEqual(result.data[0]["patch"], "diff --git a/a b/a")
        poll = json.loads(transport.calls[2][3])
        self.assertEqual(poll["method"], "GetTask")
        self.assertEqual(poll["params"], {"id": "task-1"})

    def test_bearer_credential_is_read_from_environment_and_never_put_in_payload(self):
        transport = FakeTransport(
            [
                (200, _card([{"bearer": []}])),
                (200, {"jsonrpc": "2.0", "id": 1, "result": {"message": {"parts": [{"text": "ok"}]}}}),
            ]
        )
        with patch.dict(os.environ, {"REMOTE_TOKEN": "top-secret"}, clear=False):
            A2AClient(
                A2AConnection("https://agent.example", "bearer", "REMOTE_TOKEN"),
                transport=transport,
            ).send_message("hello")
        self.assertEqual(transport.calls[1][2]["Authorization"], "Bearer top-secret")
        self.assertNotIn(b"top-secret", transport.calls[1][3])

    def test_streaming_message_subscribes_until_terminal_and_collects_events(self):
        transport = FakeTransport([(200, _card(streaming=True))])
        calls = []

        def sse_transport(url, headers, body, _timeout, _limit):
            request = json.loads(body)
            calls.append((url, dict(headers), request))
            if request["method"] == "SendStreamingMessage":
                events = [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {"task": {"id": "task-stream", "status": {"state": "TASK_STATE_WORKING"}}},
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {
                            "artifactUpdate": {
                                "taskId": "task-stream",
                                "artifact": {"parts": [{"data": {"patch": "diff --git a/a b/a"}}]},
                            }
                        },
                    },
                ]
            else:
                events = [
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "result": {
                            "statusUpdate": {
                                "taskId": "task-stream",
                                "status": {
                                    "state": "TASK_STATE_COMPLETED",
                                    "message": {"parts": [{"text": "complete"}]},
                                },
                            }
                        },
                    }
                ]
            return 200, {"Content-Type": "text/event-stream"}, events, url

        result = A2AClient(
            A2AConnection("https://agent.example", prefer_streaming=True),
            transport=transport,
            sse_transport=sse_transport,
        ).send_message("work")

        self.assertEqual([item[2]["method"] for item in calls], ["SendStreamingMessage", "SubscribeToTask"])
        self.assertEqual(result.task_id, "task-stream")
        self.assertEqual(result.state, "COMPLETED")
        self.assertEqual(result.text, "complete")
        self.assertEqual(result.data[0]["patch"], "diff --git a/a b/a")

    def test_push_configuration_uses_authenticated_callback(self):
        transport = FakeTransport(
            [
                (200, _card()),
                (200, {"jsonrpc": "2.0", "id": 1, "result": {"message": {"parts": [{"text": "ok"}]}}}),
            ]
        )
        with patch.dict(os.environ, {"PUSH_TOKEN": "push-secret"}, clear=False):
            A2AClient(
                A2AConnection(
                    "https://agent.example",
                    push_callback_url="https://hoh.example/a2a/push",
                    push_token_env="PUSH_TOKEN",
                ),
                transport=transport,
            ).send_message("hello")

        request = json.loads(transport.calls[1][3])
        config = request["params"]["configuration"]["taskPushNotificationConfig"]
        self.assertEqual(config["url"], "https://hoh.example/a2a/push")
        self.assertEqual(config["authentication"], {"scheme": "Bearer", "credentials": "push-secret"})

    def test_push_inbox_completes_task_without_polling(self):
        inbox = A2APushInbox()
        methods = []

        def transport(url, method, headers, body, timeout, limit):
            del headers, timeout, limit
            if method == "GET":
                return 200, {}, json.dumps(_card()).encode(), url
            request = json.loads(body)
            methods.append(request["method"])
            inbox.record(
                {
                    "statusUpdate": {
                        "taskId": "task-push",
                        "status": {
                            "state": "TASK_STATE_COMPLETED",
                            "message": {"parts": [{"text": "push-complete"}]},
                        },
                    }
                }
            )
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"task": {"id": "task-push", "status": {"state": "TASK_STATE_WORKING"}}},
            }
            return 200, {}, json.dumps(response).encode(), url

        with patch.dict(os.environ, {"PUSH_TOKEN": "push-secret"}, clear=False):
            result = A2AClient(
                A2AConnection(
                    "https://agent.example",
                    push_callback_url="https://hoh.example/a2a/push",
                    push_token_env="PUSH_TOKEN",
                    timeout_seconds=1,
                ),
                transport=transport,
                push_inbox=inbox,
            ).send_message("hello")

        self.assertEqual(result.text, "push-complete")
        self.assertEqual(methods, ["SendMessage"])

    def test_loopback_push_callback_starts_runtime_receiver(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        callback_url = f"http://127.0.0.1:{port}/a2a/push"
        methods = []

        def transport(url, method, headers, body, timeout, limit):
            del headers, timeout, limit
            if method == "GET":
                return 200, {}, json.dumps(_card()).encode(), url
            request = json.loads(body)
            methods.append(request["method"])
            configured = request["params"]["configuration"]["taskPushNotificationConfig"]
            event = json.dumps(
                {
                    "statusUpdate": {
                        "taskId": "task-runtime-push",
                        "status": {
                            "state": "TASK_STATE_COMPLETED",
                            "message": {"parts": [{"text": "runtime-push-complete"}]},
                        },
                    }
                }
            ).encode()
            callback = Request(
                configured["url"],
                data=event,
                headers={"Authorization": "Bearer push-secret", "Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(callback, timeout=2) as response:
                self.assertEqual(response.status, 202)
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "task": {"id": "task-runtime-push", "status": {"state": "TASK_STATE_WORKING"}}
                },
            }
            return 200, {}, json.dumps(response).encode(), url

        with patch.dict(os.environ, {"PUSH_TOKEN": "push-secret"}, clear=False):
            result = A2AClient(
                A2AConnection(
                    "https://agent.example",
                    push_callback_url=callback_url,
                    push_token_env="PUSH_TOKEN",
                    timeout_seconds=2,
                ),
                transport=transport,
            ).send_message("hello")

        self.assertEqual(result.text, "runtime-push-complete")
        self.assertEqual(methods, ["SendMessage"])

    def test_required_auth_without_configuration_fails_closed(self):
        transport = FakeTransport([(200, _card([{"bearer": []}]))])
        with self.assertRaises(A2AAuthenticationError):
            A2AClient(A2AConnection("https://agent.example"), transport=transport).send_message("hello")

    def test_oidc_discovery_url_is_selected_from_required_agent_card_scheme(self):
        card = _card([{"companyOidc": ["a2a.execute"]}])
        card["securitySchemes"] = {
            "companyOidc": {
                "type": "openIdConnect",
                "openIdConnectUrl": "https://identity.example/.well-known/openid-configuration",
            }
        }
        client = A2AClient(
            A2AConnection(
                "https://agent.example",
                auth_kind="oidc",
                oauth_flow="device_code",
                client_id_env="OIDC_CLIENT_ID",
            ),
            transport=FakeTransport([(200, card)]),
        )

        discovered = client.discover()

        self.assertIn("companyOidc", discovered.security_schemes)
        self.assertEqual(
            client._oauth.config.discovery_url,  # type: ignore[union-attr]
            "https://identity.example/.well-known/openid-configuration",
        )

    def test_plain_http_remote_endpoint_is_rejected(self):
        with self.assertRaisesRegex(A2AError, "requires HTTPS"):
            A2AConnection("http://agent.example")
        A2AConnection("http://127.0.0.1:8080")

    def test_api_key_header_cannot_override_protocol_headers(self):
        with self.assertRaisesRegex(A2AError, "protocol-owned"):
            A2AConnection("https://agent.example", "api_key", "TOKEN", "A2A-Version")
        with self.assertRaisesRegex(A2AError, "valid HTTP"):
            A2AConnection("https://agent.example", "api_key", "TOKEN", "X-Key\r\nInjected")

    def test_v03_only_card_is_rejected_instead_of_guessing_wire_shape(self):
        card = _card()
        card["supportedInterfaces"][0]["protocolVersion"] = "0.3"
        transport = FakeTransport([(200, card)])
        with self.assertRaisesRegex(A2AError, "v1.0"):
            A2AClient(A2AConnection("https://agent.example"), transport=transport).discover()


if __name__ == "__main__":
    unittest.main()
