from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from llm_harness.cli import _reconcile_pending_review_setup
from llm_harness.config import HarnessConfig, RoleIdentityConfig, ThreeHeadConfig
from llm_harness.domain import WorkItem
from llm_harness.protocol import PROTOCOL_NAMESPACE, PROTOCOL_VERSION
from llm_harness.review_protocol import _git as review_protocol_git
from llm_harness.review_protocol import (
    REVIEW_DECISION_MESSAGE,
    ReviewProtocolError,
    export_review_bundle,
    import_verifier_decision,
    validate_review_bundle,
    validate_critic_decision,
    validate_verifier_decision,
)
from llm_harness.state import HohStateStore
from llm_harness.supervisor import Supervisor
from llm_harness.supervisor_protocol import build_supervisor_status
from llm_harness.verifier import PolicyVerifier
from llm_harness.workers import StubPatchWorker


class ReviewProtocolTests(unittest.TestCase):
    def test_crashed_bundle_export_is_idempotently_repaired_without_critic_turn(self):
        for point in ("review.bundle_after_file", "review.bundle_after_index"):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repository, store, run_id = self._completed_run(
                    root,
                    review_required=True,
                )
                code = (
                    "import sys\n"
                    "from pathlib import Path\n"
                    "from llm_harness.config import RoleIdentityConfig,ThreeHeadConfig\n"
                    "from llm_harness.review_protocol import export_review_bundle\n"
                    "from llm_harness.state import HohStateStore\n"
                    "repo,state,run=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]\n"
                    "roles=ThreeHeadConfig(mode='required',logic=RoleIdentityConfig('logic','cloud','logic-model'),"
                    "worker=RoleIdentityConfig('worker','local','worker-model'),"
                    "critic=RoleIdentityConfig('critic','cloud','critic-model'))\n"
                    "export_review_bundle(repo,HohStateStore(state),run,three_head=roles)\n"
                )
                environment = os.environ.copy()
                environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
                environment["HOH_ENABLE_TEST_CRASH_INJECTION"] = "1"
                environment["HOH_TEST_CRASH_POINT"] = point

                crashed = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        code,
                        str(repository),
                        str(store.root),
                        run_id,
                    ],
                    cwd=Path(__file__).parents[1],
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                _reconcile_pending_review_setup(
                    repository,
                    store,
                    HarnessConfig(three_head=self._three_head()),
                )
                task = store.latest_task("review-task")

                self.assertEqual(crashed.returncode, 91)
                self.assertIsNotNone(task.pending_review_bundle_id)
                self.assertEqual(len(store.review_bundles()), 1)
                self.assertEqual(store.review_decisions(), ())

    def test_crashed_decision_import_replays_state_only_without_duplicate_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, run_id = self._completed_run(
                root,
                review_required=True,
            )
            bundle = export_review_bundle(
                repository,
                store,
                run_id,
                three_head=self._three_head(),
            )
            store.attach_review_bundle("review-task", bundle.record.bundle_id)
            decision_path = root / "critic-approve.json"
            decision_path.write_text(
                json.dumps(self._critic_decision(bundle.payload, "approve")),
                encoding="utf-8",
            )
            code = (
                "import sys\n"
                "from pathlib import Path\n"
                "from llm_harness.review_protocol import import_verifier_decision\n"
                "from llm_harness.state import HohStateStore\n"
                "repo,state,decision=map(Path,sys.argv[1:4])\n"
                "import_verifier_decision(repo,HohStateStore(state),decision)\n"
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
            environment["HOH_ENABLE_TEST_CRASH_INJECTION"] = "1"
            environment["HOH_TEST_CRASH_POINT"] = "review.decision_after_record"

            crashed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    code,
                    str(repository),
                    str(store.root),
                    str(decision_path),
                ],
                cwd=Path(__file__).parents[1],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            pending = store.latest_task("review-task")
            recovered = import_verifier_decision(repository, store, decision_path)

            self.assertEqual(crashed.returncode, 91)
            self.assertEqual(pending.status, "review_pending")
            self.assertEqual(len(store.review_decisions()), 1)
            self.assertEqual(recovered.decision, "approve")
            self.assertEqual(store.latest_task("review-task").status, "done")
            self.assertEqual(len(store.review_decisions()), 1)

    def test_export_contains_exact_commit_patch_scope_verification_and_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository, store, run_id = self._completed_run(Path(tmp))

            exported = export_review_bundle(repository, store, run_id)

            payload = exported.payload
            self.assertEqual(payload["protocol_version"], PROTOCOL_VERSION)
            self.assertEqual(payload["task"]["id"], "review-task")
            self.assertEqual(payload["scope"]["changed_files"], ["HARNESS_DEMO.md"])
            self.assertIn("diff --git", payload["artifact"]["patch_text"])
            self.assertTrue(payload["verification"]["commands_ok"])
            self.assertFalse(payload["authority"]["worker_commit_allowed"])
            self.assertFalse(payload["authority"]["verifier_merge_allowed"])
            self.assertTrue(exported.output_path.exists())
            self.assertEqual(len(store.review_bundles()), 1)
            validate_review_bundle(payload)

    def test_export_is_idempotent_and_can_copy_to_explicit_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, run_id = self._completed_run(root)
            first = export_review_bundle(repository, store, run_id)
            output = root / "handoff" / "bundle.json"

            second = export_review_bundle(repository, store, run_id, output)

            self.assertEqual(first.record, second.record)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), first.payload)
            self.assertEqual(len(store.review_bundles()), 1)

    def test_import_approve_is_immutable_idempotent_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, run_id = self._completed_run(root)
            bundle = export_review_bundle(repository, store, run_id)
            decision_path = root / "approve.json"
            decision_path.write_text(json.dumps(self._decision(bundle.payload, "approve")), encoding="utf-8")

            first = import_verifier_decision(repository, store, decision_path)
            second = import_verifier_decision(repository, store, decision_path)

            self.assertEqual(first, second)
            self.assertEqual(first.decision, "approve")
            self.assertEqual(first.commit, bundle.payload["artifact"]["commit"])
            self.assertEqual(len(store.review_decisions()), 1)

    def test_concurrent_process_import_is_single_immutable_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, run_id = self._completed_run(root)
            bundle = export_review_bundle(repository, store, run_id)
            decision_path = root / "approve.json"
            decision_path.write_text(
                json.dumps(self._decision(bundle.payload, "approve")),
                encoding="utf-8",
            )
            trigger = root / "go"
            code = (
                "import sys,time\n"
                "from pathlib import Path\n"
                "from llm_harness.review_protocol import import_verifier_decision\n"
                "from llm_harness.state import HohStateStore\n"
                "repo,state,decision,go=map(Path,sys.argv[1:5])\n"
                "while not go.exists(): time.sleep(0.005)\n"
                "record=import_verifier_decision(repo,HohStateStore(state),decision)\n"
                "print(record.decision_id,flush=True)\n"
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
            processes = [
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        code,
                        str(repository),
                        str(store.root),
                        str(decision_path),
                        str(trigger),
                    ],
                    cwd=Path(__file__).parents[1],
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for _ in range(2)
            ]
            trigger.write_text("go", encoding="utf-8")
            outputs = [process.communicate(timeout=15) for process in processes]
            self.assertEqual([process.returncode for process in processes], [0, 0])
            self.assertEqual(
                [stdout.strip() for stdout, _ in outputs],
                ["decision-approve", "decision-approve"],
            )
            self.assertEqual(len(store.review_decisions()), 1)

    def test_import_reject_requires_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, run_id = self._completed_run(root)
            bundle = export_review_bundle(repository, store, run_id)
            decision = self._decision(bundle.payload, "reject")
            decision["findings"] = []
            decision_path = root / "reject.json"
            decision_path.write_text(json.dumps(decision), encoding="utf-8")

            with self.assertRaisesRegex(ReviewProtocolError, "at least one finding"):
                import_verifier_decision(repository, store, decision_path)

    def test_valid_reject_is_persisted_and_blocks_supervisor_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, run_id = self._completed_run(root)
            bundle = export_review_bundle(repository, store, run_id)
            decision_path = root / "reject.json"
            decision_path.write_text(json.dumps(self._decision(bundle.payload, "reject")), encoding="utf-8")

            record = import_verifier_decision(repository, store, decision_path)
            status = build_supervisor_status(repository, store.root)

            self.assertEqual(record.decision, "reject")
            self.assertEqual(status.rejected_reviews, 1)
            self.assertFalse(status.ok)

    def test_import_rejects_tampered_bundle_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, run_id = self._completed_run(root)
            bundle = export_review_bundle(repository, store, run_id)
            payload = json.loads(bundle.record.bundle_path.read_text(encoding="utf-8"))
            payload["artifact"]["patch_text"] += "tampered"
            bundle.record.bundle_path.write_text(json.dumps(payload), encoding="utf-8")
            decision_path = root / "approve.json"
            decision_path.write_text(json.dumps(self._decision(bundle.payload, "approve")), encoding="utf-8")

            with self.assertRaisesRegex(ReviewProtocolError, "integrity digest"):
                import_verifier_decision(repository, store, decision_path)

    def test_decision_rejects_unknown_authority_or_action_fields(self):
        payload = {
            "protocol": PROTOCOL_NAMESPACE,
            "protocol_version": PROTOCOL_VERSION,
            "message_type": REVIEW_DECISION_MESSAGE,
            "decision_id": "decision-1",
            "bundle_id": "review-run-1",
            "bundle_sha256": "a" * 64,
            "reviewer": {"id": "verifier", "kind": "agent"},
            "decision": "approve",
            "summary": "Evidence accepted.",
            "findings": [],
            "created_at_utc": datetime.now(UTC).isoformat(),
            "attestation": {"reviewed_commit": "abc", "reviewed_patch_sha256": "b" * 64},
            "git_action": "merge",
        }

        with self.assertRaisesRegex(ReviewProtocolError, "unknown fields"):
            validate_verifier_decision(payload)

    def test_required_three_head_reject_stores_brief_and_requires_rework(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, run_id = self._completed_run(root, review_required=True)
            bundle = export_review_bundle(repository, store, run_id, three_head=self._three_head())
            store.attach_review_bundle("review-task", bundle.record.bundle_id)
            decision = self._critic_decision(bundle.payload, "reject")
            decision_path = root / "critic-reject.json"
            decision_path.write_text(json.dumps(decision), encoding="utf-8")

            record = import_verifier_decision(repository, store, decision_path)
            task = store.list_tasks()[0]

            self.assertEqual(bundle.payload["protocol_version"], "2.0")
            self.assertEqual(bundle.payload["three_head"]["roles"]["critic"]["identity"], "critic")
            self.assertEqual(record.correction_brief.instructions, ("Correct the recorded defect.",))  # type: ignore[union-attr]
            self.assertEqual(task.status, "rework_required")
            self.assertEqual(task.last_review_decision_id, record.decision_id)

    def test_required_three_head_escalation_requires_customer_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, run_id = self._completed_run(root, review_required=True)
            bundle = export_review_bundle(repository, store, run_id, three_head=self._three_head())
            store.attach_review_bundle("review-task", bundle.record.bundle_id)
            decision = self._critic_decision(bundle.payload, "escalate")
            validate_critic_decision(decision)
            decision["escalation"]["options"] = ["stop"]

            with self.assertRaisesRegex(ReviewProtocolError, "реши сам"):
                validate_critic_decision(decision)

    def test_required_three_head_rejects_wrong_critic_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, run_id = self._completed_run(root, review_required=True)
            bundle = export_review_bundle(repository, store, run_id, three_head=self._three_head())
            store.attach_review_bundle("review-task", bundle.record.bundle_id)
            decision = self._critic_decision(bundle.payload, "approve")
            decision["critic"]["identity"] = "worker"
            path = root / "wrong-critic.json"
            path.write_text(json.dumps(decision), encoding="utf-8")

            with self.assertRaisesRegex(ReviewProtocolError, "identity"):
                import_verifier_decision(repository, store, path)

    def _completed_run(self, root: Path, review_required: bool = False) -> tuple[Path, HohStateStore, str]:
        repository = root / "repo"
        repository.mkdir()
        self._git(repository, "init")
        self._git(repository, "config", "user.email", "review@example.local")
        self._git(repository, "config", "user.name", "Review Test")
        (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
        self._git(repository, "add", ".gitignore")
        self._git(repository, "commit", "-m", "Initial commit")
        task = WorkItem(
            id="review-task",
            title="Review task",
            objective="Create an artifact for independent review.",
            acceptance_criteria=("HARNESS_DEMO.md exists.",),
            verification_commands=(
                f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'HARNESS_DEMO.md\').exists()"',
            ),
            allowed_paths=("HARNESS_DEMO.md",),
            non_goals=("Do not commit or merge from worker or verifier.",),
        )
        store = HohStateStore(root / "state")
        store.enqueue(task, source="task.json")
        store.mark_running(task.id)
        started = datetime.now(UTC).isoformat()
        result = Supervisor(PolicyVerifier()).execute_work_item(repository, task, StubPatchWorker())
        run = store.record_result(task.id, started, result, review_required=review_required)
        self.assertTrue(run.ok)
        return repository, store, run.run_id

    def _three_head(self) -> ThreeHeadConfig:
        return ThreeHeadConfig(
            mode="required",
            max_attempts=3,
            logic=RoleIdentityConfig("logic", "cloud", "logic-model"),
            worker=RoleIdentityConfig("worker", "local", "worker-model"),
            critic=RoleIdentityConfig("critic", "cloud", "critic-model"),
        )

    def _critic_decision(self, bundle: dict, decision: str) -> dict:
        finding = {"code": "DEFECT", "severity": "error", "message": "A recorded defect remains."}
        return {
            "protocol": PROTOCOL_NAMESPACE,
            "protocol_version": "2.0",
            "message_type": "critic.decision",
            "decision_id": f"critic-{decision}",
            "bundle_id": bundle["bundle_id"],
            "bundle_sha256": bundle["integrity"]["payload_sha256"],
            "critic": {"identity": "critic", "kind": "agent"},
            "decision": decision,
            "summary": f"Critic decision: {decision}.",
            "findings": [] if decision == "approve" else [finding],
            "correction_brief": {
                "rationale": "The acceptance evidence is incomplete.",
                "instructions": ["Correct the recorded defect."],
                "validation_focus": ["Re-run the acceptance check."],
            } if decision == "reject" else None,
            "escalation": {
                "reason": "Business intent is ambiguous.",
                "question": "Which behavior should be authoritative?",
                "options": ["реши сам", "stop", "свой"],
            } if decision == "escalate" else None,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "attestation": {
                "reviewed_commit": bundle["artifact"]["commit"],
                "reviewed_patch_sha256": bundle["artifact"]["patch_sha256"],
            },
        }

    def _decision(self, bundle: dict, decision: str) -> dict:
        return {
            "protocol": PROTOCOL_NAMESPACE,
            "protocol_version": PROTOCOL_VERSION,
            "message_type": REVIEW_DECISION_MESSAGE,
            "decision_id": f"decision-{decision}",
            "bundle_id": bundle["bundle_id"],
            "bundle_sha256": bundle["integrity"]["payload_sha256"],
            "reviewer": {"id": "external-verifier", "kind": "agent"},
            "decision": decision,
            "summary": "Evidence accepted." if decision == "approve" else "Evidence rejected.",
            "findings": [] if decision == "approve" else [
                {"code": "REVIEW_FINDING", "severity": "error", "message": "Evidence is incomplete."}
            ],
            "created_at_utc": datetime.now(UTC).isoformat(),
            "attestation": {
                "reviewed_commit": bundle["artifact"]["commit"],
                "reviewed_patch_sha256": bundle["artifact"]["patch_sha256"],
            },
        }

    def test_bundle_patch_reading_keeps_the_carriage_returns_git_reported(self):
        """The reviewed patch is hashed, so a rewritten newline forges the evidence.

        Reading git in text mode turns every CRLF in "git show --binary" into LF, and
        the critic would then review, and sign off on, a patch the commit never held.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp) / "repo"
            repository.mkdir()
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "review@example.local")
            self._git(repository, "config", "user.name", "Review Test")
            self._git(repository, "config", "core.autocrlf", "false")
            (repository / "CRLF.md").write_bytes(b"first line\r\nsecond line\r\n")
            self._git(repository, "add", "CRLF.md")
            self._git(repository, "commit", "-m", "Seed CRLF content")

            patch_text = review_protocol_git(repository, "show", "--format=", "--binary", "--no-ext-diff", "HEAD")

            raw = subprocess.run(
                ["git", "show", "--format=", "--binary", "--no-ext-diff", "HEAD"],
                cwd=repository,
                capture_output=True,
                check=True,
            ).stdout
            self.assertEqual(patch_text, raw.decode("utf-8"))
            self.assertIn("\r\n", patch_text)

    def _git(self, repository: Path, *args: str) -> None:
        completed = subprocess.run(["git", *args], cwd=repository, text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)


if __name__ == "__main__":
    unittest.main()
