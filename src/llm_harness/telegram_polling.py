from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .coordination import named_state_operation_lease
from .operator_decisions import OperatorDecisionResult, handle_operator_command
from .state import HohStateStore
from .telegram import TelegramBotNotifier, text_message_from_update


@dataclass(frozen=True)
class TelegramPollResult:
    processed: int
    ignored: int
    next_offset: int | None
    responses: tuple[str, ...]


def poll_operator_commands(
    store: HohStateStore,
    bot: TelegramBotNotifier,
    allowed_user_id: str,
    allowed_chat_id: str,
    stale_minutes: float = 60.0,
    limit: int = 100,
    poll_timeout_seconds: int = 0,
    repository: Path | None = None,
) -> TelegramPollResult:
    lease = named_state_operation_lease(store.root, "telegram-poll", store.coordination)
    with lease.hold("telegram.poll"):
        return _poll_operator_commands_locked(
            store,
            bot,
            allowed_user_id,
            allowed_chat_id,
            stale_minutes,
            limit,
            poll_timeout_seconds,
            repository,
        )


def _poll_operator_commands_locked(
    store: HohStateStore,
    bot: TelegramBotNotifier,
    allowed_user_id: str,
    allowed_chat_id: str,
    stale_minutes: float,
    limit: int,
    poll_timeout_seconds: int,
    repository: Path | None,
) -> TelegramPollResult:
    offset = store.telegram_update_offset()
    updates = bot.get_updates(offset=offset, limit=limit, poll_timeout_seconds=poll_timeout_seconds)
    processed = 0
    ignored = 0
    responses: list[str] = []
    max_update_id: int | None = None

    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update_id = update_id if max_update_id is None else max(max_update_id, update_id)

        message = text_message_from_update(update)
        if message is None:
            ignored += 1
            continue
        if message.user_id != allowed_user_id or message.chat_id != allowed_chat_id:
            ignored += 1
            continue

        decision = handle_operator_command(
            store,
            message.text,
            stale_minutes=stale_minutes,
            repository=repository,
        )
        response = _format_response(decision)
        bot.send_message(response)
        responses.append(response)
        processed += 1

    next_offset = max_update_id + 1 if max_update_id is not None else offset
    if next_offset is not None:
        store.set_telegram_update_offset(next_offset)

    return TelegramPollResult(
        processed=processed,
        ignored=ignored,
        next_offset=next_offset,
        responses=tuple(responses),
    )


def _format_response(result: OperatorDecisionResult) -> str:
    status = "OK" if result.ok else "ERROR"
    lines = [
        f"HoH operator command: {status}",
        f"Command: {result.command}",
    ]
    if result.task_id:
        lines.append(f"Task: {result.task_id}")
    lines.append(result.message)
    return "\n".join(lines)
