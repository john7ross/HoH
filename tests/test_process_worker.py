from pathlib import Path
import json
import sys
import tempfile
import unittest

from llm_harness.config import (
    AgentConfig,
    HarnessConfig,
    ProcessProfileConfig,
    WorkerConfig,
    load_config,
    write_config,
)
from llm_harness.domain import WorkItem
from llm_harness.jobs import WorkerJob
from llm_harness.process_worker import ProfiledProcessWorker
from llm_harness.role_profiles import ProjectRoleProfile, RoleSelection, apply_role_profile
from llm_harness.targets import LocalAgentTarget
from llm_harness.worker_adapters import create_worker_adapter


class ProfiledProcessWorkerTests(unittest.TestCase):
    def test_file_prompt_profile_applies_required_args_and_model(self):
        script = (
            "import json,sys; from pathlib import Path; "
            "p=Path(sys.argv[sys.argv.index('--message-file')+1]); "
            "assert '--no-auto-commits' in sys.argv; "
            "assert sys.argv[sys.argv.index('--model')+1]=='test-model'; "
            "Path('RESULT.json').write_text(json.dumps({'prompt':p.read_text(encoding='utf-8')}),encoding='utf-8')"
        )
        profile = ProcessProfileConfig(
            profile_id="fake-file-cli",
            prompt_transport="file",
            prompt_argument="--message-file",
            required_args=("--no-auto-commits",),
            forbidden_args=("--auto-commits",),
            model_argument="--model",
        )
        worker = ProfiledProcessWorker(
            target=LocalAgentTarget("fake", sys.executable, ("-c", script)),
            profile=profile,
            model="test-model",
        )
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            completion = worker.run_job(repository, self._job())
            result = json.loads((repository / "RESULT.json").read_text(encoding="utf-8"))

        self.assertIsNone(completion.error)
        self.assertIn("low-trust local worker", result["prompt"])
        self.assertIn("Task id: process-test", result["prompt"])

    def test_stdin_profile_sends_prompt_on_standard_input(self):
        script = (
            "import sys; from pathlib import Path; "
            "Path('STDIN.txt').write_text(sys.stdin.read(),encoding='utf-8')"
        )
        worker = ProfiledProcessWorker(
            target=LocalAgentTarget("fake", sys.executable, ("-c", script)),
            profile=ProcessProfileConfig(profile_id="fake-stdin"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            completion = worker.run_job(repository, self._job())
            prompt = (repository / "STDIN.txt").read_text(encoding="utf-8")

        self.assertIsNone(completion.error)
        self.assertIn("Objective: Exercise declarative process transport.", prompt)

    def test_adapter_rejects_forbidden_or_secret_args(self):
        profile = ProcessProfileConfig(
            profile_id="safe-cli",
            forbidden_args=("--auto-commit",),
        )
        for configured_args in (("--auto-commit",), ("--api-key=secret",)):
            with self.subTest(configured_args=configured_args):
                with self.assertRaises(ValueError):
                    create_worker_adapter(
                        WorkerConfig(
                            driver="process",
                            command="fake",
                            args=configured_args,
                            process_profile=profile,
                        )
                    )

    def test_aider_is_a_declarative_default_profile(self):
        aider = next(item for item in HarnessConfig().agents if item.name == "aider")

        self.assertEqual(aider.worker_driver, "process")
        self.assertEqual(aider.worker_process_profile.profile_id, "aider")  # type: ignore[union-attr]
        self.assertIn("--no-auto-commits", aider.worker_process_profile.required_args)  # type: ignore[union-attr]
        self.assertIn("--no-dirty-commits", aider.worker_process_profile.required_args)  # type: ignore[union-attr]

    def test_process_profile_round_trips_and_flows_from_role_selection(self):
        process_profile = ProcessProfileConfig(
            profile_id="future-cli",
            prompt_transport="file",
            prompt_argument="--prompt-file",
            required_args=("--non-interactive",),
            forbidden_args=("--commit",),
            model_argument="--model",
        )
        base = HarnessConfig(
            agents=(
                AgentConfig(
                    name="future-cli",
                    command="future-cli",
                    worker_driver="process",
                    worker_process_profile=process_profile,
                ),
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "harness.json"
            write_config(path, base)
            loaded = load_config(path)

        effective = apply_role_profile(
            loaded,
            ProjectRoleProfile(
                supervisor=RoleSelection("future-cli", driver="command_json"),
                worker=RoleSelection("future-cli", model="future-model"),
                critic=None,
            ),
        )

        self.assertEqual(loaded.agents[0].worker_process_profile, process_profile)
        self.assertEqual(effective.worker.driver, "process")
        self.assertEqual(effective.worker.process_profile, process_profile)
        self.assertEqual(effective.worker.model, "future-model")

    @staticmethod
    def _job() -> WorkerJob:
        return WorkerJob(
            id="job-process",
            work_item=WorkItem(
                id="process-test",
                title="Process profile",
                objective="Exercise declarative process transport.",
                acceptance_criteria=("A fake CLI receives the prompt.",),
                verification_commands=("git status --short",),
                allowed_paths=("RESULT.json", "STDIN.txt"),
            ),
            target=LocalAgentTarget("fake", sys.executable),
            callback_token="callback-process",
        )


if __name__ == "__main__":
    unittest.main()
