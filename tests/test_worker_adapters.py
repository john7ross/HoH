import unittest

from llm_harness.command_worker import CommandWorker
from llm_harness.config import WorkerConfig
from llm_harness.hermes import HermesAcpAdapter
from llm_harness.provider_workers import ClaudeCodeWorker, OpenClawWorker
from llm_harness.worker_adapters import WorkerAdapterError, create_worker_adapter
from llm_harness.workers import StubPatchWorker


class WorkerAdapterFactoryTests(unittest.TestCase):
    def test_create_hermes_acp_adapter_from_worker_config(self):
        spec = create_worker_adapter(
            WorkerConfig(
                type="hermes_acp",
                command="hermes",
                args=("acp",),
                timeout_seconds=123,
            )
        )

        self.assertEqual(spec.name, "hermes-acp")
        self.assertTrue(spec.isolated_worktree)
        self.assertEqual(spec.task_transport, "acp_stdio")
        self.assertEqual(spec.artifact_contract, "worktree_diff")
        self.assertFalse(spec.supports_subagents)
        self.assertEqual(spec.target.command, "hermes")
        self.assertEqual(spec.target.args, ("acp",))
        self.assertIsInstance(spec.worker, HermesAcpAdapter)
        self.assertEqual(spec.worker.turn_timeout_seconds, 123)

    def test_create_stub_adapter_for_tests_and_demo(self):
        spec = create_worker_adapter(WorkerConfig(type="stub", command="stub", args=()))

        self.assertEqual(spec.name, "stub-worker")
        self.assertFalse(spec.isolated_worktree)
        self.assertEqual(spec.task_transport, "in_process")
        self.assertEqual(spec.artifact_contract, "direct_patch")
        self.assertIsInstance(spec.worker, StubPatchWorker)

    def test_create_command_adapter_for_generic_cli_workers(self):
        spec = create_worker_adapter(
            WorkerConfig(
                type="command",
                command="worker-cli",
                args=("--run",),
                timeout_seconds=45,
            )
        )

        self.assertEqual(spec.name, "command-worker")
        self.assertTrue(spec.isolated_worktree)
        self.assertEqual(spec.task_transport, "stdin_prompt")
        self.assertEqual(spec.artifact_contract, "worktree_diff")
        self.assertEqual(spec.target.command, "worker-cli")
        self.assertEqual(spec.target.args, ("--run",))
        self.assertIsInstance(spec.worker, CommandWorker)
        self.assertEqual(spec.worker.timeout_seconds, 45)

    def test_rejects_unknown_worker_type(self):
        with self.assertRaises(WorkerAdapterError):
            create_worker_adapter(WorkerConfig(type="openclaw"))

    def test_create_named_claude_and_openclaw_adapters(self):
        claude = create_worker_adapter(
            WorkerConfig(type="claude_code", model="sonnet")
        )
        openclaw = create_worker_adapter(
            WorkerConfig(type="openclaw", model="openai/gpt-test")
        )
        self.assertIsInstance(claude.worker, ClaudeCodeWorker)
        self.assertEqual(claude.provider, "claude")
        self.assertIsInstance(openclaw.worker, OpenClawWorker)
        self.assertEqual(openclaw.provider, "openclaw")


if __name__ == "__main__":
    unittest.main()
