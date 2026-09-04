from __future__ import annotations

import locale
from pathlib import Path
import subprocess

from .command_policy import CommandPolicy, CommandPolicyError
from .domain import CommandResult


class GitError(RuntimeError):
    pass


def decode_process_output(raw: bytes | str | None) -> str:
    """Child processes do not agree on an encoding, so try UTF-8 and fall back to the OS default.

    Decoding a Cyrillic diff as the Windows ANSI code page loses it entirely, and decoding
    ANSI stderr as UTF-8 turns it into replacement characters. Trying both keeps either usable.
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(locale.getpreferredencoding(False), errors="replace")


def ensure_git_repository(repository: Path) -> None:
    result = _run(["git", "rev-parse", "--is-inside-work-tree"], repository)
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise GitError(f"Not a git repository: {repository}")


def repository_is_clean(repository: Path) -> bool:
    result = _run(["git", "status", "--porcelain"], repository)
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or "git status failed")
    return not result.stdout.strip()


def resolve_commit(repository: Path, revision: str) -> str:
    result = _run(["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], repository)
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or f"Unknown git commit: {revision}")
    return result.stdout.strip()


def current_head(repository: Path) -> str:
    return resolve_commit(repository, "HEAD")


def find_commit_by_trailer(repository: Path, trailer: str, value: str) -> str | None:
    result = _run(
        ["git", "log", "--all", "--format=%H%x00%B%x00", f"--grep=^{trailer}: {value}$"],
        repository,
    )
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or "git log trailer lookup failed")
    parts = [part for part in result.stdout.split("\0") if part.strip()]
    matches = [parts[index].strip() for index in range(0, len(parts), 2) if index < len(parts)]
    if len(matches) > 1:
        raise GitError(f"Multiple commits reference {trailer}: {value}")
    return matches[0] if matches else None


def commit_is_ancestor(repository: Path, ancestor: str, descendant: str = "HEAD") -> bool:
    result = _run(["git", "merge-base", "--is-ancestor", ancestor, descendant], repository)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise GitError(result.stderr.strip() or "git merge-base --is-ancestor failed")


def commit_parents(repository: Path, commit: str) -> tuple[str, ...]:
    result = _run(["git", "show", "-s", "--format=%P", commit], repository)
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or "git show parents failed")
    return tuple(result.stdout.strip().split())


def commit_changed_files(repository: Path, commit: str) -> tuple[str, ...]:
    result = _run(
        ["git", "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit],
        repository,
    )
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or "git diff-tree failed")
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def revert_commit_no_commit(repository: Path, commit: str) -> None:
    result = _run(["git", "revert", "--no-commit", commit], repository)
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git revert --no-commit failed for {commit}")


def apply_patch(repository: Path, patch: str) -> None:
    check = _run(["git", "apply", "--check"], repository, input_text=patch)
    if check.returncode != 0:
        raise GitError(check.stderr.strip() or "git apply --check failed")
    applied = _run(["git", "apply"], repository, input_text=patch)
    if applied.returncode != 0:
        raise GitError(applied.stderr.strip() or "git apply failed")


def stage_paths(repository: Path, paths: tuple[str, ...]) -> None:
    if not paths:
        return
    result = _run(["git", "add", "--", *paths], repository)
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or "git add paths failed")


def restore_paths_to_head(repository: Path, paths: tuple[str, ...]) -> None:
    if not paths:
        return
    result = _run(["git", "restore", "--staged", "--worktree", "--", *paths], repository)
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or "git restore paths failed")


def commit_staged(repository: Path, message: str) -> str | None:
    diff = _run(["git", "diff", "--cached", "--quiet"], repository)
    if diff.returncode == 0:
        return None
    committed = _run(["git", "commit", "-m", message], repository)
    if committed.returncode != 0:
        raise GitError(committed.stderr.strip() or "git commit failed")
    rev = _run(["git", "rev-parse", "--short", "HEAD"], repository)
    if rev.returncode != 0:
        raise GitError(rev.stderr.strip() or "git rev-parse failed")
    return rev.stdout.strip()


def run_verification_commands(
    repository: Path,
    commands: tuple[str, ...],
    timeout_seconds: float = 120.0,
    policy: CommandPolicy | None = None,
) -> tuple[CommandResult, ...]:
    active_policy = policy or CommandPolicy()
    results: list[CommandResult] = []
    for command in commands:
        try:
            argv = active_policy.resolve(command)
        except CommandPolicyError as exc:
            results.append(
                CommandResult(command=command, return_code=126, stdout="", stderr=str(exc))
            )
            continue
        try:
            completed = subprocess.run(
                argv,
                cwd=repository,
                env=active_policy.child_environment(),
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
            results.append(
                CommandResult(
                    command=command,
                    return_code=completed.returncode,
                    stdout=decode_process_output(completed.stdout),
                    stderr=decode_process_output(completed.stderr),
                )
            )
        except subprocess.TimeoutExpired as exc:
            results.append(
                CommandResult(
                    command=command,
                    return_code=124,
                    stdout=decode_process_output(exc.stdout),
                    stderr=f"Verification command timed out after {timeout_seconds} seconds.",
                )
            )
        except OSError as exc:
            results.append(
                CommandResult(
                    command=command,
                    return_code=127,
                    stdout="",
                    stderr=f"Verification command could not be started: {exc}",
                )
            )
    return tuple(results)


def revert_patch(repository: Path, patch: str) -> None:
    reversed_patch = _run(["git", "apply", "-R"], repository, input_text=patch)
    if reversed_patch.returncode != 0:
        raise GitError(reversed_patch.stderr.strip() or "git apply -R failed")


def _run(args: list[str], repository: Path, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        cwd=repository,
        input=input_text.encode("utf-8") if input_text is not None else None,
        capture_output=True,
        check=False,
    )
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        decode_process_output(completed.stdout),
        decode_process_output(completed.stderr),
    )
