from io import StringIO
import json
import os
import unittest
from unittest.mock import patch

from llm_harness.agent_auth import (
    AcpAuthService,
    AgentAuthenticationError,
    vendor_login,
    vendor_login_profile,
)


class FakeProcess:
    def __init__(self, responses):
        self.stdin = StringIO()
        self.stdout = StringIO("".join(json.dumps(item) + "\n" for item in responses))
        self.stderr = StringIO()
        self.closed = False

    def terminate(self):
        self.closed = True

    def wait(self, timeout=None):
        return 0


class AgentAuthenticationTests(unittest.TestCase):
    def test_inspect_parses_agent_terminal_and_environment_methods(self):
        process = FakeProcess(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": 1,
                        "agentInfo": {"name": "Future", "version": "2.3.4"},
                        "agentCapabilities": {"auth": {"logout": {}}},
                        "authMethods": [
                            {"id": "web", "name": "Browser"},
                            {"id": "terminal", "name": "Terminal", "type": "terminal", "args": ["--login"]},
                            {
                                "id": "key",
                                "name": "API key",
                                "type": "env_var",
                                "vars": [{"name": "FUTURE_KEY", "label": "Key"}],
                                "link": "https://example.test/keys",
                            },
                        ],
                    },
                }
            ]
        )
        inspection = AcpAuthService(process_factory=lambda _args, _env: process).inspect("future", ())

        self.assertEqual(inspection.agent_name, "Future")
        self.assertTrue(inspection.logout_supported)
        self.assertEqual([item.method_type for item in inspection.methods], ["agent", "terminal", "env_var"])
        self.assertEqual(inspection.methods[2].variables[0][0], "FUTURE_KEY")
        outbound = json.loads(process.stdin.getvalue().strip())
        self.assertTrue(outbound["params"]["clientCapabilities"]["auth"]["terminal"])
        self.assertTrue(process.closed)

    def test_protocol_login_revalidates_method_and_sends_authenticate(self):
        responses = [
            {"jsonrpc": "2.0", "id": 1, "result": {"authMethods": [{"id": "web", "name": "Browser"}]}},
            {"jsonrpc": "2.0", "id": 2, "result": {}},
        ]
        process = FakeProcess(responses)
        service = AcpAuthService(process_factory=lambda _args, _env: process)
        method = service.inspect("future", ()).methods[0]
        process = FakeProcess(responses)
        service = AcpAuthService(process_factory=lambda _args, _env: process)

        result = service.login("future", (), method)

        self.assertTrue(result.succeeded)
        outbound = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        self.assertEqual(outbound[1]["method"], "authenticate")
        self.assertEqual(outbound[1]["params"]["methodId"], "web")

    def test_environment_method_fails_before_launch_when_required_value_is_missing(self):
        process = FakeProcess(
            [{"jsonrpc": "2.0", "id": 1, "result": {"authMethods": [{"id": "key", "name": "Key", "type": "env_var", "vars": [{"name": "MISSING_KEY"}]}]}}]
        )
        service = AcpAuthService(process_factory=lambda _args, _env: process)
        method = service.inspect("future", ()).methods[0]
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(AgentAuthenticationError, "MISSING_KEY"):
                service.login("future", (), method)

    def test_terminal_method_uses_same_agent_command_and_appends_declared_args(self):
        captured = []
        service = AcpAuthService(interactive_runner=lambda args, env, timeout: captured.append((args, env, timeout)) or 0)
        method = type("Method", (), {
            "method_type": "terminal",
            "environment": (("LOGIN", "1"),),
            "args": ("--login",),
        })()

        result = service.login("future", ("--acp",), method)

        self.assertTrue(result.succeeded)
        self.assertEqual(captured[0][0][-2:], ("--acp", "--login"))
        self.assertEqual(captured[0][1]["LOGIN"], "1")

    def test_codex_vendor_profile_uses_official_cli_login_and_status(self):
        profile = vendor_login_profile("codex-acp")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.login_args, ("login",))
        self.assertEqual(profile.status_args, ("login", "status"))
        with patch("llm_harness.agent_auth.shutil.which", return_value="C:/tools/codex.exe"), patch(
            "llm_harness.agent_auth.vendor_login_status",
            side_effect=[type("Result", (), {"succeeded": False})(), type("Result", (), {"succeeded": True, "detail": "active"})()],
        ):
            result = vendor_login(profile, interactive_runner=lambda _args, _env, _timeout: 0)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.detail, "active")


if __name__ == "__main__":
    unittest.main()
