from pathlib import Path
import contextlib
import io
import sys
import tempfile
import unittest

from llm_harness.cli import main
from llm_harness.config import AgentConfig, HarnessConfig, ProcessProfileConfig, WorkerConfig
from llm_harness.conformance import run_worker_conformance
from llm_harness.role_profiles import ProjectRoleProfile, RoleSelection, apply_role_profile


class WorkerConformanceTests(unittest.TestCase):
    def test_command_worker_passes_conformance_suite(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_worker_conformance(
                Path(tmp) / "repo",
                HarnessConfig(worker=self._command_worker_config()),
                timeout_seconds=10,
            )

        self.assertTrue(report.ok)
        self.assertEqual(report.worker_type, "command")
        self.assertEqual(report.adapter_name, "command-worker")
        self.assertTrue(all(check.ok for check in report.checks))
        self.assertIsNotNone(report.run_result)
        self.assertIsNotNone(report.run_result.commit if report.run_result else None)

    def test_unknown_agent_reaches_supervisor_commit_through_declared_command_driver(self):
        script = "from pathlib import Path; Path('HARNESS_DEMO.md').write_text('neutral\\n', encoding='utf-8')"
        base = HarnessConfig(
            agents=(
                AgentConfig(
                    name="future-agent-9000",
                    command=sys.executable,
                    args=("-c", script),
                    supervisor_driver="command_json",
                    worker_driver="command",
                ),
            )
        )
        config = apply_role_profile(
            base,
            ProjectRoleProfile(
                supervisor=RoleSelection("future-agent-9000"),
                worker=RoleSelection("future-agent-9000"),
                critic=None,
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            report = run_worker_conformance(Path(tmp) / "repo", config, timeout_seconds=10)

        self.assertTrue(report.ok)
        self.assertEqual(config.worker.driver, "command")
        self.assertIsNotNone(report.run_result.commit if report.run_result else None)

    def test_declarative_file_process_reaches_supervisor_owned_commit(self):
        script = (
            "import sys; from pathlib import Path; "
            "p=Path(sys.argv[sys.argv.index('--prompt-file')+1]); "
            "assert 'low-trust local worker' in p.read_text(encoding='utf-8'); "
            "Path('HARNESS_DEMO.md').write_text('profiled\\n',encoding='utf-8')"
        )
        config = HarnessConfig(
            worker=WorkerConfig(
                driver="process",
                command=sys.executable,
                args=("-c", script),
                timeout_seconds=10,
                process_profile=ProcessProfileConfig(
                    profile_id="conformance-cli",
                    prompt_transport="file",
                    prompt_argument="--prompt-file",
                    required_args=("--non-interactive",),
                ),
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            report = run_worker_conformance(Path(tmp) / "repo", config, timeout_seconds=10)

        self.assertTrue(report.ok)
        self.assertEqual(report.worker_type, "process")
        self.assertEqual(report.adapter_name, "process-conformance-cli-worker")
        self.assertIsNotNone(report.run_result.commit if report.run_result else None)

    def test_conformance_fails_when_manifest_isolation_disagrees_with_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "harness.toml"
            config_path.write_text(
                self._command_worker_toml(requires_isolated_worktree=False),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "worker-conformance",
                        "--config",
                        str(config_path),
                        "--timeout",
                        "10",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("ok=False", stdout.getvalue())
        self.assertIn("check=isolation_matches_manifest ok=False", stdout.getvalue())

    def test_cli_worker_conformance_reports_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "harness.toml"
            config_path.write_text(
                self._command_worker_toml(requires_isolated_worktree=True),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "worker-conformance",
                        "--config",
                        str(config_path),
                        "--timeout",
                        "10",
                    ]
                )

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertIn("ok=True", stdout.getvalue())
        self.assertIn("worker_type=command", stdout.getvalue())
        self.assertIn("check=supervisor_run_ok ok=True", stdout.getvalue())

    def _command_worker_config(self) -> WorkerConfig:
        return WorkerConfig(
            type="command",
            command=sys.executable,
            args=(
                "-c",
                "from pathlib import Path; Path('HARNESS_DEMO.md').write_text('ok\\n', encoding='utf-8')",
            ),
            timeout_seconds=10,
        )

    def _command_worker_toml(self, requires_isolated_worktree: bool) -> str:
        escaped_python = sys.executable.replace("\\", "\\\\")
        return f"""
[worker]
type = "command"
command = "{escaped_python}"
args = ["-c", "from pathlib import Path; Path('HARNESS_DEMO.md').write_text('ok\\\\n', encoding='utf-8')"]
timeout_seconds = 10

[worker.capabilities]
task_transport = "stdin_prompt"
artifact_contract = "worktree_diff"
requires_isolated_worktree = {str(requires_isolated_worktree).lower()}
supports_subagents = false
"""


if __name__ == "__main__":
    unittest.main()
