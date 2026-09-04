import json
from pathlib import Path
import tempfile
import unittest

from llm_harness.lifecycle import ProjectSpecError, load_project_spec, materialize_project_plan
from llm_harness.config import (
    AgentConfig,
    CloudModelEndpoint,
    CriticConfig,
    HarnessConfig,
    SupervisorConfig,
    WorkerCapabilitiesConfig,
    WorkerConfig,
)
from llm_harness.mcp import RoleMcpPolicyConfig
from llm_harness.state import HohStateStore
from llm_harness.tasks import load_work_item
from llm_harness.role_profiles import (
    DIRECT_CRITIC_MODEL_AGENT,
    DIRECT_SUPERVISOR_MODEL_AGENT,
    ProjectRoleProfile,
    RoleSelection,
    apply_role_profile,
    load_effective_config,
    load_role_profile,
)


class LifecycleTests(unittest.TestCase):
    def test_role_profile_inference_preserves_each_role_mcp_policy(self):
        supervisor_policy = RoleMcpPolicyConfig("allow_once", ("read",))
        worker_policy = RoleMcpPolicyConfig("allow_once", ("read", "edit"))
        critic_policy = RoleMcpPolicyConfig("allow_once", ("read",))
        base = HarnessConfig(
            supervisor=SupervisorConfig(mcp=supervisor_policy),
            worker=WorkerConfig(mcp=worker_policy),
            critic=CriticConfig(mcp=critic_policy),
        )
        profile = ProjectRoleProfile(
            supervisor=RoleSelection("codex", "supervisor-model", "acp"),
            worker=RoleSelection("claude", "worker-model", "claude_code"),
            critic=RoleSelection("codex", "critic-model", "acp"),
        )

        effective = apply_role_profile(base, profile)

        self.assertEqual(effective.supervisor.driver, "acp")
        self.assertEqual(effective.worker.driver, "claude_code")
        self.assertEqual(effective.critic.driver, "acp")
        self.assertEqual(effective.supervisor.mcp, supervisor_policy)
        self.assertEqual(effective.worker.mcp, worker_policy)
        self.assertEqual(effective.critic.mcp, critic_policy)

    def test_direct_model_targets_materialize_supervisor_and_critic_drivers(self):
        base = HarnessConfig(
            supervisor_model=CloudModelEndpoint("openai", "gpt-supervisor"),
            verifier_model=CloudModelEndpoint("deepseek", "deepseek-chat"),
        )
        profile = ProjectRoleProfile(
            supervisor=RoleSelection(DIRECT_SUPERVISOR_MODEL_AGENT, "gpt-5.4", "model_json"),
            worker=RoleSelection("codex", "gpt-worker", "acp"),
            critic=RoleSelection(DIRECT_CRITIC_MODEL_AGENT, "deepseek-reasoner", "model_json"),
        )

        effective = apply_role_profile(base, profile)

        self.assertEqual(effective.supervisor.driver, "model_json")
        self.assertEqual(effective.supervisor_model.model, "gpt-5.4")
        self.assertEqual(effective.critic.driver, "model_json")
        self.assertEqual(effective.verifier_model.model, "deepseek-reasoner")
        self.assertEqual(effective.three_head.critic.provider, "deepseek")
        self.assertEqual(effective.three_head.critic.model, "deepseek-reasoner")

    def test_load_project_spec_requires_business_requirements_definition_of_done_and_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "project.json"
            spec_path.write_text(json.dumps({"id": "p1"}), encoding="utf-8")

            with self.assertRaises(ProjectSpecError):
                load_project_spec(spec_path)

    def test_materialize_project_plan_writes_docs_and_atomic_task_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = load_project_spec(self._spec_file(root))

            result = materialize_project_plan(root, spec, write=True)

            self.assertTrue(result.brief_path.exists())
            self.assertTrue(result.roadmap_path.exists())
            self.assertEqual(len(result.task_paths), 2)
            self.assertIn("Business requirements", result.brief_path.read_text(encoding="utf-8"))
            self.assertIn("Definition of done", result.roadmap_path.read_text(encoding="utf-8"))
            self.assertEqual(load_work_item(result.task_paths[0]).id, "task-001")

    def test_materialize_project_plan_can_enqueue_generated_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = load_project_spec(self._spec_file(root))
            store = HohStateStore(root / "state")

            result = materialize_project_plan(root, spec, write=True, queue=store)

            self.assertEqual(result.enqueued_task_ids, ("task-001", "task-002"))
            self.assertEqual([task.work_item.id for task in store.list_tasks()], ["task-001", "task-002"])
            self.assertEqual(store.next_queued().work_item.id, "task-001")  # type: ignore[union-attr]
            second = store.list_tasks()[1].work_item
            self.assertEqual(second.depends_on, ("task-001",))
            self.assertEqual(second.priority, 10)

    def test_project_spec_rejects_unknown_dependency_and_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unknown = _spec_payload()
            unknown["tasks"][0]["depends_on"] = ["missing"]
            unknown_path = root / "unknown.json"
            unknown_path.write_text(json.dumps(unknown), encoding="utf-8")

            with self.assertRaisesRegex(ProjectSpecError, "unknown dependencies"):
                load_project_spec(unknown_path)

            cyclic = _spec_payload()
            cyclic["tasks"][0]["depends_on"] = ["task-002"]
            cyclic["tasks"][1]["depends_on"] = ["task-001"]
            cyclic_path = root / "cyclic.json"
            cyclic_path.write_text(json.dumps(cyclic), encoding="utf-8")

            with self.assertRaisesRegex(ProjectSpecError, "dependency cycle"):
                load_project_spec(cyclic_path)

    def test_v2_brief_materializes_roles_without_manual_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = _spec_payload()
            payload["spec_version"] = "2.0"
            payload["orchestration"] = {
                "supervisor": {"agent": "codex", "model": "gpt-supervisor"},
                "worker": {"agent": "hermes", "model": "local-worker"},
                "critic": {"agent": "claude", "model": "critic-model"},
                "max_attempts": 4,
            }
            path = root / "project.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            result = materialize_project_plan(root, load_project_spec(path), write=True)
            profile = load_role_profile(root)
            config = load_effective_config(root)

            self.assertEqual(
                result.role_profile_path.resolve(),
                (root / ".hoh" / "role-profile.json").resolve(),
            )
            self.assertEqual(profile.worker.agent, "hermes")  # type: ignore[union-attr]
            self.assertTrue(config.three_head.required)
            self.assertEqual(config.three_head.max_attempts, 4)
            self.assertEqual(config.worker.type, "hermes_acp")
            brief = result.brief_path.read_text(encoding="utf-8")
            self.assertIn("Supervisor: codex / gpt-supervisor", brief)
            self.assertIn("Critic: claude / critic-model", brief)

    def test_v2_can_disable_critic_and_supervisor_closes_after_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = _spec_payload()
            payload["spec_version"] = "2.0"
            payload["orchestration"] = {
                "supervisor": {"agent": "hermes"},
                "worker": {"agent": "codex"},
                "critic": None,
                "max_attempts": 2,
            }
            path = root / "project.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            spec = load_project_spec(path)
            result = materialize_project_plan(root, spec, write=True)
            config = load_effective_config(root)

            self.assertFalse(spec.orchestration.critic_enabled)  # type: ignore[union-attr]
            self.assertEqual(config.three_head.mode, "disabled")
            self.assertEqual(config.worker.type, "acp")
            self.assertIn("Critic: Disabled", result.brief_path.read_text(encoding="utf-8"))

    def test_role_profile_preserves_explicit_worker_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = _spec_payload()
            payload["spec_version"] = "2.0"
            payload["orchestration"] = {
                "supervisor": {"agent": "codex"},
                "worker": {"agent": "hermes"},
                "critic": None,
                "max_attempts": 2,
            }
            path = root / "project.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            materialize_project_plan(root, load_project_spec(path), write=True)
            command_worker = WorkerConfig(
                type="command",
                command="worker-wrapper",
                args=("--stdin",),
                timeout_seconds=240,
                capabilities=WorkerCapabilitiesConfig(
                    task_transport="stdin_prompt",
                    artifact_contract="worktree_diff",
                    requires_isolated_worktree=True,
                    supports_subagents=False,
                ),
            )

            config = load_effective_config(root, base=HarnessConfig(worker=command_worker))

            self.assertEqual(config.worker, command_worker)

    def test_role_profile_infers_named_provider_worker_adapters(self):
        for agent, model, expected_type in (
            ("claude", "sonnet", "claude_code"),
            ("openclaw", "openai/gpt-test", "openclaw"),
        ):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                payload = _spec_payload()
                payload["spec_version"] = "2.0"
                payload["orchestration"] = {
                    "supervisor": {"agent": "codex"},
                    "worker": {"agent": agent, "model": model},
                    "critic": None,
                    "max_attempts": 2,
                }
                path = root / "project.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                materialize_project_plan(root, load_project_spec(path), write=True)

                config = load_effective_config(root)

                self.assertEqual(config.worker.type, expected_type)
                self.assertEqual(config.worker.model, model)

    def test_unknown_agent_name_uses_declared_driver_without_name_inference(self):
        base = HarnessConfig(
            agents=(
                AgentConfig(
                    name="future-agent-9000",
                    command="future-agent-cli",
                    args=("run",),
                    supervisor_driver="command_json",
                    worker_driver="command",
                    critic_driver="command_json",
                ),
            )
        )
        profile = ProjectRoleProfile(
            supervisor=RoleSelection("future-agent-9000"),
            worker=RoleSelection("future-agent-9000"),
            critic=RoleSelection("future-agent-9000"),
        )

        config = apply_role_profile(base, profile)

        self.assertEqual(config.worker.driver, "command")
        self.assertEqual(config.worker.command, "future-agent-cli")
        self.assertEqual(config.critic.driver, "command_json")
        self.assertTrue(config.critic.automatic)

    def test_legacy_role_profile_v1_remains_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_path = root / ".hoh" / "role-profile.json"
            profile_path.parent.mkdir()
            profile_path.write_text(
                json.dumps(
                    {
                        "profile_version": "1.0",
                        "critic_enabled": False,
                        "max_attempts": 3,
                        "roles": {
                            "supervisor": {"agent": "codex", "model": None},
                            "worker": {"agent": "hermes", "model": None},
                            "critic": None,
                        },
                    }
                ),
                encoding="utf-8",
            )

            profile = load_role_profile(root)

        self.assertEqual(profile.worker.agent, "hermes")  # type: ignore[union-attr]
        self.assertIsNone(profile.worker.driver)  # type: ignore[union-attr]

    def _spec_file(self, root: Path) -> Path:
        spec = root / "project.json"
        spec.write_text(json.dumps(_spec_payload()), encoding="utf-8")
        return spec


def _spec_payload() -> dict:
    return {
        "id": "demo-project",
        "title": "Demo project",
        "goal": "Create a daily-use project workflow.",
        "customer": "Customer",
        "business_requirements": ["Queue tasks are explicit."],
        "non_functional_requirements": ["Traceability is preserved."],
        "documentation_requirements": ["Write Markdown docs."],
        "definition_of_done": ["All tasks pass verification."],
        "constraints": ["Use git only."],
        "open_questions": ["None."],
        "tasks": [
            {
                "id": "task-001",
                "title": "Create first artifact",
                "objective": "Create FIRST.md.",
                "acceptance_criteria": ["FIRST.md exists."],
                "verification_commands": [
                    "python -c \"from pathlib import Path; assert Path('FIRST.md').exists()\""
                ],
                "allowed_paths": ["FIRST.md"],
                "non_goals": ["Do not edit SECOND.md."],
                "priority": 1,
            },
            {
                "id": "task-002",
                "title": "Create second artifact",
                "objective": "Create SECOND.md.",
                "acceptance_criteria": ["SECOND.md exists."],
                "verification_commands": [
                    "python -c \"from pathlib import Path; assert Path('SECOND.md').exists()\""
                ],
                "allowed_paths": ["SECOND.md"],
                "non_goals": ["Do not edit FIRST.md."],
                "depends_on": ["task-001"],
                "priority": 10,
            },
        ],
    }


if __name__ == "__main__":
    unittest.main()
