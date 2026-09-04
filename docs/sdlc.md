# SDLC Rules

[Русский](sdlc.ru.md) · **English**

Every project managed by the harness follows the same lifecycle.

## 1. Research

The supervisor inspects repository structure, existing implementations, configs, docs, tests, logs,
CI, and architecture before planning code changes.

Success criteria:

- current behavior is described from observed facts
- assumptions are separated from verified facts
- unknowns are listed

## 2. Clarification

The supervisor asks the user when business logic, scope, acceptance criteria, architecture,
integration behavior, deployment target, compatibility, security constraints, or data model are
unclear.

Each question must include `реши сам` and `свой`.

Success criteria:

- no implementation-critical ambiguity remains
- defaults chosen by the supervisor are recorded as assumptions
- the customer has selected Supervisor and Worker agents, optional models, whether Critic is
  enabled, and the bounded attempt count through plain-language questions
- project-spec v2 records those answers; the customer is not asked to edit configuration files

## 3. Formal specification

The supervisor creates a technical specification with:

- problem statement
- business requirements
- non-functional requirements
- architecture decision
- risks and constraints
- acceptance metrics
- testing requirements
- documentation requirements
- deployment or handoff plan

Success criteria:

- every requirement has at least one verification method
- `project-plan` can validate the lifecycle spec without inventing missing requirements
- the supervisor-facing protocol artifacts are explicit: project spec, brief, roadmap, task files,
  queue state, run history, audit evidence, and git commits
- `supervisor-status` can report the current queue, latest run, latest audit, and latest operator
  event without mutating state
- machine consumers use the versioned `hoh.protocol` v1 JSON envelope and shipped JSON Schemas
- `.hoh/role-profile.json` is deterministically generated from the brief and loaded by runtime

## 4. Atomic task decomposition

Tasks must be small enough for a low-trust worker.

Each task contains:

- objective
- worker trust level
- allowed files or areas
- explicit non-goals
- acceptance criteria
- expected tests
- maximum blast radius
- rollback expectation
- dependency task IDs
- integer scheduler priority

Success criteria:

- the verifier can approve or reject the task without interpreting broad intent
- `project-plan --write` can materialize task files and Markdown roadmap from the spec
- `project-plan --write --enqueue` can enqueue the generated task files when the operator approves
- `project-plan --requirements --config harness.toml` may generate the same validated v1 spec
  through `supervisor_model`; model output cannot choose role identities or Git authority
- project planning rejects unknown dependencies, duplicate dependency edges, and dependency cycles
- only tasks whose dependencies are `done` can enter execution
- ready tasks are selected by descending priority and FIFO insertion order for equal priorities
- bounded parallel batches contain only tasks with non-overlapping `allowed_paths`; tasks without
  an explicit scope are exclusive
- worker preparation may overlap in isolated worktrees, while verification, canonical commits,
  review transitions, and state persistence remain deterministic and serialized
- the task contains enough evidence inputs for a reviewer: objective, scope, non-goals,
  verification commands, and acceptance criteria

## 5. Implementation

The worker receives one task and returns a patch. The supervisor applies patches and owns git.
The deterministic policy verifier and commands run before any configured semantic model. A
semantic `fail`, `escalate`, invalid response, timeout exhaustion, or provider error reverts the
applied patch before staging and commit.

Named Claude Code and OpenClaw Workers are process providers, not authority holders. They can write
only in the isolated attempt workspace under their fixed adapter tool policy. HoH collects the
candidate `worktree_diff`; the canonical Supervisor alone applies, verifies, stages, commits,
rolls back, and changes task lifecycle state.

## Production rollback

Rollback is an explicit Supervisor/operator action, not a Worker or Critic side effect.

Success criteria:

- a read-only plan binds the request to the latest successful recorded HoH commit
- exact expected commit, reason, and post-rollback checks are required before mutation
- dirty Git, active work, unsupported commit shapes, and completed/active downstream tasks block
  execution
- reverse-patch construction and first verification happen in an isolated worktree
- canonical apply, verification, commit, task transition, and append-only evidence are serialized
- an OS-backed repository execution lease prevents another process from entering canonical Git
  mutation; lock contention fails before mutation with owner diagnostics
- failed verification restores the canonical repository without destructive reset
- successful rollback changes the task to `rolled_back` and blocks final handoff until a corrected
  replacement lifecycle completes

File-backed state uses a separate short-lived state lease. Queue claims, lifecycle transitions,
history, review, audit, operator, journal, rollback evidence, and Telegram offsets are durable
read-modify-write transactions. The state lease is never held across a Worker or Critic model turn.
Corrupted JSON/JSONL fails closed and must be inspected rather than silently replaced.

Canonical Git and StateStore are coordinated through a repository-scoped durable operation ledger.
The Supervisor writes intent before apply, persists verification evidence before commit, and adds
an immutable operation trailer to the commit. A successful state transition then closes the
operation. On restart, HoH may replay only the missing idempotent state write; it does not replay an
agent/model turn. Uncommitted patches and contradictory evidence always require a guarded operator
action, and new canonical mutations remain blocked until resolution.

Success criteria:

- patch applies cleanly
- changed files match task scope
- no forbidden files are changed

## 6. Verification

The supervisor runs deterministic checks. The verifier independently checks the result.

Success criteria:

- tests pass
- acceptance metrics pass
- docs are present when behavior changes
- deployment impact is documented
- verifier/reviewer evidence includes pre-apply findings, post-apply findings, command results,
  and the supervisor-owned commit id
- an external review bundle is bound to the exact run, full commit, patch SHA-256, scope, and command
  evidence
- the external verifier decision attests to the bundle, commit, and patch digest and grants no
  commit or merge authority
- in required three-head mode, Logic, Worker, and Critic manifests are distinct; only Critic
  approval closes the task
- when Critic is disabled by the customer, Supervisor closes only after deterministic verification
- a rejection contains a correction brief, and only the Supervisor can confirm it for bounded
  rework without changing the original task scope
- escalation stops execution and asks the customer; attempt exhaustion cannot loop indefinitely

## 7. Final project audit

Before deployment or handoff, the supervisor audits the entire repository. The audit is not limited
to the last accepted task.

### Checked automatically by `llm-harness audit`

Run it with explicit verification commands, for example
`llm-harness audit --check "<test command>" --markdown`. It checks:

- the commands you pass with `--check` all exit `0`
- the git worktree is clean and no generated artifact is tracked
- `README.md` and `docs/` exist, and every file under `docs/` is Markdown
- `docs/architecture.md` exists, ships a PlantUML C4 component diagram and sequence diagram,
  references their committed renders, and the recorded source digests still match, so an
  edited diagram that was never re-rendered blocks instead of shipping
- if lifecycle artifacts were materialized: `docs/hoh/project-brief.md`, `docs/hoh/roadmap.md`,
  and valid, unique task files under `tasks/hoh/`
- no blocking markers (TODO, FIXME, placeholder text) in tracked files <!-- hoh-audit: ignore-line -->
- the configured language analyzers report `passed`, and configured entry points define strict
  unused-file reachability

### Operator review the audit cannot perform

These are real requirements, but no command verifies them. They are yours to check and to state
in the handoff:

- run the builds, type checks, linters, and documented manual validation steps that are not
  wired into `--check`
- read README and setup instructions against the commands that actually exist
- confirm the architecture documentation still matches the implementation, not merely that the
  diagrams are present
- review configuration, secrets handling, runtime, deployment, and operating instructions
- confirm every acceptance criterion has evidence behind it
- confirm every verifier finding is resolved, or explicitly accepted as a known limitation
- export the redacted interaction journal and read the message, tool, decision, notification,
  artifact, and state-transition evidence, checking that no raw secret is present

Success criteria:

- audit evidence is recorded
- language analyzer evidence records `passed`, `findings`, `unsupported`, or `unavailable`;
  configured entry points define strict unused-file reachability, and HoH never deletes findings
- automated handoff stores the Markdown audit report and JSONL index under the HoH state root
- `llm-harness audit` exits with code `0`
- automated queue handoff, when used, runs `queue-run-loop --final-audit` with explicit
  `--final-check` commands before notifying readiness
- `workflow-smoke` passes in ready mode before packaging changes that affect the daily workflow
- `workflow-smoke --mode blocker` passes before packaging changes that affect blocker handling or
  customer notification
- `protocol-conformance --distribution-root . --json` passes before packaging changes that affect
  supervisor/verifier integration, protocol assets, review evidence, or readiness gates
- `three-head-conformance --json` passes before packaging changes that affect critic decisions,
  rework, escalation, role separation, or attempt bounds
- mandatory documentation and diagrams exist
- no stale documentation is found
- no blocking TODOs, placeholders, unused artifacts, or unresolved findings remain
- final commit is created by the supervisor after the audit passes

## 8. Deploy / handoff

The user does not receive a final solution if:

- components are unverified
- tests are missing or failing
- documentation is incomplete
- deployment steps are unknown
- required user action is unresolved
- final project audit failed
- final supervisor-owned commit is missing
- an explicitly exported external review is pending or rejected
- the release manifest reports a dirty source tree, a skipped package gate, or a ZIP digest that
  does not match the delivered archive

Success criteria:

- final report states what was found, changed, verified, and what remains uncertain
- Telegram readiness notification is sent to the user
- all explicitly exported review bundles have a latest valid external approval, or their rejection
  has been superseded by a new run and approved bundle for the same work item
- the delivered ZIP and sidecar release manifest are kept together and independently hash-checked
- target-platform claims name only the platforms on which the finished artifact was executed
