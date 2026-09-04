from pathlib import Path
import tempfile
import unittest

from llm_harness.state import HohStateStore
from llm_harness.telegram_polling import poll_operator_commands


class TelegramPollingTests(unittest.TestCase):
    def test_poll_processes_authorized_text_command_and_advances_offset(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")
            bot = FakeBot(
                [
                    {
                        "update_id": 101,
                        "message": {
                            "from": {"id": 42},
                            "chat": {"id": 123456},
                            "text": "/status",
                        },
                    }
                ]
            )

            result = poll_operator_commands(
                store=store,
                bot=bot,  # type: ignore[arg-type]
                allowed_user_id="42",
                allowed_chat_id="123456",
            )

            self.assertEqual(result.processed, 1)
            self.assertEqual(result.ignored, 0)
            self.assertEqual(result.next_offset, 102)
            self.assertEqual(store.telegram_update_offset(), 102)
            self.assertEqual(store.operator_events()[0].command, "status")
            self.assertIn("HoH operator command: OK", bot.sent_messages[0])

    def test_poll_ignores_unauthorized_sender_but_advances_offset(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")
            bot = FakeBot(
                [
                    {
                        "update_id": 201,
                        "message": {
                            "from": {"id": 7},
                            "chat": {"id": 123456},
                            "text": "/status",
                        },
                    }
                ]
            )

            result = poll_operator_commands(
                store=store,
                bot=bot,  # type: ignore[arg-type]
                allowed_user_id="42",
                allowed_chat_id="123456",
            )

            self.assertEqual(result.processed, 0)
            self.assertEqual(result.ignored, 1)
            self.assertEqual(store.telegram_update_offset(), 202)
            self.assertEqual(store.operator_events(), ())
            self.assertEqual(bot.sent_messages, [])

    def test_poll_uses_persisted_offset_for_next_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HohStateStore(Path(tmp) / "state")
            store.set_telegram_update_offset(300)
            bot = FakeBot([])

            result = poll_operator_commands(
                store=store,
                bot=bot,  # type: ignore[arg-type]
                allowed_user_id="42",
                allowed_chat_id="123456",
            )

            self.assertEqual(result.next_offset, 300)
            self.assertEqual(bot.requested_offsets, [300])


class FakeBot:
    def __init__(self, updates: list[dict]) -> None:
        self.updates = tuple(updates)
        self.sent_messages: list[str] = []
        self.requested_offsets: list[int | None] = []

    def get_updates(self, offset=None, limit=100, poll_timeout_seconds=0):
        self.requested_offsets.append(offset)
        return self.updates

    def send_message(self, text: str) -> None:
        self.sent_messages.append(text)


if __name__ == "__main__":
    unittest.main()
