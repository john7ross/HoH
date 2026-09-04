# Architecture

[Русский](ARCHITECTURE.ru.md) · **English**

HoH is a harness-over-harness control plane: the Supervisor plans and owns
integration, the low-trust Worker proposes bounded changes, and the independent
Critic evaluates immutable evidence. The same model is never allowed to plan,
edit, and approve its own result.

## System shape

```text
Customer
   │ goal, clarifications, approval
   ▼
Desktop GUI / Headless API ── Workspace Registry ── Project state
   │                              │
   ├─ Supervisor chat + planner   ├─ queue/history/audit/journal
   ├─ role/driver registry        └─ scheduler + notifications
   └─ install/auth/A2A gateway
          │             │
          ▼             ▼
   Supervisor       Worker attempt ──> deterministic verifier ──> Git Gate
          │                                      │                    │
          └────────────── Critic review <────────┴──── immutable bundle ┘
```

## Boundaries

- **GUI service** owns validated configuration, role profiles, credentials
  injection, and guarded operational commands; the widgets do not edit Git.
- **Protocol/loop engine** owns the state machine, queue transitions, evidence,
  retry/recovery, and final handoff.
- **Driver registry** maps explicit role and driver ids to ACP, process,
  direct-model, or A2A transports. Agent names never select a driver implicitly.
- **Worker adapter** receives only a constrained task and allowed paths. The
  Git Gate rejects a moved attempt `HEAD` and is the only commit owner.
- **Review Gateway** binds the Critic decision to the exact run, commit, patch
  digest, scope, and deterministic checks.
- **A2A Gateway** handles remote Agent Cards, bearer/API-key/OAuth2/OIDC,
  JSON-RPC, SSE, push, cancellation, and bounded fallback polling.
- **State Store** and **Interaction Journal** live outside the canonical
  repository and use separate leases for state and Git operations.

## Main lifecycle

1. Customer configures roles and confirms the Supervisor plan.
2. Scheduler selects dependency-ready tasks by priority and FIFO order.
3. HoH creates an isolated worktree and invokes the selected Worker driver.
4. Deterministic policy and verification gates inspect the returned patch.
5. Critic reviews immutable evidence when enabled.
6. Supervisor applies, stages, and commits only an approved patch.
7. Queue/history, metrics, journal, notifications, and audit evidence are
   written atomically outside the project worktree.

## Deployment views

- Desktop: Tkinter/ttk GUI plus the same structured application service.
- Headless: authenticated HTTP control API; non-loopback binding requires
  operator-provided TLS certificate and key.
- Local agents: ACP stdio or explicit process/direct-model drivers.
- Remote agents: A2A v1 over HTTPS, with loopback HTTP allowed only locally.
- Background operation: Windows Task Scheduler, Linux systemd user service, or
  macOS launchd; all invoke the existing guarded queue loop.

## Source of truth

The complete component and sequence diagrams, protocol contracts, storage rules,
rollback policy, metrics, and module map are maintained in
[`docs/architecture.md`](docs/architecture.md). The Russian companion is
[`docs/architecture.ru.md`](docs/architecture.ru.md).
