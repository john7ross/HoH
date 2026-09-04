import json
import unittest

from llm_harness.config import TelegramConfig
from llm_harness.telegram import (
    TELEGRAM_MAX_TEXT_LENGTH,
    StubTelegramNotifier,
    TelegramIncomingMessage,
    TelegramBotNotifier,
    TelegramError,
    build_notifier,
    text_message_from_update,
)


class TelegramNotifierTests(unittest.TestCase):
    def test_build_notifier_returns_stub_when_disabled(self):
        notifier = build_notifier(TelegramConfig(enabled=False), env={})

        self.assertIsInstance(notifier, StubTelegramNotifier)

    def test_build_notifier_requires_env_values_when_enabled(self):
        with self.assertRaises(ValueError):
            build_notifier(
                TelegramConfig(
                    enabled=True,
                    bot_token_env="HOH_TELEGRAM_BOT_TOKEN",
                    chat_id_env="HOH_TELEGRAM_CHAT_ID",
                ),
                env={},
            )

    def test_telegram_notifier_posts_json_send_message_request(self):
        calls = []

        def fake_post(request, timeout):
            calls.append((request, timeout))
            return _Response(200, {"ok": True, "result": {"message_id": 1}})

        notifier = TelegramBotNotifier(
            bot_token="secret-token",
            chat_id="123456",
            http_post=fake_post,
        )

        notifier.notify_user_action_required("Need clarification")

        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(timeout, 10.0)
        self.assertEqual(request.full_url, "https://api.telegram.org/botsecret-token/sendMessage")
        self.assertEqual(request.get_method(), "POST")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload, {"chat_id": "123456", "text": "Need clarification"})

    def test_telegram_notifier_splits_long_messages(self):
        calls = []

        def fake_post(request, timeout):
            calls.append(json.loads(request.data.decode("utf-8"))["text"])
            return _Response(200, {"ok": True, "result": {"message_id": len(calls)}})

        notifier = TelegramBotNotifier(
            bot_token="secret-token",
            chat_id="123456",
            http_post=fake_post,
        )

        notifier.notify_project_ready("x" * (TELEGRAM_MAX_TEXT_LENGTH + 1))

        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[0]), TELEGRAM_MAX_TEXT_LENGTH)
        self.assertEqual(calls[1], "x")

    def test_telegram_notifier_raises_on_api_error(self):
        def fake_post(request, timeout):
            return _Response(200, {"ok": False, "description": "chat not found"})

        notifier = TelegramBotNotifier(
            bot_token="secret-token",
            chat_id="123456",
            http_post=fake_post,
        )

        with self.assertRaises(TelegramError):
            notifier.notify_audit_failed("Audit failed")

    def test_telegram_get_updates_posts_offset_limit_timeout(self):
        calls = []

        def fake_post(request, timeout):
            calls.append((request, timeout))
            return _Response(
                200,
                {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 101,
                            "message": {
                                "from": {"id": 42},
                                "chat": {"id": 123456},
                                "text": "/status",
                            },
                        }
                    ],
                },
            )

        notifier = TelegramBotNotifier(
            bot_token="secret-token",
            chat_id="123456",
            http_post=fake_post,
        )

        updates = notifier.get_updates(offset=100, limit=10, poll_timeout_seconds=2)

        self.assertEqual(len(updates), 1)
        request, timeout = calls[0]
        self.assertEqual(timeout, 10.0)
        self.assertEqual(request.full_url, "https://api.telegram.org/botsecret-token/getUpdates")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["offset"], 100)
        self.assertEqual(payload["limit"], 10)
        self.assertEqual(payload["timeout"], 2)
        self.assertEqual(payload["allowed_updates"], ["message"])

    def test_text_message_from_update_extracts_authenticated_fields(self):
        message = text_message_from_update(
            {
                "update_id": 101,
                "message": {
                    "from": {"id": 42},
                    "chat": {"id": 123456},
                    "text": "/status",
                },
            }
        )

        self.assertEqual(
            message,
            TelegramIncomingMessage(update_id=101, user_id="42", chat_id="123456", text="/status"),
        )

    def test_text_message_from_update_ignores_non_text_updates(self):
        self.assertIsNone(text_message_from_update({"update_id": 101, "message": {"photo": []}}))


class _Response:
    def __init__(self, status: int, body: dict) -> None:
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
