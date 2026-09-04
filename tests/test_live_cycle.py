"""End-to-end cycle over a real repository with a real external worker process.

Every other test drives the control plane with an in-process stub and a patch
that creates a new file. A new-file patch carries no context lines, so it
applies even when the patch pipeline has corrupted it — which is how a bug that
blocked the queue on its first task on Windows survived a fully green suite.

This test therefore insists on the awkward parts: an external process, edits to
existing files, more than one file per task, a dependency edge between tasks,
and a task that tries to write outside its allowed paths.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKER = FIXTURES / "live_worker.py"


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


class LiveCycleTests(unittest.TestCase):
    maxDiff = None

    def test_three_role_cycle_over_a_real_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            repository = self._seed_repository(workspace / "repo")
            state_root = workspace / "state"
            config = self._write_config(workspace / "harness.toml")

            for task in ("quote-aware-split", "document-quoting", "out-of-scope"):
                self._hoh(
                    "queue-add",
                    "--project-root", str(repository),
                    "--state-root", str(state_root),
                    "--task", str(self._write_task(workspace, task)),
                )

            # The scope violation is expected to block the loop, so run until it stops.
            for _attempt in range(3):
                self._hoh(
                    "queue-run-loop",
                    "--project-root", str(repository),
                    "--state-root", str(state_root),
                    "--config", str(config),
                    "--timeout", "300",
                    check=False,
                )
                if self._status(repository, state_root, "out-of-scope") == "failed":
                    break

            statuses = {
                task: self._status(repository, state_root, task)
                for task in ("quote-aware-split", "document-quoting", "out-of-scope")
            }

            self.assertEqual(statuses["quote-aware-split"], "done", statuses)
            self.assertEqual(statuses["document-quoting"], "done", statuses)

            # The worker really changed existing files, and the Supervisor committed it.
            implementation = (repository / "src" / "calc" / "tokens.py").read_text(encoding="utf-8")
            self.assertIn("csv.reader", implementation)
            self.assertIn("Quoted fields", (repository / "README.md").read_text(encoding="utf-8"))

            # The project's own tests pass against the committed result.
            verification = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(verification.returncode, 0, verification.stderr)
            self.assertIn("Ran 3 tests", verification.stderr)

            # The scope violation was refused, and nothing of it reached the repository.
            self.assertEqual(statuses["out-of-scope"], "failed", statuses)
            self.assertIn("outside allowed paths", self._last_error(repository, state_root, "out-of-scope"))
            self.assertFalse((repository / "secrets.env").exists())

            # Two Supervisor commits on top of the seed, and a clean worktree.
            self.assertEqual(self._git(repository, "status", "--porcelain"), "")
            subjects = self._git(repository, "log", "--format=%s").splitlines()
            self.assertEqual(len(subjects), 3, subjects)
            self.assertTrue(subjects[0].startswith("document-quoting:"), subjects)
            self.assertTrue(subjects[1].startswith("quote-aware-split:"), subjects)

            # The dependent task never ran before the task it depends on.
            self.assertLess(
                self._finished_at(repository, state_root, "quote-aware-split"),
                self._finished_at(repository, state_root, "document-quoting"),
            )

            audit = self._hoh(
                "audit",
                "--project-root", str(repository),
                "--check", f'"{_posix(Path(sys.executable))}" -m unittest discover -s tests',
                "--json",
            )
            self.assertTrue(json.loads(audit.stdout)["ok"], audit.stdout)

    def _seed_repository(self, repository: Path) -> Path:
        shutil.copytree(FIXTURES / "live_project", repository)
        self._git(repository, "init", "-q", ".")
        self._git(repository, "config", "user.email", "live@example.local")
        self._git(repository, "config", "user.name", "Live Cycle")
        self._git(repository, "add", "-A")
        self._git(repository, "commit", "-qm", "Seed billing parser")
        return repository

    def _write_config(self, path: Path) -> Path:
        path.write_text(
            "\n".join(
                (
                    'worker_trust_level = "patch_only"',
                    "require_tests = true",
                    "",
                    "[runtime]",
                    "require_embedded_python = false",
                    "",
                    "[worker]",
                    'driver = "command"',
                    f'command = "{_posix(Path(sys.executable))}"',
                    f'args = ["{_posix(WORKER)}"]',
                    "timeout_seconds = 300",
                    "",
                    "[worker.capabilities]",
                    'task_transport = "stdin_prompt"',
                    'artifact_contract = "worktree_diff"',
                    "requires_isolated_worktree = true",
                    "supports_subagents = false",
                    "",
                )
            ),
            encoding="utf-8",
            newline="\n",
        )
        return path

    def _write_task(self, workspace: Path, task_id: str) -> Path:
        runner = f'"{_posix(Path(sys.executable))}" -m unittest discover -s tests'
        readme_check = (
            f'"{_posix(Path(sys.executable))}" -c '
            '"import pathlib,sys; sys.exit(0 if \'Quoted fields\' in '
            'pathlib.Path(\'README.md\').read_text(encoding=\'utf-8\') else 1)"'
        )
        definitions = {
            "quote-aware-split": {
                "id": "quote-aware-split",
                "title": "Keep commas inside quoted billing fields",
                "objective": "split_line must not break a quoted field, and must return [] for an empty line.",
                "acceptance_criteria": [
                    "A quoted field keeps its comma.",
                    "An empty line yields no fields.",
                ],
                "verification_commands": [runner],
                "allowed_paths": ["src/calc", "tests"],
                "non_goals": ["Do not rename the public function."],
                "depends_on": [],
                "priority": 10,
            },
            "document-quoting": {
                "id": "document-quoting",
                "title": "Document the quoted-field behaviour",
                "objective": "The README must show the quoted-field example.",
                "acceptance_criteria": ["README.md documents the quoted-field example."],
                "verification_commands": [readme_check],
                "allowed_paths": ["README.md"],
                "non_goals": ["Do not change library code."],
                "depends_on": ["quote-aware-split"],
                "priority": 5,
            },
            "out-of-scope": {
                "id": "out-of-scope",
                "title": "Write outside the declared scope",
                "objective": "The worker writes a file outside allowed_paths; the gate must refuse the patch.",
                "acceptance_criteria": ["The deterministic gate refuses the patch."],
                "verification_commands": [runner],
                "allowed_paths": ["src/calc"],
                "non_goals": ["Nothing."],
                "depends_on": [],
                "priority": 1,
            },
        }
        path = workspace / f"{task_id}.json"
        path.write_text(json.dumps(definitions[task_id], indent=2), encoding="utf-8", newline="\n")
        return path

    def _queue(self, repository: Path, state_root: Path) -> list[dict]:
        result = self._hoh(
            "queue-list",
            "--project-root", str(repository),
            "--state-root", str(state_root),
            "--json",
        )
        return json.loads(result.stdout)["data"]["tasks"]

    def _task(self, repository: Path, state_root: Path, task_id: str) -> dict:
        for task in self._queue(repository, state_root):
            if task["work_item"]["id"] == task_id:
                return task
        raise AssertionError(f"Task is not in the queue: {task_id}")

    def _status(self, repository: Path, state_root: Path, task_id: str) -> str:
        return self._task(repository, state_root, task_id)["status"]

    def _last_error(self, repository: Path, state_root: Path, task_id: str) -> str:
        return self._task(repository, state_root, task_id)["last_error"] or ""

    def _finished_at(self, repository: Path, state_root: Path, task_id: str) -> str:
        result = self._hoh(
            "queue-history",
            "--project-root", str(repository),
            "--state-root", str(state_root),
            "--json",
        )
        finished = [
            record["finished_at_utc"]
            for record in json.loads(result.stdout)["data"]["runs"]
            if record["work_item_id"] == task_id and record["ok"]
        ]
        self.assertTrue(finished, f"No successful run recorded for {task_id}")
        return max(finished)

    def _hoh(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [sys.executable, "-m", "llm_harness", *args],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check and completed.returncode != 0:
            raise AssertionError(
                f"hoh {args[0]} failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
            )
        return completed

    def _git(self, repository: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {completed.stderr}")
        return completed.stdout.strip()


if __name__ == "__main__":
    unittest.main()
