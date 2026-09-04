# Embedded Runtime

[Русский](runtime.ru.md) · **English**

## Requirement

Production runs must not depend on the user's system Python. The harness must ship or be installed
with an embedded Python runtime and vendored dependencies.

## Layout

```text
runtime/
  python/
    python.exe
  site-packages/
vendor/
  wheels/
schemas/
  protocol-envelope-v1.schema.json
  review-bundle-v1.schema.json
  verifier-decision-v1.schema.json
  critic-review-bundle-v2.schema.json
  critic-decision-v2.schema.json
  project-spec-v2.schema.json
  role-profile-v1.schema.json
  role-profile-v2.schema.json
  driver-manifest-v1.schema.json
  journal-event-v1.schema.json
  journal-ingress-v1.schema.json
examples/
  protocol/
```

The expected Windows interpreter path is `runtime/python/python.exe`.

## Dependency policy

- Runtime dependencies are pinned in the tracked `requirements.lock` file.
- Pinned dependencies are stored as wheels under `vendor/wheels`.
- Installation must use `--no-index --find-links vendor/wheels`.
- No runtime dependency may be resolved from the internet during normal execution.
- System Python may be used for development/build tests and by the Debian package dependency model;
  Windows and macOS application users receive a bundled runtime.
- The current runtime has no third-party package dependencies; `requirements.lock` records that
  decision explicitly and must be updated before adding runtime dependencies to `pyproject.toml`.

## Validation

Use:

```powershell
python -m llm_harness runtime
```

Validation requires:

- configured embedded `python.exe` exists
- configured embedded `python.exe --version` starts successfully
- `vendor/wheels` exists
- `vendor/wheels` contains at least one `.whl` file

Runtime validation passes after `scripts/bootstrap-runtime.ps1` has copied a prepared Python
runtime and `scripts/build-wheelhouse.ps1` or `scripts/build-update-payload.ps1` has populated
`vendor/wheels`.

## Bootstrap

Use a prepared Python directory as the source:

```powershell
.\scripts\bootstrap-runtime.ps1 -PythonSource "C:\path\to\python" -BuildWheelhouse
```

The source directory must contain `python.exe`. The script copies that runtime into
`runtime/python`, creates `vendor/wheels`, verifies the embedded interpreter starts, and optionally
builds the wheelhouse.

Full local bootstrap from a prepared Python directory:

```powershell
.\scripts\bootstrap-runtime.ps1 `
  -PythonSource "C:\path\to\python" `
  -BuildWheelhouse `
  -InstallOffline
```

To build or refresh only the wheelhouse:

```powershell
.\scripts\build-wheelhouse.ps1
```

Normal packaged installation must install from the wheelhouse only:

```powershell
.\runtime\python\python.exe -m pip install --no-index --find-links .\vendor\wheels llm-harness
```

The provided offline installer uses this policy and installs into `runtime/site-packages`:

```powershell
.\scripts\install-offline.ps1
```

During source development, run HoH through the repository launcher:

```powershell
.\scripts\hoh.ps1 init-project
.\scripts\hoh.ps1 doctor
.\scripts\hoh.ps1 scan
```

## Native application packages

After runtime bootstrap and offline installation, create the Windows installer:

```powershell
.\scripts\package-windows-installer.ps1
```

The packager refuses to create Setup unless:

- `runtime/python/python.exe` exists
- `runtime/site-packages` exists
- `vendor/wheels` contains `.whl` files
- `scripts/hoh.ps1` exists
- `doctor` passes, including configured worker executable preflight
- `audit` passes with `doctor` as a verification command

The Windows packager first rebuilds the wheelhouse from the current source tree and refreshes
`runtime/site-packages` through `scripts/install-offline.ps1`. This prevents stale installed code
from being packaged after source changes. The internal payload builder retains `-SkipRefresh` only
for local diagnostic work.

The packager writes `dist/HoH-Setup-<version>.manifest.json`. This sidecar binds the Setup SHA-256
and size to the exact clean source commit. The internal update payload keeps its own detailed
runtime, dependency-lock, wheel, Doctor, and audit manifest.

Keep Setup and its manifest together. Verify them without importing HoH:

```powershell
$manifest = Get-Content .\dist\HoH-Setup-1.0.0.manifest.json -Raw | ConvertFrom-Json
$actual = (Get-FileHash .\dist\HoH-Setup-1.0.0.exe -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $manifest.package.sha256) { throw "Setup digest mismatch" }
```

Windows x64 uses the embedded runtime and `package-windows-installer.ps1`. Ubuntu/Debian uses
`package-deb.sh`; the package carries its own CPython and Tk under `/opt/hoh/runtime`, so `git` is
its only dependency. macOS uses `package-macos.sh`; PyInstaller bundles Python/Tk into `HoH.app`
and `hdiutil` creates the DMG. In that bundle `sys.executable` is the application itself, so it
also reads `-m` and `-c` the way an interpreter does: the GUI runs each operation as
`-m llm_harness <subcommand>`, and without that the buttons opened a second copy of the app. For the
same reason the verification-command allow-list accepts the program HoH is itself running as, which
is `python3` in a source checkout and the application inside the bundle.
All builders run tests, compile, and Doctor from a clean checkout. A build on one OS does not certify
another; each installer requires target-machine install, GUI, headless, update, and uninstall smoke.

Native packages include the application, required schemas, safe examples, scheduler helpers, and
configuration template. Raw Telegram tokens are not stored in the example configuration.

OpenAI and DeepSeek model access also uses only the Python standard library HTTPS client, so the
runtime does not add provider SDK wheels. Credentials are read at invocation time from
`OPENAI_API_KEY` / `DEEPSEEK_API_KEY`, or the explicitly configured `api_key_env`. `doctor` checks
configuration and environment presence without network access. `model-smoke` remains offline
unless the operator adds `--live`.

Claude Code and OpenClaw worker integrations add no Python packages. They invoke operator-installed
CLI executables through the standard library process boundary. `worker-smoke` performs a local
`--version` probe by default; only `--live` starts a model-backed turn. The application
does not bundle either external CLI, its credentials, or its persistent state.

Generic CLI workers do not need additional Python dependencies inside HoH. Configure
`[worker] driver = "command"` with the worker executable and arguments; HoH runs it from an isolated
attempt worktree, passes task data through stdin and `HOH_*` environment variables, then collects
the diff.

CLIs that require a prompt-file flag or mandatory non-interactive/safety arguments use
`[worker] driver = "process"` plus `worker.process_profile`, or select a catalog agent whose
`worker_process_profile` declares that contract. Aider is present in the default catalog with a
file-prompt profile and enforced `--no-auto-commits` / `--no-dirty-commits` arguments. It remains an
operator-installed external executable and uses provider keys from the process environment.

The attempt manager verifies that Git `HEAD` did not move before collecting any Worker diff. This is
independent of the selected adapter: a CLI that commits despite its declared flags fails closed and
its disposable attempt is removed without changing canonical history.

Before using a live worker for daily work, run the adapter conformance suite from the portable
launcher:

```powershell
.\scripts\hoh.ps1 worker-conformance --config .\harness.toml --timeout 300
```

The suite creates a disposable git repository, runs the configured worker through the same
supervisor/verifier path as `worker-run`, checks the worker capability manifest, verifies the
worker-produced artifact, commits through the supervisor, and confirms the canonical worktree is
clean. Use `--keep` only when you need the temporary repository path for debugging.

For Hermes, first run the cheaper ACP availability check:

```powershell
.\scripts\hoh.ps1 hermes-check
.\scripts\hoh.ps1 worker-conformance --config .\harness.toml --timeout 300
```

On native Windows, HoH launches the ACP child with `CREATE_BREAKAWAY_FROM_JOB` because Hermes
v0.20.0 can otherwise deadlock while probing Git Bash from a nested desktop-process job. A scoped
child bootstrap short-circuits only the exact upstream health-probe command; normal commands and
ACP permission requests are unchanged. HoH also sets `HERMES_ACP_SKIP_CONFIGURED_MCP=1`; only MCP
servers explicitly supplied by the isolated ACP session are available to the Worker.

To verify the full supervisor-facing daily workflow without external secrets:

```powershell
.\scripts\hoh.ps1 workflow-smoke
.\scripts\hoh.ps1 workflow-smoke --mode blocker
```

The first command proves the disposable ready path from project spec to final audit handoff. The
second command proves the blocker path and stub notification contract.

To verify the external supervisor/verifier boundary from source or a freshly extracted package:

```powershell
.\scripts\hoh.ps1 protocol-conformance --distribution-root . --json
```

This command validates the bundled JSON assets and runs an offline disposable flow through a
supervisor-owned commit, review bundle export, pending gate, external approval import, readiness
gate, and clean canonical git check. It uses no provider API, network access, or secrets.

To verify the strict three-role state machine from source or a freshly extracted package:

```powershell
.\scripts\hoh.ps1 three-head-conformance --json
```

This offline smoke covers critic approval, rejection with an immutable correction brief,
Supervisor-confirmed second attempt, final approval, escalation, Critic-disabled closure, and clean
canonical git state.

Project runtime selection is generated, not hand-edited. `project-plan --write` reads role choices
from project-spec v2 and creates `.hoh/role-profile.json`. The portable launcher automatically merges
that profile with the installed agent catalog for `doctor`, worker/queue execution, and review
commands. The customer only answers the briefing questions.

The interaction transcript is stored as redacted JSONL under the external HoH state root, not in
the portable application ZIP or canonical project history. Export it explicitly with
`journal-export` when a customer or auditor needs a Markdown or JSONL copy.

Repository operation records live beside the execution lease at
`<repo-parent>/.hoh-leases/<repo-name>-<hash>/operations/<operation-id>.json`. They are deliberately
outside both the canonical repository and the portable ZIP. Every atomic rewrite carries a
SHA-256 record digest; malformed or altered records fail closed. Copy this directory together with
the matching state root when preserving incident evidence. Records contain patch and file-state
digests rather than raw patch text, and recovery payload text is redacted before durable writes.

## Windows Telegram polling task

For daily operator communication on Windows, register a bounded Scheduled Task around the portable
launcher:

```powershell
.\scripts\register-telegram-watch-task.ps1 `
  -Config .\harness.toml `
  -EveryMinutes 1 `
  -Iterations 1 `
  -RunNow
```

The task runs `scripts\hoh.ps1 telegram-watch` through the embedded runtime. Scheduled runs require
`-Iterations` to be at least `1` so a repeated schedule does not start unbounded long-running
processes. Use a manual session for continuous polling:

```powershell
.\scripts\hoh.ps1 telegram-watch --config .\harness.toml --iterations 0
```

Remove the scheduled task with:

```powershell
.\scripts\register-telegram-watch-task.ps1 -Unregister
```

Telegram tokens and IDs remain outside project files. The task runs in the current interactive user
context and reads the environment variables named by `harness.toml`.

## Daily operations wrapper

Use `scripts/run-daily-ops.ps1` to run the production queue loop through the portable launcher:

```powershell
.\scripts\run-daily-ops.ps1 `
  -Config .\harness.toml `
  -TimeoutSeconds 300 `
  -StaleMinutes 60
```

The wrapper runs `doctor` first and stops before worker execution if the environment is not ready.
When `-FinalCheck` values are provided, the wrapper enables `queue-run-loop --final-audit` and
passes each check through to the audit gate. See `docs/daily-ops.md` for the full operator runbook.

For development only, the launcher can use `src/` when offline installation has not happened:

```powershell
.\scripts\hoh.ps1 -Python python -AllowSourceFallback doctor
```
