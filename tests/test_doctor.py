import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from llm_harness.config import (
    AuditConfig,
    AgentConfig,
    CriticConfig,
    CloudModelEndpoint,
    HarnessConfig,
    RoleIdentityConfig,
    SupervisorConfig,
    TelegramConfig,
    ThreeHeadConfig,
    WorkerCapabilitiesConfig,
    WorkerConfig,
)
from llm_harness.doctor import run_doctor
from llm_harness.runtime import RuntimeConfig
from llm_harness.targets import LocalModelEndpointTarget
from llm_harness.mcp import McpServerConfig, RoleMcpPolicyConfig, SecretBinding


class DoctorTests(unittest.TestCase):
    def test_default_stub_configuration_is_ok_but_not_ready_for_real_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = HarnessConfig(
                runtime=RuntimeConfig(require_embedded_python=False),
                worker=WorkerConfig(driver="stub", command="stub", args=()),
            )

            report = run_doctor(
                root,
                config,
                env={},
                # Without a resolver the agents check asks the real PATH, so the
                # outcome depended on whether the machine happened to have npx.
                agent_resolver=lambda command: "C:/bin/npx.exe" if command == "npx" else None,
            )

            self.assertTrue(report.ok)
            self.assertFalse(report.ready_for_real_work)
            warned = {check.name for check in report.warnings}
            self.assertEqual(warned, {"worker", "supervisor_model", "verifier_model"})
            self.assertEqual(len(report.next_steps), 3)
            text = report.to_text()
            self.assertIn("warnings=3", text)
            self.assertIn("state=warning", text)
            self.assertIn("next_step=", text)

    def test_missing_embedded_runtime_explains_the_next_step(self):
        """The installation is the empty directory here, not the project.

        Pointing the check at the project told an installed Windows user that the
        interpreter they were running on was missing, naming a path inside their
        own repository.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = HarnessConfig(runtime=RuntimeConfig(require_embedded_python=True))

            with patch.dict(os.environ, {"HOH_DISTRIBUTION_ROOT": tmp}):
                report = run_doctor(root, config, env={})

            runtime_check = next(check for check in report.checks if check.name == "runtime")
            self.assertFalse(runtime_check.ok)
            self.assertIn("belongs to the HoH installation", runtime_check.next_step)

    def test_a_project_without_a_runtime_is_not_a_broken_installation(self):
        """What an installed user actually does: run doctor inside their own repository."""
        with tempfile.TemporaryDirectory() as tmp:
            installation = Path(tmp) / "installation"
            project = Path(tmp) / "project"
            wheels = installation / "vendor" / "wheels"
            wheels.mkdir(parents=True)
            (wheels / "llm_harness-0.1.0-py3-none-any.whl").write_text("", encoding="utf-8")
            project.mkdir()
            config = HarnessConfig(
                runtime=RuntimeConfig(
                    require_embedded_python=True, embedded_python_path=sys.executable
                )
            )

            with patch.dict(os.environ, {"HOH_DISTRIBUTION_ROOT": str(installation)}):
                report = run_doctor(project, config, env={})

            runtime_check = next(check for check in report.checks if check.name == "runtime")
            self.assertTrue(runtime_check.ok, runtime_check.detail)

    def test_doctor_preflights_active_role_mcp_command_and_secret_names(self):
        policy = RoleMcpPolicyConfig(
            "allow_once",
            ("read",),
            (
                McpServerConfig(
                    "local",
                    "stdio",
                    command="missing-mcp",
                    environment=(SecretBinding("TOKEN", "MCP_TOKEN"),),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = HarnessConfig(
                runtime=RuntimeConfig(require_embedded_python=False),
                worker=WorkerConfig(driver="acp", command="worker", mcp=policy),
                agents=(AgentConfig(name="worker", command="worker"),),
            )
            missing_env = run_doctor(
                root,
                config,
                env={},
                agent_resolver=lambda command: "C:/bin/worker.exe" if command == "worker" else None,
            )
            missing_command = run_doctor(
                root,
                config,
                env={"MCP_TOKEN": "secret"},
                agent_resolver=lambda command: "C:/bin/worker.exe" if command == "worker" else None,
            )

        env_check = next(check for check in missing_env.checks if check.name == "mcp_worker")
        command_check = next(check for check in missing_command.checks if check.name == "mcp_worker")
        self.assertFalse(env_check.ok)
        self.assertEqual(env_check.detail, "missing env=MCP_TOKEN")
        self.assertFalse(command_check.ok)
        self.assertEqual(command_check.detail, "missing command=missing-mcp")
        self.assertNotIn("secret", missing_command.to_text())

    def test_doctor_preflights_direct_model_critic_without_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = HarnessConfig(
                runtime=RuntimeConfig(require_embedded_python=False),
                worker=WorkerConfig(type="stub", command="stub", args=()),
                verifier_model=CloudModelEndpoint("deepseek", "deepseek-chat"),
                critic=CriticConfig(driver="model_json", command=""),
                three_head=ThreeHeadConfig(
                    mode="required",
                    logic=RoleIdentityConfig("logic", "openai", "gpt"),
                    worker=RoleIdentityConfig("worker", "local", "worker"),
                    critic=RoleIdentityConfig("critic", "deepseek", "deepseek-chat"),
                ),
                agents=(),
            )

            report = run_doctor(root, config, env={"DEEPSEEK_API_KEY": "secret"})

        critic = next(check for check in report.checks if check.name == "critic")
        self.assertTrue(critic.ok)
        self.assertIn("adapter=model-json-critic", critic.detail)
        self.assertNotIn("secret", critic.detail)

    def test_doctor_passes_with_git_runtime_agent_and_telegram_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheels = root / "vendor/wheels"
            wheels.mkdir(parents=True)
            (wheels / "llm_harness-0.1.0-py3-none-any.whl").write_text("", encoding="utf-8")
            config = HarnessConfig(
                runtime=RuntimeConfig(embedded_python_path=sys.executable),
                telegram=TelegramConfig(
                    enabled=True,
                    bot_token_env="HOH_TELEGRAM_BOT_TOKEN",
                    chat_id_env="HOH_TELEGRAM_CHAT_ID",
                    user_id_env="HOH_TELEGRAM_USER_ID",
                ),
                agents=(AgentConfig(name="hermes", command="hermes"),),
            )

            report = run_doctor(
                root,
                config,
                env={
                    "HOH_TELEGRAM_BOT_TOKEN": "token",
                    "HOH_TELEGRAM_CHAT_ID": "123456",
                    "HOH_TELEGRAM_USER_ID": "42",
                },
                agent_resolver=lambda command: "C:/bin/hermes.exe",
            )

        self.assertTrue(report.ok, report.to_text())
        checks = {check.name: check for check in report.checks}
        self.assertTrue(checks["worker"].ok)
        self.assertIn("driver=hermes_acp", checks["worker"].detail)
        self.assertIn("artifact_contract=worktree_diff", checks["worker"].detail)
        self.assertTrue(checks["scheduler"].ok)
        self.assertIn("configured_parallelism=1", checks["scheduler"].detail)
        self.assertTrue(checks["three_head"].ok)
        self.assertIn("mode=manual", checks["three_head"].detail)

    def test_doctor_reports_missing_runtime_agent_model_and_telegram_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = HarnessConfig(
                runtime=RuntimeConfig(require_embedded_python=True),
                telegram=TelegramConfig(
                    enabled=True,
                    bot_token_env="HOH_TELEGRAM_BOT_TOKEN",
                    chat_id_env="HOH_TELEGRAM_CHAT_ID",
                    user_id_env="HOH_TELEGRAM_USER_ID",
                ),
                agents=(AgentConfig(name="missing", command="missing"),),
                local_models=(
                    LocalModelEndpointTarget(
                        name="qwen",
                        base_url="http://127.0.0.1:8080",
                        model="qwen",
                    ),
                ),
            )

            with patch.dict(os.environ, {"HOH_DISTRIBUTION_ROOT": tmp}):
                report = run_doctor(
                    root,
                    config,
                    env={},
                    agent_resolver=lambda command: None,
                    model_probe=lambda model: False,
                )

        checks = {check.name: check for check in report.checks}
        self.assertFalse(report.ok)
        self.assertFalse(checks["runtime"].ok)
        self.assertFalse(checks["worker"].ok)
        self.assertFalse(checks["agents"].ok)
        self.assertFalse(checks["local_models"].ok)
        self.assertFalse(checks["telegram"].ok)

    def test_doctor_passes_for_stub_worker_without_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = HarnessConfig(
                runtime=RuntimeConfig(require_embedded_python=False),
                worker=WorkerConfig(type="stub", command="stub", args=()),
                agents=(AgentConfig(name="missing", command="missing"),),
            )

            report = run_doctor(
                root,
                config,
                agent_resolver=lambda command: None,
            )

        checks = {check.name: check for check in report.checks}
        self.assertTrue(checks["worker"].ok)
        self.assertIn("executable_check=skipped", checks["worker"].detail)

    def test_doctor_reports_worker_manifest_isolation_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = HarnessConfig(
                runtime=RuntimeConfig(require_embedded_python=False),
                worker=WorkerConfig(
                    type="command",
                    command="present",
                    args=(),
                    capabilities=WorkerCapabilitiesConfig(
                        task_transport="stdin_prompt",
                        artifact_contract="worktree_diff",
                        requires_isolated_worktree=False,
                    ),
                ),
                agents=(AgentConfig(name="present", command="present"),),
            )

            report = run_doctor(
                root,
                config,
                agent_resolver=lambda command: "C:/bin/present.exe",
            )

        checks = {check.name: check for check in report.checks}
        self.assertFalse(checks["worker"].ok)
        self.assertIn("manifest_requires_isolated_worktree=False", checks["worker"].detail)

    def test_doctor_reports_missing_command_worker_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = HarnessConfig(
                runtime=RuntimeConfig(require_embedded_python=False),
                worker=WorkerConfig(type="command", command="missing-worker", args=()),
                agents=(AgentConfig(name="present", command="present"),),
            )

            report = run_doctor(
                root,
                config,
                agent_resolver=lambda command: "C:/bin/present.exe" if command == "present" else None,
            )

        checks = {check.name: check for check in report.checks}
        self.assertFalse(checks["worker"].ok)
        self.assertIn("missing command=missing-worker", checks["worker"].detail)

    def test_doctor_checks_automatic_critic_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = HarnessConfig(
                runtime=RuntimeConfig(require_embedded_python=False),
                worker=WorkerConfig(type="stub", command="stub", args=()),
                critic=CriticConfig(type="claude_code", command="claude"),
                three_head=ThreeHeadConfig(
                    mode="required",
                    logic=RoleIdentityConfig("logic", "codex", "supervisor"),
                    worker=RoleIdentityConfig("worker", "hermes", "worker"),
                    critic=RoleIdentityConfig("critic", "claude", "sonnet"),
                ),
                agents=(AgentConfig(name="claude", command="claude"),),
            )

            report = run_doctor(
                root,
                config,
                agent_resolver=lambda command: None,
            )

        checks = {check.name: check for check in report.checks}
        self.assertFalse(checks["critic"].ok)
        self.assertIn("missing command=claude", checks["critic"].detail)

    def test_doctor_checks_process_supervisor_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = HarnessConfig(
                runtime=RuntimeConfig(require_embedded_python=False),
                supervisor=SupervisorConfig(driver="acp", command="missing-supervisor"),
                worker=WorkerConfig(type="stub", command="stub", args=()),
                agents=(AgentConfig(name="present", command="present"),),
            )

            report = run_doctor(
                root,
                config,
                agent_resolver=lambda command: "C:/bin/present.exe" if command == "present" else None,
            )

        checks = {check.name: check for check in report.checks}
        self.assertFalse(checks["supervisor"].ok)
        self.assertIn("missing command=missing-supervisor", checks["supervisor"].detail)
        self.assertTrue(checks["supervisor_model"].ok)

    def test_doctor_rejects_unknown_language_analyzer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = HarnessConfig(
                runtime=RuntimeConfig(require_embedded_python=False),
                worker=WorkerConfig(type="stub", command="stub", args=()),
                audit=AuditConfig(language_analyzers=("python", "unknown")),
                agents=(AgentConfig(name="present", command="present"),),
            )

            report = run_doctor(
                root,
                config,
                agent_resolver=lambda command: "C:/bin/present.exe",
            )

        checks = {check.name: check for check in report.checks}
        self.assertFalse(checks["audit_analyzers"].ok)
        self.assertIn("unsupported=unknown", checks["audit_analyzers"].detail)


if __name__ == "__main__":
    unittest.main()
