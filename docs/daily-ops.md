# Daily operations

[Русский](daily-ops.ru.md) · **English**

This runbook describes daily use of the installed HoH application on Windows, Linux, and macOS.

## Files

- `config.example.toml` - safe example config without raw secrets.
- `harness.toml` - local operator config copied from `config.example.toml`.
- `scripts/hoh.ps1` - embedded-runtime launcher used by Windows Setup and engineering checks.
- `scripts/hoh.sh` / `scripts/hoh-gui.sh` - launchers embedded in Linux packages and macOS resources.
- `scripts/hoh-env.template.ps1` - environment variable template with redacted sample values.
- `scripts/register-telegram-watch-task.ps1` - optional Windows Scheduled Task registration for
  inbound Telegram commands.
- `scripts/register-workspace-scheduler-task.ps1` - least-privilege Windows Scheduled Task that
  runs every due registered project without overlapping an active project execution lock.
- `scripts/register-workspace-scheduler.sh` - equivalent systemd user timer or macOS LaunchAgent.
- `scripts/run-daily-ops.ps1` - doctor-gated queue worker loop for daily execution.

## First-time setup

```powershell
.\scripts\hoh.ps1 init-project
```

Edit `harness.toml` for the chosen worker adapter. Local model endpoints are opt-in; leave the
example `[[local_models]]` section commented unless the endpoint is running. Edit
`hoh-env.local.ps1` with real environment values and keep it out of git.

`init-project` refuses to overwrite existing local files. Use `--force` only when intentionally
refreshing local config from templates.

Load the environment for the current shell:

```powershell
. .\hoh-env.local.ps1
```

## Desktop setup and Supervisor conversation

Launch **HoH** from the operating-system application menu. From a source checkout, the equivalent
engineering command is:

```powershell
.\scripts\hoh-gui.ps1
```

Use the pages in this order:

1. **Roles** - select an available Supervisor and Worker, optionally enable an independent
   Critic, then save the three-role profile. Agents whose executable or launcher cannot be found
   are not offered. A stale or manually edited unavailable selection is rejected again before the
   profile is written.
2. **Project settings** - configure the Supervisor and verifier model endpoints, runtime paths,
   test requirement, Worker trust level, scheduler, and safe one-shot Worker CLI profiles. Every
   field includes an explanation. The environment check reports only variable names and whether
   they are defined. A missing API key may be pasted into the masked session field; it stays only
   in memory until this GUI window closes and is never written to disk or output.
3. **Agents and connections** - install or update a Registry ACP agent into the current user's
   `%LOCALAPPDATA%\HoH\agents` directory, inspect its advertised login methods, open the vendor
   login flow, or configure and test a remote A2A v1 Agent Card. Secret values may be entered for
   the current process only and are never written to project configuration.
4. **MCP policies** - review Supervisor, Worker, and Critic separately. Keep planning/review roles
   on `reject` unless they genuinely need a tool; for Worker, select only required ACP categories
   and add role-scoped stdio/HTTP/SSE servers. Enter secrets as `TARGET=SOURCE_ENV`, never as values.
5. **Supervisor chat** - describe the task and answer clarifying questions. HoH keeps the
   conversation in the private state directory outside the project Git repository. Creating a plan
   requires an explicit confirmation and then writes the normal brief, roadmap, task files, and
   queue entries through the existing Supervisor planning pipeline.
6. **Projects** - register several Git roots, inspect their shared read-only queue summary, configure
   fixed-interval background runs and Windows/Telegram summaries, then explicitly enable the
   user-level background runner.
7. **Metrics** - inspect invocation, token, cost, duration, and quality evidence; add optional exact
   provider/model rates when the adapter does not report cost.
8. **Operations** - run Doctor, inspect queue plus run/audit history, execute one task or the whole
   queue, retry a reviewed failed task, recover a confirmed interrupted task, and run the audit.

The exact storage paths are shown inside **Project settings**:

- project settings: `<project>/.hoh/harness.json`;
- role profile: `<project>/.hoh/role-profile.json`;
- Supervisor chat: `<project-parent>/.hoh-state/<project>-<hash>/supervisor-chat.json`;
- GUI language, theme, and last project: `%LOCALAPPDATA%\HoH\gui-settings.json` on Windows;
- secret values: only the environment of Windows or the process that launches HoH.

Daily project work does not require raw JSON or a terminal. Safe third-party installation, vendor
login, and remote A2A connection setup are available on **Agents and connections**. Release
packaging, exact rollback, reconciliation, manual review-file exchange, and journal export remain
guarded engineering/forensic CLI operations.

HoH installs an exact pinned `npx`/`uv` package in its own user-scope directory. Direct Registry
archives are accepted only over HTTPS with a published SHA-256 and bounded safe extraction; entries
without integrity metadata remain visibly blocked. Provider authentication can be completed through
ACP or a known vendor CLI, while model access, organization policy, and quota remain vendor-owned
checks. Any failed prerequisite blocks dispatch with a visible error.

## Multi-project workspace and background runs

The desktop **Projects** page is the normal control surface. Equivalent headless commands are:

```powershell
.\scripts\hoh.ps1 workspace register --project-root C:\work\first --json
.\scripts\hoh.ps1 workspace summary --json
.\scripts\hoh.ps1 workspace schedule --project-id <id> --enabled --interval-minutes 30 --notify-windows --json
.\scripts\hoh.ps1 workspace run-due --json
.\scripts\hoh.ps1 workspace service-install --interval-minutes 1 --json
.\scripts\hoh.ps1 workspace service-status --json
```

The registry is `%LOCALAPPDATA%\HoH\workspace-v1.json`; it stores project paths and schedule policy,
not tasks or credentials. The shared queue is calculated from every project's existing state root.
Each due run invokes `queue-run-loop`, so normal role preflight, state lease, Git execution lease,
Critic gate, audit, and notification behavior remain authoritative. Disable the OS runner with
`workspace service-uninstall`; removing a project from the registry never deletes its files or
queue evidence. Desktop delivery attempts are logged in
`%LOCALAPPDATA%\HoH\desktop-notifications.jsonl`.

On installed Linux use `hoh`; on macOS use `/Applications/HoH.app/Contents/MacOS/HoH` for headless
commands. State follows OS user-data conventions, desktop notifications use
`notify-send`/`osascript`, and the same `workspace service-*` commands install the native user
scheduler. Headless HTTP operation is documented in [`headless-server.md`](headless-server.md).

Inspect the evidence-qualified compatibility matrix:

```powershell
.\scripts\hoh.ps1 compatibility-matrix --project-root C:\path\to\repo --json
```

The matrix answers one question — what is on this machine:

- `available` - the launcher or direct endpoint is present and passes preflight;
- `unavailable` - a configured or Registry entry exists, but its launcher is not here.

Whether a role actually works is a separate question, and the only way to answer it is to run it:

```powershell
.\scripts\hoh.ps1 role-conformance `
  --project-root C:\path\to\repo `
  --role worker `
  --json
```

This runs the selected role once in a throwaway Git repository and reports every check it made.
Use `--agent`, `--model`, or `--agent-version` when you want the report to name an exact identity;
without them the identity is reported as `unknown` and the run still happens. Nothing is written to
disk: the answer describes this machine, this agent version and this account at this moment, and a
stored one would only go stale while continuing to look authoritative. Supervisor and Critic use the
same command and validate the structured plan and decision contracts plus a clean disposable
repository.

Validate the portable runtime and configured integrations:

```powershell
.\scripts\hoh.ps1 doctor --config .\harness.toml
```

## Worker preflight and live smoke

Named provider workers are read-only preflighted by default:

```powershell
.\scripts\hoh.ps1 worker-smoke --config .\harness.toml --json
```

This resolves the executable, checks its version, validates provider-owned safety settings, and
does not start an agent turn. Add `--live` explicitly to authorize one candidate change in a
disposable repository:

```powershell
.\scripts\hoh.ps1 worker-smoke --config .\harness.toml --live --timeout 300 --json
```

The live path verifies that:

- the worker adapter can be constructed;
- `[worker.capabilities]` matches actual isolation behavior;
- the worker can produce the expected repository artifact;
- supervisor-owned verification and commit succeed;
- the canonical conformance repository finishes clean.

For Hermes:

```powershell
.\scripts\hoh.ps1 hermes-check
.\scripts\hoh.ps1 worker-conformance --config .\harness.toml --timeout 300
```

If Hermes asks for a manual model switch or cannot start ACP, stop daily execution and fix the
Hermes configuration before running queued work.

For Claude Code, use `[worker] driver = "claude_code"`. HoH supplies the permission and tool flags;
do not add permission, tools, session, plugin, MCP, Chrome, worktree, or background flags to
`args`. The named adapter intentionally has no Bash tool and does not run tests itself.

For OpenClaw, use `[worker] driver = "openclaw"` and set an explicit `provider/model` value. HoH uses
`--local`, never delivers to a channel, and does not reuse the operator's broad workspace/tool
configuration. If `openclaw` is not installed, preflight reports `available=false` without trying
to install it or silently falling back to another Worker.

Codex or any other CLI can act as Worker through the generic `command` driver when its non-interactive
invocation consumes stdin and edits the current worktree. The integration remains operator-owned and
does not weaken the safety profile of bundled named drivers.

For Aider, select the default `aider` catalog entry after `aider --version` works in the same Windows
environment that launches HoH. Its declarative `process` profile sends the task with
`--message-file`, forces `--no-stream --yes --no-auto-commits --no-dirty-commits`, and optionally
passes the selected model through `--model`. Credentials must be environment variables, not CLI
arguments. Run `worker-smoke` first and an explicit live `worker-conformance` before assigning real
queued work.

For another one-shot coding CLI, create a profile on **Project settings**: specify its executable,
stdin/file prompt transport, optional prompt-file and model flags, and one required/forbidden/base
argument per line. Saving performs the same fail-closed validation as the config loader; a secret
flag such as `--api-key`, a conflicting prompt flag, or an unsafe forbidden argument is rejected.

Every Worker attempt now has an independent Git ownership check: if the external CLI creates a
commit and moves `HEAD`, HoH rejects the completion even if the process returned zero. The canonical
repository remains unchanged and the disposable attempt is removed.

## Telegram command polling

For a manual continuous Telegram polling session:

```powershell
.\scripts\hoh.ps1 telegram-watch --config .\harness.toml --iterations 0
```

For unattended daily use, register the bounded polling task:

```powershell
.\scripts\register-telegram-watch-task.ps1 `
  -Config .\harness.toml `
  -EveryMinutes 1 `
  -Iterations 1 `
  -RunNow
```

Remove it with:

```powershell
.\scripts\register-telegram-watch-task.ps1 -Unregister
```

## Queue execution loop

Run queued work through the configured worker:

```powershell
.\scripts\run-daily-ops.ps1 `
  -Config .\harness.toml `
  -TimeoutSeconds 300 `
  -StaleMinutes 60
```

The script runs `doctor` first unless `-SkipDoctor` is passed. It then runs `queue-run-loop` until
the queue is empty, `-MaxTasks` is reached, or a blocker/stale task stops execution.

Set the persistent worker-preparation limit in the config:

```toml
[scheduler]
max_parallel_tasks = 2
```

Configure bounded coordination waits separately from scheduling:

```toml
[coordination]
state_lock_timeout_seconds = 5
execution_lock_timeout_seconds = 5
poll_interval_seconds = 0.05
```

For a one-off operator run, call the launcher with `queue-run-loop --parallelism 2`. Only
dependency-ready tasks with non-overlapping `allowed_paths` share a batch. Missing `allowed_paths`
means exclusive execution. Canonical verification, commits, Critic review, and state writes remain
serialized. If a batch contains a failure, inspect every run in the returned `runs` array: HoH lets
the other already-started independent workers finish, records all outcomes, and starts no new batch.

An external supervisor that needs a parseable result should call the launcher directly with
`queue-run-loop --json`. The JSON mode returns one versioned document containing every processed
run and the terminal status (`empty`, `blocked`, `max_tasks_reached`, `review_blocked`,
`rollback_blocked`, `task_state_blocked`, `ready`, or `audit_failed`). All other `queue-*` and
`rollback-*` commands also accept `--json`.

## End-to-end workflow smoke

Before changing the daily runbook or sharing a new package, run the deterministic supervised
workflow smoke:

```powershell
.\scripts\hoh.ps1 workflow-smoke
.\scripts\hoh.ps1 workflow-smoke --mode blocker
```

`workflow-smoke` uses a disposable git repository, a supervisor-authored project spec, generated
brief and roadmap docs, queued task JSON files, a deterministic generic command worker, verifier
checks, supervisor-owned commits, final audit evidence, and a stub notifier. The `blocker` mode
intentionally fails the worker and verifies that blocker notification evidence is produced without
Telegram secrets.

## Multi-task scheduling

Project tasks may add two optional fields:

```json
{
  "depends_on": ["foundation-task"],
  "priority": 20
}
```

`project-plan` treats the task set as a directed acyclic graph. It rejects unknown task IDs,
duplicate dependency edges, and cycles before enqueueing anything. Standalone `queue-add` accepts
dependencies only when those task IDs are already present in queue history.

At each sequential queue step, HoH considers only tasks whose dependencies have reached `done`.
Among ready tasks, the highest integer priority runs first; equal priorities preserve enqueue order.
Use `queue-list --json`, `/queue`, or `supervisor-status --json` to inspect the selected task and the
ready/waiting counts. `dependency_blocked` means queued work exists but every candidate is waiting
on a non-`done` dependency; it does not mean the queue is empty.

## External verifier handoff

Validate the portable vendor-neutral protocol before connecting a supervisor or verifier agent:

```powershell
.\scripts\hoh.ps1 protocol-conformance --distribution-root . --json
```

After a queue run creates a supervisor-owned commit, obtain the run id and export its exact review
evidence:

```powershell
.\scripts\hoh.ps1 queue-history --project-root C:\path\to\repo --json
.\scripts\hoh.ps1 review-export `
  --project-root C:\path\to\repo `
  --run-id <run-id> `
  --output C:\path\to\handoff\review-bundle.json `
  --json
```

Give `review-bundle.json` to an independent agent or service together with
`schemas/verifier-decision-v1.schema.json`. The verifier must return only the documented decision
object. It has no commit or merge authority.

Import the returned evidence:

```powershell
.\scripts\hoh.ps1 review-import `
  --project-root C:\path\to\repo `
  --decision C:\path\to\handoff\verifier-decision.json `
  --json
```

An approved decision exits successfully. A valid rejection is recorded but exits non-zero so an
automation loop stops. A rejection never changes git by itself; the Supervisor must define
remediation work or explicitly invoke the production rollback policy. Inspect all evidence with:

```powershell
.\scripts\hoh.ps1 review-list --project-root C:\path\to\repo --json
.\scripts\hoh.ps1 supervisor-status --project-root C:\path\to\repo --json
```

## Production rollback

Always inspect the plan first:

```powershell
.\scripts\hoh.ps1 rollback-plan `
  --project-root C:\path\to\repo `
  --task-id task-001 `
  --json
```

Proceed only when `eligible=true`. Copy the returned full `target_commit` exactly and supply
checks that prove the desired post-rollback state:

```powershell
.\scripts\hoh.ps1 rollback-apply `
  --project-root C:\path\to\repo `
  --task-id task-001 `
  --expected-commit <full-target-commit> `
  --reason "Production regression" `
  --check "python -m unittest discover -s tests" `
  --json
```

HoH supports multiple CLI processes against the same state root. Short queue, journal, review,
audit, operator, and Telegram offset updates are serialized by the state lease. Canonical patch,
commit, and rollback actions are serialized independently by the repository execution lease.
Concurrent commands therefore either complete in order or stop after the configured bounded wait
with owner diagnostics; they never delete a live lock.

Inspect coordination before recovery:

```powershell
.\scripts\hoh.ps1 lock-status `
  --project-root C:\path\to\repo `
  --json
```

`available=false` means an OS-confirmed live owner; wait for it or investigate the reported PID,
host, command, action, and timestamp. `available=true` with `stale_owner=true` means the process
died without updating metadata. Recover only through the guarded command:

```powershell
.\scripts\hoh.ps1 lock-recover `
  --project-root C:\path\to\repo `
  --target execution `
  --json
```

Recovery first acquires the same kernel lock with zero wait. It cannot break a live owner and never
deletes the lock file. After an interrupted queue run, inspect the operation ledger before changing
queue state:

```powershell
.\scripts\hoh.ps1 reconcile-status --project-root C:\path\to\repo --json
```

Each non-terminal operation is classified as `not_started`, `patch_applied`,
`commit_exists_state_missing`, `state_complete`, `contradictory`, or `corrupted`. Queue startup
automatically completes only an exact idempotent StateStore write when commit trailer, parent,
changed-file scope, clean worktree, run/rollback identity, and durable verification payload all
match. It never invokes the Worker or Critic during reconciliation.

Use `reconcile-apply` only with the `safe_action` reported by status:

```powershell
.\scripts\hoh.ps1 reconcile-apply `
  --project-root C:\path\to\repo `
  --operation-id <operation-id> `
  --action complete-state `
  --json
```

`cancel-intent` is accepted only while HEAD and the worktree still equal the baseline.
`restore-patch` first proves exact expected post-image hashes and scope, restores only recorded
baseline paths, and verifies the baseline. The ledger stores the patch SHA-256 and per-file
baseline/post-image hashes, not raw patch text; recovery command output is redacted.
`finish-ledger` only closes an operation whose
StateStore evidence already matches. A live execution lease, unexpected HEAD, dirty path, changed
content, missing trailer, corrupted record digest, or contradictory state blocks mutation.

Do not use `queue-recover-running` or `/recover` while the task has unresolved operation evidence;
both commands reject that transition. Resolve the ledger first, then recover a truly unstarted
task if needed.

Crash injection is test-only. HoH recognizes `HOH_TEST_CRASH_POINT` only when
`HOH_ENABLE_TEST_CRASH_INJECTION=1`; production scripts must not set either variable.

If the command fails:

- inspect `rollback-history --json`
- confirm canonical `git status --porcelain` is clean
- resolve every reported blocker instead of bypassing it
- do not use `reset --hard`

On success, record the rollback commit in the incident/change record. The task becomes
`rolled_back`; final handoff remains blocked until a corrected lifecycle for the same task id reaches
`done`. Queued dependents stay blocked automatically. Completed dependents must be handled before the
rollback is permitted.

Once a bundle has been exported, pending or rejected review evidence makes `supervisor-status`
non-ready. The latest bundle for the work item must have a valid approval before handoff.

## Required three-head operation

The customer does not configure roles in TOML. At briefing time the Supervisor runs:

```powershell
.\scripts\hoh.ps1 role-catalog --project-root C:\path\to\repo --refresh-registry --json
```

Ask which agent should be Supervisor and Worker, whether Critic is wanted, optional model names,
and the attempt bound. Put the answers in project-spec v2 and run `project-plan --write --enqueue`.
HoH generates `.hoh/role-profile.json` and all execution commands load it automatically. The same
agent product may be assigned to multiple roles, but each role receives a distinct session identity.

Validate the complete local state machine before daily use:

```powershell
.\scripts\hoh.ps1 three-head-conformance --json
```

In required mode, HoH always exports the exact v2 bundle. With `[critic] driver = "manual"`,
`queue-run-loop` stops at `review_pending`; deliver the canonical bundle to the configured critic
and import its v2 decision:

```powershell
.\scripts\hoh.ps1 review-import --config .\harness.toml --decision .\critic-decision.json --json
```

With `[critic] driver = "claude_code"`, the queue automatically invokes Claude Code with tools and
session persistence disabled, imports the schema-constrained decision, and continues after an
approval. If Claude is unavailable, unauthenticated, times out, or returns invalid output, the task
stays `review_pending`. Retry only the review:

Any other read-only reviewer CLI can use `[critic] driver = "command_json"`. It receives the same
immutable review prompt on stdin and must return only the documented judgment JSON on stdout; HoH
adds hashes and attestations and performs the same import checks.

Any cached ACP Registry agent can instead use `[critic] driver = "acp"`; HoH starts it outside the
repository, rejects all tool permissions, selects the requested ACP model when present, and requires
the same judgment JSON. A DeepSeek Critic therefore needs both a harness ID (for example
`qwen-code`) and the exact DeepSeek model option exposed by that harness.

For a direct model-only Critic, configure `[verifier_model]` and use
`[critic] driver = "model_json"`, or select `direct-critic-model` in the GUI. This path sends only
the immutable review bundle to the structured model provider and needs no local Critic executable:

```toml
[verifier_model]
provider = "deepseek"
model = "deepseek-chat"
api_key_env = "DEEPSEEK_API_KEY"

[critic]
driver = "model_json"
command = ""
```

Doctor fails before queue execution when the credential variable or endpoint is invalid. Provider
authentication, quota, or schema failures leave the task at `review_pending`; retry with
`critic-run` without rerunning Worker.

```powershell
.\scripts\hoh.ps1 critic-run --config .\harness.toml --project-root C:\path\to\repo --json
```

This command reuses the immutable pending bundle and does not rerun the Worker.

On `reject`, inspect the immutable findings, then let the Supervisor confirm the recorded brief:

```powershell
.\scripts\hoh.ps1 review-rework --config .\harness.toml --decision-id <decision-id> --json
.\scripts\hoh.ps1 queue-run-loop --config .\harness.toml --project-root C:\path\to\repo --json
```

`review-rework` cannot replace the correction instructions or alter the original task scope. It
queues the next attempt only when the decision is the latest rejection and the bundle's attempt
limit has not been reached. `escalate` and exhausted attempts remain blocked and trigger the
configured Telegram notifier. Tokens and user/chat IDs stay in environment variables only.

If the customer disables Critic, the generated profile uses `mode=disabled`: a successful Worker
candidate becomes `done` only after Supervisor-owned deterministic policy, acceptance, and command
checks. No critic bundle is created. `three-head-conformance` tests both enabled and disabled routes.

## Interaction journal

Inspect the latest redacted events:

```powershell
.\scripts\hoh.ps1 journal-list --project-root C:\path\to\repo --limit 100
.\scripts\hoh.ps1 journal-list --project-root C:\path\to\repo --task-id task-001 --json
```

Export an audit-friendly transcript:

```powershell
.\scripts\hoh.ps1 journal-export `
  --project-root C:\path\to\repo `
  --output C:\path\to\handoff\interaction-journal.md `
  --format markdown `
  --json
```

The journal contains routed prompts/messages, available ACP or CLI tool telemetry, returned patch
evidence, verification commands and output, Git actions, critic decisions, notifications, and state
transitions. Redaction happens before append. Do not expect HoH to display private internal calls
from an opaque agent that does not expose them through its adapter protocol.

When a Supervisor or Critic message/tool call happens in an external UI, that agent writes the
closed event from `schemas/journal-ingress-v1.schema.json` and appends it with:

```powershell
.\scripts\hoh.ps1 journal-record `
  --project-root C:\path\to\repo `
  --event C:\path\to\journal-event.json `
  --json
```

This is how the briefing conversation becomes part of the same timeline even though the provider
UI itself is outside the portable HoH process.

## Usage, cost, duration, and quality metrics

Inspect aggregate evidence or the immutable underlying events:

```powershell
.\scripts\hoh.ps1 metrics-summary --project-root C:\path\to\repo --json
.\scripts\hoh.ps1 metrics-list --project-root C:\path\to\repo --limit 100 --json
```

HoH records adapter-reported input/output/total tokens, elapsed agent time, provider-reported cost,
and objective completion/review outcomes. Missing telemetry stays missing; HoH does not invent token
counts or cost. To calculate cost when the adapter reports tokens but no charge, add an exact rate
on the desktop **Metrics** page or configure `metrics.prices` in `.hoh/harness.json`. Provider cost
always wins over the configured calculation. The append-only event file is
`usage-metrics.jsonl` under the project state root, outside canonical Git.

## Model-provider operations

Keep API keys in process environment variables, never in `harness.toml`:

```powershell
$env:OPENAI_API_KEY = "<session-secret>"
$env:DEEPSEEK_API_KEY = "<session-secret>"
.\scripts\hoh.ps1 doctor --config .\harness.toml
.\scripts\hoh.ps1 model-smoke --config .\harness.toml --role supervisor
```

The last command is configuration-only. To authorize one small network request, add `--live`.
Generate a plan only after reviewing the credential target and selected model:

```powershell
.\scripts\hoh.ps1 project-plan `
  --config .\harness.toml `
  --project-root C:\path\to\repo `
  --requirements C:\path\to\requirements.md `
  --write
```

Review the generated brief, roadmap, and task files before adding `--enqueue`. Provider journal
events contain request identifiers, hashes, latency, attempts, and token counts, but not API keys
or prompt bodies. Stub roles preserve fully offline `--spec` planning and deterministic-only
verification.

## Final audit handoff

Configure the offline language analyzers in `harness.toml`:

```toml
[audit]
language_analyzers = ["python", "javascript", "typescript"]
entry_points = ["src/app.py", "src/index.ts"]
exclude_paths = ["vendor/**", "generated/**"]
fail_on_unavailable = true
```

Run a standalone audit with the same policy:

```powershell
.\scripts\hoh.ps1 audit --config .\harness.toml --check "python -m unittest discover -s tests" --markdown
```

Configured entry points make reachability strict for a language when at least one pattern matches
one of its source files. Review each `*_UNUSED_FILE`, unreachable-code, or unused-declaration
finding and either remove/fix it yourself or document an intentional exclusion. HoH does not delete
files. A missing language reports `unsupported`; a failed runner reports `unavailable` and blocks
handoff by default.

When the project has explicit verification commands, pass them as `-FinalCheck` values:

```powershell
.\scripts\run-daily-ops.ps1 `
  -Config .\harness.toml `
  -FinalCheck "set PYTHONPATH=src&& python -m unittest discover -s tests" `
  -FinalCheck "python -m compileall -q src tests"
```

When the queue becomes empty, HoH runs the final audit, stores Markdown evidence under the HoH state
root, and sends a Telegram readiness or audit-failure notification if Telegram is enabled. If any
explicitly exported latest review is pending or rejected, the handoff stops before audit with
`status=review_blocked` and sends a user-action-required notification instead.

## Operator commands

- `/status` - queue counters and stale count.
- `/queue` - current queue state.
- `/stale` - stale running tasks.
- `/retry <task-id>` - requeue a failed task after review.
- `/recover <task-id> [reason]` - requeue a confirmed interrupted running task.
- `/continue` - acknowledge continuation; run `queue-run-loop` to resume work.
- `/stop` - record stop decision without mutating the queue.

For a read-only supervisor snapshot that includes queue counters, stale task ids, latest run,
latest audit, latest operator event, and external review gates:

```powershell
.\scripts\hoh.ps1 supervisor-status --project-root C:\path\to\repo
.\scripts\hoh.ps1 supervisor-status --project-root C:\path\to\repo --json
```

## Verification checklist

Before sharing an installer:

```powershell
.\scripts\hoh.ps1 doctor --config .\harness.toml
.\scripts\hoh.ps1 worker-conformance --config .\harness.toml --timeout 300
.\scripts\hoh.ps1 workflow-smoke
.\scripts\hoh.ps1 workflow-smoke --mode blocker
.\scripts\hoh.ps1 protocol-conformance --distribution-root . --json
.\scripts\hoh.ps1 audit --check "powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\hoh.ps1 doctor --config .\harness.toml" --markdown
.\scripts\package-windows-installer.ps1
```

The Windows package command refreshes the wheelhouse/offline install and refuses to create Setup
unless Doctor and audit gates pass. Preserve the adjacent installer manifest and verify its SHA-256
before installation. See `docs/release.md` for target-specific install and cold-start evidence.
