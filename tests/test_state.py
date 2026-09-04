from datetime import UTC, datetime
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from llm_harness.domain import HarnessRunResult, VerificationReport, WorkItem
from llm_harness.state import HohStateStore, StateStoreError, default_state_root
from llm_harness.tasks import load_work_item


class StateStoreTests(unittest.TestCase):
    def test_enqueue_list_and_next_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_file = self._task_file(Path(tmp))
            work_item = load_work_item(task_file)
            store = HohStateStore(Path(tmp) / "state")

            task = store.enqueue(work_item, source=str(task_file))

            self.assertEqual(task.status, "queued")
            self.assertEqual(store.next_queued().work_item.id, "state-task")  # type: ignore[union-attr]
            self.assertEqual(store.list_tasks()[0].source, str(task_file))

    def test_enqueue_rejects_duplicate_active_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_item = load_work_item(self._task_file(Path(tmp)))
            store = HohStateStore(Path(tmp) / "state")
            store.enqueue(work_item)

            with self.assertRaises(StateStoreError):
                store.enqueue(work_item)

    def test_reenqueue_completed_id_transitions_latest_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_item = self._work_item("repeatable")
            store = HohStateStore(root / "state")
            store.enqueue(work_item)
            store.mark_running(work_item.id)
            store.record_result(
                work_item.id,
                "2026-01-01T00:00:00+00:00",
                self._accepted_result(root, work_item.id),
            )

            second = store.enqueue(work_item)
            running = store.mark_running(work_item.id)

            self.assertEqual(second.status, "queued")
            self.assertEqual(running.status, "running")
            self.assertEqual([task.status for task in store.list_tasks()], ["done", "running"])

    def test_scheduler_respects_dependencies_then_priority_then_fifo(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")
            base = self._work_item("base", priority=1)
            low = self._work_item("low", priority=2)
            high = self._work_item("high", priority=10)
            dependent = self._work_item("dependent", priority=100, depends_on=("base",))
            store.enqueue_many(
                (
                    (base, "base.json"),
                    (low, "low.json"),
                    (high, "high.json"),
                    (dependent, "dependent.json"),
                )
            )

            schedule = store.schedule()

            self.assertEqual(schedule.selected.work_item.id, "high")  # type: ignore[union-attr]
            self.assertEqual(
                [item.task.work_item.id for item in schedule.ready],
                ["high", "low", "base"],
            )
            self.assertEqual([item.task.work_item.id for item in schedule.waiting], ["dependent"])
            self.assertEqual(schedule.waiting[0].blockers[0].status, "queued")

    def test_dependency_becomes_ready_only_after_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HohStateStore(root / "state")
            base = self._work_item("base")
            dependent = self._work_item("dependent", depends_on=("base",))
            store.enqueue_many(((base, ""), (dependent, "")))

            with self.assertRaisesRegex(StateStoreError, "dependencies are not ready"):
                store.mark_running("dependent")

            store.mark_running("base")
            store.record_result(
                "base",
                "2026-01-01T00:00:00+00:00",
                self._accepted_result(root, "base"),
            )

            self.assertEqual(store.next_queued().work_item.id, "dependent")  # type: ignore[union-attr]

    def test_schedule_batch_respects_priority_limit_and_path_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")
            first = self._work_item("first", priority=30)
            second = self._work_item("second", priority=20)
            conflict = self._work_item("conflict", priority=10)
            first = replace(first, allowed_paths=("src/first.py",))
            second = replace(second, allowed_paths=("docs/second.md",))
            conflict = replace(conflict, allowed_paths=("src",))
            store.enqueue_many(((first, ""), (second, ""), (conflict, "")))

            batch = store.schedule_batch(3)

            self.assertEqual([task.work_item.id for task in batch], ["first", "second"])

    def test_schedule_batch_treats_missing_allowed_paths_as_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")
            exclusive = self._work_item("exclusive", priority=10)
            scoped = replace(
                self._work_item("scoped", priority=5),
                allowed_paths=("README.md",),
            )
            store.enqueue_many(((exclusive, ""), (scoped, "")))

            self.assertEqual(
                [task.work_item.id for task in store.schedule_batch(2)],
                ["exclusive"],
            )

    def test_enqueue_rejects_unknown_dependencies_and_cycles_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")

            with self.assertRaisesRegex(StateStoreError, "unknown dependencies"):
                store.enqueue(self._work_item("unknown", depends_on=("missing",)))
            self.assertEqual(store.list_tasks(), ())

            first = self._work_item("first", depends_on=("second",))
            second = self._work_item("second", depends_on=("first",))
            with self.assertRaisesRegex(StateStoreError, "dependency cycle"):
                store.enqueue_many(((first, ""), (second, "")))
            self.assertEqual(store.list_tasks(), ())

    def test_record_result_updates_queue_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_item = load_work_item(self._task_file(Path(tmp)))
            store = HohStateStore(Path(tmp) / "state")
            store.enqueue(work_item)
            store.mark_running(work_item.id)
            result = HarnessRunResult(
                repository=Path(tmp),
                work_item_id=work_item.id,
                commit="abc123",
                pre_apply=VerificationReport(ok=True),
                post_apply=VerificationReport(ok=True),
                command_results=(),
            )

            record = store.record_result(work_item.id, "2026-01-01T00:00:00+00:00", result)

            task = store.list_tasks()[0]
            self.assertEqual(task.status, "done")
            self.assertEqual(task.attempts, 1)
            self.assertEqual(task.last_commit, "abc123")
            self.assertTrue(record.ok)
            self.assertEqual(store.history()[0].commit, "abc123")

    def test_required_review_transitions_through_pending_to_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_item = load_work_item(self._task_file(Path(tmp)))
            store = HohStateStore(Path(tmp) / "state")
            store.enqueue(work_item)
            store.mark_running(work_item.id)

            run = store.record_result(
                work_item.id,
                "2026-01-01T00:00:00+00:00",
                self._accepted_result(Path(tmp), work_item.id),
                review_required=True,
            )
            pending = store.attach_review_bundle(work_item.id, f"review-{run.run_id}")
            approved = store.resolve_review(
                work_item.id,
                pending.pending_review_bundle_id or "",
                "decision-approved",
                "approve",
                "Accepted.",
            )

            self.assertEqual(pending.status, "review_pending")
            self.assertEqual(approved.status, "done")
            self.assertEqual(approved.last_review_decision_id, "decision-approved")
            self.assertIsNone(approved.pending_review_bundle_id)

    def test_critic_failure_keeps_candidate_review_pending_for_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_item = load_work_item(self._task_file(Path(tmp)))
            store = HohStateStore(Path(tmp) / "state")
            store.enqueue(work_item)
            store.mark_running(work_item.id)
            run = store.record_result(
                work_item.id,
                "2026-01-01T00:00:00+00:00",
                self._accepted_result(Path(tmp), work_item.id),
                review_required=True,
            )
            store.attach_review_bundle(work_item.id, f"review-{run.run_id}")

            failed = store.record_review_failure(work_item.id, "Critic timed out.")

            self.assertEqual(failed.status, "review_pending")
            self.assertEqual(failed.last_error, "Critic timed out.")
            self.assertIsNotNone(failed.pending_review_bundle_id)

    def test_reject_requires_supervisor_confirmation_before_bounded_rework(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_item = load_work_item(self._task_file(Path(tmp)))
            store = HohStateStore(Path(tmp) / "state")
            store.enqueue(work_item)
            store.mark_running(work_item.id)
            run = store.record_result(
                work_item.id,
                "2026-01-01T00:00:00+00:00",
                self._accepted_result(Path(tmp), work_item.id),
                review_required=True,
            )
            bundle_id = f"review-{run.run_id}"
            store.attach_review_bundle(work_item.id, bundle_id)
            rejected = store.resolve_review(
                work_item.id,
                bundle_id,
                "decision-rejected",
                "reject",
                "Fix the missing edge case.",
            )

            rework = store.confirm_rework(
                work_item.id,
                "decision-rejected",
                ("Handle the recorded edge case without changing scope.",),
                max_attempts=3,
            )

            self.assertEqual(rejected.status, "rework_required")
            self.assertEqual(rework.status, "queued")
            self.assertEqual(rework.work_item.objective, work_item.objective)
            self.assertEqual(rework.work_item.allowed_paths, work_item.allowed_paths)
            self.assertEqual(rework.work_item.correction_decision_id, "decision-rejected")
            self.assertEqual(len(rework.work_item.correction_instructions), 1)

    def test_rework_attempt_limit_escalates_instead_of_running_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_item = load_work_item(self._task_file(Path(tmp)))
            store = HohStateStore(Path(tmp) / "state")
            store.enqueue(work_item)
            store.mark_running(work_item.id)
            run = store.record_result(
                work_item.id,
                "2026-01-01T00:00:00+00:00",
                self._accepted_result(Path(tmp), work_item.id),
                review_required=True,
            )
            bundle_id = f"review-{run.run_id}"
            store.attach_review_bundle(work_item.id, bundle_id)
            store.resolve_review(work_item.id, bundle_id, "decision-rejected", "reject", "Rejected.")

            escalated = store.confirm_rework(
                work_item.id,
                "decision-rejected",
                ("Try again.",),
                max_attempts=1,
            )

            self.assertEqual(escalated.status, "escalated")
            self.assertIn("attempt limit", escalated.last_error or "")

    def test_escalated_task_can_be_returned_to_the_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._escalated_store(Path(tmp))
            work_item_id = store.list_tasks()[-1].work_item.id

            task = store.resolve_escalation(work_item_id, "requeue", "Operator fixed the worker environment.")

            self.assertEqual(task.status, "queued")
            self.assertEqual(task.attempts, 0)
            self.assertIsNone(task.last_error)
            self.assertIsNotNone(store.next_queued())

    def test_escalated_task_can_be_closed_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._escalated_store(Path(tmp))
            work_item_id = store.list_tasks()[-1].work_item.id

            task = store.resolve_escalation(work_item_id, "fail", "Requirement was withdrawn.")

            self.assertEqual(task.status, "failed")
            self.assertEqual(task.last_error, "Requirement was withdrawn.")

    def test_escalation_resolution_rejects_unknown_resolution_and_empty_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._escalated_store(Path(tmp))
            work_item_id = store.list_tasks()[-1].work_item.id

            with self.assertRaises(StateStoreError):
                store.resolve_escalation(work_item_id, "ignore", "Because.")
            with self.assertRaises(StateStoreError):
                store.resolve_escalation(work_item_id, "requeue", "   ")
            self.assertEqual(store.list_tasks()[-1].status, "escalated")

    def test_only_escalated_tasks_can_be_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_item = load_work_item(self._task_file(Path(tmp)))
            store = HohStateStore(Path(tmp) / "state")
            store.enqueue(work_item)

            with self.assertRaises(StateStoreError):
                store.resolve_escalation(work_item.id, "requeue", "Not escalated.")

    def _escalated_store(self, tmp: Path) -> HohStateStore:
        work_item = load_work_item(self._task_file(tmp))
        store = HohStateStore(tmp / "state")
        store.enqueue(work_item)
        store.mark_running(work_item.id)
        run = store.record_result(
            work_item.id,
            "2026-01-01T00:00:00+00:00",
            self._accepted_result(tmp, work_item.id),
            review_required=True,
        )
        bundle_id = f"review-{run.run_id}"
        store.attach_review_bundle(work_item.id, bundle_id)
        store.resolve_review(work_item.id, bundle_id, "decision-escalated", "escalate", "Needs an operator.")
        assert store.list_tasks()[-1].status == "escalated"
        return store

    def test_requeue_failed_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_item = load_work_item(self._task_file(Path(tmp)))
            store = HohStateStore(Path(tmp) / "state")
            store.enqueue(work_item)
            store.mark_running(work_item.id)
            store.record_failure(work_item.id, "2026-01-01T00:00:00+00:00", "worker failed")

            task = store.requeue_failed(work_item.id)

            self.assertEqual(task.status, "queued")
            self.assertEqual(task.attempts, 1)
            self.assertIsNone(task.last_error)

    def test_recover_running_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_item = load_work_item(self._task_file(Path(tmp)))
            store = HohStateStore(Path(tmp) / "state")
            store.enqueue(work_item)
            store.mark_running(work_item.id)

            task = store.recover_running(work_item.id, "interrupted")

            self.assertEqual(task.status, "queued")
            self.assertEqual(task.attempts, 1)
            self.assertEqual(task.last_error, "interrupted")

    def test_stale_running_tasks_use_updated_timestamp_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_item = load_work_item(self._task_file(Path(tmp)))
            store = HohStateStore(Path(tmp) / "state")
            store.enqueue(work_item)
            store.mark_running(work_item.id)
            self._set_updated_at(store, work_item.id, "2026-01-01T00:00:00+00:00")

            stale = store.stale_running_tasks(
                60,
                now=datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC),
            )

            self.assertEqual([task.work_item.id for task in stale], [work_item.id])

    def test_stale_running_tasks_ignore_fresh_running_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_item = load_work_item(self._task_file(Path(tmp)))
            store = HohStateStore(Path(tmp) / "state")
            store.enqueue(work_item)
            store.mark_running(work_item.id)
            self._set_updated_at(store, work_item.id, "2026-01-01T01:30:00+00:00")

            stale = store.stale_running_tasks(
                60,
                now=datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC),
            )

            self.assertEqual(stale, ())

    def test_requeue_rejects_wrong_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_item = load_work_item(self._task_file(Path(tmp)))
            store = HohStateStore(Path(tmp) / "state")
            store.enqueue(work_item)

            with self.assertRaises(StateStoreError):
                store.requeue_failed(work_item.id)

    def test_operator_events_are_appended_to_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")

            event = store.record_operator_event(
                command="status",
                ok=True,
                message="Queue status.",
                raw_text="/status",
            )

            events = store.operator_events()
            self.assertEqual(events[0], event)
            self.assertEqual(events[0].command, "status")
            self.assertEqual(events[0].raw_text, "/status")

    def test_telegram_update_offset_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")

            self.assertIsNone(store.telegram_update_offset())
            store.set_telegram_update_offset(102)

            self.assertEqual(HohStateStore(Path(tmp) / "state").telegram_update_offset(), 102)

    def test_audit_report_is_persisted_with_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")

            record = store.record_audit_report(
                "# Project audit report\n\n- Ready: `true`\n",
                ok=True,
                findings_count=0,
                command_results_count=2,
            )

            reports = HohStateStore(Path(tmp) / "state").audit_reports()
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0].audit_id, record.audit_id)
            self.assertTrue(reports[0].ok)
            self.assertEqual(reports[0].findings_count, 0)
            self.assertEqual(reports[0].command_results_count, 2)
            self.assertEqual(record.report_path.read_text(encoding="utf-8"), "# Project audit report\n\n- Ready: `true`\n")
            self.assertEqual(reports[0].report_path.read_text(encoding="utf-8"), record.report_path.read_text(encoding="utf-8"))

    def test_default_state_root_is_outside_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp) / "repo"
            repository.mkdir()

            state_root = default_state_root(repository)

        self.assertEqual(state_root.parent.name, ".hoh-state")
        self.assertNotEqual(state_root.parent, repository)

    def _task_file(self, root: Path) -> Path:
        task = root / "task.json"
        task.write_text(
            """{
  "id": "state-task",
  "title": "State task",
  "objective": "Exercise state persistence.",
  "acceptance_criteria": ["State is persisted."],
  "verification_commands": ["python -m unittest"],
  "allowed_paths": ["HARNESS_STATE.md"],
  "non_goals": []
}
""",
            encoding="utf-8",
        )
        return task

    def _set_updated_at(self, store: HohStateStore, work_item_id: str, updated_at: str) -> None:
        payload = json.loads(store.queue_path.read_text(encoding="utf-8"))
        for task in payload:
            if task["work_item"]["id"] == work_item_id:
                task["updated_at_utc"] = updated_at
        store.queue_path.write_text(json.dumps(payload), encoding="utf-8")

    def _accepted_result(self, repository: Path, work_item_id: str) -> HarnessRunResult:
        return HarnessRunResult(
            repository=repository,
            work_item_id=work_item_id,
            commit="abc123",
            pre_apply=VerificationReport(ok=True),
            post_apply=VerificationReport(ok=True),
            command_results=(),
        )

    def _work_item(
        self,
        task_id: str,
        *,
        priority: int = 0,
        depends_on: tuple[str, ...] = (),
    ) -> WorkItem:
        return WorkItem(
            id=task_id,
            title=task_id,
            objective=f"Complete {task_id}.",
            acceptance_criteria=(f"{task_id} is complete.",),
            verification_commands=("python -m unittest",),
            depends_on=depends_on,
            priority=priority,
        )


if __name__ == "__main__":
    unittest.main()
