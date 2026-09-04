import contextlib
from datetime import UTC, datetime
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from llm_harness.cli import main
from llm_harness.config import (
    CriticConfig,
    HarnessConfig,
    RoleIdentityConfig,
    ThreeHeadConfig,
)
from llm_harness.critic_adapters import CriticAdapterError, build_critic_decision
from llm_harness.critic_runtime import run_critic_review
from llm_harness.domain import WorkItem
from llm_harness.review_protocol import export_review_bundle
from llm_harness.state import HohStateStore
from llm_harness.supervisor import Supervisor
from llm_harness.verifier import PolicyVerifier
from llm_harness.workers import StubPatchWorker


class _ApproveCritic:
    name = "approve-critic"

    def __init__(self, identity):
        self.identity = identity

    def review(self, repository, bundle):
        return build_critic_decision(
            bundle,
            self.identity,
            {
                "decision": "approve",
                "summary": "All recorded evidence passes.",
                "findings": [],
                "correction_brief": None,
                "escalation": None,
            },
        )


class _FailingCritic:
    name = "failing-critic"

    def review(self, repository, bundle):
        raise CriticAdapterError("Critic transport unavailable.")


class CriticRuntimeTests(unittest.TestCase):
    @patch("llm_harness.critic_runtime.create_critic_adapter")
    def test_automatic_approve_imports_decision_and_closes_task(self, create_adapter):
        with tempfile.TemporaryDirectory() as tmp:
            repository, store, bundle, config = self._pending_review(Path(tmp))
            create_adapter.return_value = _ApproveCritic(config.three_head.critic)

            result = run_critic_review(repository, store, config, bundle=bundle)

            self.assertIs(create_adapter.call_args.args[2], config.verifier_model)
            self.assertEqual(result.decision.decision, "approve")
            self.assertEqual(result.task_status, "done")
            self.assertTrue(result.decision_path.exists())
            self.assertEqual(len(store.review_decisions()), 1)

    @patch("llm_harness.critic_runtime.create_critic_adapter")
    def test_transport_failure_keeps_pending_bundle_for_retry(self, create_adapter):
        with tempfile.TemporaryDirectory() as tmp:
            repository, store, bundle, config = self._pending_review(Path(tmp))
            create_adapter.return_value = _FailingCritic()

            with self.assertRaisesRegex(CriticAdapterError, "transport unavailable"):
                run_critic_review(repository, store, config, bundle=bundle)

            task = store.list_tasks()[0]
            self.assertEqual(task.status, "review_pending")
            self.assertEqual(task.pending_review_bundle_id, bundle.record.bundle_id)
            self.assertEqual(task.last_error, "Critic transport unavailable.")

    @patch("llm_harness.critic_runtime.create_critic_adapter")
    def test_critic_run_cli_retries_existing_pending_bundle(self, create_adapter):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, bundle, config = self._pending_review(root)
            create_adapter.return_value = _ApproveCritic(config.three_head.critic)
            config_path = root / "harness.toml"
            config_path.write_text(
                """
[critic]
type = "claude_code"
command = "claude"

[three_head]
mode = "required"

[three_head.roles.logic]
identity = "logic"
provider = "cloud"
model = "logic-model"

[three_head.roles.worker]
identity = "worker"
provider = "local"
model = "worker-model"

[three_head.roles.critic]
identity = "critic"
provider = "claude"
model = "sonnet"
""",
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "critic-run",
                        "--project-root",
                        str(repository),
                        "--state-root",
                        str(store.root),
                        "--bundle-id",
                        bundle.record.bundle_id,
                        "--config",
                        str(config_path),
                    ]
                )

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertIn("decision=approve", stdout.getvalue())
        self.assertIn("status=done", stdout.getvalue())

    def _pending_review(self, root: Path):
        repository = root / "repo"
        repository.mkdir()
        self._git(repository, "init")
        self._git(repository, "config", "user.email", "critic@example.local")
        self._git(repository, "config", "user.name", "Critic Test")
        (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
        self._git(repository, "add", ".gitignore")
        self._git(repository, "commit", "-m", "Initial commit")
        task = WorkItem(
            id="critic-runtime-task",
            title="Critic runtime task",
            objective="Create an artifact for live Critic review.",
            acceptance_criteria=("HARNESS_DEMO.md exists.",),
            verification_commands=(
                f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'HARNESS_DEMO.md\').exists()"',
            ),
            allowed_paths=("HARNESS_DEMO.md",),
            non_goals=("Do not let the Critic mutate the repository.",),
        )
        store = HohStateStore(root / "state")
        store.enqueue(task)
        store.mark_running(task.id)
        result = Supervisor(PolicyVerifier()).execute_work_item(
            repository,
            task,
            StubPatchWorker(),
        )
        run = store.record_result(
            task.id,
            datetime.now(UTC).isoformat(),
            result,
            review_required=True,
        )
        config = HarnessConfig(
            critic=CriticConfig(type="claude_code"),
            three_head=ThreeHeadConfig(
                mode="required",
                logic=RoleIdentityConfig("logic", "cloud", "logic-model"),
                worker=RoleIdentityConfig("worker", "local", "worker-model"),
                critic=RoleIdentityConfig("critic", "claude", "sonnet"),
            ),
        )
        bundle = export_review_bundle(
            repository,
            store,
            run.run_id,
            three_head=config.three_head,
        )
        store.attach_review_bundle(task.id, bundle.record.bundle_id)
        return repository, store, bundle, config

    @staticmethod
    def _git(repository: Path, *args: str) -> None:
        completed = subprocess.run(
            ("git", *args),
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)


if __name__ == "__main__":
    unittest.main()
