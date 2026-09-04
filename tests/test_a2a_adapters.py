import json
from pathlib import Path
import tempfile
import unittest

from llm_harness.a2a import A2AResult
from llm_harness.a2a_adapters import A2AAdapterError, A2ACriticAdapter, A2ASupervisorAdapter, A2AWorkerAdapter
from llm_harness.config import CriticConfig, RoleIdentityConfig, SupervisorConfig, WorkerConfig
from llm_harness.jobs import WorkerJob
from llm_harness.domain import WorkItem
from llm_harness.targets import LocalAgentTarget


def _project_spec():
    return {
        "spec_version": "1.0",
        "id": "remote-plan",
        "title": "Remote plan",
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
                "allowed_paths": ["app.py"],
                "non_goals": [],
                "depends_on": [],
                "priority": 1,
            }
        ],
    }


class FakeClient:
    result = A2AResult((), (), None, "COMPLETED", "request-1")
    prompts = []

    def __init__(self, _connection):
        pass

    def send_message(self, prompt):
        self.prompts.append(prompt)
        return self.result


class A2AAdapterTests(unittest.TestCase):
    def test_supervisor_accepts_structured_data_artifact(self):
        FakeClient.result = A2AResult((), ({"result": _project_spec()},), None, "COMPLETED", "request-1")
        adapter = A2ASupervisorAdapter(
            SupervisorConfig(driver="a2a", command="https://agent.example/card.json"),
            RoleIdentityConfig("remote-supervisor", "remote", "planner"),
            client_factory=FakeClient,
        )
        spec, evidence = adapter.plan(Path.cwd(), "Build")
        self.assertEqual(spec.id, "remote-plan")
        self.assertEqual(evidence.endpoint, "a2a/jsonrpc-v1")
        self.assertEqual(evidence.request_id, "request-1")

    def test_worker_exports_only_allowed_text_and_returns_patch(self):
        patch = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
        FakeClient.result = A2AResult((), ({"patch": patch},), None, "COMPLETED", "request-2")
        adapter = A2AWorkerAdapter(
            WorkerConfig(driver="a2a", command="https://agent.example/card.json"),
            client_factory=FakeClient,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("old\n", encoding="utf-8")
            (root / "secret.txt").write_text("do-not-send", encoding="utf-8")
            job = WorkerJob(
                "job-1",
                WorkItem(
                    "task-1",
                    "Edit",
                    "Edit app",
                    ("Updated",),
                    ("python -m unittest",),
                    allowed_paths=("app.py",),
                ),
                LocalAgentTarget("remote", "remote"),
                "callback",
            )
            completion = adapter.run_job(root, job)
        self.assertIsNone(completion.error)
        self.assertEqual(completion.patch.patch, patch)
        self.assertNotIn("secret.txt", FakeClient.prompts[-1])
        self.assertNotIn(str(root), FakeClient.prompts[-1])

    def test_critic_returns_attested_decision_for_immutable_bundle(self):
        judgment = {
            "decision": "approve",
            "summary": "Evidence is sufficient.",
            "findings": [],
            "correction_brief": None,
            "escalation": None,
        }
        FakeClient.result = A2AResult((), ({"result": judgment},), None, "COMPLETED", "request-3")
        adapter = A2ACriticAdapter(
            CriticConfig(driver="a2a", command="https://agent.example/card.json"),
            RoleIdentityConfig("remote-critic", "remote", "critic"),
            client_factory=FakeClient,
        )
        bundle = {
            "protocol": "hoh.protocol",
            "protocol_version": "2.0",
            "message_type": "critic.review_bundle",
            "bundle_id": "remote-review-1",
            "artifact": {"commit": "b" * 40, "patch_sha256": "c" * 64},
            "integrity": {"payload_sha256": "a" * 64},
        }
        decision = adapter.review(Path.cwd(), bundle)
        self.assertEqual(decision["decision"], "approve")
        self.assertEqual(decision["bundle_id"], "remote-review-1")

    def test_worker_refuses_unbounded_repository_upload(self):
        adapter = A2AWorkerAdapter(
            WorkerConfig(driver="a2a", command="https://agent.example/card.json"),
            client_factory=FakeClient,
        )
        job = WorkerJob(
            "job-1",
            WorkItem("task-1", "Edit", "Edit", ("Done",), ("test",)),
            LocalAgentTarget("remote", "remote"),
            "callback",
        )
        completion = adapter.run_job(Path.cwd(), job)
        self.assertIn("requires explicit allowed_paths", completion.error)


if __name__ == "__main__":
    unittest.main()
