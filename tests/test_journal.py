import contextlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from llm_harness.cli import main
from llm_harness.domain import WorkItem
from llm_harness.journal import InteractionJournal, redact
from llm_harness.state import HohStateStore


class JournalTests(unittest.TestCase):
    def test_redacts_secret_keys_bot_tokens_bearer_and_env_assignments(self):
        value = {
            "bot_token": "123456789:abcdefghijklmnopqrstuvwxyz_ABCD",
            "message": "Bearer abc.def.ghi HOH_API_KEY=plain-secret",
            "safe": "visible",
        }

        result = redact(value)

        self.assertEqual(result["bot_token"], "<redacted>")
        self.assertNotIn("abc.def.ghi", result["message"])
        self.assertNotIn("plain-secret", result["message"])
        self.assertEqual(result["safe"], "visible")

    def test_redacts_bare_credentials_printed_by_a_command(self):
        stdout = "\n".join(
            (
                "Configured key sk-proj-AbCdEf0123456789AbCdEf0123456789",
                "Anthropic sk-ant-api03-abcdefghijklmnopqrstuvwxyz01",
                "GitHub gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                "AWS AKIAIOSFODNN7EXAMPLE",
                "JWT eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p",
            )
        )

        redacted = redact({"stdout": stdout})["stdout"]

        for secret in (
            "sk-proj-AbCdEf0123456789AbCdEf0123456789",
            "sk-ant-api03-abcdefghijklmnopqrstuvwxyz01",
            "gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "AKIAIOSFODNN7EXAMPLE",
        ):
            self.assertNotIn(secret, redacted)
        self.assertIn("<redacted-openai-key>", redacted)
        self.assertIn("<redacted-anthropic-key>", redacted)
        self.assertIn("<redacted-github-token>", redacted)
        self.assertIn("<redacted-aws-key-id>", redacted)
        self.assertIn("<redacted-jwt>", redacted)

    def test_redaction_leaves_ordinary_output_alone(self):
        text = (
            "commit 3f75f56 changed src/llm_harness/cli.py and docs/architecture.md; "
            "see sk- prefix discussion and path C:/Users/x/sk-something"
        )

        self.assertEqual(redact(text), text)

    def test_private_key_blocks_are_removed_whole(self):
        text = "\n".join(
            ("before", "-----BEGIN RSA PRIVATE KEY-----", "MIIabc", "-----END RSA PRIVATE KEY-----", "after")
        )

        redacted = redact(text)

        self.assertNotIn("MIIabc", redacted)
        self.assertIn("<redacted-private-key>", redacted)
        self.assertTrue(redacted.startswith("before"))
        self.assertTrue(redacted.endswith("after"))

    def test_state_transitions_create_append_only_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")
            task = self._task()
            store.enqueue(task, source="brief")
            store.mark_running(task.id)
            store.record_failure(task.id, "2026-01-01T00:00:00+00:00", "simulated")

            events = store.journal.events()

        self.assertEqual([item.action for item in events], ["task.enqueued", "task.started", "task.failed"])
        self.assertEqual(events[-1].content["to"], "failed")

    def test_cli_lists_and_exports_redacted_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            journal = InteractionJournal(state)
            journal.record(
                "interaction",
                "supervisor",
                "worker.message",
                recipient="worker",
                content={"text": "hello", "api_key": "must-not-leak"},
                task_id="task-1",
            )
            stdout = StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "journal-list",
                        "--project-root", str(root),
                        "--state-root", str(state),
                        "--task-id", "task-1",
                        "--json",
                    ]
                )
            payload = json.loads(stdout.getvalue())
            output = root / "journal.md"
            with contextlib.redirect_stdout(StringIO()):
                export_code = main(
                    [
                        "journal-export",
                        "--project-root", str(root),
                        "--state-root", str(state),
                        "--output", str(output),
                        "--format", "markdown",
                    ]
                )

            markdown = output.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(export_code, 0)
        self.assertEqual(payload["data"]["events"][0]["content"]["api_key"], "<redacted>")
        self.assertNotIn("must-not-leak", markdown)
        self.assertIn("worker.message", markdown)

    def test_external_role_can_record_brief_event_through_closed_ingress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            ingress = root / "event.json"
            ingress.write_text(
                json.dumps(
                    {
                        "event_version": "1.0",
                        "event_type": "interaction",
                        "actor": "supervisor",
                        "recipient": "customer",
                        "action": "brief.role_question",
                        "content": {"question": "Choose Critic", "api_key": "hidden"},
                        "metadata": {"channel": "agent-ui"},
                        "task_id": None,
                        "run_id": None,
                        "correlation_id": "brief-1",
                    }
                ),
                encoding="utf-8",
            )
            stdout = StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "journal-record", "--project-root", str(root), "--state-root", str(state),
                        "--event", str(ingress), "--json",
                    ]
                )
            payload = json.loads(stdout.getvalue())
            stored = InteractionJournal(state).events()[0]

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(stored.action, "brief.role_question")
        self.assertEqual(stored.content["api_key"], "<redacted>")

    def _task(self) -> WorkItem:
        return WorkItem(
            id="task-1",
            title="Trace task",
            objective="Create trace evidence.",
            acceptance_criteria=("Trace exists.",),
            verification_commands=("python -c \"print('ok')\"",),
        )


if __name__ == "__main__":
    unittest.main()
