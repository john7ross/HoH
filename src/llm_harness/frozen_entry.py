"""Argument handling for the frozen macOS application bundle.

Inside the bundle ``sys.executable`` is ``HoH.app/Contents/MacOS/HoH``, and the
rest of the product treats ``sys.executable`` as the interpreter that runs its
own code: every GUI operation shells out to ``-m llm_harness <subcommand>``, the
background scheduler runs the queue loop the same way, and the conformance
fixtures verify their work with ``-c <python>``. The bundle understood none of
those forms, so on macOS a GUI operation opened a second copy of the application
and ``protocol-conformance`` could not pass from an installed app.

The bundle embeds CPython, so it can honour both forms itself. ``-c`` runs the
code it is given exactly as ``python -c`` does; the binary is ad-hoc signed and
carries no trust of its own, so this hands a local caller nothing they did not
already have by running the interpreter that ships beside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class FrozenInvocation:
    """What the bundle should do with the arguments it was started with."""

    python_code: str | None
    module: str | None
    cli_args: tuple[str, ...]


def plan_frozen_invocation(argv: Sequence[str]) -> FrozenInvocation:
    """Read interpreter-style arguments the way CPython would.

    Double-clicking the application passes no arguments and opens the GUI, which
    is why an empty argv is not an error.
    """
    arguments = tuple(argv)
    if not arguments:
        return FrozenInvocation(None, None, ("gui",))
    if arguments[0] == "-c":
        if len(arguments) < 2:
            raise ValueError("Argument expected for the -c option")
        return FrozenInvocation(arguments[1], None, ())
    if arguments[0] == "-m":
        if len(arguments) < 2:
            raise ValueError("Argument expected for the -m option")
        # The product's own module goes straight to the CLI already loaded here.
        # Anything else is run as a module, which reports honestly when the bundle
        # does not carry it instead of claiming the option is unsupported.
        if arguments[1] == "llm_harness":
            return FrozenInvocation(None, None, arguments[2:])
        return FrozenInvocation(None, arguments[1], arguments[2:])
    return FrozenInvocation(None, None, arguments)
