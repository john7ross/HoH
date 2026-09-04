from pathlib import Path
import tempfile
import unittest

from llm_harness.domain import HarnessRunResult, VerificationReport, WorkItem
from llm_harness.operator_decisions import handle_operator_command
from llm_harness.state import HohStateStore
from llm_harness.tasks import load_work_item


class OperatorDecisionTests(unittest.TestCase):
    def test_status_reports_counts_and_records_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")
            store.enqueue(load_work_item(self._task_file(Path(tmp))))

            result = handle_operator_command(store, "/status")

            self.assertTrue(result.ok)
            self.assertIn("queued=1", result.message)
            self.assertEqual(store.operator_events()[0].command, "status")

    def test_status_reports_rolled_back_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_item = load_work_item(self._task_file(root))
            store = HohStateStore(root / "state")
            store.enqueue(work_item)
            store.mark_running(work_item.id)
            store.record_result(
                work_item.id,
                "2026-01-01T00:00:00+00:00",
                HarnessRunResult(
                    repository=root,
                    work_item_id=work_item.id,
                    commit="abc123",
                    pre_apply=VerificationReport(ok=True),
                    post_apply=VerificationReport(ok=True),
                    command_results=(),
                ),
            )
            store.mark_rolled_back(work_item.id, "abc123", "def456")

            result = handle_operator_command(store, "/status")

            self.assertTrue(result.ok)
            self.assertIn("rolled_back=1", result.message)

    def test_retry_requeues_failed_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_item = load_work_item(self._task_file(root))
            store = HohStateStore(root / "state")
            store.enqueue(work_item)
            store.mark_running(work_item.id)
            store.record_failure(work_item.id, "2026-01-01T00:00:00+00:00", "worker failed")

            result = handle_operator_command(store, "/retry operator-task")

            self.assertTrue(result.ok)
            self.assertEqual(store.list_tasks()[0].status, "queued")
            self.assertEqual(store.operator_events()[0].task_id, "operator-task")

    def test_recover_requeues_running_task_with_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_item = load_work_item(self._task_file(root))
            store = HohStateStore(root / "state")
            store.enqueue(work_item)
            store.mark_running(work_item.id)

            result = handle_operator_command(store, "/recover operator-task process crashed")

            task = store.list_tasks()[0]
            self.assertTrue(result.ok)
            self.assertEqual(task.status, "queued")
            self.assertEqual(task.last_error, "process crashed")

    def test_unknown_command_is_recorded_as_failed_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")

            result = handle_operator_command(store, "/wat")

            self.assertFalse(result.ok)
            self.assertEqual(store.operator_events()[0].command, "wat")
            self.assertFalse(store.operator_events()[0].ok)

    def test_continue_is_acknowledged_without_mutating_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")

            result = handle_operator_command(store, "/continue")

            self.assertTrue(result.ok)
            self.assertTrue(result.should_continue)
            self.assertEqual(store.list_tasks(), ())

    def test_queue_command_reports_scheduler_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")
            base = WorkItem(
                id="base",
                title="Base",
                objective="Base.",
                acceptance_criteria=("Base.",),
                verification_commands=("check",),
            )
            dependent = WorkItem(
                id="dependent",
                title="Dependent",
                objective="Dependent.",
                acceptance_criteria=("Dependent.",),
                verification_commands=("check",),
                depends_on=("base",),
                priority=5,
            )
            store.enqueue_many(((base, ""), (dependent, "")))

            result = handle_operator_command(store, "/queue")

            self.assertIn("base: status=queued", result.message)
            self.assertIn("ready=True", result.message)
            self.assertIn("dependent: status=queued", result.message)
            self.assertIn("blockers=base=queued", result.message)

    def _task_file(self, root: Path) -> Path:
        task = root / "task.json"
        task.write_text(
            """{
  "id": "operator-task",
  "title": "Operator task",
  "objective": "Exercise operator decisions.",
  "acceptance_criteria": ["Operator command is handled."],
  "verification_commands": ["python -m unittest"],
  "allowed_paths": ["HARNESS_OPERATOR.md"],
  "non_goals": []
}
""",
            encoding="utf-8",
        )
        return task


if __name__ == "__main__":
    unittest.main()
