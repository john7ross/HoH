from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path, PurePath
import re
import shlex
import sys
from typing import Mapping


SECRET_ENV_RE = re.compile(
    r"(?i)(TOKEN|SECRET|PASSWORD|PASSWD|API[_-]?KEY|CREDENTIAL|PRIVATE[_-]?KEY|SESSION|COOKIE)"
)

# Test and build runners a project realistically names in verification_commands.
# Shells are deliberately absent: allowing `sh -c` would restore exactly the hole
# this policy closes.
DEFAULT_ALLOWED_COMMANDS: tuple[str, ...] = (
    "cargo",
    "composer",
    "ctest",
    "dotnet",
    "flake8",
    "go",
    "gradle",
    "gradlew",
    "java",
    "make",
    "mvn",
    "mypy",
    "node",
    "npm",
    "npx",
    "phpunit",
    "pnpm",
    "poetry",
    "py",
    "pytest",
    "python",
    "python3",
    "rake",
    "ruff",
    "ruby",
    "tox",
    "yarn",
)

# Unquoted shell control operators. Without a shell they would silently become extra
# arguments to the first program, so the command would "pass" while doing half the work.
SHELL_OPERATORS = frozenset({"&&", "||", "|", ";", "&", ">", ">>", "<", "<<", "2>", "2>&1"})

# The characters those operators are built from. Matching on characters rather than on
# whole words is what catches "src&&" -- see _shell_operators.
_CONTROL_PUNCTUATION = frozenset("&|;<>()")

BLOCKED_EXECUTABLES = frozenset(
    {
        "ash",
        "bash",
        "busybox",
        "cmd",
        "cmd.exe",
        "csh",
        "dash",
        "env",
        "fish",
        "ksh",
        "powershell",
        "powershell.exe",
        "pwsh",
        "sh",
        "start",
        "tcsh",
        "zsh",
    }
)


class CommandPolicyError(ValueError):
    """A verification command was refused before anything was executed."""


@dataclass(frozen=True)
class CommandPolicy:
    """What a task's verification commands are allowed to run, and with which environment."""

    allowed_commands: tuple[str, ...] = DEFAULT_ALLOWED_COMMANDS
    allow_unlisted_commands: bool = False
    inherit_secret_environment: bool = False

    def resolve(self, command: str) -> tuple[str, ...]:
        """Turn one verification command into argv, or refuse it."""
        if not command.strip():
            raise CommandPolicyError("Verification command is empty.")
        try:
            argv = tuple(shlex.split(command, posix=os.name != "nt"))
        except ValueError as exc:
            raise CommandPolicyError(f"Verification command cannot be parsed: {exc}") from exc
        if not argv:
            raise CommandPolicyError("Verification command is empty.")
        if os.name == "nt":
            argv = tuple(part.strip('"') for part in argv)
        operators = _shell_operators(command)
        if operators:
            raise CommandPolicyError(
                f"Verification command uses shell operators ({', '.join(sorted(set(operators)))}), "
                "but HoH runs verification commands without a shell. "
                "Split it into separate verification commands."
            )
        executable = _executable_name(argv[0])
        if executable in BLOCKED_EXECUTABLES:
            raise CommandPolicyError(
                f"Verification command '{executable}' would start a shell, which HoH never runs "
                "on behalf of a planning model. Name the test runner directly instead."
            )
        if self.allow_unlisted_commands or _is_running_interpreter(argv[0]):
            return argv
        if executable not in {name.casefold() for name in self.allowed_commands}:
            raise CommandPolicyError(
                f"Verification command '{executable}' is not in the allowed command list. "
                "Add it to [verification] allowed_commands, or set allow_unlisted_commands "
                "if you accept running arbitrary planner-provided programs."
            )
        return argv

    def child_environment(self, environment: Mapping[str, str] | None = None) -> dict[str, str]:
        """The environment a verification command sees; provider credentials are removed by default."""
        source = dict(os.environ if environment is None else environment)
        if self.inherit_secret_environment:
            return source
        return {name: value for name, value in source.items() if not SECRET_ENV_RE.search(name)}


@dataclass(frozen=True)
class OperatorCommandPolicy(CommandPolicy):
    """Commands the operator typed themselves; still no shell, but no allow-list."""

    allow_unlisted_commands: bool = True
    inherit_secret_environment: bool = True
    allowed_commands: tuple[str, ...] = field(default=())


def _is_running_interpreter(program: str) -> bool:
    """True when the command names the very program HoH is running as.

    In the macOS bundle sys.executable is HoH.app/Contents/MacOS/HoH, so the
    product's own fixtures -- which verify their work by running the interpreter --
    named a program no allow-list contains, and protocol-conformance could not pass
    from an installed application. This is narrow on purpose: the comparison is
    against the resolved path, so another program merely named HoH still has to be
    listed, and it grants nothing new, because "python" is already allowed where
    sys.executable is a plain interpreter.
    """
    try:
        return Path(program).resolve() == Path(sys.executable).resolve()
    except OSError:
        return False


def _shell_operators(command: str) -> tuple[str, ...]:
    """Find the unquoted shell control operators in a command.

    Comparing whole argv words only sees an operator when spaces surround it, so
    "set PYTHONPATH=src&& python -m unittest" passed the check and then ran as one
    meaningless argument to "set" -- the exact silent half-run this refusal exists to
    prevent. This lexer splits punctuation off unquoted words and leaves quoted text
    alone, so a query string like "http://host?a=1&b=2" stays one argument.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return ()
    return tuple(token for token in tokens if token and set(token) <= _CONTROL_PUNCTUATION)


def _executable_name(value: str) -> str:
    name = PurePath(value.replace("\\", "/")).name.casefold()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name
