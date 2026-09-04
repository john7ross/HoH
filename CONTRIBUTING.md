# Contributing

**English** · [Русский](CONTRIBUTING.ru.md)

## Getting the project to run

A checkout is source only and needs nothing built. HoH is written against the
Python standard library alone — [`requirements.lock`](requirements.lock) is
deliberately empty — so any Python 3.11 or newer with `tkinter` will do.

```bash
python -m venv .venv          # optional; there is nothing to install into it
PYTHONPATH=src python -m llm_harness doctor --project-root .
PYTHONPATH=src python -m llm_harness gui
```

On Windows and POSIX the launchers do the same thing without the environment
variable:

```bash
scripts/hoh.sh doctor --project-root .
scripts/hoh-gui.sh
```

`doctor` is the first command to run and the first one to read. It reports what
is configured, what is missing, and prints one concrete next step per finding. A
fresh checkout is expected to report warnings — the default Supervisor and
Verifier are offline stand-ins, and the default Worker is a test driver.

The installed packages carry their own interpreter under `runtime/python`; a
checkout has no such directory and the launchers fall back to the system
`python3`. `HOH_PYTHON` overrides both.

## Tests

One suite, standard library only, no plugins:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

It runs in about a minute on Linux and three on Windows. Everything else the
release gates run is also a plain command:

```bash
PYTHONPATH=src python -m compileall -q src tests
PYTHONPATH=src python -m llm_harness protocol-conformance --distribution-root . --json
PYTHONPATH=src python -m llm_harness audit --project-root . \
  --check "python -m unittest discover -s tests" \
  --check "python -m compileall -q src tests" \
  --markdown
```

The audit is the gate a release has to pass. It refuses a dirty worktree, blocks
on unresolved markers, and runs the verification commands you give it. Those
commands go through the same policy as a planning model's: **no shell**. A check
written as `set X=Y&& python -m unittest` is refused, because without a shell it
would silently run as one dead argument to a program called `set`.

### What a good test looks like here

- **Assert behaviour, not source text.** A test that greps a script for a
  substring proves nothing about whether the command in it can run. That exact
  test passed for months while the Windows release gate was unbuildable.
- **Prove the test can fail.** After writing a test for a fix, put the bug back
  and watch it fail. A test that passes against the broken code is not a test.
  This is not optional here — several fixes in this project are pinned by tests
  whose only evidence of working is that mutation check.
- **Make it independent of the machine.** A test that asks the real `PATH` for an
  executable passes or fails depending on what happens to be installed. Inject a
  resolver instead; `tests/test_doctor.py` shows the pattern.
- **A flake is a defect.** Find the nondeterminism and pin it.
- **Never weaken an assertion to make it pass.** Decide first whether the bug is
  in the product or in the test, and say which.

## Style

Match the file you are editing. Comments explain *why*, not *what* — most of the
odd-looking code here is a fix for something that actually broke, and the comment
is the only record of what it was. Two examples worth reading before you
"simplify" anything: why `attempts.py` reads git in binary, and why
`gui_widgets.py` lets a card's width flow one way only.

## Architecture rules that are not negotiable

These are enforced by tests and by the review protocol, not by convention:

- **Only the Supervisor touches git.** A Worker produces a patch and never
  commits, merges or pushes. A Critic inspects evidence and decides; it does not
  write to the repository either.
- **The patch gate is deterministic and fails closed.** Every path a patch
  touches is checked against `allowed_paths` before anything is applied. A patch
  the parser cannot fully understand is rejected, not waved through.
- **Every attempt runs in an isolated worktree.** The canonical checkout is never
  the workspace of an agent.
- **The ledger is append-only and crash-safe.** Any operation that changes state
  is recoverable after a kill at any point; `crash_point` markers exist so the
  tests can prove it.

## Before opening a PR

1. The full suite is green, and `audit` reports zero findings.
2. A defect is fixed everywhere its class applies, not only where it was seen.
   If a bug came from reading a subprocess in text mode, check the other places
   that read subprocesses.
3. User-visible strings exist in both Russian and English. `validate_translations`
   fails the GUI if a key is missing on either side.
4. Documentation that mentions what you changed is updated in **both** languages,
   and each language's links point at that language's files.
5. If you changed anything that ships, say so. The three packages and their
   checksums are rebuilt after the last edit, not before it — and the build now
   refuses to run from a dirty checkout for exactly this reason.

## Reporting bugs

Open an issue at <https://github.com/john7ross/HoH/issues> with what you did,
what happened and what you expected. Include your platform, whether you were in
the GUI or the CLI, and the output of `doctor` — it is designed to be pasted into
a report and contains no secrets.

**For anything security-related, do not open an issue** — see
[SECURITY.md](SECURITY.md), which also lists what must never be attached.
