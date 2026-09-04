from pathlib import Path
import contextlib
import io
import tempfile
import unittest

from llm_harness.cli import main
from llm_harness.supervisor_protocol import build_supervisor_status
from llm_harness.workflow_smoke import run_supervised_workflow_smoke


class SupervisorProtocolTests(unittest.TestCase):
    def test_supervisor_status_reports_ready_workflow_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp) / "repo"
            smoke = run_supervised_workflow_smoke(repository)

            report = build_supervisor_status(repository, smoke.state_root)

        self.assertTrue(smoke.ok)
        self.assertTrue(report.ok)
        self.assertEqual(report.queued, 0)
        self.assertEqual(report.running, 0)
        self.assertEqual(report.done, 2)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.stale, 0)
        self.assertIsNotNone(report.latest_run)
        self.assertIsNotNone(report.latest_audit)
        self.assertTrue(report.latest_audit.ok if report.latest_audit else False)

    def test_cli_supervisor_status_reports_failed_blocker_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp) / "repo"
            smoke = run_supervised_workflow_smoke(repository, mode="blocker")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "supervisor-status",
                        "--project-root",
                        str(repository),
                        "--state-root",
                        str(smoke.state_root),
                    ]
                )

        self.assertTrue(smoke.ok)
        self.assertEqual(exit_code, 1)
        self.assertIn("ok=False", stdout.getvalue())
        self.assertIn("failed=1", stdout.getvalue())
        self.assertIn("latest_run=", stdout.getvalue())
        self.assertIn("task=smoke-deliverable", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
