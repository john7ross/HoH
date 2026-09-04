from pathlib import Path
import tempfile
import unittest

from llm_harness.config import (
    A2AAgentConfig,
    CriticConfig,
    HarnessConfig,
    MetricsConfig,
    ModelPriceConfig,
    RoleIdentityConfig,
    SupervisorConfig,
    ThreeHeadConfig,
    config_payload,
    load_config,
    project_config_path,
    write_config,
)
from llm_harness.mcp import McpServerConfig, RoleMcpPolicyConfig, SecretBinding
from llm_harness.role_profiles import load_effective_config
from llm_harness.trust import WorkerTrustLevel


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_role_mcp_policies_round_trip_without_secret_values(self):
        policy = RoleMcpPolicyConfig(
            "allow_once",
            ("read", "search", "fetch"),
            (
                McpServerConfig(
                    "docs",
                    "http",
                    url="https://mcp.example/api",
                    headers=(SecretBinding("Authorization", "MCP_DOCS_TOKEN"),),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "harness.json"
            write_config(path, HarnessConfig(supervisor=SupervisorConfig(mcp=policy)))
            loaded = load_config(path)
            raw = path.read_text(encoding="utf-8")

        self.assertEqual(loaded.supervisor.mcp, policy)
        self.assertIn("MCP_DOCS_TOKEN", raw)
        self.assertNotIn("secret-value", raw)

    def test_mcp_policy_validates_unknown_kinds_and_remote_http(self):
        with self.assertRaisesRegex(ValueError, "Unknown ACP tool kinds"):
            RoleMcpPolicyConfig("allow_once", ("shell",))
        with self.assertRaisesRegex(ValueError, "require HTTPS"):
            McpServerConfig("remote", "http", url="http://mcp.example/api")

    def test_metrics_prices_round_trip_without_hard_coded_provider_rates(self):
        expected = MetricsConfig(
            prices=(ModelPriceConfig("openai", "gpt-test", 1.25, 5.0, "USD"),)
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "harness.json"
            write_config(path, HarnessConfig(metrics=expected))
            loaded = load_config(path)

        self.assertEqual(loaded.metrics, expected)

    def test_a2a_oauth_streaming_and_push_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "harness.json"
            expected = A2AAgentConfig(
                name="remote-critic",
                card_url="https://agent.example/.well-known/agent-card.json",
                auth_kind="oidc",
                oauth_flow="device_code",
                client_id_env="REMOTE_CLIENT_ID",
                oidc_discovery_url="https://identity.example/.well-known/openid-configuration",
                scopes=("openid", "a2a.execute"),
                prefer_streaming=True,
                push_callback_url="https://hoh.example/a2a/push",
                push_token_env="HOH_PUSH_TOKEN",
            )

            write_config(path, HarnessConfig(a2a_agents=(expected,)))
            loaded = load_config(path).a2a_agents[0]

        self.assertEqual(loaded, expected)
        payload = config_payload(HarnessConfig(a2a_agents=(expected,)))
        serialized = payload["a2a_agents"][0]
        self.assertEqual(serialized["auth_kind"], "oidc")
        self.assertEqual(serialized["scopes"], ["openid", "a2a.execute"])
        self.assertNotIn("credential", serialized)

    def test_a2a_credentials_require_safe_environment_and_header_names(self):
        with self.assertRaisesRegex(ValueError, "environment-variable"):
            A2AAgentConfig("remote", "https://agent.example/card.json", auth_kind="bearer", credential_env="1BAD")
        with self.assertRaisesRegex(ValueError, "protocol-owned"):
            A2AAgentConfig(
                "remote",
                "https://agent.example/card.json",
                auth_kind="api_key",
                credential_env="TOKEN",
                api_key_header="Host",
            )

    def test_model_json_critic_does_not_require_process_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "harness.json"
            write_config(path, HarnessConfig(critic=CriticConfig(driver="model_json", command="")))
            critic = load_config(path).critic

        self.assertTrue(critic.automatic)
        self.assertEqual(critic.driver, "model_json")
        self.assertEqual(critic.command, "")

    def test_json_project_config_round_trips_and_is_auto_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = HarnessConfig(require_tests=False)
            path = write_config(project_config_path(root), expected)

            loaded = load_config(path)
            effective = load_effective_config(root)

        self.assertEqual(loaded, expected)
        self.assertEqual(effective, expected)
        self.assertFalse(config_payload(expected)["require_tests"])

    def test_config_saved_with_a_byte_order_mark_still_loads(self):
        """Windows editors and Set-Content write UTF-8 with a BOM by default.

        Strict UTF-8 kept the mark as a leading character, and tomllib then refused
        the file with "Invalid statement (at line 1, column 1)" -- an error that
        names neither the BOM nor the editor that added it. Found by installing the
        release on Windows 11 and pointing it at a config written there.
        """
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in (
                ("harness.toml", 'require_tests = false\n'),
                ("harness.json", '{"require_tests": false}\n'),
            ):
                path = Path(tmp) / name
                path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
                with self.subTest(name=name):
                    self.assertFalse(load_config(path).require_tests)

    def test_writable_config_requires_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "json"):
                write_config(Path(tmp) / "harness.toml", HarnessConfig())

    def test_critic_defaults_to_manual_and_validates_automatic_settings(self):
        self.assertFalse(CriticConfig().automatic)
        self.assertTrue(CriticConfig(type="claude_code").automatic)
        with self.assertRaisesRegex(ValueError, "critic.type"):
            CriticConfig(type="unknown")
        with self.assertRaisesRegex(ValueError, "timeout_seconds"):
            CriticConfig(timeout_seconds=0)

    def test_load_config_parses_runtime_trust_and_local_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "harness.toml"
            config_path.write_text(
                """
worker_trust_level = "patch_only"

[runtime]
require_embedded_python = true
embedded_python_path = "runtime/python/python.exe"
wheels_path = "vendor/wheels"

[worker]
type = "hermes_acp"
command = "hermes"
args = ["acp"]
timeout_seconds = 123

[worker.capabilities]
task_transport = "acp_stdio"
artifact_contract = "worktree_diff"
requires_isolated_worktree = true
supports_subagents = true

[[local_models]]
name = "qwen-llama-cpp"
base_url = "http://127.0.0.1:8080"
model = "qwen"
protocol = "openai_compatible"

[telegram]
enabled = true
bot_token_env = "HOH_TELEGRAM_BOT_TOKEN"
chat_id_env = "HOH_TELEGRAM_CHAT_ID"
user_id_env = "HOH_TELEGRAM_USER_ID"
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(config.worker_trust_level, WorkerTrustLevel.PATCH_ONLY)
        self.assertTrue(config.runtime.require_embedded_python)
        self.assertEqual(config.local_models[0].name, "qwen-llama-cpp")
        self.assertEqual(config.local_models[0].base_url, "http://127.0.0.1:8080")
        self.assertEqual(config.worker.type, "hermes_acp")
        self.assertEqual(config.worker.command, "hermes")
        self.assertEqual(config.worker.args, ("acp",))
        self.assertEqual(config.worker.timeout_seconds, 123)
        self.assertEqual(config.worker.capabilities.task_transport, "acp_stdio")
        self.assertEqual(config.worker.capabilities.artifact_contract, "worktree_diff")
        self.assertTrue(config.worker.capabilities.requires_isolated_worktree)
        self.assertTrue(config.worker.capabilities.supports_subagents)
        self.assertTrue(config.telegram.enabled)
        self.assertEqual(config.telegram.bot_token_env, "HOH_TELEGRAM_BOT_TOKEN")
        self.assertEqual(config.telegram.chat_id_env, "HOH_TELEGRAM_CHAT_ID")
        self.assertEqual(config.telegram.user_id_env, "HOH_TELEGRAM_USER_ID")

    def test_unimplemented_worker_trust_levels_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for level in ("branch_only", "branch_and_commit"):
                config_path = Path(tmp) / f"{level}.toml"
                config_path.write_text(f'worker_trust_level = "{level}"\n', encoding="utf-8")

                with self.assertRaisesRegex(ValueError, "not supported"):
                    load_config(config_path)

    def test_example_config_loads(self):
        config = load_config(PROJECT_ROOT / "config.example.toml")

        self.assertEqual(config.worker_trust_level, WorkerTrustLevel.PATCH_ONLY)
        self.assertFalse(config.telegram.enabled)
        self.assertEqual(config.worker.type, "hermes_acp")
        self.assertEqual(config.worker.args, ("acp",))
        self.assertEqual(config.worker.capabilities.task_transport, "acp_stdio")
        self.assertEqual(config.worker.capabilities.artifact_contract, "worktree_diff")
        self.assertTrue(config.worker.capabilities.requires_isolated_worktree)
        self.assertFalse(config.worker.capabilities.supports_subagents)
        self.assertEqual(config.agents[0].name, "codex")
        self.assertEqual(config.agents[2].args, ("acp",))
        self.assertEqual(config.local_models, ())
        self.assertFalse(config.three_head.required)
        self.assertEqual(config.three_head.max_attempts, 3)
        self.assertEqual(config.three_head.worker.identity, "hermes-worker")
        self.assertEqual(config.scheduler.max_parallel_tasks, 1)
        self.assertEqual(config.coordination.state_lock_timeout_seconds, 5)
        self.assertEqual(config.coordination.execution_lock_timeout_seconds, 5)
        self.assertEqual(config.coordination.poll_interval_seconds, 0.05)
        self.assertEqual(config.audit.language_analyzers, ("python", "javascript", "typescript"))
        self.assertTrue(config.audit.fail_on_unavailable)

    def test_audit_analyzer_policy_is_configurable(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "harness.toml"
            config_path.write_text(
                """
[audit]
language_analyzers = ["python", "typescript", "python"]
entry_points = ["src/main.py", "src/index.ts"]
exclude_paths = ["vendor/**"]
fail_on_unavailable = false
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(config.audit.language_analyzers, ("python", "typescript"))
        self.assertEqual(config.audit.entry_points, ("src/main.py", "src/index.ts"))
        self.assertEqual(config.audit.exclude_paths, ("vendor/**",))
        self.assertFalse(config.audit.fail_on_unavailable)

    def test_scheduler_parallelism_is_configurable_and_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "harness.toml"
            config_path.write_text(
                "[scheduler]\nmax_parallel_tasks = 4\n",
                encoding="utf-8",
            )
            config = load_config(config_path)
            self.assertEqual(config.scheduler.max_parallel_tasks, 4)

            config_path.write_text(
                "[scheduler]\nmax_parallel_tasks = 0\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "at least 1"):
                load_config(config_path)

    def test_coordination_timeouts_are_configurable_and_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "harness.toml"
            config_path.write_text(
                """
[coordination]
state_lock_timeout_seconds = 1.5
execution_lock_timeout_seconds = 7
poll_interval_seconds = 0.02
""",
                encoding="utf-8",
            )
            config = load_config(config_path)
            self.assertEqual(config.coordination.state_lock_timeout_seconds, 1.5)
            self.assertEqual(config.coordination.execution_lock_timeout_seconds, 7)
            self.assertEqual(config.coordination.poll_interval_seconds, 0.02)

            config_path.write_text(
                "[coordination]\nstate_lock_timeout_seconds = 0\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "state_lock_timeout_seconds"):
                load_config(config_path)

    def test_worker_capabilities_default_by_worker_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "harness.toml"
            config_path.write_text(
                """
[worker]
type = "command"
command = "worker-cli"
args = ["run"]
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(config.worker.capabilities.task_transport, "stdin_prompt")
        self.assertEqual(config.worker.capabilities.artifact_contract, "worktree_diff")
        self.assertTrue(config.worker.capabilities.requires_isolated_worktree)
        self.assertFalse(config.worker.capabilities.supports_subagents)

    def test_driver_is_canonical_and_type_remains_compatible_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "harness.toml"
            config_path.write_text(
                '[worker]\ndriver = "command"\ncommand = "new-agent"\n',
                encoding="utf-8",
            )
            config = load_config(config_path)

        self.assertEqual(config.worker.driver, "command")
        self.assertEqual(config.worker.type, "command")

    def test_conflicting_driver_and_legacy_type_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "harness.toml"
            config_path.write_text(
                '[worker]\ndriver = "command"\ntype = "hermes_acp"\ncommand = "agent"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must identify the same driver"):
                load_config(config_path)

    def test_required_three_head_rejects_duplicate_identity_but_allows_reused_agent_model(self):
        with self.assertRaisesRegex(ValueError, "distinct logic, worker, and critic identities"):
            ThreeHeadConfig(
                mode="required",
                logic=RoleIdentityConfig("same", "openai", "logic"),
                worker=RoleIdentityConfig("same", "local", "worker"),
                critic=RoleIdentityConfig("critic", "anthropic", "critic"),
            )

        policy = ThreeHeadConfig(
            mode="required",
            logic=RoleIdentityConfig("logic-session", "codex", "same-model"),
            worker=RoleIdentityConfig("worker-session", "codex", "same-model"),
            critic=RoleIdentityConfig("critic-session", "codex", "same-model"),
        )
        self.assertTrue(policy.required)

    def test_disabled_critic_is_explicit_two_role_mode(self):
        policy = ThreeHeadConfig(mode="disabled")

        self.assertFalse(policy.required)
        self.assertEqual(policy.mode, "disabled")

    def test_manual_three_head_preserves_legacy_compatibility(self):
        policy = ThreeHeadConfig(
            mode="manual",
            logic=RoleIdentityConfig("shared", "same", "model"),
            worker=RoleIdentityConfig("shared", "same", "model"),
            critic=RoleIdentityConfig("shared", "same", "model"),
        )

        self.assertFalse(policy.required)


if __name__ == "__main__":
    unittest.main()
