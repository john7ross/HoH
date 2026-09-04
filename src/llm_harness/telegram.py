from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
import os
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import TelegramConfig
from .journal import InteractionJournal


TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_MAX_TEXT_LENGTH = 4096


class TelegramError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramIncomingMessage:
    update_id: int
    user_id: str
    chat_id: str
    text: str


class Notifier(ABC):
    @abstractmethod
    def notify_user_action_required(self, text: str) -> None:
        ...

    def notify_project_ready(self, text: str) -> None:
        self.notify_user_action_required(text)

    def notify_audit_failed(self, text: str) -> None:
        self.notify_user_action_required(text)


@dataclass
class StubTelegramNotifier(Notifier):
    messages: list[str] = field(default_factory=list)

    def notify_user_action_required(self, text: str) -> None:
        self.messages.append(text)


@dataclass
class JournaledNotifier(Notifier):
    inner: Notifier
    journal: InteractionJournal

    def notify_user_action_required(self, text: str) -> None:
        self._send("notification.user_action_required", text, self.inner.notify_user_action_required)

    def notify_project_ready(self, text: str) -> None:
        self._send("notification.project_ready", text, self.inner.notify_project_ready)

    def notify_audit_failed(self, text: str) -> None:
        self._send("notification.audit_failed", text, self.inner.notify_audit_failed)

    def _send(self, action: str, text: str, sender) -> None:
        self.journal.record(
            "interaction", "supervisor", action, recipient="customer", content={"text": text}
        )
        sender(text)


@dataclass(frozen=True)
class TelegramBotNotifier(Notifier):
    bot_token: str
    chat_id: str
    api_base_url: str = TELEGRAM_API_BASE_URL
    timeout_seconds: float = 10.0
    http_post: object | None = None

    def __post_init__(self) -> None:
        if not self.bot_token:
            raise ValueError("Telegram bot token is required.")
        if not self.chat_id:
            raise ValueError("Telegram chat id is required.")

    def notify_user_action_required(self, text: str) -> None:
        self.send_message(text)

    def notify_project_ready(self, text: str) -> None:
        self.send_message(text)

    def notify_audit_failed(self, text: str) -> None:
        self.send_message(text)

    def send_message(self, text: str) -> None:
        for chunk in _split_telegram_text(text):
            self._post_send_message(chunk)

    def get_updates(self, offset: int | None = None, limit: int = 100, poll_timeout_seconds: int = 0) -> tuple[dict, ...]:
        payload: dict[str, object] = {
            "limit": limit,
            "timeout": poll_timeout_seconds,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        decoded = self._post_json("getUpdates", payload, timeout=max(self.timeout_seconds, poll_timeout_seconds + 5.0))
        result = decoded.get("result")
        if not isinstance(result, list):
            raise TelegramError("Telegram getUpdates returned a non-list result.")
        return tuple(item for item in result if isinstance(item, dict))

    def _post_send_message(self, text: str) -> None:
        self._post_json(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
            },
        )

    def _post_json(self, method: str, payload: Mapping[str, object], timeout: float | None = None) -> dict:
        url = f"{self.api_base_url.rstrip('/')}/bot{self.bot_token}/{method}"
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            opener = self.http_post or urlopen
            with opener(request, timeout=timeout or self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
                if response.status < 200 or response.status >= 300:
                    raise TelegramError(f"Telegram {method} failed with HTTP {response.status}.")
                decoded = json.loads(body) if body else {}
                if decoded.get("ok") is not True:
                    description = decoded.get("description", "unknown Telegram API error")
                    raise TelegramError(f"Telegram {method} failed: {description}")
                return decoded
        except HTTPError as exc:
            raise TelegramError(f"Telegram {method} failed with HTTP {exc.code}.") from exc
        except URLError as exc:
            raise TelegramError(f"Telegram {method} network error: {exc.reason}") from exc


def build_notifier(
    config: TelegramConfig,
    env: Mapping[str, str] | None = None,
) -> Notifier:
    if not config.enabled:
        return StubTelegramNotifier()

    environment = env or os.environ
    if not config.bot_token_env:
        raise ValueError("telegram.bot_token_env is required when Telegram is enabled.")
    if not config.chat_id_env:
        raise ValueError("telegram.chat_id_env is required when Telegram is enabled.")

    bot_token = environment.get(config.bot_token_env)
    chat_id = environment.get(config.chat_id_env)
    if not bot_token:
        raise ValueError(f"Environment variable {config.bot_token_env} is required for Telegram bot token.")
    if not chat_id:
        raise ValueError(f"Environment variable {config.chat_id_env} is required for Telegram chat id.")

    return TelegramBotNotifier(bot_token=bot_token, chat_id=chat_id)


def text_message_from_update(update: Mapping[str, object]) -> TelegramIncomingMessage | None:
    update_id = update.get("update_id")
    message = update.get("message")
    if not isinstance(update_id, int) or not isinstance(message, dict):
        return None

    text = message.get("text")
    user = message.get("from")
    chat = message.get("chat")
    if not isinstance(text, str) or not isinstance(user, dict) or not isinstance(chat, dict):
        return None

    user_id = user.get("id")
    chat_id = chat.get("id")
    if user_id is None or chat_id is None:
        return None

    return TelegramIncomingMessage(
        update_id=update_id,
        user_id=str(user_id),
        chat_id=str(chat_id),
        text=text,
    )


def _split_telegram_text(text: str) -> tuple[str, ...]:
    if not text:
        return (" ",)
    return tuple(
        text[index : index + TELEGRAM_MAX_TEXT_LENGTH]
        for index in range(0, len(text), TELEGRAM_MAX_TEXT_LENGTH)
    )
