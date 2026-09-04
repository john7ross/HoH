from pathlib import Path
import contextlib
import io
import tempfile
import unittest

from llm_harness.cli import main
from llm_harness.state import HohStateStore
from llm_harness.workflow_smoke import run_supervised_workflow_smoke


class SupervisedWorkflowSmokeTests(unittest.TestCase):
    def test_ready_smoke_runs_full_supervised_daily_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp) / "repo"
            report = run_supervised_workflow_smoke(repository)
            store = HohStateStore(report.state_root)
            tasks = store.list_tasks()
            brief_exists = report.brief_path.exists()
            roadmap_exists = report.roadmap_path.exists()
            audit_report_exists = report.audit_report_path.exists() if report.audit_report_path else False
            journal_actions = [event.action for event in store.journal.events()]

        self.assertTrue(report.ok)
        self.assertEqual(report.workflow_status, "ready")
        self.assertEqual(report.completed_tasks, 2)
        self.assertEqual(report.history_count, 2)
        self.assertEqual([task.status for task in tasks], ["done", "done"])
        self.assertTrue(brief_exists)
        self.assertTrue(roadmap_exists)
        self.assertTrue(report.audit_ready)
        self.assertIsNotNone(report.audit_report_path)
        self.assertTrue(audit_report_exists)
        self.assertEqual(len(report.notifier_messages), 1)
        self.assertIn("project ready", report.notifier_messages[0])
        self.assertGreater(report.journal_events, 0)
        self.assertIn("worker.task_dispatched", journal_actions)
        self.assertIn("verification.command", journal_actions)
        self.assertIn("git.commit", journal_actions)

    def test_blocker_smoke_notifies_without_external_telegram(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp) / "repo"
            report = run_supervised_workflow_smoke(repository, mode="blocker")
            store = HohStateStore(report.state_root)
            tasks = store.list_tasks()

        self.assertTrue(report.ok)
        self.assertEqual(report.workflow_status, "blocked")
        self.assertEqual(report.completed_tasks, 0)
        self.assertEqual(report.history_count, 1)
        self.assertEqual(tasks[0].status, "failed")
        self.assertEqual(report.blocker_task_id, "smoke-deliverable")
        self.assertEqual(len(report.notifier_messages), 2)
        self.assertIn("Command worker exited with 7", report.notifier_messages[0])
        self.assertIn("HoH queue blocked", report.notifier_messages[1])
        self.assertIn("реши сам", report.notifier_messages[1])
        self.assertIn("свой", report.notifier_messages[1])

    def test_cli_workflow_smoke_ready_reports_handoff(self):
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            exit_code = main(["workflow-smoke"])

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertIn("ok=True", stdout.getvalue())
        self.assertIn("workflow_status=ready", stdout.getvalue())
        self.assertIn("final_audit_ready=True", stdout.getvalue())
        self.assertIn("notifications=1", stdout.getvalue())

    def test_cli_workflow_smoke_blocker_reports_expected_blocker(self):
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            exit_code = main(["workflow-smoke", "--mode", "blocker"])

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertIn("ok=True", stdout.getvalue())
        self.assertIn("workflow_status=blocked", stdout.getvalue())
        self.assertIn("blocked_task=smoke-deliverable", stdout.getvalue())
        self.assertIn("notifications=2", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
