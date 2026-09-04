from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from llm_harness.config import CloudModelEndpoint
from llm_harness.domain import (
    HarnessRunResult,
    ModelInvocationEvidence,
    SemanticVerificationReport,
    VerificationReport,
    WorkItem,
    WorkerPatch,
)
from llm_harness.model_providers import ModelProviderError, ModelResponse
from llm_harness.model_runtime import ModelSemanticVerifier, generate_project_spec
from llm_harness.supervisor import Supervisor
from llm_harness.state import HohStateStore
from llm_harness.verifier import PolicyVerifier


class _Provider:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = []

    def invoke(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return ModelResponse(
            output=self.output,
            evidence=ModelInvocationEvidence(
                provider="openai",
                model="test",
                endpoint="/responses",
                request_id="req-test",
                attempts=1,
                latency_ms=4,
                request_sha256="a" * 64,
                response_sha256="b" * 64,
            ),
        )


class _PatchWorker:
    name = "worker"

    def __init__(self, patch):
        self.patch = patch

    def produce_patch(self, repository, work_item):
        return WorkerPatch(self.name, work_item.id, self.patch)


class ModelRuntimeTests(unittest.TestCase):
    def test_planner_produces_valid_v1_spec_without_orchestration_authority(self):
        provider = _Provider(
            {
                "spec_version": "1.0",
                "id": "demo",
                "title": "Demo",
                "goal": "Ship demo",
                "customer": "Customer",
                "business_requirements": ["Feature works"],
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
                        "allowed_paths": ["src/"],
                        "non_goals": [],
                        "depends_on": [],
                        "priority": 1,
                    }
                ],
            }
        )
        spec, evidence = generate_project_spec(
            "Build a demo",
            CloudModelEndpoint("openai", "test"),
            provider=provider,
        )
        self.assertEqual(spec.id, "demo")
        self.assertEqual(spec.spec_version, "1.0")
        self.assertIsNone(spec.orchestration)
        self.assertEqual(evidence.request_id, "req-test")
        self.assertIn("git authority", provider.calls[0].instructions.casefold())

    def test_planner_rejects_model_attempt_to_add_orchestration_authority(self):
        output = {
            "spec_version": "1.0",
            "id": "demo",
            "title": "Demo",
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
                    "objective": "Build",
                    "acceptance_criteria": ["Works"],
                    "verification_commands": ["check"],
                    "allowed_paths": [],
                    "non_goals": [],
                    "depends_on": [],
                    "priority": 0,
                }
            ],
            "orchestration": {"supervisor": {"agent": "self"}},
        }
        with self.assertRaisesRegex(ValueError, "unexpected=orchestration"):
            generate_project_spec(
                "Build",
                CloudModelEndpoint("deepseek", "test"),
                provider=_Provider(output),
            )

    def test_deterministic_failure_skips_semantic_model(self):
        provider = _Provider({"decision": "pass", "summary": "ok", "findings": []})
        result, exists, status = self._run(
            provider,
            decision="pass",
            verification=f'"{sys.executable}" -c "raise SystemExit(2)"',
        )
        self.assertFalse(result.ok)
        self.assertEqual(provider.calls, [])
        self.assertFalse(exists)
        self.assertEqual(status, "")

    def test_semantic_pass_commits_and_persists_evidence(self):
        provider = _Provider({"decision": "pass", "summary": "meets criteria", "findings": []})
        result, exists, _ = self._run(provider, decision="pass")
        self.assertTrue(result.ok)
        self.assertTrue(exists)
        self.assertEqual(result.semantic_verification.evidence.request_id, "req-test")
        self.assertEqual(len(provider.calls), 1)

    def test_semantic_fail_reverts_before_commit(self):
        provider = _Provider(
            {"decision": "fail", "summary": "missing behavior", "findings": ["criterion not met"]}
        )
        result, exists, status = self._run(provider, decision="fail")
        self.assertFalse(result.ok)
        self.assertEqual(result.semantic_verification.decision, "fail")
        self.assertFalse(exists)
        self.assertEqual(status, "")

    def test_semantic_escalation_reverts_before_commit(self):
        provider = _Provider(
            {"decision": "escalate", "summary": "customer choice required", "findings": []}
        )
        result, exists, status = self._run(provider, decision="escalate")
        self.assertFalse(result.ok)
        self.assertEqual(result.semantic_verification.decision, "escalate")
        self.assertFalse(exists)
        self.assertEqual(status, "")

    def test_provider_error_fails_closed_and_reverts(self):
        provider = _Provider(
            error=ModelProviderError(
                "provider unavailable",
                provider="openai",
                kind="provider_unavailable",
                retryable=True,
            )
        )
        result, exists, status = self._run(provider, decision="fail")
        self.assertFalse(result.ok)
        self.assertIn("provider unavailable", result.semantic_verification.findings)
        self.assertFalse(exists)
        self.assertEqual(status, "")

    def test_semantic_evidence_round_trips_through_run_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HohStateStore(root / "state")
            task = WorkItem(
                id="history-task",
                title="History",
                objective="Persist evidence",
                acceptance_criteria=("Evidence persists.",),
                verification_commands=("check",),
            )
            store.enqueue(task)
            store.mark_running(task.id)
            semantic = SemanticVerificationReport(
                decision="pass",
                summary="accepted",
                evidence=ModelInvocationEvidence(
                    provider="openai",
                    model="test",
                    endpoint="/responses",
                    request_id="req-history",
                    attempts=1,
                    latency_ms=2,
                ),
            )
            result = HarnessRunResult(
                repository=root,
                work_item_id=task.id,
                commit="abc123",
                pre_apply=VerificationReport(ok=True),
                post_apply=VerificationReport(ok=True),
                command_results=(),
                semantic_verification=semantic,
            )
            store.record_result(task.id, "2026-01-01T00:00:00+00:00", result)
            loaded = store.history()[0].semantic_verification

        self.assertEqual(loaded.decision, "pass")
        self.assertEqual(loaded.evidence.request_id, "req-history")

    def _run(self, provider, decision, verification=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            (root / ".gitignore").write_text(".tmp\n", encoding="utf-8")
            self._git(root, "add", ".gitignore")
            self._git(root, "commit", "-m", "initial")
            patch = (
                "diff --git a/result.txt b/result.txt\n"
                "new file mode 100644\n"
                "index 0000000..ce01362\n"
                "--- /dev/null\n"
                "+++ b/result.txt\n"
                "@@ -0,0 +1 @@\n"
                "+hello\n"
            )
            check = verification or (
                f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'result.txt\').exists()"'
            )
            task = WorkItem(
                id="semantic-task",
                title="Semantic task",
                objective="Create result",
                acceptance_criteria=("Result is correct.",),
                verification_commands=(check,),
                allowed_paths=("result.txt",),
            )
            verifier = ModelSemanticVerifier(
                CloudModelEndpoint("openai", "test"),
                provider=provider,
            )
            result = Supervisor(
                PolicyVerifier(),
                semantic_verifier=verifier,
            ).execute_work_item(root, task, _PatchWorker(patch))
            exists = (root / "result.txt").exists()
            status = self._git(root, "status", "--short")
            return result, exists, status

    def _git(self, root, *args):
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode:
            raise AssertionError(completed.stderr)
        return completed.stdout.strip()


if __name__ == "__main__":
    unittest.main()
