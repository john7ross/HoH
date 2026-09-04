from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess
import tempfile
import unittest

from llm_harness.domain import WorkItem
from llm_harness.state import HohStateStore, default_state_root
from llm_harness.workspace import WorkspaceRegistry


class WorkspaceRegistryTests(unittest.TestCase):
    def test_register_aggregate_schedule_and_remove_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = WorkspaceRegistry(base / "workspace.json")
            first = base / "first"
            second = base / "second"
            subprocess.run(["git", "init", "-q", str(first)], check=True)
            subprocess.run(["git", "init", "-q", str(second)], check=True)

            first_entry = registry.register(first, "First project")
            second_entry = registry.register(second)
            registry.register(first, "Renamed project")
            self.assertEqual(len(registry.load().projects), 2)
            self.assertEqual(registry.load().projects[0].name, "Renamed project")

            store = HohStateStore(default_state_root(first))
            store.enqueue(WorkItem("task-1", "Ready task", "Do work", ("done",), ("test",)))
            summaries = {item.project_id: item for item in registry.summaries()}
            self.assertEqual(summaries[first_entry.project_id].ready, 1)
            self.assertEqual(summaries[first_entry.project_id].next_task_id, "task-1")
            self.assertEqual(summaries[second_entry.project_id].queued, 0)

            scheduled = registry.configure_schedule(
                first_entry.project_id,
                enabled=True,
                interval_minutes=15,
                notify_telegram=True,
                start_immediately=True,
            )
            self.assertTrue(scheduled.schedule.enabled)
            self.assertTrue(scheduled.schedule.notify_telegram)
            due = registry.due_projects(datetime.now(UTC) + timedelta(seconds=1))
            self.assertEqual([item.project_id for item in due], [first_entry.project_id])

            recorded = registry.record_run(first_entry.project_id, "empty")
            self.assertEqual(recorded.last_run_status, "empty")
            self.assertGreater(
                datetime.fromisoformat(recorded.schedule.next_run_at_utc),  # type: ignore[arg-type]
                datetime.now(UTC),
            )
            registry.remove(second_entry.project_id)
            self.assertEqual([item.project_id for item in registry.load().projects], [first_entry.project_id])

    def test_register_rejects_non_git_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = WorkspaceRegistry(root / "workspace.json")
            (root / "plain").mkdir()
            with self.assertRaisesRegex(ValueError, "not a Git repository"):
                registry.register(root / "plain")


if __name__ == "__main__":
    unittest.main()
