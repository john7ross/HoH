import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from llm_harness.background_scheduler import run_due_projects
from llm_harness.desktop_notifications import DesktopNotificationRecord
from llm_harness.workspace import WorkspaceRegistry


class _DesktopStub:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def notify(self, title: str, text: str) -> DesktopNotificationRecord:
        self.messages.append((title, text))
        return DesktopNotificationRecord("id", "2026-01-01T00:00:00+00:00", title, text, "test", True)


class BackgroundSchedulerTests(unittest.TestCase):
    def test_due_project_runs_existing_queue_loop_and_advances_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            registry = WorkspaceRegistry(base / "workspace.json")
            project = registry.register(root, "Scheduled project")
            registry.configure_schedule(
                project.project_id,
                enabled=True,
                interval_minutes=10,
                start_immediately=True,
            )
            completed = subprocess.CompletedProcess(
                args=(),
                returncode=0,
                stdout=json.dumps({"data": {"status": "empty", "completed": 2}}),
                stderr="",
            )
            desktop = _DesktopStub()
            run = unittest.mock.Mock(return_value=completed)
            results = run_due_projects(registry, runner=run, desktop_notifier=desktop)  # type: ignore[arg-type]

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "empty")
            self.assertEqual(results[0].completed, 2)
            self.assertIn("queue-run-loop", run.call_args.args[0])
            self.assertEqual(len(desktop.messages), 1)
            updated = registry.load().projects[0]
            self.assertEqual(updated.last_run_status, "empty")
            self.assertIsNotNone(updated.schedule.next_run_at_utc)


if __name__ == "__main__":
    unittest.main()
