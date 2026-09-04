from datetime import UTC, datetime
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from support import write_architecture_docs

from llm_harness.cli import main
from llm_harness.domain import WorkItem
from llm_harness.protocol import PROTOCOL_NAMESPACE, PROTOCOL_VERSION
from llm_harness.review_protocol import REVIEW_DECISION_MESSAGE
from llm_harness.state import HohStateStore
from llm_harness.supervisor import Supervisor
from llm_harness.verifier import PolicyVerifier
from llm_harness.workers import StubPatchWorker


class MachineCliTests(unittest.TestCase):
    def test_read_only_supervisor_commands_emit_closed_versioned_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = root / "repo"
            repository.mkdir()
            state = root / "state"
            for command in (
                "queue-list",
                "queue-history",
                "audit-history",
                "rollback-history",
                "supervisor-status",
                "review-list",
            ):
                exit_code, payload = self._json_cli(
                    [command, "--project-root", str(repository), "--state-root", str(state), "--json"]
                )
                self.assertEqual(exit_code, 0, command)
                self.assertEqual(payload["protocol"], PROTOCOL_NAMESPACE)
                self.assertEqual(payload["protocol_version"], PROTOCOL_VERSION)
                self.assertIsInstance(payload["data"], dict)
            exit_code, payload = self._json_cli(
                ["reconcile-status", "--project-root", str(repository), "--json"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["message_type"], "reconciliation.status")
            self.assertEqual(payload["data"]["blocking"], 0)

    def test_audit_json_contains_full_findings_and_command_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp) / "repo"
            repository.mkdir()
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "machine@example.local")
            self._git(repository, "config", "user.name", "Machine CLI")
            (repository / "README.md").write_text("# Test\n", encoding="utf-8")
            write_architecture_docs(repository / "docs")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "Initial")

            exit_code, payload = self._json_cli(
                [
                    "audit",
                    "--project-root",
                    str(repository),
                    "--check",
                    f'"{sys.executable}" -c "raise SystemExit(0)"',
                    "--json",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["data"]["ready"])
            self.assertEqual(payload["data"]["findings"], [])
            self.assertTrue(payload["data"]["command_results"][0]["ok"])
            self.assertEqual(
                [item["status"] for item in payload["data"]["language_analyzers"]],
                ["unsupported", "unsupported", "unsupported"],
            )

            loop_code, loop_payload = self._json_cli(
                [
                    "queue-run-loop",
                    "--project-root",
                    str(repository),
                    "--state-root",
                    str(Path(tmp) / "state"),
                    "--final-audit",
                    "--final-check",
                    f'"{sys.executable}" -c "raise SystemExit(0)"',
                    "--json",
                ]
            )
            self.assertEqual(loop_code, 0)
            self.assertEqual(loop_payload["data"]["status"], "ready")
            self.assertTrue(loop_payload["data"]["audit"]["ready"])

    def test_audit_config_controls_strict_unused_file_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp) / "repo"
            repository.mkdir()
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "machine@example.local")
            self._git(repository, "config", "user.name", "Machine CLI")
            (repository / "README.md").write_text("# Test\n", encoding="utf-8")
            write_architecture_docs(repository / "docs")
            src = repository / "src"
            src.mkdir()
            (src / "main.py").write_text("print('main')\n", encoding="utf-8")
            (src / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "Initial")
            config = Path(tmp) / "harness.toml"
            config.write_text(
                '[audit]\nlanguage_analyzers = ["python"]\nentry_points = ["src/main.py"]\n',
                encoding="utf-8",
            )

            exit_code, payload = self._json_cli(
                [
                    "audit",
                    "--project-root",
                    str(repository),
                    "--config",
                    str(config),
                    "--check",
                    f'"{sys.executable}" -c "raise SystemExit(0)"',
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["data"]["ready"])
        self.assertEqual(payload["data"]["language_analyzers"][0]["status"], "findings")
        self.assertEqual(
            payload["data"]["language_analyzers"][0]["findings"][0]["code"],
            "PYTHON_UNUSED_FILE",
        )
        self.assertIn("PYTHON_UNUSED_FILE", {item["code"] for item in payload["data"]["findings"]})

    def test_queue_final_handoff_uses_audit_analyzer_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp) / "repo"
            repository.mkdir()
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "machine@example.local")
            self._git(repository, "config", "user.name", "Machine CLI")
            (repository / "README.md").write_text("# Test\n", encoding="utf-8")
            write_architecture_docs(repository / "docs")
            src = repository / "src"
            src.mkdir()
            (src / "main.py").write_text("print('main')\n", encoding="utf-8")
            (src / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "Initial")
            config = Path(tmp) / "harness.toml"
            config.write_text(
                '[audit]\nlanguage_analyzers = ["python"]\nentry_points = ["src/main.py"]\n',
                encoding="utf-8",
            )

            exit_code, payload = self._json_cli(
                [
                    "queue-run-loop",
                    "--project-root",
                    str(repository),
                    "--state-root",
                    str(Path(tmp) / "state"),
                    "--config",
                    str(config),
                    "--final-audit",
                    "--final-check",
                    f'"{sys.executable}" -c "raise SystemExit(0)"',
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["data"]["status"], "audit_failed")
        self.assertEqual(payload["data"]["audit"]["language_analyzers"][0]["status"], "findings")

    def test_review_export_import_and_status_json_gate_external_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, state, run_id = self._completed_run(root)
            output = root / "handoff" / "bundle.json"
            export_code, exported = self._json_cli(
                [
                    "review-export",
                    "--project-root",
                    str(repository),
                    "--state-root",
                    str(state),
                    "--run-id",
                    run_id,
                    "--output",
                    str(output),
                    "--json",
                ]
            )
            self.assertEqual(export_code, 0)
            self.assertTrue(output.exists())

            pending_code, pending = self._json_cli(
                ["supervisor-status", "--project-root", str(repository), "--state-root", str(state), "--json"]
            )
            self.assertEqual(pending_code, 1)
            self.assertEqual(pending["data"]["reviews"]["pending"], 1)

            handoff_code, handoff = self._json_cli(
                [
                    "queue-run-loop",
                    "--project-root",
                    str(repository),
                    "--state-root",
                    str(state),
                    "--final-audit",
                    "--final-check",
                    f'"{sys.executable}" -c "raise SystemExit(0)"',
                    "--json",
                ]
            )
            self.assertEqual(handoff_code, 1)
            self.assertEqual(handoff["data"]["status"], "review_blocked")
            self.assertEqual(HohStateStore(state).audit_reports(), ())

            bundle = json.loads(output.read_text(encoding="utf-8"))
            decision_path = root / "decision.json"
            decision_path.write_text(json.dumps(self._approval(bundle)), encoding="utf-8")
            import_code, imported = self._json_cli(
                [
                    "review-import",
                    "--project-root",
                    str(repository),
                    "--state-root",
                    str(state),
                    "--decision",
                    str(decision_path),
                    "--json",
                ]
            )

            self.assertEqual(import_code, 0)
            self.assertTrue(imported["ok"])
            self.assertEqual(imported["data"]["decision"], "approve")
            self.assertEqual(imported["data"]["bundle_sha256"], exported["data"]["bundle_sha256"])
            ready_code, ready = self._json_cli(
                ["supervisor-status", "--project-root", str(repository), "--state-root", str(state), "--json"]
            )
            self.assertEqual(ready_code, 0)
            self.assertEqual(ready["data"]["reviews"]["approved"], 1)

    def test_protocol_conformance_cli_is_machine_readable(self):
        root = Path(__file__).resolve().parents[1]
        exit_code, payload = self._json_cli(
            ["protocol-conformance", "--distribution-root", str(root), "--json"]
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["assets_checked"], 20)
        self.assertTrue(payload["data"]["canonical_git_clean"])

    def test_queue_mutation_and_execution_commands_emit_single_json_envelopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = root / "repo"
            repository.mkdir()
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "queue@example.local")
            self._git(repository, "config", "user.name", "Queue Machine")
            (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
            self._git(repository, "add", ".gitignore")
            self._git(repository, "commit", "-m", "Initial")
            state = root / "state"
            task_path = root / "task.json"
            task_path.write_text(json.dumps(self._task_payload("queue-json")), encoding="utf-8")
            config = root / "harness.toml"
            config.write_text(
                '[worker]\ntype = "stub"\ncommand = "stub"\nargs = []\ntimeout_seconds = 5\n',
                encoding="utf-8",
            )

            add_code, added = self._json_cli(
                [
                    "queue-add",
                    "--project-root",
                    str(repository),
                    "--state-root",
                    str(state),
                    "--task",
                    str(task_path),
                    "--json",
                ]
            )
            self.assertEqual(add_code, 0)
            self.assertEqual(added["message_type"], "queue.add")

            loop_code, loop = self._json_cli(
                [
                    "queue-run-loop",
                    "--project-root",
                    str(repository),
                    "--state-root",
                    str(state),
                    "--config",
                    str(config),
                    "--json",
                ]
            )
            self.assertEqual(loop_code, 0)
            self.assertEqual(loop["message_type"], "queue.run_loop")
            self.assertEqual(loop["data"]["completed"], 1)
            self.assertTrue(loop["data"]["runs"][0]["result"]["ok"])

            next_code, next_payload = self._json_cli(
                [
                    "queue-run-next",
                    "--project-root",
                    str(repository),
                    "--state-root",
                    str(state),
                    "--config",
                    str(config),
                    "--json",
                ]
            )
            self.assertEqual(next_code, 1)
            self.assertEqual(next_payload["data"]["status"], "empty")

            store = HohStateStore(state)
            retry_item = WorkItem(**self._work_item_kwargs("retry-json"))
            store.enqueue(retry_item)
            store.mark_running(retry_item.id)
            store.record_failure(retry_item.id, datetime.now(UTC).isoformat(), "failed")
            retry_code, retried = self._json_cli(
                [
                    "queue-retry",
                    "--project-root",
                    str(repository),
                    "--state-root",
                    str(state),
                    "--task-id",
                    retry_item.id,
                    "--json",
                ]
            )
            self.assertEqual(retry_code, 0)
            self.assertEqual(retried["data"]["task"]["status"], "queued")

            recover_item = WorkItem(**self._work_item_kwargs("recover-json"))
            store.enqueue(recover_item)
            store.mark_running(recover_item.id)
            recover_code, recovered = self._json_cli(
                [
                    "queue-recover-running",
                    "--project-root",
                    str(repository),
                    "--state-root",
                    str(state),
                    "--task-id",
                    recover_item.id,
                    "--reason",
                    "confirmed interrupted",
                    "--json",
                ]
            )
            self.assertEqual(recover_code, 0)
            self.assertEqual(recovered["data"]["task"]["last_error"], "confirmed interrupted")

    def _completed_run(self, root: Path) -> tuple[Path, Path, str]:
        repository = root / "repo"
        repository.mkdir()
        self._git(repository, "init")
        self._git(repository, "config", "user.email", "machine@example.local")
        self._git(repository, "config", "user.name", "Machine CLI")
        (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
        self._git(repository, "add", ".gitignore")
        self._git(repository, "commit", "-m", "Initial")
        work_item = WorkItem(
            id="machine-review",
            title="Machine review",
            objective="Produce exact review evidence.",
            acceptance_criteria=("HARNESS_DEMO.md exists.",),
            verification_commands=(
                f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'HARNESS_DEMO.md\').exists()"',
            ),
            allowed_paths=("HARNESS_DEMO.md",),
            non_goals=("Worker and verifier do not commit or merge.",),
        )
        state = root / "state"
        store = HohStateStore(state)
        store.enqueue(work_item)
        store.mark_running(work_item.id)
        result = Supervisor(PolicyVerifier()).execute_work_item(repository, work_item, StubPatchWorker())
        run = store.record_result(work_item.id, datetime.now(UTC).isoformat(), result)
        return repository, state, run.run_id

    def _approval(self, bundle: dict) -> dict:
        return {
            "protocol": PROTOCOL_NAMESPACE,
            "protocol_version": PROTOCOL_VERSION,
            "message_type": REVIEW_DECISION_MESSAGE,
            "decision_id": "machine-approval",
            "bundle_id": bundle["bundle_id"],
            "bundle_sha256": bundle["integrity"]["payload_sha256"],
            "reviewer": {"id": "machine-verifier", "kind": "agent"},
            "decision": "approve",
            "summary": "Evidence accepted.",
            "findings": [],
            "created_at_utc": datetime.now(UTC).isoformat(),
            "attestation": {
                "reviewed_commit": bundle["artifact"]["commit"],
                "reviewed_patch_sha256": bundle["artifact"]["patch_sha256"],
            },
        }

    def _task_payload(self, task_id: str) -> dict:
        return {
            "id": task_id,
            "title": "Queue JSON",
            "objective": "Exercise a machine-readable queue command.",
            "acceptance_criteria": ["HARNESS_DEMO.md exists."],
            "verification_commands": [
                f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'HARNESS_DEMO.md\').exists()"'
            ],
            "allowed_paths": ["HARNESS_DEMO.md"],
            "non_goals": ["Do not commit from worker."],
        }

    def _work_item_kwargs(self, task_id: str) -> dict:
        payload = self._task_payload(task_id)
        return {
            "id": payload["id"],
            "title": payload["title"],
            "objective": payload["objective"],
            "acceptance_criteria": tuple(payload["acceptance_criteria"]),
            "verification_commands": tuple(payload["verification_commands"]),
            "allowed_paths": tuple(payload["allowed_paths"]),
            "non_goals": tuple(payload["non_goals"]),
        }

    def _json_cli(self, argv: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(argv)
        return exit_code, json.loads(stdout.getvalue())

    def _git(self, repository: Path, *args: str) -> None:
        completed = subprocess.run(["git", *args], cwd=repository, text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)


if __name__ == "__main__":
    unittest.main()
