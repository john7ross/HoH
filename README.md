# LLM Harness

[Русский](README.ru.md) · **English**

<p align="center">
  <img src="Promo-GitHub.gif" alt="promo" width="200"/>
</p>

Source, issues and releases: <https://github.com/john7ross/HoH>

LLM Harness is a desktop and headless control plane for a two- or three-level development workflow:

1. A selected Supervisor harness or structured model formalizes vague user intent into verifiable work items.
2. A local worker agent receives atomic tasks and returns patches only.
3. When the customer enables it, an independent critic checks the patch, tests, metrics, and
   handoff quality and is the only role that may close a task.

The implementation intentionally keeps cloud LLMs and local coding agents outside the package. HoH
provides the communication, git, queue, verification, audit, runtime, and handoff harness around
those tools.

The user-facing product overview is available in [English](docs/overview.md) and
[Russian](docs/overview.ru.md).

## Production release scope

Implemented now:

- Native dependency-free Windows desktop GUI using the embedded Tkinter runtime, with RU/EN,
  light/dark themes, separate Supervisor/Worker/Critic agent-model-driver controls, ACP Registry
  refresh, guided project settings, explicit storage/env help, fail-fast unavailable-agent
  validation, private multi-turn Supervisor chat, confirmed chat-to-plan transition, guided
  process-profile editing, masked session-only credentials, queue/status/history operations,
  whole-queue execution, retry/recovery, Doctor, and final audit.
- User-scope managed ACP installation for exact pinned `npx`/`uv` packages and checksummed Windows
  archives, with atomic receipts, bounded extraction, update/removal controls, and fail-closed
  handling of unsigned Registry binaries.
- ACP-advertised and known vendor CLI login flows. HoH can launch interactive login and check the
  resulting session without reading or persisting the vendor token.
- A2A v1 JSON-RPC remote Supervisor, Worker, and Critic drivers with Agent Card discovery,
  bearer/API-key/OAuth2/OIDC authentication, SSE streaming, authenticated push delivery with
  bounded polling fallback, cancellation, bounded responses, and a least-data Worker snapshot
  restricted to explicit `allowed_paths`.
- Embedded Python runtime policy and validation command.
- System scanner for configured local CLI agents, including `codex`, `claude`, `hermes`,
  `openclaw`, and the declarative Aider profile.
- Hermes ACP availability check, isolated prompt builder, and live smoke test.
- Generic JSON and Markdown task files for live Hermes execution.
- Worker capability manifest under `[worker.capabilities]`.
- Versioned `hoh.driver` registry for Supervisor, Worker, and Critic transports, with explicit driver selection
  independent of agent/executable names.
- Generic ACP v1 Supervisor, Worker, and Critic adapters with session model selection and role-specific
  permissions; the official ACP Registry is explicitly refreshed and cached by `role-catalog`.
- ACP Registry launch resolution prefers exact HoH-managed installations and retains the existing
  launcher/PATH path for explicitly operator-managed agents.
- Universal `command` Worker and `command_json` Critic process boundaries; a new conforming agent
  can be configured without changing HoH core code.
- Declarative `process` Worker profiles for one-shot CLIs that need stdin/file prompt placement,
  mandatory and forbidden arguments, model routing, and version probes. Aider is bundled as the
  first declaration with auto-commit disabled.
- Worker adapter conformance suite for deterministic and live worker smoke checks.
- Named role-safe Claude Code and OpenClaw worker adapters with provider-specific non-interactive
  invocation, strict isolated-attempt checks, classified failures, and content-free telemetry.
- End-to-end supervised workflow smoke for plan, queue, worker, verifier, audit, and notifier gates.
- Versioned vendor-neutral JSON envelopes for supervisor-facing plan, status, queue, history, and
  audit commands.
- Immutable external verifier review bundles bound to an exact run, commit, patch digest, scope,
  and deterministic verification evidence.
- Required three-head mode with distinct Logic, Worker, and Critic identity/model manifests,
  bounded attempts, automatic review bundle export, and `approve` / `reject` / `escalate` decisions.
- Optional live Claude Code Critic adapter with schema-constrained output, disabled tools,
  automatic decision import, and retry without rerunning the Worker.
- Supervisor-confirmed rework from an immutable critic correction brief; neither Worker nor Critic
  receives commit, merge, requirements, or self-approval authority.
- Customer-facing project-spec v2 role selection generates `.hoh/role-profile.json`; the customer
  chooses agents/models in the brief and never edits TOML.
- Optional Critic: disabled mode closes through Supervisor after deterministic checks; enabled mode
  requires independent Critic approval.
- Append-only redacted journal for messages, adapter frames, exposed agent tool calls, patches,
  verification commands, Git actions, decisions, notifications, and state transitions.
- JSON Schemas, protocol examples, and a disposable `protocol-conformance` smoke flow.
- Persistent file-based task queue and execution history.
- Cross-process single-writer coordination for queue/history/review/operator/audit state, with
  atomic durable replacement, immutable evidence preservation, bounded lock waits, owner metadata,
  kernel-confirmed stale recovery, and fail-closed corrupted-state reads.
- A separate repository execution lease serializes canonical patch, verification, commit, and
  rollback operations without holding the state lock during Worker or model turns.
- A repository-scoped durable operation ledger closes the Git/StateStore crash window. It records
  intent before canonical mutation, binds commits through `HoH-Operation` trailers, automatically
  finishes only exact idempotent state writes, and blocks new Worker/Git work on ambiguity.
- Deterministic DAG scheduling with task dependencies, integer priorities, FIFO tie-breaking,
  readiness diagnostics, and explicit dependency-blocked outcomes.
- Manual retry and interrupted-run recovery for queued tasks.
- Scanner support for raw local model endpoints such as llama.cpp OpenAI-compatible servers.
- Environment `doctor` command for local preflight diagnostics.
- Configurable model endpoints for supervisor and verifier.
- Dependency-free HTTPS providers for OpenAI Responses, DeepSeek Chat Completions, and explicitly
  configured OpenAI-compatible endpoints, with
  environment-only credentials, bounded retry/error classification, and redacted evidence.
- Requirements-to-project-spec planning through `model_json`, generic `command_json`, or ACP
  Supervisor drivers; the existing `--spec` path remains offline.
- Optional fail-closed semantic verification through `verifier_model`, after the mandatory
  deterministic verifier and before Supervisor-owned staging/commit.
- Configurable local worker trust level.
- Worker job lifecycle with completion callback contract.
- Worker contract that returns a unified diff patch.
- Isolated git worktree attempts for local worker execution.
- Fail-closed rejection if any Worker moves the isolated attempt's Git `HEAD`; HoH remains the only
  commit owner even when an external CLI ignores its no-commit flags.
- Supervisor-owned `git apply`, test execution, staging, and commit.
- Independent deterministic verifier gates.
- Telegram notifier interface with no-send stub and Bot API `sendMessage` adapter.
- Deterministic `audit` command for final readiness checks.
- Built-in offline Python and JavaScript/TypeScript dead-code and unused-file analyzers with
  explicit `passed`, `findings`, `unavailable`, and `unsupported` evidence.
- Documentation of SDLC, metrics, architecture diagrams, final audit, and handoff rules.
- Automatic stale `running` queue detection with explicit operator recovery.
- Transport-independent operator command handler with audit trail.
- Deterministic project lifecycle spec materialization into Markdown docs, task files, and queue.
- Tracked runtime dependency lock used by the internal update payload and offline install scripts.
- SHA-256 release manifest binding the internal update payload, wheelhouse, dependency lock, source commit,
  target platform, embedded runtime, source cleanliness, and executed package gates.
- Explicitly trusted self-signed RSA release catalogs, user-scope side-by-side installation,
  rollback pointer, safe ZIP extraction, and an in-app update workflow without a paid signing CA.
- Signed static compatibility catalogs for free central publication, kept separate from what is
  actually installed on this machine.
- Native Windows Setup, Debian desktop-package, and macOS app/DMG build flows; systemd user and
  launchd scheduling; native desktop notifications; and an authenticated optional-TLS headless
  control API with OpenAPI 3.1.

Roadmap status:

- Production cross-process single-writer coordination is implemented for state, review, Telegram,
  canonical Git integration, and rollback, including guarded crash recovery and machine-readable
  lock diagnostics.
- Automatic crash reconciliation is implemented for queue/Supervisor commits and production
  rollback, including deterministic crash injection, concurrent startup recovery, corrupted-ledger
  fail-closed behavior, review setup repair, status/doctor/operator diagnostics, and guarded
  operator resolution. This closes the current production recovery milestone.
- The control-plane core accepts every conforming ACP Registry agent through one three-role adapter,
  installs every safely pinned/checksummed distribution, and exposes its advertised authentication
  methods. Provider quota, organization policy and model access remain external account gates
  rather than hidden fallbacks.
- A2A v1 is the remote-agent boundary for all three roles. An A2A connection is project-local,
  credentials remain environment-only, and protocol support does not imply that an arbitrary
  remote service will work.
- Supervisor and Critic also expose direct provider-neutral `model_json` targets. The latter uses
  `verifier_model`, so DeepSeek/OpenAI/OpenAI-compatible review does not require a harness process.
- An agent catalog entry declares optional `supervisor_driver`, a `worker_driver`, and optional
  `critic_driver`; HoH never infers a
  driver from an agent name or executable basename.
- The current ecosystem research and ACP-first compatibility roadmap are documented in
  [`docs/harness-ecosystem.md`](docs/harness-ecosystem.md). Whether a given agent works here is a
  question for your machine, not a claim HoH carries: run `role-conformance --role <role>` and see.
- Release and evidence-retention rules are documented in
  [`docs/release.md`](docs/release.md); workspace cleanup is governed by
  [`docs/workspace-cleanup.md`](docs/workspace-cleanup.md).
- Paste-ready GitHub Release notes are maintained in [`RELEASE_NOTES.md`](RELEASE_NOTES.md)
  and [`RELEASE_NOTES.ru.md`](RELEASE_NOTES.ru.md).
- `compatibility-matrix` lists which agents are installed here, their version, and the roles their
  driver covers. `role-conformance --role <role>` runs that role once against a throwaway
  repository and reports whether it worked. Neither writes a record: the answer belongs to the
  machine you asked it on, and a stored one only goes stale.

## Quick check

```powershell
python -m unittest discover -s tests
python -m llm_harness doctor
python -m llm_harness demo
python -m llm_harness runtime
python -m llm_harness init-project --project-root C:\path\to\HoH
python -m llm_harness role-catalog --refresh-registry --json
python -m llm_harness gui --project-root C:\path\to\repo
python -m llm_harness gui --smoke --locale ru --theme dark
python -m llm_harness driver-catalog --json
python -m llm_harness compatibility-matrix --project-root C:\path\to\repo --json
python -m llm_harness publisher import --publisher C:\path\hoh-publisher.public.json
python -m llm_harness update check --source https://host/HoH-releases.signed.json --json
python -m llm_harness update install --source https://host/HoH-releases.signed.json --json
python -m llm_harness server --host 127.0.0.1 --port 8765
python -m llm_harness role-conformance --project-root C:\path\to\repo --role worker --json
python -m llm_harness lock-status --project-root C:\path\to\repo --json
python -m llm_harness reconcile-status --project-root C:\path\to\repo --json
python -m llm_harness reconcile-apply --project-root C:\path\to\repo --operation-id <id> --action complete-state --json
python -m llm_harness hermes-check
python -m llm_harness hermes-smoke --timeout 180
python -m llm_harness worker-conformance --config harness.toml --timeout 300
python -m llm_harness worker-conformance --config harness.toml --timeout 300 --json
python -m llm_harness worker-smoke --config harness.toml --json
python -m llm_harness worker-smoke --config harness.toml --live --json
python -m llm_harness workflow-smoke
python -m llm_harness workflow-smoke --mode blocker
python -m llm_harness hermes-run --project-root C:\path\to\repo --task C:\path\to\task.md --timeout 300
python -m llm_harness worker-run --config harness.toml --project-root C:\path\to\repo --task C:\path\to\task.md
python -m llm_harness project-plan --project-root C:\path\to\repo --spec C:\path\to\project.json --write --enqueue
python -m llm_harness project-plan --config harness.toml --project-root C:\path\to\repo --requirements C:\path\to\requirements.md --write --enqueue
python -m llm_harness model-smoke --config harness.toml --role supervisor
python -m llm_harness model-smoke --config harness.toml --role supervisor --live
python -m llm_harness queue-add --project-root C:\path\to\repo --task C:\path\to\task.md
python -m llm_harness queue-list --project-root C:\path\to\repo
python -m llm_harness queue-run-next --project-root C:\path\to\repo --timeout 300
python -m llm_harness queue-run-loop --project-root C:\path\to\repo --timeout 300 --config harness.toml --parallelism 2
python -m llm_harness rollback-plan --project-root C:\path\to\repo --task-id task-001 --json
python -m llm_harness rollback-history --project-root C:\path\to\repo --json
python -m llm_harness queue-history --project-root C:\path\to\repo
python -m llm_harness audit-history --project-root C:\path\to\repo
python -m llm_harness supervisor-status --project-root C:\path\to\repo
python -m llm_harness supervisor-status --project-root C:\path\to\repo --json
python -m llm_harness review-export --project-root C:\path\to\repo --run-id <run-id> --json
python -m llm_harness review-import --project-root C:\path\to\repo --decision C:\path\to\decision.json --json
python -m llm_harness critic-run --config harness.toml --project-root C:\path\to\repo --json
python -m llm_harness review-list --project-root C:\path\to\repo --json
python -m llm_harness journal-list --project-root C:\path\to\repo --json
python -m llm_harness journal-record --project-root C:\path\to\repo --event C:\path\to\event.json --json
python -m llm_harness journal-export --project-root C:\path\to\repo --output C:\path\to\journal.md
python -m llm_harness protocol-conformance --distribution-root . --json
python -m llm_harness three-head-conformance --json
python -m llm_harness queue-stale --project-root C:\path\to\repo --max-age-minutes 60
python -m llm_harness queue-retry --project-root C:\path\to\repo --task-id task-001
python -m llm_harness queue-recover-running --project-root C:\path\to\repo --task-id task-001
python -m llm_harness operator-command --project-root C:\path\to\repo --text "/status"
python -m llm_harness telegram-poll --config harness.toml --project-root C:\path\to\repo
python -m llm_harness telegram-watch --config harness.toml --project-root C:\path\to\repo --iterations 12
python -m llm_harness audit --config harness.toml --check "python -m unittest discover -s tests" --markdown
```

The demo creates a temporary git repository, asks the stub worker for a patch, applies it through
the supervisor, runs a verification command, and commits the result.

For packaged desktop use, run `scripts\hoh-gui.ps1 -ProjectRoot C:\path\to\repo`. The window stores
only language, theme, and the last project path in the user profile. Project policy is written to
`.hoh/harness.json`, role choices to `.hoh/role-profile.json`, and secret values are never stored by
the GUI. See [`docs/gui.md`](docs/gui.md) for workflows, security boundaries, and acceptance tests.

The audit command is a deterministic readiness gate. It checks git cleanliness, README presence,
Markdown documentation, required architecture diagrams, lifecycle artifacts under `docs/hoh/` and
`tasks/hoh/` when present, blocking markers such as TODO/FIXME/placeholder <!-- hoh-audit: ignore-line -->
text, generated artifacts, and explicit verification commands supplied through `--check`.
Within generated lifecycle specifications, requirements that explicitly prohibit or assert the
absence of those markers are treated as policy text; an actual unresolved marker remains blocking.
It also runs the built-in analyzers selected under `[audit]`. Python analysis uses the standard
library AST to find unreachable statements and unreferenced private top-level declarations.
Python, JavaScript, and TypeScript unused-file analysis builds a static relative-import graph.
With configured `entry_points`, every tracked source file outside the reachable graph is blocking;
without them, the conservative default only reports private orphan files. Analyzer evidence is
included in Markdown and JSON even when a language is absent (`unsupported`) or an analyzer cannot
run (`unavailable`). HoH reports findings only; it never deletes candidate files.

The doctor command is a read-only local environment preflight. It checks Git, embedded runtime
layout, configured worker executable, worker capability manifest consistency, configured agent
CLIs, local model endpoints, supervisor/verifier provider URLs and credential environment-variable
presence, and Telegram environment variable presence. It performs no provider request and sends
no Telegram message.

## License

HoH is distributed under the Apache License 2.0. Copyright 2026 Sergey Lebedev.
See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE). The license permits use, modification, and
redistribution, including commercial use, as long as the copyright notice and the license text
are preserved. Bundled third-party components keep their own licenses, listed in
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).

To work on HoH itself, see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Configuration sketch

The built-in OpenAI and DeepSeek provider integrations use TOML or JSON config with this shape:

```toml
# Top-level keys must come before the first [table] header.
worker_trust_level = "patch_only"

[supervisor_model]
provider = "openai"
model = "<approved OpenAI model>"
api_key_env = "OPENAI_API_KEY"
timeout_seconds = 60
max_retries = 2
max_output_tokens = 8192

[supervisor]
# model_json uses [supervisor_model]; acp/command_json run an external harness.
driver = "model_json"
timeout_seconds = 300

[verifier_model]
provider = "deepseek"
model = "<approved DeepSeek model>"
api_key_env = "DEEPSEEK_API_KEY"
timeout_seconds = 60
max_retries = 2
max_output_tokens = 8192

[runtime]
require_embedded_python = true
embedded_python_path = "runtime/python/python.exe"
wheels_path = "vendor/wheels"

[scheduler]
max_parallel_tasks = 2

[coordination]
state_lock_timeout_seconds = 5
execution_lock_timeout_seconds = 5
poll_interval_seconds = 0.05

[audit]
language_analyzers = ["python", "javascript", "typescript"]
# Defining entry points enables strict reachability for each language with a matching root.
entry_points = ["src/app.py", "src/index.ts"]
exclude_paths = ["vendor/**", "generated/**"]
fail_on_unavailable = true

[worker]
driver = "hermes_acp"
command = "hermes"
args = ["acp"]
timeout_seconds = 300

[worker.capabilities]
task_transport = "acp_stdio"
artifact_contract = "worktree_diff"
requires_isolated_worktree = true
supports_subagents = false

[critic]
# manual = explicit review-export/review-import
# model_json = direct structured review through [verifier_model]
# claude_code = automatic read-only Claude Code review
# command_json = any compatible read-only JSON CLI
driver = "manual"
command = "claude"
args = []
timeout_seconds = 300
# max_budget_usd = 2.0

[telegram]
enabled = false
bot_token_env = "HOH_TELEGRAM_BOT_TOKEN"
chat_id_env = "HOH_TELEGRAM_CHAT_ID"
user_id_env = "HOH_TELEGRAM_USER_ID"

[[agents]]
name = "codex"
command = "npx"
args = ["-y", "@agentclientprotocol/codex-acp@1.7.0"]
supervisor_driver = "acp"
worker_driver = "acp"
critic_driver = "acp"

[[agents]]
name = "claude"
command = "claude"
worker_driver = "claude_code"
critic_driver = "claude_code"

[[agents]]
name = "hermes"
command = "hermes"
args = ["acp"]
supervisor_driver = "acp"
worker_driver = "hermes_acp"

[[agents]]
name = "openclaw"
command = "openclaw"
worker_driver = "openclaw"

# Optional local model endpoint example. Uncomment only when the endpoint is running.
# [[local_models]]
# name = "qwen-llama-cpp"
# base_url = "http://127.0.0.1:8080"
# model = "qwen"
# protocol = "openai_compatible"
```

Worker trust levels:

- `patch_only` (the only level this release implements): the worker gets a disposable worktree and
  returns a patch. It never creates a branch or a commit in the canonical repository; the
  supervisor owns every Git state change.
- `branch_only` and `branch_and_commit` are described in `docs/architecture.md` as the intended
  extension. They are **not supported**, and configuring one is refused at load time rather than
  silently downgraded.

Local execution targets are intentionally separate:

- `local_agent`: a coding agent CLI such as Codex, Claude Code, or Hermes.
- `local_model_endpoint`: a raw model server such as llama.cpp; it needs an executor adapter before it
  can safely touch repositories.

Project lifecycle policy:

- The Supervisor first runs `role-catalog --json` and asks four plain-language questions: which
  agent/model is Supervisor, which is Worker, whether Critic is wanted (and which agent/model), and
  how many Worker attempts are allowed. Model names are optional.
- `examples/project-spec-v2.json` is the machine artifact the Supervisor creates from the customer
  conversation; it is not a form the customer must edit.
- `project-plan --write` generates `.hoh/role-profile.json`, and execution commands load it
  automatically. The same agent product/model may occupy different roles in separate sessions.
- `project-plan` validates a supervisor-authored project spec before work is queued.
- Required spec fields are project id, title, goal, customer, business requirements, definition of
  done, and at least one atomic task.
- `project-plan --write` materializes Markdown docs under `docs/hoh/` and JSON task files under
  `tasks/hoh/`.
- `project-plan --write --enqueue` also enqueues the generated task files into HoH state.
- The command is deterministic: it validates and materializes explicit input; it does not invent
  requirements or acceptance criteria.

Supervisor-facing protocol:

- The external supervisor writes a project spec JSON with business requirements, definition of
  done, and atomic tasks.
- HoH validates and materializes that spec through `project-plan --write --enqueue`.
- The durable handoff artifacts are `docs/hoh/project-brief.md`, `docs/hoh/roadmap.md`, task JSON
  files under `tasks/hoh/`, queue state, run history, audit reports, and git commits.
- The supervisor observes execution through `queue-list`, `queue-history`, `audit-history`,
  `rollback-history`, `supervisor-status`, `operator-command /status`, and final audit evidence.
- Add `--json` to `project-plan`, `supervisor-status`, `audit`, `audit-history`, every
  `rollback-*` command, and every `queue-*` command for the stable `hoh.protocol` v1 envelope.
  `queue-run-loop --json` emits one
  final document rather than mixed progress lines. The
  top-level contract is `protocol`, `protocol_version`, `message_type`, `ok`,
  `generated_at_utc`, and `data`, with `error` present only on failures.
- The verifier/reviewer contract is explicit: each task must define acceptance criteria,
  verification commands, allowed paths, and non-goals; HoH records pre-apply findings,
  post-apply findings, command results, commit ids, and final audit evidence.
- `workflow-smoke` proves this protocol without secrets by running the full path in a disposable
  repository with a deterministic command worker and stub notifier.

External verifier handoff:

- `three_head.mode = "manual"` preserves the v1 manual external-review workflow. In
  `mode = "required"`, HoH moves a successful worker run to `review_pending` and exports a v2
  critic bundle.
- `[critic] driver = "manual"` stops there for an explicit handoff. `[critic] driver = "model_json"`
  invokes the configured `verifier_model` with the same closed judgment schema and no repository
  path or tools. `[critic] driver = "claude_code"`
  invokes Claude Code non-interactively, with tools disabled and schema-constrained structured
  output, then imports the decision through the same protocol validator.
- `critic-run` retries a failed automatic review against the existing immutable bundle; it never
  reruns the Worker. Process failures and timeouts leave the task in `review_pending`.
- Required mode uses distinct role-session identities. The role snapshot and attempt limit are
  embedded in each immutable bundle so later configuration cannot weaken an in-flight task.

- `review-export --run-id <id>` writes the canonical bundle below the HoH state root. `--output`
  may also copy the same evidence to a verifier handoff path.
- A bundle contains the atomic task, allowed paths, non-goals, changed files, full commit diff,
  commit and patch SHA-256 evidence, deterministic policy findings, verification command output,
  and the authority matrix.
- The external verifier returns a document conforming to
  `schemas/verifier-decision-v1.schema.json`. `review-import` validates closed fields, protocol
  version, bundle digest, reviewed commit, and reviewed patch digest before appending immutable
  evidence.
- Exporting a bundle creates a pending review gate. `supervisor-status` is non-ready while the
  latest exported review for a task is pending or rejected. A valid approval clears that gate.
- `queue-run-loop --final-audit` refuses readiness notification while an exported review is pending
  or rejected and sends a user-action-required notification through the configured notifier.
- A rejection never changes Git automatically. The Supervisor may create remediation work or use
  the explicit rollback policy after reviewing `rollback-plan` evidence and confirming the exact
  commit, reason, and post-rollback checks.
- Additional Supervisor/verifier providers and additional Critic providers can be added behind
  the documented boundaries. Any agent or service that can read and write the JSON contract can
  still use the manual boundary.
- The Claude adapter asks the model only for judgment fields. HoH injects the canonical bundle id,
  bundle digest, Critic identity, commit attestation, patch attestation, and timestamp before
  validating and persisting the final decision.

Three-head critic decisions use `schemas/critic-decision-v2.schema.json`:

- `approve` closes the task and clears the readiness gate;
- `reject` records findings and a mandatory correction brief, then sets `rework_required`;
- `review-rework --decision-id <id>` is the explicit supervisor confirmation that queues exactly
  those correction instructions without changing the original objective, scope, or non-goals;
- `escalate` blocks the task and notifies the customer with a question and options including
  `реши сам` and `свой`;
- reaching the bundle's immutable `max_attempts` also escalates instead of looping forever.

Run `three-head-conformance --json` to prove approve, reject, supervisor-confirmed rework, second
attempt approval, escalation, Critic-disabled closure, and clean canonical git state without
providers, network, or secrets.

Interaction journal:

- `interaction-journal.jsonl` lives under the external HoH state root. Each event has a UTC time,
  actor, recipient, action, task/run/correlation ids, content, and metadata.
- `journal-list` filters events; `journal-export` creates redacted JSONL or Markdown.
- Secret-like keys, Telegram bot tokens, Bearer credentials, and secret environment assignments are
  redacted before disk write.
- ACP tool messages and generic CLI process telemetry are recorded. Private tool calls inside an
  opaque agent can only be shown if its protocol emits them.
- External Supervisor/Critic surfaces use the closed `schemas/journal-ingress-v1.schema.json` and
  `journal-record` to append briefing messages or tool events that occur outside HoH adapters.

Agent-neutral driver policy:

- `project-plan --requirements` executes through the configured `[supervisor]` adapter.
- `worker-run` loads one JSON or Markdown task file and executes it through the configured
  `[worker]` adapter.
- `queue-run-next` and `queue-run-loop` use the same `[worker]` adapter instead of hard-coding a
  concrete agent.
- `driver` is the canonical configuration field. Legacy `type` remains a compatibility alias and
  conflicting `driver`/`type` values fail closed.
- The built-in registry is versioned as `hoh.driver` `1.0`; inspect it with `driver-catalog`.
- The external ACP Registry is cached only on explicit
  `role-catalog --refresh-registry`; a registry ID can then fill any role without TOML edits.
- Default worker driver remains `hermes_acp` for upgrade compatibility.
- `command` runs a generic CLI worker in an isolated git worktree and lets HoH collect the resulting
  diff. Use this for Hermes wrappers, custom scripts, or other local agent CLIs that can
  operate from a repository path.
- `process` uses a declarative one-shot profile when a CLI needs a temporary prompt file or fixed
  safety/model flags. The default Aider declaration enforces non-streaming, non-interactive,
  no-auto-commit execution; another CLI can be added in `agents[].worker_process_profile` without a
  Python adapter.
- `claude_code` invokes Claude Code in print/JSON mode with `safe-mode`, `dontAsk`, no session
  persistence, and only `Read`, `Edit`, `Write`, `Glob`, and `Grep`. Bash, MCP, plugins, Chrome,
  background agents, permission bypass, and user-owned safety flags are unavailable.
- `openclaw` invokes `openclaw agent --local --json` with a unique session and a temporary
  no-secret config. The attempt worktree is the workspace; only workspace-scoped `read`, `write`,
  `edit`, and `apply_patch` tools are present. Exec, process, browser, messaging, delivery,
  background sessions, and elevated tools are denied.
- `stub` is available for deterministic tests and local demo flows only.
- Unknown driver ids fail explicitly; there is no silent fallback to another worker.
- Named provider adapters reject HoH-owned or unsafe CLI flags in `[worker].args`.
- `worker-smoke` performs only executable/version/safety preflight by default. `--live` is required
  to authorize a disposable-repository conformance turn.

Worker capability manifest:

- `[worker.capabilities]` declares the contract HoH expects from the configured worker.
- `task_transport` describes how HoH sends the task to the worker adapter, for example
  `acp_stdio`, `stdin_prompt`, `profiled_process`, or `in_process`.
- `artifact_contract` describes the artifact HoH accepts from the adapter. Current supported values
  are `worktree_diff` and `direct_patch`.
- `requires_isolated_worktree` must match the actual adapter behavior; `doctor` and
  `worker-conformance` fail on mismatch.
- `supports_subagents` documents whether the worker may run its own internal subagents. HoH still
  treats the worker boundary as low-trust and accepts only the configured artifact contract.

Command worker contract:

- HoH starts `command` plus `args` with the attempt worktree as the process working directory.
- HoH sends the constrained task prompt to stdin.
- HoH sets `HOH_JOB_ID`, `HOH_CALLBACK_TOKEN`, `HOH_REPOSITORY`, `HOH_WORK_ITEM_ID`,
  `HOH_WORK_ITEM_TITLE`, and `HOH_WORK_ITEM_JSON`.
- Exit code `0` means the worker finished writing candidate files; HoH then collects the git diff
  from the isolated worktree.
- Non-zero exit or timeout is a worker blocker and does not modify the canonical repository.
- The worker must not commit, branch, merge, push, or decide readiness.
- HoH verifies the attempt still points at its starting commit before collecting the diff. A moved
  `HEAD` rejects the completion and the disposable attempt is removed.

Declarative process profiles are documented in
[`docs/agent-neutral-drivers.md`](docs/agent-neutral-drivers.md). Use them instead of a wrapper when
the external CLI can run once, edit its current working directory, and expose deterministic prompt
and exit semantics.

Example generic command worker config:

```toml
[worker]
driver = "command"
command = "python"
args = ["path/to/worker_cli.py"]
timeout_seconds = 300

[worker.capabilities]
task_transport = "stdin_prompt"
artifact_contract = "worktree_diff"
requires_isolated_worktree = true
supports_subagents = false
```

Example Claude Code worker:

```toml
[worker]
driver = "claude_code"
command = "claude"
model = "sonnet"
timeout_seconds = 300
max_budget_usd = 2.0
```

Example OpenClaw worker:

```toml
[worker]
driver = "openclaw"
command = "openclaw"
model = "openai/gpt-5.6-sol"
thinking = "high"
timeout_seconds = 300
```

OpenClaw requires an explicit model because its HoH adapter deliberately does not inherit the
operator's potentially broader persistent agent configuration. The generated per-turn config is
deleted after the process exits.

Hermes ACP policy:

- `hermes acp --check` validates that Hermes ACP is available.
- HoH suppresses globally configured MCP servers for the isolated ACP session. On Windows it starts
  Hermes outside the parent Win32 Job Object and applies a process-local workaround for the exact
  Git Bash health probe that can deadlock in nested ACP hosts. The workaround does not bypass ACP
  edit/command permissions, replace the user's Hermes installation, or approve arbitrary commands.
- `hermes-dry-run` prints the exact isolated-worktree worker prompt HoH would send.
- `hermes-smoke` runs a real Hermes ACP turn in a temporary git repository through an isolated
  worktree attempt and commits only after verifier checks pass.
- `worker-conformance --config harness.toml` runs the generic adapter conformance suite against the
  configured worker. With `[worker] driver = "hermes_acp"` this is the portable live-worker smoke flow
  for Hermes.
- `hermes-run` remains a Hermes-specific compatibility command. For configurable execution, use
  `worker-run` or the queue commands with `[worker] driver = "hermes_acp"`.
- Worker adapters can now be wrapped in an isolated git worktree attempt. The wrapper lets a local
  agent write only inside the temporary worktree, stages that attempt worktree, collects a binary
  unified diff, removes the worktree, and returns the diff to the supervisor.
- Live Hermes execution is accepted only from a branch named `hoh/attempt/*`; direct execution on
  the canonical repository branch is rejected.

Task file JSON format:

```json
{
  "id": "task-001",
  "title": "Create artifact",
  "objective": "Create HARNESS_DEMO.md with a short sentence.",
  "acceptance_criteria": ["HARNESS_DEMO.md exists."],
  "verification_commands": ["python -c \"from pathlib import Path; assert Path('HARNESS_DEMO.md').exists()\""],
  "allowed_paths": ["HARNESS_DEMO.md"],
  "non_goals": ["Do not edit docs."],
  "depends_on": ["task-000"],
  "priority": 10
}
```

Task file Markdown format:

```markdown
# Create artifact

## id

task-001

## objective

Create HARNESS_DEMO.md with a short sentence.

## acceptance criteria

- HARNESS_DEMO.md exists.

## verification commands

- python -c "from pathlib import Path; assert Path('HARNESS_DEMO.md').exists()"

## allowed paths

- HARNESS_DEMO.md

## non-goals

- Do not edit docs.

## depends on

- task-000

## priority

10
```

Queue and history policy:

- `queue-add` stores a validated task in a persistent queue.
- `depends_on` is optional and lists task IDs that must reach `done`; `priority` is an optional
  integer and defaults to `0`.
- Project plans validate all dependency IDs and reject cycles before materialization. A standalone
  `queue-add` requires every dependency to exist already.
- `queue-run-next` selects only dependency-ready tasks, then chooses the highest priority and uses
  insertion order as the deterministic tie-breaker. Selection and the `queued → running` transition
  are one locked claim, so separate HoH processes cannot claim the same task.
- If queued tasks exist but none is ready, queue commands report `dependency_blocked` and list each
  dependency with its current status; this is distinct from an empty queue.
- The selected task runs through the configured worker adapter, isolated worktree attempts when
  required, verifier checks, and supervisor-owned commit.
- `queue-run-loop` forms bounded batches of dependency-ready tasks in priority/FIFO order. Worker
  preparation overlaps only when `allowed_paths` scopes do not overlap; verifier checks, canonical
  patch application, commits, review transitions, and state writes remain serialized.
- Configure the default with `[scheduler] max_parallel_tasks = N` or override it with
  `--parallelism N`. The default is `1`. A task with no `allowed_paths` is treated as exclusive.
- Required three-head review and worker adapters without isolated-worktree capability automatically
  use parallelism `1`, preserving the review and canonical-repository barriers.
- `queue-run-loop --final-audit --final-check "<command>"` runs the final readiness audit when the
  queue becomes empty, stores the Markdown audit report under the state root, then notifies the
  customer that the project is ready or that audit failed.
- `queue-list` shows the selected task, ready/waiting sets, priorities, dependency blockers,
  attempts, last commit, and last error.
- `queue-history` shows immutable run records.
- `audit-history` shows persisted final audit evidence records and report paths.
- `queue-stale` reports `running` tasks whose `updated_at_utc` is older than the configured
  threshold.
- `queue-retry` requeues a failed task after the operator has reviewed the failure.
- `queue-recover-running` requeues a manually confirmed stuck `running` task after an interrupted
  process or machine restart.
- By default, queue state is stored outside the canonical repository at
  `<repo-parent>/.hoh-state/<repo-name>-<hash>` so operational state does not make the git worktree
  dirty before an isolated attempt is created.
- Use `--state-root` to override the state directory.
- Every state mutation uses an OS-backed lease at `<state-root>/.coordination/state.lock`.
  Owner metadata records lease id, PID, host, command, action, and acquisition time. JSON and JSONL
  state is written through a unique same-directory temporary file, flushed with `fsync`, and
  atomically replaced.
- Canonical Git mutation uses a distinct repository lease under
  `<repo-parent>/.hoh-leases/<repo-name>-<hash>/execution.lock`. Slow Worker turns do not hold the
  state lease; canonical integration and rollback do hold the repository lease.
- `[coordination]` configures bounded state/execution wait times and polling interval. Contention
  returns owner diagnostics instead of deleting or stealing a live lock.

Retry and recovery are explicit operator actions. `queue-run-next` only runs tasks with status
`queued`; it does not automatically rerun `failed` or `running` tasks. `queue-run-loop` checks for
stale `running` tasks before starting new work, notifies the customer when they exist, and stops
until an operator confirms recovery or another action. The default stale threshold is 60 minutes and
can be changed with `--stale-minutes`; `--stale-minutes 0` disables the preflight check.

Production rollback policy:

- `rollback-plan --task-id <id> --json` is read-only. It resolves the latest successful HoH run to
  a full Git commit, reports the exact changed files and transitive downstream tasks, and lists every
  policy blocker.
- `rollback-apply` requires `--expected-commit`, `--reason`, and one or more post-rollback `--check`
  commands. The expected commit is an optimistic-lock confirmation, not a free-form revision.
- The target must be the latest successful commit for a task currently in `done`, must be a
  non-root/non-merge ancestor of `HEAD`, and the canonical repository must be clean.
- Running or unresolved work blocks rollback. Completed or active transitive downstream tasks also
  block it; HoH never silently cascades a rollback. Queued downstream work may remain queued and
  becomes dependency-blocked after the target enters `rolled_back`.
- HoH creates the reverse patch with `git revert --no-commit` in an isolated worktree, runs all
  operator-provided checks there, revalidates policy, applies the exact patch to the canonical
  repository, runs the checks again, and creates one Supervisor-owned rollback commit.
- The repository execution lease covers rollback planning, isolated verification, canonical apply,
  verification, and commit. A competing process fails before Git mutation and reports the live
  owner.
- HoH never uses `git reset --hard`. A failed isolated check leaves canonical Git untouched; a failed
  canonical check restores only the target commit paths. Every attempted execution that reaches the
  isolated phase receives append-only evidence in `rollback-records.jsonl`.
- A successful repeat of the same task/target request returns the existing record instead of
  reverting the revert. `rollback-history` exposes the evidence, and `supervisor-status` reports
  rolled-back tasks and the latest rollback.
- Final handoff is blocked while any latest task lifecycle is `rolled_back`. Re-enqueue and complete
  a corrected lifecycle for the same task id before running the final audit.

Example:

```powershell
.\scripts\hoh.ps1 rollback-plan `
  --project-root C:\path\to\repo `
  --task-id task-001 `
  --json

.\scripts\hoh.ps1 rollback-apply `
  --project-root C:\path\to\repo `
  --task-id task-001 `
  --expected-commit <full-sha-from-plan> `
  --reason "Production regression" `
  --check "python -m unittest discover -s tests" `
  --json
```

Blocker notification policy:

- A blocker is a worker/adapter exception or a task result rejected by verifier/test gates.
- `queue-run-loop` stops dispatching new batches after a blocker or stale `running` preflight
  finding. Already-started independent tasks in the same batch finish and are recorded before the
  loop reports the blocker.
- The blocked task remains recorded as `failed` unless an operator explicitly retries it.
- If Telegram is enabled through `--config`, HoH sends the customer a message with the task id,
  blocker reason, and clarification options.
- Clarification options always include `реши сам` and `свой`, plus concrete choices such as retry
  or stop for manual inspection.

Final handoff notification policy:

- `--final-audit` is opt-in because every project needs explicit verification commands.
- Final handoff is blocked before the project audit while the latest queue lifecycle contains
  queued, running, failed, review-pending, rework-required, escalated, stale, or unreconciled state.
  Inspect `queue-list` and `supervisor-status`, then use the explicit retry or recovery operation.
- Provide one or more `--final-check` commands; they are passed to the same deterministic audit gate
  as `llm-harness audit --check`.
- HoH writes audit evidence to `audit-reports/<timestamp>-<id>.md` and appends an index entry to
  `audit-reports.jsonl` under the queue state root.
- If the audit passes, HoH calls `notify_project_ready` and includes the evidence path.
- If the audit fails, HoH calls `notify_audit_failed`, includes the evidence path, prints finding
  codes, and exits non-zero.

Operator command policy:

- `operator-command --text "/status"` returns queue counters and stale count.
- `operator-command --text "/queue"` lists queued state.
- `operator-command --text "/stale"` lists stale `running` tasks.
- `operator-command --text "/retry <task-id>"` requeues a failed task.
- `operator-command --text "/recover <task-id> [reason]"` requeues a confirmed interrupted
  `running` task.
- `operator-command --text "/continue"` acknowledges that the operator wants work to continue; run
  `queue-run-loop` to actually resume execution.
- `operator-command --text "/stop"` records the decision and leaves queue state unchanged.
- Every operator command is appended to `operator-events.jsonl` under the queue state root.
- The command handler is transport-independent; Telegram polling should call this same handler after
  authenticating the sender.

Python runtime policy:

- Production runs must use `runtime/python/python.exe`, not system Python.
- Runtime dependencies are pinned in `requirements.lock`.
- Wheels and dependencies must be vendored under `vendor/wheels`.
- Use `scripts/bootstrap-runtime.ps1 -PythonSource <path-to-python-directory> -BuildWheelhouse -InstallOffline`
  to copy a prepared runtime, build wheels, and install from the local wheelhouse.
- Use `scripts/build-wheelhouse.ps1` to populate `vendor/wheels`.
- Use `scripts/install-offline.ps1` to install from `vendor/wheels` into `runtime/site-packages`.
- Use `scripts/hoh.ps1` as the source-tree launcher during development and release verification.
- Use `scripts/package-windows-installer.ps1` to refresh/audit the embedded runtime and compile the
  user-facing Windows Setup application.
- Runtime validation starts the embedded Python and requires at least one `.whl` in the wheelhouse.
- Runtime and wheel artifacts are generated locally and excluded from git; the user-facing artifact
  is `dist/HoH-Setup-<version>.exe`.

Development launcher examples:

```powershell
.\scripts\hoh.ps1 doctor
.\scripts\hoh.ps1 scan
```

Windows Setup build flow:

```powershell
.\scripts\bootstrap-runtime.ps1 `
  -PythonSource "C:\path\to\python" `
  -BuildWheelhouse `
  -InstallOffline

.\scripts\package-windows-installer.ps1
```

Telegram notification policy:

- Telegram is disabled by default.
- Bot tokens, chat IDs, and allowed user IDs are never stored directly in project config.
- Config stores only environment variable names.
- Outbound messages use the Telegram Bot API `sendMessage` method.
- Inbound operator commands use `telegram-poll`, which calls `getUpdates` once, authenticates both
  `chat_id` and `from.id`, forwards authorized text commands to the deterministic operator command
  handler, replies with the result, and stores the next update offset in queue state.
- Use `telegram-watch` when a scheduler, service wrapper, or manual operator session needs repeated
  polling. `--iterations 0` runs until interrupted; a positive `--iterations` value gives a bounded
  scheduler-friendly run.
- Telegram inbound commands use bounded polling; authenticated A2A push callbacks are implemented
  separately from Telegram.
- To test delivery after creating a bot and starting a chat with it:

```powershell
$env:HOH_TELEGRAM_BOT_TOKEN = "<bot-token>"
$env:HOH_TELEGRAM_CHAT_ID = "<your-user-or-chat-id>"
$env:HOH_TELEGRAM_USER_ID = "<your-telegram-user-id>"
python -m llm_harness notify --config harness.toml --message "HoH Telegram check"
python -m llm_harness telegram-poll --config harness.toml --project-root C:\path\to\repo
python -m llm_harness telegram-watch --config harness.toml --project-root C:\path\to\repo --iterations 3
```

Daily operations wrapper:

```powershell
.\scripts\hoh.ps1 init-project
. .\hoh-env.local.ps1

.\scripts\run-daily-ops.ps1 `
  -Config .\harness.toml `
  -FinalCheck "set PYTHONPATH=src&& python -m unittest discover -s tests" `
  -FinalCheck "python -m compileall -q src tests"
```

Windows Task Scheduler wrapper:

```powershell
.\scripts\register-telegram-watch-task.ps1 `
  -Config .\harness.toml `
  -EveryMinutes 1 `
  -Iterations 1 `
  -RunNow

.\scripts\register-telegram-watch-task.ps1 -Unregister
```

The scheduled task runs the installed `scripts\hoh.ps1` launcher in the current user session and
does not store Telegram secrets. Configure bot token, chat ID, and allowed user ID as environment
variables before registering or running the task.

See [ARCHITECTURE.md](ARCHITECTURE.md) / [ARCHITECTURE.ru.md](ARCHITECTURE.ru.md) and
[docs/architecture.md](docs/architecture.md) / [docs/architecture.ru.md](docs/architecture.ru.md)
for the formal architecture and delivery rules. See [docs/daily-ops.md](docs/daily-ops.md) /
[docs/daily-ops.ru.md](docs/daily-ops.ru.md) for the operator runbook, including environment
setup, Telegram polling, queue execution, and final audit handoff. See
[docs/headless-server.md](docs/headless-server.md) / [docs/headless-server.ru.md](docs/headless-server.ru.md)
for the authenticated server API and Linux/macOS user-service setup.

## Support author

<p align="center">
  <img src="donate-qr.png" alt="Donate QR" width="200"/>
</p>

BTC: bc1q3frrup5neh7nhfg944etu2agd4j9u0vg3jyee6

ETH(Arbitrum): 0x43B349d8Cea83215D707EBa3bc35e9917f746b0a

TRX: THSzvy49KNeqRjXsGkurh2A5G4avV4RgN4

XRP: rLWZjS3DMupC4ZdXCX3BVYn4dEtC3iNhgy

SOL: 3xwfybxJ6Tz5t6pjBBkL5yYQCZo6wfbv932UNA4ThdP8

ADA: addr1q926ys75jp5wn2pv32a3t8r8pdhr7w02v0t9j4a8pmg0ruww5rlkctu4lnz2hfcwa5qfn3zhsd0s23r22uqwzx9gu6cq5c4e76

TON: UQC4qlAOD9Nly4K_66GJ_yCsSM3x2sB0vZ2GrBQbc--gZUui

DOGE: DTjNYmbtymzcjUiV4MsZY8MP4dM7MJ6qLC

XMR: 44qRqM6YtnxXUhkgCFqDDrKMPjWriu69FLBoop8Kwp7e1VQsBUJoVQ8JYQjfMV5C6uidTUgSSyoJ65mq8aYG2esZ1rrqfwt
