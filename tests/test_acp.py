from io import StringIO
from pathlib import Path
import json
import unittest

from llm_harness.acp import AcpError, JsonRpcStdioClient
from llm_harness.mcp import McpServerConfig, RoleMcpPolicyConfig, SecretBinding


class FakeProcess:
    def __init__(self, responses: list[dict]) -> None:
        self.stdin = StringIO()
        self.stdout = StringIO("".join(json.dumps(item) + "\n" for item in responses))
        self.stderr = StringIO()

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0


class AcpClientTests(unittest.TestCase):
    def test_initialize_writes_newline_delimited_json_rpc(self):
        process = FakeProcess([{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}])
        client = JsonRpcStdioClient(process)

        response = client.initialize()

        self.assertEqual(response["result"]["protocolVersion"], 1)
        outbound = json.loads(process.stdin.getvalue().strip())
        self.assertEqual(outbound["method"], "initialize")
        self.assertEqual(outbound["params"]["clientCapabilities"]["terminal"], False)
        self.assertEqual(outbound["params"]["clientCapabilities"]["auth"]["terminal"], False)

    def test_authenticate_uses_advertised_method_id_contract(self):
        process = FakeProcess([{"jsonrpc": "2.0", "id": 1, "result": {}}])
        client = JsonRpcStdioClient(process)

        client.authenticate("chat-gpt")

        outbound = json.loads(process.stdin.getvalue().strip())
        self.assertEqual(outbound["method"], "authenticate")
        self.assertEqual(outbound["params"], {"methodId": "chat-gpt"})

    def test_new_session_requires_session_id(self):
        process = FakeProcess([{"jsonrpc": "2.0", "id": 1, "result": {}}])
        client = JsonRpcStdioClient(process)

        with self.assertRaises(AcpError):
            client.new_session(Path("C:/repo"))

        outbound = json.loads(process.stdin.getvalue().strip())
        self.assertEqual(outbound["params"]["mcpServers"], [])

    def test_prompt_sends_text_content_block(self):
        process = FakeProcess([{"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}])
        client = JsonRpcStdioClient(process)

        client.prompt("session-1", "hello")

        outbound = json.loads(process.stdin.getvalue().strip())
        self.assertEqual(outbound["method"], "session/prompt")
        self.assertEqual(outbound["params"]["sessionId"], "session-1")
        self.assertEqual(outbound["params"]["prompt"][0]["text"], "hello")

    def test_permission_request_can_be_allowed_once(self):
        process = FakeProcess(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": "session-1",
                        "toolCall": {"toolCallId": "call-1"},
                        "options": [
                            {"optionId": "allow-1", "kind": "allow_once", "name": "Allow once"},
                            {"optionId": "reject-1", "kind": "reject_once", "name": "Reject"},
                        ],
                    },
                },
                {"jsonrpc": "2.0", "id": 1, "result": {"stopReason": "complete"}},
            ]
        )
        client = JsonRpcStdioClient(process, permission_policy="allow_once")

        response = client.prompt("session-1", "hello")

        self.assertEqual(response["result"]["stopReason"], "complete")
        outbound = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        self.assertEqual(outbound[1]["id"], 7)
        self.assertEqual(outbound[1]["result"]["outcome"]["optionId"], "allow-1")

    def test_permission_request_rejects_by_default(self):
        process = FakeProcess(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": "session-1",
                        "toolCall": {"toolCallId": "call-1"},
                        "options": [
                            {"optionId": "allow-1", "kind": "allow_once", "name": "Allow once"},
                            {"optionId": "reject-1", "kind": "reject_once", "name": "Reject"},
                        ],
                    },
                },
                {"jsonrpc": "2.0", "id": 1, "result": {"stopReason": "complete"}},
            ]
        )
        client = JsonRpcStdioClient(process)

        client.prompt("session-1", "hello")

        outbound = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        self.assertEqual(outbound[1]["result"]["outcome"]["optionId"], "reject-1")

    def test_event_sink_receives_protocol_frames_including_tool_permission(self):
        process = FakeProcess(
            [
                {
                    "jsonrpc": "2.0", "id": 7, "method": "session/request_permission",
                    "params": {"toolCall": {"toolCallId": "call-1", "title": "Edit file"}, "options": []},
                },
                {"jsonrpc": "2.0", "id": 1, "result": {"stopReason": "complete"}},
            ]
        )
        events = []
        client = JsonRpcStdioClient(process, event_sink=lambda direction, message: events.append((direction, message)))

        client.prompt("session-1", "hello")

        self.assertEqual(events[0][0], "outbound")
        self.assertEqual(events[1][0], "inbound")
        self.assertEqual(events[1][1]["params"]["toolCall"]["toolCallId"], "call-1")

    def test_permission_allowlist_rejects_unlisted_tool_kind(self):
        process = FakeProcess(
            [
                {
                    "jsonrpc": "2.0", "id": 7, "method": "session/request_permission",
                    "params": {
                        "toolCall": {"toolCallId": "call-1", "kind": "execute"},
                        "options": [
                            {"optionId": "allow-1", "kind": "allow_once"},
                            {"optionId": "reject-1", "kind": "reject_once"},
                        ],
                    },
                },
                {"jsonrpc": "2.0", "id": 1, "result": {"stopReason": "complete"}},
            ]
        )
        policy = RoleMcpPolicyConfig("allow_once", ("read", "edit"))

        JsonRpcStdioClient(process, permission_policy=policy).prompt("session-1", "hello")

        outbound = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        self.assertEqual(outbound[1]["result"]["outcome"]["optionId"], "reject-1")

    def test_new_session_resolves_mcp_secrets_but_redacts_event_log(self):
        process = FakeProcess(
            [
                {
                    "jsonrpc": "2.0", "id": 1,
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {"mcpCapabilities": {"http": True, "sse": False}},
                    },
                },
                {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "session-1"}},
            ]
        )
        events = []
        policy = RoleMcpPolicyConfig(
            "allow_once",
            ("fetch",),
            (
                McpServerConfig(
                    "knowledge",
                    "http",
                    url="https://mcp.example/api",
                    headers=(SecretBinding("Authorization", "HOH_TEST_MCP_TOKEN"),),
                ),
            ),
        )
        client = JsonRpcStdioClient(
            process,
            permission_policy=policy,
            event_sink=lambda direction, message: events.append((direction, message)),
        )

        from unittest.mock import patch
        with patch.dict("os.environ", {"HOH_TEST_MCP_TOKEN": "Bearer secret-value"}):
            client.initialize()
            client.new_session(Path("C:/repo"))

        outbound = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        self.assertEqual(
            outbound[1]["params"]["mcpServers"][0]["headers"][0]["value"],
            "Bearer secret-value",
        )
        self.assertEqual(
            events[2][1]["params"]["mcpServers"][0]["headers"][0]["value"],
            "<redacted>",
        )

    def test_new_session_fails_when_agent_lacks_configured_transport(self):
        process = FakeProcess(
            [{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}]
        )
        policy = RoleMcpPolicyConfig(
            "allow_once",
            ("fetch",),
            (McpServerConfig("remote", "sse", url="https://mcp.example/sse"),),
        )
        client = JsonRpcStdioClient(process, permission_policy=policy)
        client.initialize()

        with self.assertRaisesRegex(AcpError, "does not advertise MCP SSE"):
            client.new_session(Path("C:/repo"))


if __name__ == "__main__":
    unittest.main()
