from pathlib import Path
import sys
import tempfile
import unittest

from llm_harness.command_worker import CommandWorker
from llm_harness.domain import WorkItem
from llm_harness.jobs import WorkerJob
from llm_harness.targets import LocalAgentTarget


class CommandWorkerTests(unittest.TestCase):
    def test_command_worker_passes_prompt_and_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            worker = CommandWorker(
                target=LocalAgentTarget(
                    name="python",
                    command=sys.executable,
                    args=(
                        "-c",
                        "import json, os, sys; "
                        "from pathlib import Path; "
                        "prompt = sys.stdin.read(); "
                        "payload = json.loads(os.environ['HOH_WORK_ITEM_JSON']); "
                        "assert 'Task id: command-task' in prompt; "
                        "assert os.environ['HOH_WORK_ITEM_ID'] == 'command-task'; "
                        "assert payload['allowed_paths'] == ['COMMAND_WORKER.md']; "
                        "Path(os.environ['HOH_REPOSITORY'], 'COMMAND_WORKER.md').write_text('ok\\n', encoding='utf-8')",
                    ),
                ),
                timeout_seconds=5,
            )
            job = self._job()

            completion = worker.run_job(repository, job)

            self.assertIsNone(completion.error)
            self.assertIsNone(completion.patch)
            self.assertEqual((repository / "COMMAND_WORKER.md").read_text(encoding="utf-8"), "ok\n")

    def test_command_worker_reports_non_zero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = CommandWorker(
                target=LocalAgentTarget(
                    name="python",
                    command=sys.executable,
                    args=("-c", "import sys; print('bad stdout'); print('bad stderr', file=sys.stderr); raise SystemExit(7)"),
                ),
                timeout_seconds=5,
            )

            completion = worker.run_job(Path(tmp), self._job())

        self.assertIsNotNone(completion.error)
        self.assertIn("exited with 7", completion.error or "")
        self.assertIn("bad stdout", completion.error or "")
        self.assertIn("bad stderr", completion.error or "")

    def test_command_worker_emits_available_process_telemetry(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = []
            worker = CommandWorker(
                target=LocalAgentTarget(name="python", command=sys.executable, args=("-c", "print('worker output')")),
                timeout_seconds=5,
                event_sink=lambda direction, payload: events.append((direction, payload)),
            )

            completion = worker.run_job(Path(tmp), self._job())

        self.assertIsNone(completion.error)
        self.assertEqual(events[0][0], "outbound")
        self.assertIn("Task id: command-task", events[0][1]["prompt"])
        self.assertEqual(events[1][0], "inbound")
        self.assertIn("worker output", events[1][1]["stdout"])

    def _job(self) -> WorkerJob:
        return WorkerJob(
            id="job-1",
            work_item=WorkItem(
                id="command-task",
                title="Command task",
                objective="Create a command worker artifact.",
                acceptance_criteria=("COMMAND_WORKER.md exists.",),
                verification_commands=("python -m unittest",),
                allowed_paths=("COMMAND_WORKER.md",),
                non_goals=("Do not commit.",),
            ),
            target=LocalAgentTarget(name="python", command=sys.executable),
            callback_token="token-1",
        )


if __name__ == "__main__":
    unittest.main()
