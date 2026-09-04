from pathlib import Path
from io import StringIO
import json
import unittest
from unittest.mock import patch

from llm_harness.domain import WorkItem
from llm_harness._hermes_bootstrap.hoh_hermes_windows_patch import _is_git_bash_health_probe
from llm_harness.jobs import WorkerJob
from llm_harness.targets import LocalAgentTarget
from llm_harness.hermes import HermesAcpAdapter, _hermes_launch_kwargs, check_hermes_acp


class HermesAcpTests(unittest.TestCase):
    def test_check_hermes_acp_reports_success(self):
        completed = _Completed(returncode=0, stdout="Hermes ACP check OK\n", stderr="")

        with patch("llm_harness.hermes.subprocess.run", return_value=completed) as run:
            status = check_hermes_acp("hermes")

        self.assertTrue(status.ok)
        self.assertEqual(status.command, ("hermes", "acp", "--check"))
        self.assertIn("Hermes ACP check OK", status.stdout)
        run.assert_called_once()

    def test_build_prompt_enforces_isolated_worktree_contract(self):
        work_item = WorkItem(
            id="task-1",
            title="Implement adapter",
            objective="Create a transport adapter.",
            acceptance_criteria=("Adapter uses an isolated worktree.",),
            verification_commands=("python -m unittest",),
            allowed_paths=("src/llm_harness",),
            non_goals=("Do not commit.",),
        )

        prompt = HermesAcpAdapter().build_prompt(Path("C:/repo"), work_item)

        self.assertIn("isolated repository path", prompt)
        self.assertIn("HoH will collect the diff", prompt)
        self.assertIn("Do not commit", prompt)
        self.assertIn("src/llm_harness", prompt)
        self.assertIn("Adapter uses an isolated worktree.", prompt)

    def test_run_job_rejects_canonical_branch(self):
        job = _job()

        with patch("llm_harness.hermes.subprocess.run", return_value=_Completed(0, "main\n", "")):
            completion = HermesAcpAdapter(turn_timeout_seconds=1).run_job(Path("C:/repo"), job)

        self.assertIsNotNone(completion.error)
        self.assertIn("isolated hoh/attempt", completion.error or "")

    def test_run_job_sends_prompt_to_hermes_acp(self):
        process = _FakePopen(
            [
                {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}},
                {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "session-1"}},
                {"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "complete"}},
            ]
        )
        job = _job()
        repository = Path("C:/repo")

        with (
            patch("llm_harness.hermes.subprocess.run", return_value=_Completed(0, "hoh/attempt/task/abc\n", "")),
            patch("llm_harness.hermes.subprocess.Popen", return_value=process) as popen,
        ):
            completion = HermesAcpAdapter(turn_timeout_seconds=5).run_job(repository, job)

        self.assertIsNone(completion.error)
        self.assertIsNone(completion.patch)
        popen.assert_called_once()
        popen_kwargs = popen.call_args.kwargs
        for key, value in _hermes_launch_kwargs().items():
            self.assertEqual(popen_kwargs[key], value)
        self.assertEqual(popen_kwargs["env"]["HERMES_ACP_SKIP_CONFIGURED_MCP"], "1")
        self.assertEqual(popen_kwargs["env"]["HOH_HERMES_WINDOWS_ACP_WORKAROUND"], "1")
        self.assertIn("_hermes_bootstrap", popen_kwargs["env"]["PYTHONPATH"])
        outbound = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        self.assertEqual([item["method"] for item in outbound], ["initialize", "session/new", "session/prompt"])
        self.assertEqual(outbound[1]["params"]["cwd"], str(repository.resolve()))
        self.assertIn("Task id: task-1", outbound[2]["params"]["prompt"][0]["text"])

    def test_windows_workaround_matches_only_exact_git_bash_health_probe(self):
        probe = [
            r"C:\Program Files\Git\usr\bin\bash.exe",
            "--noprofile",
            "--norc",
            "-c",
            "/usr/bin/true; /usr/bin/cat --version >/dev/null",
        ]

        self.assertTrue(_is_git_bash_health_probe(probe))
        self.assertFalse(_is_git_bash_health_probe([*probe[:-1], "git status"]))
        self.assertFalse(_is_git_bash_health_probe(["powershell.exe", *probe[1:]]))


class _Completed:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakePopen:
    def __init__(self, responses: list[dict]) -> None:
        self.stdin = StringIO()
        self.stdout = StringIO("".join(json.dumps(response) + "\n" for response in responses))
        self.stderr = StringIO()
        self.terminated = False
        self.killed = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self.terminated = True
        return 0


def _job() -> WorkerJob:
    work_item = WorkItem(
        id="task-1",
        title="Implement adapter",
        objective="Create a transport adapter.",
        acceptance_criteria=("Adapter uses an isolated worktree.",),
        verification_commands=("python -m unittest",),
        allowed_paths=("src/llm_harness",),
        non_goals=("Do not commit.",),
    )
    return WorkerJob(
        id="job-1",
        work_item=work_item,
        target=LocalAgentTarget(name="hermes", command="hermes", args=("acp",)),
        callback_token="token-1",
    )


if __name__ == "__main__":
    unittest.main()
