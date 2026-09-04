from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .domain import WorkItem
from .durable_io import read_authored_text


class TaskLoadError(ValueError):
    pass


def load_work_item(path: Path) -> WorkItem:
    text = read_authored_text(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _load_json_task(text, path)
    if suffix in {".md", ".markdown"}:
        return _load_markdown_task(text, path)
    raise TaskLoadError(f"Unsupported task file extension: {path.suffix}")


def _load_json_task(text: str, path: Path) -> WorkItem:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TaskLoadError(f"Invalid JSON task file: {path}") from exc
    if not isinstance(data, dict):
        raise TaskLoadError("JSON task file must contain an object.")
    return _work_item_from_mapping(data)


def _load_markdown_task(text: str, path: Path) -> WorkItem:
    sections = _markdown_sections(text)
    title = _first_markdown_heading(text) or _required_scalar(sections, "title")
    data: dict[str, Any] = {
        "id": _required_scalar(sections, "id"),
        "title": title,
        "objective": _required_scalar(sections, "objective"),
        "acceptance_criteria": _required_list(sections, "acceptance criteria"),
        "verification_commands": _required_list(sections, "verification commands"),
        "allowed_paths": _optional_list(sections, "allowed paths"),
        "non_goals": _optional_list(sections, "non-goals"),
        "depends_on": _optional_list(sections, "depends on"),
        "priority": _markdown_integer(sections, "priority"),
        "correction_instructions": _optional_list(sections, "correction instructions"),
        "correction_decision_id": _optional_scalar(sections, "correction decision id"),
    }
    try:
        return _work_item_from_mapping(data)
    except TaskLoadError as exc:
        raise TaskLoadError(f"Invalid Markdown task file {path}: {exc}") from exc


def _work_item_from_mapping(data: dict[str, Any]) -> WorkItem:
    return WorkItem(
        id=_required_string(data, "id"),
        title=_required_string(data, "title"),
        objective=_required_string(data, "objective"),
        acceptance_criteria=_required_string_tuple(data, "acceptance_criteria"),
        verification_commands=_required_string_tuple(data, "verification_commands"),
        allowed_paths=_optional_string_tuple(data, "allowed_paths"),
        non_goals=_optional_string_tuple(data, "non_goals"),
        depends_on=_optional_string_tuple(data, "depends_on"),
        priority=_optional_integer(data, "priority"),
        correction_instructions=_optional_string_tuple(data, "correction_instructions"),
        correction_decision_id=_optional_mapping_string(data, "correction_decision_id"),
    )


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TaskLoadError(f"Task field '{key}' must be a non-empty string.")
    return value.strip()


def _optional_mapping_string(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TaskLoadError(f"Task field '{key}' must be null or a non-empty string.")
    return value.strip()


def _optional_integer(data: dict[str, Any], key: str) -> int:
    value = data.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskLoadError(f"Task field '{key}' must be an integer.")
    return value


def _required_string_tuple(data: dict[str, Any], key: str) -> tuple[str, ...]:
    values = _optional_string_tuple(data, key)
    if not values:
        raise TaskLoadError(f"Task field '{key}' must contain at least one item.")
    return values


def _optional_string_tuple(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TaskLoadError(f"Task field '{key}' must be a list of strings.")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise TaskLoadError(f"Task field '{key}' must be a list of non-empty strings.")
        items.append(item.strip())
    return tuple(items)


def _markdown_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            current = _normalize_heading(line[3:])
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def _first_markdown_heading(text: str) -> str | None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return None


def _required_scalar(sections: dict[str, list[str]], heading: str) -> str:
    lines = _content_lines(sections, heading)
    if not lines:
        raise TaskLoadError(f"Markdown task section '## {heading}' is required.")
    return "\n".join(lines).strip()


def _optional_scalar(sections: dict[str, list[str]], heading: str) -> str | None:
    lines = _content_lines(sections, heading)
    return "\n".join(lines).strip() if lines else None


def _markdown_integer(sections: dict[str, list[str]], heading: str) -> int:
    value = _optional_scalar(sections, heading)
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError as exc:
        raise TaskLoadError(f"Markdown task section '## {heading}' must be an integer.") from exc


def _required_list(sections: dict[str, list[str]], heading: str) -> list[str]:
    items = _optional_list(sections, heading)
    if not items:
        raise TaskLoadError(f"Markdown task section '## {heading}' must contain at least one list item.")
    return items


def _optional_list(sections: dict[str, list[str]], heading: str) -> list[str]:
    return [_strip_list_marker(line) for line in _content_lines(sections, heading) if _is_list_item(line)]


def _content_lines(sections: dict[str, list[str]], heading: str) -> list[str]:
    return [line.strip() for line in sections.get(_normalize_heading(heading), ()) if line.strip()]


def _is_list_item(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("- ") or stripped.startswith("* ")


def _strip_list_marker(line: str) -> str:
    return line.strip()[2:].strip()


def _normalize_heading(value: str) -> str:
    return value.strip().lower().replace("_", " ")
