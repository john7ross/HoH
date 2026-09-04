import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from llm_harness.config import CloudModelEndpoint, CriticConfig, RoleIdentityConfig
from llm_harness.critic_adapters import (
    ClaudeCodeCriticAdapter,
    CommandJsonCriticAdapter,
    CriticAdapterError,
    ModelJsonCriticAdapter,
    build_critic_decision,
    create_critic_adapter,
)
from llm_harness.domain import ModelInvocationEvidence
from llm_harness.model_providers import ModelProviderError, ModelResponse


class ClaudeCodeCriticAdapterTests(unittest.TestCase):
    @patch("llm_harness.critic_adapters.create_model_provider")
    def test_model_json_critic_uses_closed_schema_and_canonical_decision(self, create_provider):
        provider = create_provider.return_value
        provider.invoke.return_value = ModelResponse(
            output=self._approve_judgment(),
            evidence=ModelInvocationEvidence(
                provider="deepseek",
                model="deepseek-chat",
                endpoint="/chat/completions",
                request_id="request-1",
                attempts=1,
                latency_ms=12,
                request_sha256="d" * 64,
                response_sha256="e" * 64,
            ),
        )
        events = []
        endpoint = CloudModelEndpoint("deepseek", "deepseek-chat")
        adapter = create_critic_adapter(
            CriticConfig(driver="model_json", command=""),
            RoleIdentityConfig("deepseek-critic", "deepseek", "deepseek-chat"),
            endpoint,
            event_sink=lambda direction, payload: events.append((direction, payload)),
        )

        decision = adapter.review(Path.cwd(), self._bundle())

        self.assertIsInstance(adapter, ModelJsonCriticAdapter)
        self.assertEqual(decision["decision"], "approve")
        request = provider.invoke.call_args.args[0]
        self.assertEqual(request.output_schema_name, "hoh_critic_judgment")
        self.assertFalse(request.output_schema["additionalProperties"])
        self.assertIn("IMMUTABLE REVIEW BUNDLE", request.input_text)
        self.assertEqual(events[-1][1]["request_sha256"], "d" * 64)
        self.assertNotIn("IMMUTABLE REVIEW BUNDLE", repr(events))

    @patch("llm_harness.critic_adapters.create_model_provider")
    def test_model_json_critic_reports_typed_provider_error_without_secret(self, create_provider):
        create_provider.return_value.invoke.side_effect = ModelProviderError(
            "server echoed top-secret-key",
            provider="deepseek",
            kind="authentication",
            status_code=401,
            request_id="request-2",
        )
        adapter = create_critic_adapter(
            CriticConfig(driver="model_json", command=""),
            RoleIdentityConfig("deepseek-critic", "deepseek", "deepseek-chat"),
            CloudModelEndpoint("deepseek", "deepseek-chat"),
        )

        with self.assertRaises(CriticAdapterError) as raised:
            adapter.review(Path.cwd(), self._bundle())

        self.assertIn("provider=deepseek kind=authentication status=401", str(raised.exception))
        self.assertNotIn("top-secret-key", str(raised.exception))

    @patch("llm_harness.critic_adapters.subprocess.run")
    def test_generic_command_json_critic_accepts_direct_judgment(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=(), returncode=0, stdout=json.dumps(self._approve_judgment()), stderr=""
        )
        adapter = create_critic_adapter(
            CriticConfig(driver="command_json", command="any-reviewer", args=("review",)),
            RoleIdentityConfig("independent-reviewer", "custom", "review-model"),
        )

        decision = adapter.review(Path.cwd(), self._bundle())

        self.assertIsInstance(adapter, CommandJsonCriticAdapter)
        self.assertEqual(decision["decision"], "approve")
        self.assertEqual(run.call_args.args[0], ("any-reviewer", "review"))
        self.assertIn("IMMUTABLE REVIEW BUNDLE", run.call_args.kwargs["input"])
        self.assertIn("REQUIRED JUDGMENT JSON SCHEMA", run.call_args.kwargs["input"])
        self.assertIn('"additionalProperties":false', run.call_args.kwargs["input"])
        self.assertNotEqual(Path(run.call_args.kwargs["cwd"]), Path.cwd())

    def test_generic_command_json_critic_cannot_write_via_repository_cwd(self):
        judgment = json.dumps(self._approve_judgment())
        script = (
            "from pathlib import Path; import sys; "
            "Path('MUTATED.md').write_text('isolated', encoding='utf-8'); "
            f"sys.stdout.write({judgment!r})"
        )
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            adapter = create_critic_adapter(
                CriticConfig(driver="command_json", command=sys.executable, args=("-c", script)),
                RoleIdentityConfig("independent-reviewer", "custom", "review-model"),
            )

            decision = adapter.review(repository, self._bundle())

            self.assertEqual(decision["decision"], "approve")
            self.assertFalse((repository / "MUTATED.md").exists())

    def test_build_decision_uses_canonical_bundle_identity_and_attestation(self):
        bundle = self._bundle()
        decision = build_critic_decision(
            bundle,
            RoleIdentityConfig("claude-reviewer", "claude", "sonnet"),
            self._approve_judgment(),
            created_at_utc="2026-07-17T12:00:00Z",
        )

        self.assertEqual(decision["bundle_id"], "three-head-run-1")
        self.assertEqual(decision["bundle_sha256"], "a" * 64)
        self.assertEqual(decision["critic"], {"identity": "claude-reviewer", "kind": "agent"})
        self.assertEqual(decision["attestation"]["reviewed_commit"], "b" * 40)
        self.assertEqual(decision["attestation"]["reviewed_patch_sha256"], "c" * 64)
        self.assertEqual(decision["decision"], "approve")

    @patch("llm_harness.critic_adapters.subprocess.run")
    def test_claude_adapter_is_read_only_and_parses_structured_output(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout=json.dumps(
                {
                    "type": "result",
                    "is_error": False,
                    "structured_output": self._approve_judgment(),
                }
            ),
            stderr="",
        )
        adapter = ClaudeCodeCriticAdapter(
            CriticConfig(
                type="claude_code",
                command="claude",
                timeout_seconds=45,
                max_budget_usd=1.25,
            ),
            RoleIdentityConfig("claude-reviewer", "claude", "sonnet"),
        )

        decision = adapter.review(Path.cwd(), self._bundle())

        self.assertEqual(decision["decision"], "approve")
        args = run.call_args.args[0]
        self.assertIn("--safe-mode", args)
        self.assertIn("--no-session-persistence", args)
        self.assertIn("--json-schema", args)
        self.assertIn("--tools", args)
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertEqual(args[args.index("--model") + 1], "sonnet")
        self.assertEqual(args[args.index("--max-budget-usd") + 1], "1.25")
        self.assertIn("IMMUTABLE REVIEW BUNDLE", run.call_args.kwargs["input"])
        self.assertEqual(run.call_args.kwargs["timeout"], 45)

    @patch("llm_harness.critic_adapters.subprocess.run")
    def test_claude_adapter_rejects_process_error(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=(),
            returncode=1,
            stdout="",
            stderr="authentication failed",
        )
        adapter = ClaudeCodeCriticAdapter(
            CriticConfig(type="claude_code"),
            RoleIdentityConfig("claude-reviewer", "claude", "agent-default"),
        )

        with self.assertRaisesRegex(CriticAdapterError, "authentication failed"):
            adapter.review(Path.cwd(), self._bundle())

    @patch("llm_harness.critic_adapters.subprocess.run")
    def test_claude_adapter_summarizes_api_error_without_session_metadata(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=(),
            returncode=1,
            stdout=json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "api_error_status": 401,
                    "result": "Failed to authenticate.",
                    "session_id": "must-not-be-exposed",
                }
            ),
            stderr="",
        )
        adapter = ClaudeCodeCriticAdapter(
            CriticConfig(type="claude_code"),
            RoleIdentityConfig("claude-reviewer", "claude", "agent-default"),
        )

        with self.assertRaises(CriticAdapterError) as raised:
            adapter.review(Path.cwd(), self._bundle())

        self.assertEqual(
            str(raised.exception),
            "Claude Code critic reported an error (HTTP 401): Failed to authenticate.",
        )
        self.assertNotIn("session", str(raised.exception))

    @patch("llm_harness.critic_adapters.subprocess.run")
    def test_claude_adapter_converts_timeout_to_retryable_error(self, run):
        run.side_effect = subprocess.TimeoutExpired(cmd=("claude",), timeout=12)
        adapter = ClaudeCodeCriticAdapter(
            CriticConfig(type="claude_code", timeout_seconds=12),
            RoleIdentityConfig("claude-reviewer", "claude", "agent-default"),
        )

        with self.assertRaisesRegex(CriticAdapterError, "timed out after 12"):
            adapter.review(Path.cwd(), self._bundle())

    def test_reject_requires_correction_brief(self):
        judgment = self._approve_judgment()
        judgment["decision"] = "reject"
        judgment["findings"] = [
            {
                "code": "TEST",
                "severity": "error",
                "message": "Missing test.",
                "path": "tests/test_feature.py",
            }
        ]

        with self.assertRaisesRegex(CriticAdapterError, "requires correction_brief"):
            build_critic_decision(
                self._bundle(),
                RoleIdentityConfig("claude-reviewer", "claude", "sonnet"),
                judgment,
            )

    def test_decision_id_is_stable_per_bundle_and_distinct_across_bundles(self):
        identity = RoleIdentityConfig("claude-reviewer", "claude", "sonnet")
        first = build_critic_decision(
            self._bundle(),
            identity,
            self._approve_judgment(),
            created_at_utc="2026-07-17T12:00:00Z",
        )
        same = build_critic_decision(
            self._bundle(),
            identity,
            self._approve_judgment(),
            created_at_utc="2026-07-17T12:00:00Z",
        )
        other_bundle = self._bundle()
        other_bundle["bundle_id"] = "three-head-run-2"
        other = build_critic_decision(
            other_bundle,
            identity,
            self._approve_judgment(),
            created_at_utc="2026-07-17T12:00:00Z",
        )

        self.assertEqual(first["decision_id"], same["decision_id"])
        self.assertNotEqual(first["decision_id"], other["decision_id"])

    @staticmethod
    def _approve_judgment():
        return {
            "decision": "approve",
            "summary": "Evidence satisfies the task.",
            "findings": [],
            "correction_brief": None,
            "escalation": None,
        }

    @staticmethod
    def _bundle():
        return {
            "protocol": "hoh.protocol",
            "protocol_version": "2.0",
            "message_type": "critic.review_bundle",
            "bundle_id": "three-head-run-1",
            "artifact": {
                "commit": "b" * 40,
                "patch_sha256": "c" * 64,
            },
            "integrity": {"payload_sha256": "a" * 64},
        }


if __name__ == "__main__":
    unittest.main()
