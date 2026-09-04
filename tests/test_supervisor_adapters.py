import json
from pathlib import Path
import sys
import tempfile
import unittest

from llm_harness.config import CloudModelEndpoint, RoleIdentityConfig, SupervisorConfig
from llm_harness.supervisor_adapters import CommandJsonSupervisorAdapter, create_supervisor_adapter


def _project_spec() -> dict:
    return {
        "spec_version": "1.0",
        "id": "planned",
        "title": "Planned",
        "goal": "Ship",
        "customer": "Customer",
        "business_requirements": ["Works"],
        "definition_of_done": ["Tests pass"],
        "non_functional_requirements": [],
        "documentation_requirements": [],
        "constraints": [],
        "open_questions": [],
        "tasks": [
            {
                "id": "task-1",
                "title": "Build",
                "objective": "Build it",
                "acceptance_criteria": ["Works"],
                "verification_commands": ["python -m unittest"],
                "allowed_paths": [],
                "non_goals": [],
                "depends_on": [],
                "priority": 1,
            }
        ],
    }


class SupervisorAdapterTests(unittest.TestCase):
    def test_command_json_supervisor_generates_validated_plan_without_repository_access(self):
        code = f"import sys; sys.stdin.read(); print({json.dumps(json.dumps(_project_spec()))})"
        config = SupervisorConfig(driver="command_json", command=sys.executable, args=("-c", code))
        adapter = CommandJsonSupervisorAdapter(
            config,
            RoleIdentityConfig("project-supervisor", "test-harness", "test-model"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            spec, evidence = adapter.plan(Path(tmp), "Build a thing")

        self.assertEqual(spec.id, "planned")
        self.assertEqual(evidence.provider, "test-harness")
        self.assertEqual(evidence.endpoint, "process/stdin-json")
        self.assertEqual(len(evidence.request_sha256), 64)

    def test_default_supervisor_uses_direct_model_driver(self):
        adapter = create_supervisor_adapter(
            SupervisorConfig(),
            CloudModelEndpoint("stub", "supervisor-stub"),
            RoleIdentityConfig("logic", "stub", "supervisor-stub"),
        )

        self.assertEqual(adapter.name, "model-json-supervisor")


if __name__ == "__main__":
    unittest.main()
