import json
from pathlib import Path
import sys
import tempfile
import unittest

from llm_harness.config import CriticConfig, HarnessConfig, RoleIdentityConfig, SupervisorConfig, ThreeHeadConfig
from llm_harness.role_conformance import run_critic_conformance, run_supervisor_conformance


def _project_spec() -> dict:
    return {
        "spec_version": "1.0",
        "id": "conformance-plan",
        "title": "Conformance plan",
        "goal": "Create HARNESS_DEMO.md.",
        "customer": "HoH conformance",
        "business_requirements": ["Produce one bounded artifact."],
        "definition_of_done": ["Verification passes."],
        "non_functional_requirements": ["Keep Git clean."],
        "documentation_requirements": ["Document the artifact."],
        "constraints": ["No Git authority."],
        "open_questions": [],
        "tasks": [
            {
                "id": "task-1",
                "title": "Create demo",
                "objective": "Create HARNESS_DEMO.md.",
                "acceptance_criteria": ["HARNESS_DEMO.md exists."],
                "verification_commands": ["test HARNESS_DEMO.md"],
                "allowed_paths": ["HARNESS_DEMO.md"],
                "non_goals": ["Do not edit other files."],
                "depends_on": [],
                "priority": 1,
            }
        ],
    }


def _approve_judgment() -> dict:
    return {
        "decision": "approve",
        "summary": "Evidence is sufficient.",
        "findings": [],
        "correction_brief": None,
        "escalation": None,
    }


class RoleConformanceTests(unittest.TestCase):
    def test_command_supervisor_live_conformance_validates_plan_and_clean_repo(self):
        script = f"import sys; sys.stdin.read(); print({json.dumps(json.dumps(_project_spec()))})"
        config = HarnessConfig(
            supervisor=SupervisorConfig(
                driver="command_json",
                command=sys.executable,
                args=("-c", script),
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            report = run_supervisor_conformance(Path(tmp), config)

        self.assertTrue(report.ok)
        self.assertEqual(report.adapter_name, "command-json-supervisor")
        self.assertEqual(report.evidence["task_count"], 1)

    def test_command_critic_live_conformance_validates_attestation_and_clean_repo(self):
        script = f"import sys; sys.stdin.read(); print({json.dumps(json.dumps(_approve_judgment()))})"
        config = HarnessConfig(
            critic=CriticConfig(
                driver="command_json",
                command=sys.executable,
                args=("-c", script),
            ),
            three_head=ThreeHeadConfig(
                mode="required",
                logic=RoleIdentityConfig("logic", "test", "logic"),
                worker=RoleIdentityConfig("worker", "test", "worker"),
                critic=RoleIdentityConfig("critic", "test", "critic"),
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            report = run_critic_conformance(Path(tmp), config)

        self.assertTrue(report.ok)
        self.assertEqual(report.adapter_name, "command-json-critic")
        self.assertEqual(report.evidence["reviewed_commit"], "b" * 40)

    def test_manual_critic_is_not_live_conformant(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_critic_conformance(Path(tmp), HarnessConfig())

        self.assertFalse(report.ok)
        self.assertIn("manual Critic", report.checks[0].detail)


if __name__ == "__main__":
    unittest.main()
