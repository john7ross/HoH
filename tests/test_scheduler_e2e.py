import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from llm_harness.cli import main
from llm_harness.state import HohStateStore


class SchedulerEndToEndTests(unittest.TestCase):
    def test_project_plan_runs_priority_and_dependency_dag_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = root / "repo"
            repository.mkdir()
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "scheduler@example.local")
            self._git(repository, "config", "user.name", "Scheduler E2E")
            (repository / "README.md").write_text("# Scheduler E2E\n", encoding="utf-8")
            self._git(repository, "add", "README.md")
            self._git(repository, "commit", "-m", "Initial")

            worker = root / "worker.py"
            worker.write_text(
                "import os\n"
                "from pathlib import Path\n"
                "task_id = os.environ['HOH_WORK_ITEM_ID']\n"
                "Path(f'{task_id}.md').write_text(f'{task_id}\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            config = root / "harness.toml"
            config.write_text(
                "[worker]\n"
                'type = "command"\n'
                f'command = "{_toml_path(sys.executable)}"\n'
                f'args = ["{_toml_path(worker)}"]\n'
                "timeout_seconds = 30\n",
                encoding="utf-8",
            )
            spec_path = root / "project.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "id": "scheduler-e2e",
                        "title": "Scheduler E2E",
                        "goal": "Prove deterministic DAG execution.",
                        "customer": "Test",
                        "business_requirements": ["Dependencies and priorities control order."],
                        "definition_of_done": ["All three tasks are done."],
                        "tasks": [
                            self._task("foundation", priority=1),
                            self._task("dependent", priority=100, depends_on=["foundation"]),
                            self._task("urgent", priority=50),
                        ],
                    }
                ),
                encoding="utf-8",
            )
            state = root / "state"

            with contextlib.redirect_stdout(io.StringIO()):
                plan_code = main(
                    [
                        "project-plan",
                        "--project-root",
                        str(repository),
                        "--state-root",
                        str(state),
                        "--spec",
                        str(spec_path),
                        "--write",
                        "--enqueue",
                    ]
                )
            self._git(repository, "add", "docs/hoh", "tasks/hoh")
            self._git(repository, "commit", "-m", "Materialize scheduler plan")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                loop_code = main(
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

            payload = json.loads(stdout.getvalue())
            store = HohStateStore(state)
            history = store.history()

            self.assertEqual(plan_code, 0)
            self.assertEqual(loop_code, 0, stdout.getvalue())
            self.assertEqual(payload["data"]["status"], "empty")
            self.assertEqual([record.work_item_id for record in history], ["urgent", "foundation", "dependent"])
            self.assertEqual([task.status for task in store.list_tasks()], ["done", "done", "done"])
            self.assertTrue(all((repository / f"{task_id}.md").exists() for task_id in ("urgent", "foundation", "dependent")))
            self.assertEqual(self._git(repository, "status", "--porcelain"), "")

    def test_parallel_workers_overlap_but_supervisor_commits_in_priority_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = root / "repo"
            repository.mkdir()
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "scheduler@example.local")
            self._git(repository, "config", "user.name", "Scheduler E2E")
            (repository / "README.md").write_text("# Parallel Scheduler E2E\n", encoding="utf-8")
            self._git(repository, "add", "README.md")
            self._git(repository, "commit", "-m", "Initial")

            barrier = root / "barrier"
            barrier.mkdir()
            worker = root / "parallel_worker.py"
            worker.write_text(
                "import os, sys, time\n"
                "from pathlib import Path\n"
                "task_id = os.environ['HOH_WORK_ITEM_ID']\n"
                "barrier = Path(sys.argv[1])\n"
                "(barrier / task_id).write_text('ready', encoding='utf-8')\n"
                "deadline = time.monotonic() + 5\n"
                "while len(list(barrier.iterdir())) < 2 and time.monotonic() < deadline:\n"
                "    time.sleep(0.02)\n"
                "if len(list(barrier.iterdir())) < 2:\n"
                "    raise SystemExit('parallel barrier timed out')\n"
                "Path(f'{task_id}.md').write_text(f'{task_id}\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            config = root / "harness.toml"
            config.write_text(
                "[scheduler]\n"
                "max_parallel_tasks = 2\n"
                "[worker]\n"
                'type = "command"\n'
                f'command = "{_toml_path(sys.executable)}"\n'
                f'args = ["{_toml_path(worker)}", "{_toml_path(barrier)}"]\n'
                "timeout_seconds = 15\n",
                encoding="utf-8",
            )
            state = root / "state"
            store = HohStateStore(state)
            store.enqueue_many(
                (
                    (self._work_item("high", priority=20), "test"),
                    (self._work_item("low", priority=10), "test"),
                )
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
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

            payload = json.loads(stdout.getvalue())
            subjects = self._git(repository, "log", "-2", "--format=%s").splitlines()
            self.assertEqual(code, 0, stdout.getvalue())
            self.assertEqual(payload["data"]["completed"], 2)
            self.assertEqual([record.work_item_id for record in store.history()], ["high", "low"])
            self.assertEqual(subjects, ["low: low", "high: high"])
            self.assertEqual({path.name for path in barrier.iterdir()}, {"high", "low"})
            self.assertEqual(self._git(repository, "status", "--porcelain"), "")

    def test_parallel_batch_records_inflight_success_then_stops_before_next_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = root / "repo"
            repository.mkdir()
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "scheduler@example.local")
            self._git(repository, "config", "user.name", "Scheduler E2E")
            (repository / "README.md").write_text("# Blocked Parallel Scheduler\n", encoding="utf-8")
            self._git(repository, "add", "README.md")
            self._git(repository, "commit", "-m", "Initial")

            barrier = root / "barrier"
            barrier.mkdir()
            worker = root / "blocking_worker.py"
            worker.write_text(
                "import os, sys, time\n"
                "from pathlib import Path\n"
                "task_id = os.environ['HOH_WORK_ITEM_ID']\n"
                "barrier = Path(sys.argv[1])\n"
                "(barrier / task_id).write_text('ready', encoding='utf-8')\n"
                "deadline = time.monotonic() + 5\n"
                "while len(list(barrier.iterdir())) < 2 and time.monotonic() < deadline:\n"
                "    time.sleep(0.02)\n"
                "if len(list(barrier.iterdir())) < 2:\n"
                "    raise SystemExit('parallel barrier timed out')\n"
                "if task_id == 'high':\n"
                "    raise SystemExit(7)\n"
                "Path(f'{task_id}.md').write_text(f'{task_id}\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            config = root / "harness.toml"
            config.write_text(
                "[scheduler]\nmax_parallel_tasks = 2\n"
                "[worker]\n"
                'type = "command"\n'
                f'command = "{_toml_path(sys.executable)}"\n'
                f'args = ["{_toml_path(worker)}", "{_toml_path(barrier)}"]\n'
                "timeout_seconds = 15\n",
                encoding="utf-8",
            )
            state = root / "state"
            store = HohStateStore(state)
            store.enqueue_many(
                (
                    (self._work_item("high", priority=30), "test"),
                    (self._work_item("low", priority=20), "test"),
                    (self._work_item("later", priority=10), "test"),
                )
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
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

            payload = json.loads(stdout.getvalue())
            statuses = {task.work_item.id: task.status for task in store.list_tasks()}
            self.assertEqual(code, 1, stdout.getvalue())
            self.assertEqual(payload["data"]["status"], "blocked")
            self.assertEqual(payload["data"]["blocked_task"], "high")
            self.assertEqual(payload["data"]["completed"], 1)
            self.assertEqual([record.work_item_id for record in store.history()], ["high", "low"])
            self.assertEqual(statuses, {"high": "failed", "low": "done", "later": "queued"})
            self.assertTrue((repository / "low.md").exists())
            self.assertFalse((repository / "later.md").exists())
            self.assertEqual({path.name for path in barrier.iterdir()}, {"high", "low"})
            self.assertEqual(self._git(repository, "status", "--porcelain"), "")

    def _task(self, task_id: str, *, priority: int, depends_on: list[str] | None = None) -> dict:
        return {
            "id": task_id,
            "title": task_id,
            "objective": f"Create {task_id}.md.",
            "acceptance_criteria": [f"{task_id}.md exists."],
            "verification_commands": [
                f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'{task_id}.md\').exists()"'
            ],
            "allowed_paths": [f"{task_id}.md"],
            "non_goals": ["Do not edit other files."],
            "depends_on": depends_on or [],
            "priority": priority,
        }

    def _work_item(self, task_id: str, *, priority: int):
        from llm_harness.domain import WorkItem

        return WorkItem(
            id=task_id,
            title=task_id,
            objective=f"Create {task_id}.md.",
            acceptance_criteria=(f"{task_id}.md exists.",),
            verification_commands=(
                f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'{task_id}.md\').exists()"',
            ),
            allowed_paths=(f"{task_id}.md",),
            non_goals=("Do not edit other files.",),
            priority=priority,
        )

    def _git(self, repository: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            self.fail(completed.stderr)
        return completed.stdout.strip()


def _toml_path(path: str | Path) -> str:
    return str(path).replace("\\", "\\\\")


if __name__ == "__main__":
    unittest.main()
