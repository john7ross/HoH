import json
import unittest

from llm_harness.domain import CommandResult, WorkItem
from llm_harness.protocol import (
    PROTOCOL_NAMESPACE,
    PROTOCOL_VERSION,
    canonical_json_bytes,
    dump_json,
    protocol_envelope,
    queue_task_payload,
    run_record_payload,
)
from llm_harness.state import QueueTask, RunRecord


class ProtocolTests(unittest.TestCase):
    def test_envelope_has_stable_versioned_shape(self):
        envelope = protocol_envelope("supervisor.status", ok=True, data={"queued": 2})

        self.assertEqual(envelope["protocol"], PROTOCOL_NAMESPACE)
        self.assertEqual(envelope["protocol_version"], PROTOCOL_VERSION)
        self.assertEqual(envelope["message_type"], "supervisor.status")
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["data"], {"queued": 2})
        self.assertIn("generated_at_utc", envelope)

    def test_dump_json_is_valid_unicode_json(self):
        encoded = dump_json(protocol_envelope("test", ok=False, error="реши сам"))

        decoded = json.loads(encoded)
        self.assertEqual(decoded["error"], "реши сам")
        self.assertTrue(encoded.endswith("\n"))

    def test_canonical_json_is_order_independent(self):
        self.assertEqual(canonical_json_bytes({"b": 2, "a": 1}), canonical_json_bytes({"a": 1, "b": 2}))

    def test_queue_and_run_payloads_are_nested_objects(self):
        work_item = WorkItem(
            id="task-1",
            title="Task",
            objective="Produce evidence.",
            acceptance_criteria=("Evidence exists.",),
            verification_commands=("python -m unittest",),
            allowed_paths=("docs",),
            non_goals=("Do not merge.",),
        )
        task = QueueTask(
            work_item=work_item,
            status="done",
            source="task.json",
            added_at_utc="2026-01-01T00:00:00+00:00",
            updated_at_utc="2026-01-01T00:01:00+00:00",
            attempts=1,
            last_commit="abc123",
        )
        run = RunRecord(
            run_id="run-1",
            work_item_id="task-1",
            started_at_utc="2026-01-01T00:00:00+00:00",
            finished_at_utc="2026-01-01T00:01:00+00:00",
            ok=True,
            commit="abc123",
            error=None,
            pre_apply_findings=(),
            post_apply_findings=(),
            command_results=(CommandResult("check", 0, "ok", ""),),
        )

        task_data = queue_task_payload(task)
        run_data = run_record_payload(run)

        self.assertEqual(task_data["work_item"]["allowed_paths"], ["docs"])
        self.assertEqual(task_data["work_item"]["depends_on"], [])
        self.assertEqual(task_data["work_item"]["priority"], 0)
        self.assertEqual(run_data["command_results"][0]["ok"], True)
        self.assertEqual(run_data["commit"], "abc123")


if __name__ == "__main__":
    unittest.main()
