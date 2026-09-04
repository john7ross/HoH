from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from llm_harness.config import HarnessConfig, SupervisorConfig
from llm_harness.supervisor_chat import (
    SupervisorChatError,
    clear_supervisor_chat,
    load_supervisor_chat,
    send_supervisor_message,
    supervisor_chat_path,
)


class SupervisorChatTests(unittest.TestCase):
    def test_chat_persists_successful_turn_outside_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)

            def invoke(_config, _root, prompt):
                self.assertIn("USER: Build a calculator", prompt)
                return {
                    "reply": "Which platforms must it support?",
                    "ready_to_plan": False,
                    "requirements_summary": "Calculator application.",
                }, None

            turn = send_supervisor_message(
                root,
                "Build a calculator",
                config=HarnessConfig(),
                invoke=invoke,
            )
            history = load_supervisor_chat(root)
            path = supervisor_chat_path(root)

        self.assertEqual(turn.supervisor.text, "Which platforms must it support?")
        self.assertEqual([item.role for item in history], ["user", "supervisor"])
        self.assertFalse(str(path).startswith(str(root)))

    def test_failed_turn_does_not_persist_user_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)

            def fail(_config, _root, _prompt):
                raise SupervisorChatError("offline")

            with self.assertRaisesRegex(SupervisorChatError, "offline"):
                send_supervisor_message(root, "hello", config=HarnessConfig(), invoke=fail)
            history = load_supervisor_chat(root)

        self.assertEqual(history, ())

    def test_follow_up_contains_prior_conversation(self):
        prompts = []
        responses = iter(
            [
                {"reply": "Which OS?", "ready_to_plan": False, "requirements_summary": "App."},
                {"reply": "Ready.", "ready_to_plan": True, "requirements_summary": "Windows app."},
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)

            def invoke(_config, _root, prompt):
                prompts.append(prompt)
                return next(responses), None

            send_supervisor_message(root, "Build an app", config=HarnessConfig(), invoke=invoke)
            second = send_supervisor_message(root, "Windows only", config=HarnessConfig(), invoke=invoke)

        self.assertIn("SUPERVISOR: Which OS?", prompts[1])
        self.assertTrue(second.supervisor.ready_to_plan)
        self.assertEqual(second.supervisor.requirements_summary, "Windows app.")

    def test_clear_chat_removes_only_chat_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            send_supervisor_message(
                root,
                "task",
                config=HarnessConfig(),
                invoke=lambda *_args: (
                    {"reply": "ok", "ready_to_plan": True, "requirements_summary": "task"},
                    None,
                ),
            )
            clear_supervisor_chat(root)
            self.assertEqual(load_supervisor_chat(root), ())

    def test_command_json_supervisor_transport_runs_in_chat_mode(self):
        script = (
            "import json,sys; prompt=sys.stdin.read(); "
            "assert 'ready_to_plan' in prompt; "
            "print(json.dumps({'reply':'Clarify scope','ready_to_plan':False,'requirements_summary':'Draft'}))"
        )
        config = HarnessConfig(
            supervisor=SupervisorConfig(
                driver="command_json",
                command=sys.executable,
                args=("-c", script),
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)

            turn = send_supervisor_message(root, "Build it", config=config)

        self.assertEqual(turn.supervisor.text, "Clarify scope")
        self.assertEqual(turn.evidence.endpoint, "process/stdin-json/chat")  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
