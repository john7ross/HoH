from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from .domain import VerificationReport, WorkItem, WorkerPatch
from .trust import WorkerTrustPolicy


GIT_HEADER_PREFIX = "diff --git "
COMBINED_HEADER_PREFIXES = ("diff --cc ", "diff --combined ")
WHOLE_REPOSITORY_SCOPE = "*"

_HUNK_RE = re.compile(r"^@@+ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_C_ESCAPES = {
    '"': 0x22,
    "\\": 0x5C,
    "a": 0x07,
    "b": 0x08,
    "f": 0x0C,
    "n": 0x0A,
    "r": 0x0D,
    "t": 0x09,
    "v": 0x0B,
}


@dataclass(frozen=True)
class PatchScan:
    """Result of reading a patch: every path it touches plus every part we could not read."""

    files: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems


class PolicyVerifier:
    """Mandatory deterministic gate; optional semantic verification runs only after this gate."""

    def __init__(
        self,
        forbidden_paths: tuple[str, ...] = (".git/",),
        require_tests: bool = True,
        trust_policy: WorkerTrustPolicy | None = None,
        require_explicit_scope: bool = True,
    ) -> None:
        self.forbidden_paths = forbidden_paths
        self.require_tests = require_tests
        self.trust_policy = trust_policy or WorkerTrustPolicy()
        self.require_explicit_scope = require_explicit_scope

    def verify_patch_before_apply(self, work_item: WorkItem, worker_patch: WorkerPatch) -> VerificationReport:
        findings: list[str] = []
        scan = scan_patch(worker_patch.patch)
        changed_files = scan.files
        unsafe_paths = tuple(path for path in changed_files if _is_unsafe_repo_path(path))

        if self.trust_policy.requires_patch_output and not worker_patch.patch.strip():
            findings.append("Patch is empty.")
        if worker_patch.work_item_id != work_item.id:
            findings.append("Patch work item id does not match task.")
        if self.require_tests and not work_item.verification_commands:
            findings.append("Work item has no verification commands.")
        if scan.problems:
            findings.append("Patch could not be parsed for scope verification: " + "; ".join(scan.problems))
        if unsafe_paths:
            findings.append("Patch contains unsafe repository paths: " + ", ".join(unsafe_paths))

        forbidden_hits = _forbidden_hits(changed_files, self.forbidden_paths)
        if forbidden_hits:
            findings.append("Patch touches forbidden paths: " + ", ".join(forbidden_hits))

        if work_item.allowed_paths:
            outside_scope = tuple(
                path for path in changed_files if not _matches_any_allowed_path(path, work_item.allowed_paths)
            )
            if outside_scope:
                findings.append("Patch changes files outside allowed paths: " + ", ".join(outside_scope))
        elif self.require_explicit_scope and changed_files:
            findings.append(
                "Work item declares no allowed paths, so patch scope cannot be verified. "
                f"Declare the paths the task may touch, or '{WHOLE_REPOSITORY_SCOPE}' for the whole repository."
            )

        return VerificationReport(
            ok=not findings,
            findings=tuple(findings),
            metrics={
                "changed_files_count": len(changed_files),
                "forbidden_paths_touched_count": len(forbidden_hits),
                "acceptance_criteria_count": len(work_item.acceptance_criteria),
                "verification_commands_count": len(work_item.verification_commands),
                "worker_trust_level": self.trust_policy.level.value,
                "worker_can_create_branch": self.trust_policy.can_create_branch,
                "worker_can_commit": self.trust_policy.can_commit,
                "patch_parse_problems_count": len(scan.problems),
            },
        )

    def verify_after_apply(
        self,
        work_item: WorkItem,
        command_results_ok: bool,
        documentation_updated: bool,
    ) -> VerificationReport:
        findings: list[str] = []
        if not command_results_ok:
            findings.append("One or more verification commands failed.")
        if not work_item.acceptance_criteria:
            findings.append("Work item has no acceptance criteria.")

        return VerificationReport(
            ok=not findings,
            findings=tuple(findings),
            metrics={
                "acceptance_criteria_count": len(work_item.acceptance_criteria),
                "verification_commands_count": len(work_item.verification_commands),
                "documentation_updated": documentation_updated,
            },
        )


def changed_files_from_patch(patch: str) -> tuple[str, ...]:
    """Every repository path the patch touches, on both sides of renames."""
    return scan_patch(patch).files


def scan_patch(patch: str) -> PatchScan:
    """Read a unified or git patch fail-closed: anything we cannot read becomes a problem."""
    files: list[str] = []
    problems: list[str] = []
    lines = patch.splitlines()
    total = len(lines)
    index = 0
    pending_header: str | None = None
    remaining_old = 0
    remaining_new = 0

    while index < total:
        line = lines[index]

        if remaining_old > 0 or remaining_new > 0:
            consumed = _hunk_body_step(line)
            if consumed is not None:
                remaining_old = max(0, remaining_old - consumed[0])
                remaining_new = max(0, remaining_new - consumed[1])
                index += 1
                continue
            remaining_old = 0
            remaining_new = 0

        if line.startswith("@@"):
            counts = _hunk_counts(line)
            if counts is None:
                problems.append(f"unreadable hunk header {line.strip()!r}")
            else:
                remaining_old, remaining_new = counts
            index += 1
            continue

        if line.startswith(GIT_HEADER_PREFIX):
            if pending_header is not None:
                problems.append(f"unreadable diff header {pending_header!r}")
            names = _git_header_paths(line[len(GIT_HEADER_PREFIX) :])
            if names is None:
                pending_header = line.strip()
            else:
                pending_header = None
                files.extend(names)
            index += 1
            continue

        if line.startswith(COMBINED_HEADER_PREFIXES):
            if pending_header is not None:
                problems.append(f"unreadable diff header {pending_header!r}")
                pending_header = None
            name = _decode_path_token(line.split(" ", 2)[2])
            if name is None:
                problems.append(f"unreadable diff header {line.strip()!r}")
            else:
                files.append(name)
            index += 1
            continue

        if line.startswith("--- ") and index + 1 < total and lines[index + 1].startswith("+++ "):
            old_name = _decode_path_token(line[4:])
            new_name = _decode_path_token(lines[index + 1][4:])
            resolved = tuple(name for name in (old_name, new_name) if name)
            if not resolved:
                problems.append(f"unreadable unified diff header {line.strip()!r}")
            else:
                files.extend(resolved)
            pending_header = None
            index += 2
            continue

        index += 1

    if pending_header is not None:
        problems.append(f"unreadable diff header {pending_header!r}")
    if patch.strip() and not files and not problems:
        problems.append("patch declares no file paths")

    return PatchScan(tuple(dict.fromkeys(files)), tuple(problems))


def _hunk_counts(line: str) -> tuple[int, int] | None:
    match = _HUNK_RE.match(line)
    if match is None:
        return None
    old_count = int(match.group(2)) if match.group(2) is not None else 1
    new_count = int(match.group(4)) if match.group(4) is not None else 1
    return old_count, new_count


def _hunk_body_step(line: str) -> tuple[int, int] | None:
    if not line:
        return 1, 1
    marker = line[0]
    if marker == " ":
        return 1, 1
    if marker == "-":
        return 1, 0
    if marker == "+":
        return 0, 1
    if marker == "\\":
        return 0, 0
    return None


def _git_header_paths(rest: str) -> tuple[str, ...] | None:
    rest = rest.rstrip()
    if not rest:
        return None

    if rest.startswith('"'):
        first, remainder = _read_quoted_token(rest)
        if first is None:
            return None
        remainder = remainder.strip()
        if not remainder:
            return None
        if remainder.startswith('"'):
            second, tail = _read_quoted_token(remainder)
            if second is None or tail.strip():
                return None
        else:
            second = remainder
        names = tuple(name for name in (_strip_diff_prefix(first), _strip_diff_prefix(second)) if name)
        return tuple(dict.fromkeys(names)) or None

    candidates: list[tuple[str, str]] = []
    for position, char in enumerate(rest):
        if char != " ":
            continue
        left = rest[:position]
        right = rest[position + 1 :]
        if left.startswith("a/") and right.startswith("b/"):
            candidates.append((left[2:], right[2:]))
    if not candidates:
        return None
    for left, right in candidates:
        if left == right and left:
            return (left,)
    if len(candidates) == 1:
        left, right = candidates[0]
        names = tuple(name for name in (left, right) if name)
        return tuple(dict.fromkeys(names)) or None
    return None


def _decode_path_token(token: str) -> str | None:
    candidate = token.split("\t", 1)[0].rstrip()
    if candidate.startswith('"'):
        decoded, tail = _read_quoted_token(candidate)
        if decoded is None or tail.strip():
            return None
        candidate = decoded
    return _strip_diff_prefix(candidate)


def _read_quoted_token(text: str) -> tuple[str | None, str]:
    raw = bytearray()
    index = 1
    total = len(text)
    while index < total:
        char = text[index]
        if char == '"':
            try:
                return raw.decode("utf-8"), text[index + 1 :]
            except UnicodeDecodeError:
                return None, ""
        if char != "\\":
            raw.extend(char.encode("utf-8"))
            index += 1
            continue
        index += 1
        if index >= total:
            return None, ""
        escape = text[index]
        if escape in _C_ESCAPES:
            raw.append(_C_ESCAPES[escape])
            index += 1
            continue
        octal = text[index : index + 3]
        if len(octal) == 3 and all(digit in "01234567" for digit in octal):
            raw.append(int(octal, 8))
            index += 3
            continue
        return None, ""
    return None, ""


def _strip_diff_prefix(name: str) -> str | None:
    candidate = name.strip()
    if not candidate or candidate == "/dev/null":
        return None
    if candidate.startswith(("a/", "b/")):
        candidate = candidate[2:]
    return candidate or None


def _forbidden_hits(paths: tuple[str, ...], forbidden_prefixes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        path
        for path in paths
        if any(_path_matches_prefix(path, forbidden_prefix) for forbidden_prefix in forbidden_prefixes)
    )


def _matches_any_allowed_path(path: str, allowed_paths: tuple[str, ...]) -> bool:
    if WHOLE_REPOSITORY_SCOPE in allowed_paths:
        return True
    return any(_path_matches_prefix(path, allowed_path) for allowed_path in allowed_paths)


def _path_matches_prefix(path: str, prefix: str) -> bool:
    normalized_path = _normalize_repo_path(path)
    normalized_prefix = _normalize_repo_path(prefix)
    return normalized_path == normalized_prefix or normalized_path.startswith(normalized_prefix + "/")


def _normalize_repo_path(path: str) -> str:
    return str(PurePosixPath(path.replace("\\", "/"))).strip("/")


DOCUMENTATION_SUFFIXES = (".md", ".markdown", ".rst", ".adoc")
DOCUMENTATION_DIRECTORIES = ("docs", "doc", "documentation")
DOCUMENTATION_STEMS = ("readme", "changelog", "release_notes", "release-notes")


def documentation_paths(changed_files: tuple[str, ...]) -> tuple[str, ...]:
    """The documentation files a patch actually touches."""
    return tuple(path for path in changed_files if _is_documentation_path(path))


def _is_documentation_path(path: str) -> bool:
    parsed = PurePosixPath(_normalize_repo_path(path))
    if not parsed.name:
        return False
    if parsed.parts and parsed.parts[0].casefold() in DOCUMENTATION_DIRECTORIES:
        return True
    if parsed.stem.casefold() in DOCUMENTATION_STEMS:
        return True
    return parsed.suffix.casefold() in DOCUMENTATION_SUFFIXES


def _is_unsafe_repo_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    parsed = PurePosixPath(normalized)
    return parsed.is_absolute() or ".." in parsed.parts
