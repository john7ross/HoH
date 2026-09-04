from pathlib import Path
import tempfile
import unittest

from llm_harness.tasks import TaskLoadError, load_work_item


class TaskLoaderTests(unittest.TestCase):
    def test_load_json_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.json"
            task.write_text(
                """{
  "id": "task-1",
  "title": "Create artifact",
  "objective": "Create a deterministic artifact.",
  "acceptance_criteria": ["HARNESS_DEMO.md exists."],
  "verification_commands": ["python -m unittest"],
  "allowed_paths": ["HARNESS_DEMO.md"],
  "non_goals": ["Do not edit docs."],
  "depends_on": ["task-0"],
  "priority": 7
}
""",
                encoding="utf-8",
            )

            work_item = load_work_item(task)

        self.assertEqual(work_item.id, "task-1")
        self.assertEqual(work_item.title, "Create artifact")
        self.assertEqual(work_item.acceptance_criteria, ("HARNESS_DEMO.md exists.",))
        self.assertEqual(work_item.allowed_paths, ("HARNESS_DEMO.md",))
        self.assertEqual(work_item.depends_on, ("task-0",))
        self.assertEqual(work_item.priority, 7)

    def test_task_saved_with_a_byte_order_mark_still_loads(self):
        """A task file is written by a person, often in a Windows editor."""
        body = (
            '{"id": "task-bom", "title": "T", "objective": "O", '
            '"acceptance_criteria": ["A"], "verification_commands": ["python -m unittest"], '
            '"allowed_paths": ["X.md"]}\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.json"
            task.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))

            work_item = load_work_item(task)

        self.assertEqual(work_item.id, "task-bom")

    def test_load_markdown_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.md"
            task.write_text(
                """# Create artifact

## id

task-2

## objective

Create a deterministic artifact.

## acceptance criteria

- HARNESS_DEMO.md exists.

## verification commands

- python -m unittest

## allowed paths

- HARNESS_DEMO.md

## non-goals

- Do not edit docs.

## depends on

- task-1

## priority

3
""",
                encoding="utf-8",
            )

            work_item = load_work_item(task)

        self.assertEqual(work_item.id, "task-2")
        self.assertEqual(work_item.title, "Create artifact")
        self.assertEqual(work_item.verification_commands, ("python -m unittest",))
        self.assertEqual(work_item.non_goals, ("Do not edit docs.",))
        self.assertEqual(work_item.depends_on, ("task-1",))
        self.assertEqual(work_item.priority, 3)

    def test_json_priority_must_be_integer(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.json"
            task.write_text(
                """{
  "id": "task-invalid-priority",
  "title": "Invalid priority",
  "objective": "Reject ambiguous scheduler metadata.",
  "acceptance_criteria": ["Rejected."],
  "verification_commands": ["python -m unittest"],
  "priority": "high"
}
""",
                encoding="utf-8",
            )

            with self.assertRaises(TaskLoadError):
                load_work_item(task)

    def test_rejects_missing_required_task_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.json"
            task.write_text('{"id": "task-3"}', encoding="utf-8")

            with self.assertRaises(TaskLoadError):
                load_work_item(task)

    def test_rejects_unknown_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.txt"
            task.write_text("task", encoding="utf-8")

            with self.assertRaises(TaskLoadError):
                load_work_item(task)


if __name__ == "__main__":
    unittest.main()
