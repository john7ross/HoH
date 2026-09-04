from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from .audit import run_project_audit
from .acp_registry import (
    AcpRegistryError,
)
from .conformance import run_worker_conformance
from .agent_matrix import agent_matrix, infer_declared_version, probe_command_version
from .config import AuditConfig, HarnessConfig, load_config
from .coordination import (
    LockContendedError,
    repository_execution_lease,
    state_lease,
)
from .critic_adapters import CriticAdapterError
from .critic_runtime import run_critic_review
from .doctor import run_doctor
from .driver_registry import driver_catalog, resolve_driver
from .domain import WorkItem
from .durable_io import atomic_write_text
from .git_ops import GitError
from .hermes import HermesAcpAdapter, check_hermes_acp
from .lifecycle import ProjectSpecError, load_project_spec, materialize_project_plan
from .journal import InteractionJournal, journal_event_payload, render_journal_markdown
from .operator_decisions import handle_operator_command
from .operations import (
    OperationContext,
    OperationLedger,
    OperationLedgerError,
    ReconciliationBlockedError,
    crash_point,
)
from .protocol import (
    audit_record_payload,
    dump_json,
    harness_run_result_payload,
    project_audit_payload,
    protocol_envelope,
    queue_schedule_payload,
    queue_task_payload,
    rollback_record_payload,
    run_record_payload,
    supervisor_status_payload,
)
from .protocol_conformance import ProtocolConformanceError, run_protocol_conformance
from .runtime import inspect_runtime
from .review_protocol import (
    ReviewProtocolError,
    export_review_bundle,
    import_verifier_decision,
    load_review_bundle,
    review_decision_payload,
)
from .rollback import RollbackError, apply_rollback, plan_rollback
from .role_profiles import (
    DIRECT_CRITIC_MODEL_AGENT,
    DIRECT_SUPERVISOR_MODEL_AGENT,
    RoleProfileError,
    apply_role_profile,
    load_effective_config,
    load_role_profile,
)
from .role_conformance import run_critic_conformance, run_supervisor_conformance
from .role_catalog import build_role_catalog
from .gui_services import (
    authorize_a2a_agent,
    editable_a2a_agents,
    inspect_registry_agent_auth,
    install_registry_agent,
    login_registry_agent,
    registry_agent_statuses,
    test_a2a_agent,
    uninstall_registry_agent,
)
from .scanner import scan_agents, scan_local_models
from .state import HohStateStore, StateStoreError, default_state_root
from .supervisor import Supervisor
from .supervisor_protocol import build_supervisor_status
from .tasks import TaskLoadError, load_work_item
from .telegram import JournaledNotifier, TelegramBotNotifier, build_notifier
from .telegram_polling import poll_operator_commands
from .three_head_conformance import ThreeHeadConformanceError, run_three_head_conformance
from .trust import WorkerTrustPolicy
from .worker_adapters import WorkerAdapterError, create_worker_adapter, with_timeout
from .workflow_smoke import run_supervised_workflow_smoke
from .verifier import PolicyVerifier
from .workers import StubPatchWorker
from .model_providers import (
    ModelProviderError,
    ModelRequest,
    create_model_provider,
    model_endpoint_preflight,
)
from .model_runtime import ModelSemanticVerifier, generate_project_spec
from .metrics import UsageContext, usage_event_payload
from .supervisor_adapters import SupervisorAdapterError, create_supervisor_adapter
from .provider_workers import probe_provider_worker
from .background_scheduler import run_due_projects, scheduled_result_payload
from .desktop_notifications import DesktopNotifier
from .workspace import WorkspaceRegistry, summary_payload, workspace_payload
from .scheduler_service import manage_scheduler_service
from .signed_catalog import (
    SignedCatalogError,
    fetch_signed_catalog,
    import_publisher,
    load_trust_store,
    publisher_trust_path,
)
from .updater import (
    UpdateError,
    check_for_update,
    current_release,
    download_release,
    install_release,
    installation_root,
    rollback_release,
)
from . import __version__
from .headless_server import HeadlessServer, HeadlessServerError
from .portable_package import (
    PortablePackageError,
    build_posix_portable_package,
    git_release_state,
    run_posix_package_gates,
)


def _tkinter_display_error() -> type[BaseException]:
    """The exception Tk raises when no display is available, or a stand-in when Tk is absent."""
    try:
        import _tkinter
    except ModuleNotFoundError:
        return RuntimeError
    return _tkinter.TclError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="llm-harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    gui_parser = subparsers.add_parser("gui", help="Open the native HoH desktop control surface.")
    gui_parser.add_argument("--project-root", type=Path)
    gui_parser.add_argument("--locale", choices=("ru", "en"))
    gui_parser.add_argument("--theme", choices=("light", "dark"))
    gui_parser.add_argument("--smoke", action="store_true", help="Create and close the GUI for release validation.")

    publisher_parser = subparsers.add_parser(
        "publisher", help="Manage explicitly trusted self-signed HoH catalog publishers."
    )
    publisher_parser.add_argument("action", choices=("list", "import"))
    publisher_parser.add_argument("--publisher", type=Path)
    publisher_parser.add_argument("--trust-store", type=Path)
    publisher_parser.add_argument("--json", action="store_true")

    catalog_parser = subparsers.add_parser(
        "catalog", help="Verify and read a signed release or compatibility catalog."
    )
    catalog_parser.add_argument("--source", required=True)
    catalog_parser.add_argument("--kind", choices=("releases", "compatibility"), required=True)
    catalog_parser.add_argument("--trust-store", type=Path)
    catalog_parser.add_argument("--json", action="store_true")

    update_parser = subparsers.add_parser(
        "update", help="Check, download, install, or roll back trusted user-scope releases."
    )
    update_parser.add_argument("action", choices=("status", "check", "download", "install", "rollback"))
    update_parser.add_argument("--source")
    update_parser.add_argument("--trust-store", type=Path)
    update_parser.add_argument("--current-version", default=__version__)
    update_parser.add_argument("--package", type=Path)
    update_parser.add_argument("--output", type=Path)
    update_parser.add_argument("--install-root", type=Path)
    update_parser.add_argument("--json", action="store_true")

    server_parser = subparsers.add_parser(
        "server", help="Run the authenticated headless HTTP control plane."
    )
    server_parser.add_argument("--host", default="127.0.0.1")
    server_parser.add_argument("--port", type=int, default=8765)
    server_parser.add_argument("--token-env", default="HOH_SERVER_TOKEN")
    server_parser.add_argument("--push-token-env", default="HOH_A2A_PUSH_TOKEN")
    server_parser.add_argument("--registry", type=Path)
    server_parser.add_argument("--tls-cert", type=Path)
    server_parser.add_argument("--tls-key", type=Path)

    posix_package_parser = subparsers.add_parser(
        "package-portable", help="Build a tested Linux/macOS source-portable ZIP."
    )
    posix_package_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    posix_package_parser.add_argument("--output-dir", type=Path, default=Path("dist"))

    workspace_parser = subparsers.add_parser(
        "workspace",
        help="Manage the user-scoped multi-project registry, shared queue summary, and schedules.",
    )
    workspace_parser.add_argument(
        "action",
        choices=(
            "list", "summary", "register", "remove", "schedule", "run-due", "notify-test",
            "service-install", "service-uninstall", "service-status",
        ),
    )
    workspace_parser.add_argument("--registry", type=Path)
    workspace_parser.add_argument("--project-root", type=Path)
    workspace_parser.add_argument("--name")
    workspace_parser.add_argument("--project-id")
    workspace_parser.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None)
    workspace_parser.add_argument("--interval-minutes", type=int, default=60)
    workspace_parser.add_argument("--final-audit", action="store_true")
    workspace_parser.add_argument("--notify-windows", action=argparse.BooleanOptionalAction, default=True)
    workspace_parser.add_argument("--notify-telegram", action=argparse.BooleanOptionalAction, default=False)
    workspace_parser.add_argument("--start-immediately", action="store_true")
    workspace_parser.add_argument("--timeout", type=float, default=3600.0)
    workspace_parser.add_argument("--message", default="HoH desktop notifications are ready.")
    workspace_parser.add_argument("--json", action="store_true")

    scan_parser = subparsers.add_parser("scan", help="Detect supported local agents.")
    scan_parser.add_argument("--config", type=Path)

    role_catalog_parser = subparsers.add_parser(
        "role-catalog",
        help="Show available agents/models and the plain-language role questions used during briefing.",
    )
    role_catalog_parser.add_argument("--config", type=Path)
    role_catalog_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    role_catalog_parser.add_argument("--json", action="store_true")
    role_catalog_parser.add_argument(
        "--refresh-registry",
        action="store_true",
        help="Refresh the official ACP Registry cache before listing roles.",
    )

    driver_catalog_parser = subparsers.add_parser(
        "driver-catalog",
        help="Show versioned Worker and Critic drivers available to agent configurations.",
    )
    driver_catalog_parser.add_argument("--role", choices=("supervisor", "worker", "critic"))
    driver_catalog_parser.add_argument("--json", action="store_true")

    agent_parser = subparsers.add_parser(
        "agent-manage",
        help="List, install, remove, inspect login, or sign in to ACP Registry agents.",
    )
    agent_parser.add_argument("action", choices=("list", "install", "uninstall", "auth", "login"))
    agent_parser.add_argument("--agent")
    agent_parser.add_argument("--method", help="ACP auth method id returned by the auth action.")
    agent_parser.add_argument("--refresh", action="store_true")
    agent_parser.add_argument("--json", action="store_true")

    a2a_test_parser = subparsers.add_parser("a2a-test", help="Probe one configured remote A2A v1 agent.")
    a2a_test_parser.add_argument("--config", type=Path, default=Path(".hoh/harness.json"))
    a2a_test_parser.add_argument("--name", required=True)
    a2a_test_parser.add_argument("--json", action="store_true")

    a2a_login_parser = subparsers.add_parser(
        "a2a-login",
        help="Authorize one configured OAuth2/OIDC A2A agent and persist its user-scoped token.",
    )
    a2a_login_parser.add_argument("--config", type=Path, default=Path(".hoh/harness.json"))
    a2a_login_parser.add_argument("--name", required=True)
    a2a_login_parser.add_argument("--json", action="store_true")

    compatibility_parser = subparsers.add_parser(
        "compatibility-matrix",
        help="Show discovered, installed, protocol-conformant, and live-certified role combinations.",
    )
    compatibility_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    compatibility_parser.add_argument("--config", type=Path)
    compatibility_parser.add_argument("--central-catalog")
    compatibility_parser.add_argument("--trust-store", type=Path)
    compatibility_parser.add_argument("--json", action="store_true")

    role_conformance_parser = subparsers.add_parser(
        "role-conformance",
        help="Run one selected role transport live in a disposable Git repository and persist evidence.",
    )
    role_conformance_parser.add_argument("--role", choices=("supervisor", "worker", "critic"), required=True)
    role_conformance_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    role_conformance_parser.add_argument("--config", type=Path)
    role_conformance_parser.add_argument("--agent")
    role_conformance_parser.add_argument("--model")
    role_conformance_parser.add_argument("--agent-version")
    role_conformance_parser.add_argument("--timeout", type=float)
    role_conformance_parser.add_argument("--keep", action="store_true")
    role_conformance_parser.add_argument("--json", action="store_true")

    runtime_parser = subparsers.add_parser("runtime", help="Validate embedded runtime layout.")
    runtime_parser.add_argument("--config", type=Path)
    runtime_parser.add_argument("--project-root", type=Path, default=Path.cwd())

    audit_parser = subparsers.add_parser("audit", help="Run deterministic final project audit.")
    audit_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    audit_parser.add_argument("--config", type=Path, help="Harness TOML/JSON with [audit] analyzer policy.")
    audit_parser.add_argument(
        "--check",
        action="append",
        default=[],
        help="Verification command to run from the project root. Can be provided multiple times.",
    )
    audit_parser.add_argument(
        "--command-timeout",
        type=float,
        default=1800.0,
        help="Seconds each --check command may take. A full test suite is a normal --check.",
    )
    audit_output = audit_parser.add_mutually_exclusive_group()
    audit_output.add_argument("--markdown", action="store_true", help="Print a Markdown audit report.")
    audit_output.add_argument("--json", action="store_true", help="Print a versioned machine-readable JSON envelope.")

    init_parser = subparsers.add_parser("init-project", help="Create local HoH config and env files from templates.")
    init_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    init_parser.add_argument("--config", type=Path, default=Path("harness.toml"))
    init_parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Environment file to create. Defaults to hoh-env.local.ps1 on Windows and "
        "hoh-env.local.sh elsewhere.",
    )
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing local files.")
    init_parser.add_argument("--skip-env", action="store_true", help="Do not create an env file.")

    notify_parser = subparsers.add_parser("notify", help="Send a supervisor notification.")
    notify_parser.add_argument("--config", type=Path)
    notify_parser.add_argument("--message", required=True)

    telegram_poll_parser = subparsers.add_parser("telegram-poll", help="Poll Telegram once for operator commands.")
    telegram_poll_parser.add_argument("--config", type=Path, required=True)
    telegram_poll_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    telegram_poll_parser.add_argument("--state-root", type=Path)
    telegram_poll_parser.add_argument("--limit", type=int, default=100)
    telegram_poll_parser.add_argument("--timeout", type=int, default=0, help="Telegram long-poll timeout in seconds.")
    telegram_poll_parser.add_argument("--stale-minutes", type=float, default=60.0)

    telegram_watch_parser = subparsers.add_parser("telegram-watch", help="Continuously poll Telegram for operator commands.")
    telegram_watch_parser.add_argument("--config", type=Path, required=True)
    telegram_watch_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    telegram_watch_parser.add_argument("--state-root", type=Path)
    telegram_watch_parser.add_argument("--limit", type=int, default=100)
    telegram_watch_parser.add_argument("--timeout", type=int, default=30, help="Telegram long-poll timeout in seconds.")
    telegram_watch_parser.add_argument("--interval-seconds", type=float, default=5.0)
    telegram_watch_parser.add_argument("--iterations", type=int, default=0, help="0 means run until interrupted.")
    telegram_watch_parser.add_argument("--stale-minutes", type=float, default=60.0)

    operator_parser = subparsers.add_parser("operator-command", help="Handle a customer/operator command.")
    operator_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    operator_parser.add_argument("--state-root", type=Path)
    operator_parser.add_argument("--text", required=True, help="Command text, for example '/status' or '/retry task-001'.")
    operator_parser.add_argument("--stale-minutes", type=float, default=60.0)

    supervisor_status_parser = subparsers.add_parser(
        "supervisor-status",
        help="Print read-only supervisor protocol status from queue, history, audit, and operator state.",
    )
    supervisor_status_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    supervisor_status_parser.add_argument("--state-root", type=Path)
    supervisor_status_parser.add_argument("--stale-minutes", type=float, default=60.0)
    supervisor_status_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    lock_status_parser = subparsers.add_parser(
        "lock-status",
        help="Inspect state and canonical repository leases without breaking a live owner.",
    )
    lock_status_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    lock_status_parser.add_argument("--state-root", type=Path)
    lock_status_parser.add_argument("--config", type=Path)
    lock_status_parser.add_argument("--json", action="store_true")

    lock_recover_parser = subparsers.add_parser(
        "lock-recover",
        help="Recover stale lease metadata only after the OS confirms no live owner.",
    )
    lock_recover_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    lock_recover_parser.add_argument("--state-root", type=Path)
    lock_recover_parser.add_argument("--config", type=Path)
    lock_recover_parser.add_argument("--target", choices=("state", "execution"), required=True)
    lock_recover_parser.add_argument("--json", action="store_true")

    reconcile_status_parser = subparsers.add_parser(
        "reconcile-status",
        help="Read-only classification of durable canonical operations after interruption.",
    )
    reconcile_status_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    reconcile_status_parser.add_argument("--json", action="store_true")

    reconcile_apply_parser = subparsers.add_parser(
        "reconcile-apply",
        help="Guardedly finish state, cancel a clean intent, or restore an exact pending patch.",
    )
    reconcile_apply_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    reconcile_apply_parser.add_argument("--operation-id", required=True)
    reconcile_apply_parser.add_argument(
        "--action",
        choices=("complete-state", "finish-ledger", "cancel-intent", "restore-patch"),
        required=True,
    )
    reconcile_apply_parser.add_argument("--config", type=Path)
    reconcile_apply_parser.add_argument("--json", action="store_true")

    review_export_parser = subparsers.add_parser(
        "review-export",
        help="Export immutable task, commit, patch, verification, and authority evidence for an external verifier.",
    )
    review_export_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    review_export_parser.add_argument("--state-root", type=Path)
    review_export_parser.add_argument("--run-id", required=True)
    review_export_parser.add_argument("--output", type=Path)
    review_export_parser.add_argument("--config", type=Path)
    review_export_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    review_import_parser = subparsers.add_parser(
        "review-import",
        help="Validate and persist an immutable external verifier approve/reject decision.",
    )
    review_import_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    review_import_parser.add_argument("--state-root", type=Path)
    review_import_parser.add_argument("--decision", type=Path, required=True)
    review_import_parser.add_argument("--config", type=Path)
    review_import_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    critic_run_parser = subparsers.add_parser(
        "critic-run",
        help="Run the configured Critic against an existing review-pending bundle.",
    )
    critic_run_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    critic_run_parser.add_argument("--state-root", type=Path)
    critic_run_parser.add_argument("--bundle-id")
    critic_run_parser.add_argument("--config", type=Path)
    critic_run_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    review_list_parser = subparsers.add_parser("review-list", help="List exported review bundles and verifier decisions.")
    review_list_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    review_list_parser.add_argument("--state-root", type=Path)
    review_list_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    journal_list_parser = subparsers.add_parser("journal-list", help="Inspect the redacted interaction and tool journal.")
    journal_list_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    journal_list_parser.add_argument("--state-root", type=Path)
    journal_list_parser.add_argument("--task-id")
    journal_list_parser.add_argument("--event-type")
    journal_list_parser.add_argument("--limit", type=int, default=100)
    journal_list_parser.add_argument("--json", action="store_true")

    journal_export_parser = subparsers.add_parser("journal-export", help="Export the redacted journal as JSONL or Markdown.")
    journal_export_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    journal_export_parser.add_argument("--state-root", type=Path)
    journal_export_parser.add_argument("--output", type=Path, required=True)
    journal_export_parser.add_argument("--format", choices=("jsonl", "markdown"), default="markdown")
    journal_export_parser.add_argument("--task-id")
    journal_export_parser.add_argument("--json", action="store_true")

    journal_record_parser = subparsers.add_parser(
        "journal-record",
        help="Append a versioned redacted event from an external Supervisor, Critic, or adapter.",
    )
    journal_record_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    journal_record_parser.add_argument("--state-root", type=Path)
    journal_record_parser.add_argument("--event", type=Path, required=True)
    journal_record_parser.add_argument("--json", action="store_true")

    metrics_summary_parser = subparsers.add_parser(
        "metrics-summary",
        help="Summarize invocation count, tokens, cost, duration, and quality by role/agent/model.",
    )
    metrics_summary_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    metrics_summary_parser.add_argument("--state-root", type=Path)
    metrics_summary_parser.add_argument("--json", action="store_true")

    metrics_list_parser = subparsers.add_parser("metrics-list", help="List immutable usage metric events.")
    metrics_list_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    metrics_list_parser.add_argument("--state-root", type=Path)
    metrics_list_parser.add_argument("--limit", type=int, default=100)
    metrics_list_parser.add_argument("--json", action="store_true")

    review_rework_parser = subparsers.add_parser(
        "review-rework",
        help="Confirm the latest critic correction brief and queue a bounded rework attempt.",
    )
    review_rework_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    review_rework_parser.add_argument("--state-root", type=Path)
    review_rework_parser.add_argument("--decision-id", required=True)
    review_rework_parser.add_argument("--config", type=Path)
    review_rework_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    protocol_conformance_parser = subparsers.add_parser(
        "protocol-conformance",
        help="Run schema, JSON envelope, review bundle, verifier decision, and authority smoke checks.",
    )
    protocol_conformance_parser.add_argument("--distribution-root", type=Path, default=Path.cwd())
    protocol_conformance_parser.add_argument("--keep", action="store_true", help="Keep the disposable repository and state.")
    protocol_conformance_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    three_head_conformance_parser = subparsers.add_parser(
        "three-head-conformance",
        help="Run deterministic approve, reject/rework, and escalation critic-loop scenarios.",
    )
    three_head_conformance_parser.add_argument("--keep", action="store_true", help="Keep the disposable scenario repositories.")
    three_head_conformance_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    project_plan_parser = subparsers.add_parser("project-plan", help="Validate and materialize a project lifecycle spec.")
    project_plan_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    project_plan_parser.add_argument("--state-root", type=Path)
    project_plan_input = project_plan_parser.add_mutually_exclusive_group(required=True)
    project_plan_input.add_argument("--spec", type=Path, help="Use an existing offline JSON project spec.")
    project_plan_input.add_argument("--requirements", type=Path, help="Generate a project spec from a requirements text file.")
    project_plan_parser.add_argument("--config", type=Path)
    project_plan_parser.add_argument("--write", action="store_true", help="Write Markdown docs and atomic task files.")
    project_plan_parser.add_argument("--enqueue", action="store_true", help="Enqueue generated task files after writing them.")
    project_plan_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    doctor_parser = subparsers.add_parser("doctor", help="Validate local HoH environment.")
    doctor_parser.add_argument("--config", type=Path)
    doctor_parser.add_argument("--project-root", type=Path, default=Path.cwd())

    model_smoke_parser = subparsers.add_parser(
        "model-smoke",
        help="Preflight a supervisor/verifier model role; network invocation requires --live.",
    )
    model_smoke_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    model_smoke_parser.add_argument("--config", type=Path)
    model_smoke_parser.add_argument("--role", choices=("supervisor", "verifier"), required=True)
    model_smoke_parser.add_argument("--live", action="store_true")
    model_smoke_parser.add_argument("--json", action="store_true")

    hermes_check_parser = subparsers.add_parser("hermes-check", help="Validate Hermes ACP availability.")
    hermes_check_parser.add_argument("--command", dest="hermes_command", default="hermes")

    hermes_dry_run_parser = subparsers.add_parser("hermes-dry-run", help="Print an isolated-worktree Hermes ACP prompt.")
    hermes_dry_run_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    hermes_dry_run_parser.add_argument("--task-id", default="dry-run")
    hermes_dry_run_parser.add_argument("--title", default="Hermes dry-run task")
    hermes_dry_run_parser.add_argument("--objective", required=True)
    hermes_dry_run_parser.add_argument("--acceptance", action="append", required=True)
    hermes_dry_run_parser.add_argument("--check", action="append", default=[])
    hermes_dry_run_parser.add_argument("--allowed-path", action="append", default=[])
    hermes_dry_run_parser.add_argument("--non-goal", action="append", default=[])

    hermes_smoke_parser = subparsers.add_parser("hermes-smoke", help="Run live Hermes ACP in an isolated temp repo.")
    hermes_smoke_parser.add_argument("--timeout", type=float, default=300.0)
    hermes_smoke_parser.add_argument("--keep", action="store_true", help="Print and keep the temporary repository path.")

    hermes_run_parser = subparsers.add_parser("hermes-run", help="Run a task file through Hermes ACP.")
    hermes_run_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    hermes_run_parser.add_argument("--task", type=Path, required=True)
    hermes_run_parser.add_argument("--timeout", type=float, default=300.0)

    worker_run_parser = subparsers.add_parser("worker-run", help="Run a task file through the configured worker adapter.")
    worker_run_parser.add_argument("--config", type=Path)
    worker_run_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    worker_run_parser.add_argument("--task", type=Path, required=True)
    worker_run_parser.add_argument("--timeout", type=float)

    worker_conformance_parser = subparsers.add_parser(
        "worker-conformance",
        help="Run the configured worker adapter conformance suite in a disposable git repository.",
    )
    worker_conformance_parser.add_argument("--config", type=Path)
    worker_conformance_parser.add_argument("--timeout", type=float)
    worker_conformance_parser.add_argument("--json", action="store_true")

    worker_smoke_parser = subparsers.add_parser(
        "worker-smoke",
        help="Preflight a named provider worker; execute it only with --live.",
    )
    worker_smoke_parser.add_argument("--config", type=Path, required=True)
    worker_smoke_parser.add_argument("--timeout", type=float)
    worker_smoke_parser.add_argument("--live", action="store_true")
    worker_smoke_parser.add_argument("--keep", action="store_true")
    worker_smoke_parser.add_argument("--json", action="store_true")
    worker_conformance_parser.add_argument("--keep", action="store_true", help="Keep and print the temporary repository path.")

    queue_add_parser = subparsers.add_parser("queue-add", help="Add a task file to the persistent queue.")
    queue_add_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    queue_add_parser.add_argument("--task", type=Path, required=True)
    queue_add_parser.add_argument("--state-root", type=Path)
    queue_add_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    queue_list_parser = subparsers.add_parser("queue-list", help="List persistent queued tasks.")
    queue_list_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    queue_list_parser.add_argument("--state-root", type=Path)
    queue_list_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    queue_history_parser = subparsers.add_parser("queue-history", help="List persistent execution history.")
    queue_history_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    queue_history_parser.add_argument("--state-root", type=Path)
    queue_history_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    audit_history_parser = subparsers.add_parser("audit-history", help="List persisted final audit evidence.")
    audit_history_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    audit_history_parser.add_argument("--state-root", type=Path)
    audit_history_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    rollback_plan_parser = subparsers.add_parser(
        "rollback-plan",
        help="Inspect whether an exact recorded HoH task commit can be safely rolled back.",
    )
    rollback_plan_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    rollback_plan_parser.add_argument("--state-root", type=Path)
    rollback_plan_parser.add_argument("--task-id", required=True)
    rollback_plan_parser.add_argument("--expected-commit")
    rollback_plan_parser.add_argument("--json", action="store_true")

    rollback_apply_parser = subparsers.add_parser(
        "rollback-apply",
        help="Verify and apply a supervisor-controlled rollback of an exact recorded HoH commit.",
    )
    rollback_apply_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    rollback_apply_parser.add_argument("--state-root", type=Path)
    rollback_apply_parser.add_argument("--task-id", required=True)
    rollback_apply_parser.add_argument("--expected-commit", required=True)
    rollback_apply_parser.add_argument("--reason", required=True)
    rollback_apply_parser.add_argument(
        "--check",
        action="append",
        required=True,
        help="Post-rollback verification command. Can be provided multiple times.",
    )
    rollback_apply_parser.add_argument("--timeout", type=float, default=120.0)
    rollback_apply_parser.add_argument("--config", type=Path)
    rollback_apply_parser.add_argument("--json", action="store_true")

    rollback_history_parser = subparsers.add_parser(
        "rollback-history",
        help="List append-only rollback evidence.",
    )
    rollback_history_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    rollback_history_parser.add_argument("--state-root", type=Path)
    rollback_history_parser.add_argument("--json", action="store_true")

    queue_retry_parser = subparsers.add_parser("queue-retry", help="Requeue a failed task.")
    queue_retry_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    queue_retry_parser.add_argument("--state-root", type=Path)
    queue_retry_parser.add_argument("--task-id", required=True)
    queue_retry_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    queue_recover_parser = subparsers.add_parser("queue-recover-running", help="Requeue a manually confirmed stuck running task.")
    queue_recover_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    queue_recover_parser.add_argument("--state-root", type=Path)
    queue_recover_parser.add_argument("--task-id", required=True)
    queue_recover_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")
    queue_recover_parser.add_argument(
        "--reason",
        default="Recovered manually after interrupted worker run.",
    )

    queue_escalation_parser = subparsers.add_parser(
        "queue-resolve-escalation",
        help="Return an escalated task to the queue, or close it as failed.",
    )
    queue_escalation_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    queue_escalation_parser.add_argument("--state-root", type=Path)
    queue_escalation_parser.add_argument("--task-id", required=True)
    queue_escalation_parser.add_argument(
        "--resolution",
        choices=("requeue", "fail"),
        required=True,
        help="requeue: queue the task again with a fresh attempt budget. fail: close it as failed.",
    )
    queue_escalation_parser.add_argument(
        "--reason",
        required=True,
        help="Why the operator resolved the escalation this way.",
    )
    queue_escalation_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    queue_stale_parser = subparsers.add_parser("queue-stale", help="Detect stale running queue tasks.")
    queue_stale_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    queue_stale_parser.add_argument("--state-root", type=Path)
    queue_stale_parser.add_argument(
        "--max-age-minutes",
        type=float,
        default=60.0,
        help="Running tasks older than this threshold are stale. Use 0 to disable detection.",
    )
    queue_stale_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    queue_run_parser = subparsers.add_parser("queue-run-next", help="Run the next queued task through the configured worker.")
    queue_run_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    queue_run_parser.add_argument("--state-root", type=Path)
    queue_run_parser.add_argument("--timeout", type=float, default=300.0)
    queue_run_parser.add_argument("--config", type=Path)
    queue_run_parser.add_argument("--json", action="store_true", help="Print a versioned JSON envelope.")

    queue_loop_parser = subparsers.add_parser("queue-run-loop", help="Run queued tasks until empty or blocked.")
    queue_loop_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    queue_loop_parser.add_argument("--state-root", type=Path)
    queue_loop_parser.add_argument("--timeout", type=float, default=300.0)
    queue_loop_parser.add_argument("--max-tasks", type=int, default=0, help="0 means run until no queued tasks remain.")
    queue_loop_parser.add_argument("--config", type=Path)
    queue_loop_parser.add_argument(
        "--parallelism",
        type=int,
        help="Maximum independent tasks prepared concurrently; overrides [scheduler].max_parallel_tasks.",
    )
    queue_loop_parser.add_argument(
        "--stale-minutes",
        type=float,
        default=60.0,
        help="Stop and notify if a running task is older than this threshold. Use 0 to disable.",
    )
    queue_loop_parser.add_argument("--json", action="store_true", help="Print one versioned JSON envelope after the loop stops.")
    queue_loop_parser.add_argument(
        "--final-audit",
        action="store_true",
        help="Run final project audit and notify readiness when the queue becomes empty.",
    )
    queue_loop_parser.add_argument(
        "--final-check",
        action="append",
        default=[],
        help="Verification command for --final-audit. Can be provided multiple times.",
    )

    demo_parser = subparsers.add_parser("demo", help="Run deterministic patch flow in a temp git repo.")
    demo_parser.add_argument("--keep", action="store_true", help="Print and keep the temporary repository path.")

    workflow_smoke_parser = subparsers.add_parser(
        "workflow-smoke",
        help="Run an end-to-end supervised daily workflow smoke scenario in a disposable git repository.",
    )
    workflow_smoke_parser.add_argument("--keep", action="store_true", help="Keep and print the temporary repository path.")
    workflow_smoke_parser.add_argument(
        "--mode",
        choices=("ready", "blocker"),
        default="ready",
        help="ready verifies final audit handoff; blocker verifies customer blocker notification.",
    )

    args = parser.parse_args(argv)
    if args.command == "gui":
        try:
            from .gui import main as gui_main
        except ModuleNotFoundError as exc:
            if exc.name == "tkinter":
                print("ok=False")
                print("error=Tkinter is unavailable. Install the Python Tk package for this operating system.")
                return 1
            raise

        try:
            return gui_main(args.project_root, smoke=args.smoke, locale=args.locale, theme=args.theme)
        except _tkinter_display_error() as exc:
            # A headless Linux session has no display; a raw _tkinter.TclError tells
            # the operator nothing about what to do next.
            print("ok=False")
            print(f"error=The desktop GUI needs a graphical display: {exc}")
            print("next=Use the CLI, or run 'llm-harness server' and drive HoH over HTTP.")
            return 1
    if args.command == "publisher":
        return _publisher_command(args)
    if args.command == "catalog":
        return _catalog_command(args)
    if args.command == "update":
        return _update_command(args)
    if args.command == "server":
        try:
            server = HeadlessServer(
                host=args.host,
                port=args.port,
                token_env=args.token_env,
                push_token_env=args.push_token_env,
                registry_path=args.registry,
                tls_cert=args.tls_cert,
                tls_key=args.tls_key,
            )
        except (HeadlessServerError, OSError, ValueError) as exc:
            print(f"ok=False")
            print(f"error={exc}")
            return 1
        print("ok=True", flush=True)
        print(f"url={server.url}", flush=True)
        print(f"token_env={args.token_env}", flush=True)
        print(f"push_token_env={args.push_token_env}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.close()
        return 0
    if args.command == "package-portable":
        try:
            run_posix_package_gates(args.project_root)
            commit, clean = git_release_state(args.project_root)
            result = build_posix_portable_package(
                args.project_root,
                args.output_dir,
                product_version=__version__,
                source_commit=commit,
                source_clean=clean,
            )
        except (PortablePackageError, OSError, ValueError) as exc:
            print("ok=False")
            print(f"error={exc}")
            return 1
        print("ok=True")
        print(f"package={result.package}")
        print(f"manifest={result.manifest}")
        print(f"sha256={result.sha256}")
        return 0
    if args.command == "scan":
        config = load_config(args.config) if args.config else HarnessConfig()
        for probe in scan_agents(config.agents):
            state = "available" if probe.available else "missing"
            print(f"agent {probe.name}: {state} ({probe.command})")
        for probe in scan_local_models(config.local_models):
            state = "available" if probe.available else "missing"
            print(f"model {probe.name}: {state} ({probe.detail})")
        return 0
    if args.command == "role-catalog":
        return _role_catalog(args.project_root, args.config, args.json, args.refresh_registry)
    if args.command == "driver-catalog":
        return _driver_catalog(args.role, args.json)
    if args.command == "agent-manage":
        return _agent_manage(args.action, args.agent, args.method, args.refresh, args.json)
    if args.command == "a2a-test":
        return _a2a_test(args.config, args.name, args.json)
    if args.command == "a2a-login":
        return _a2a_login(args.config, args.name, args.json)
    if args.command == "compatibility-matrix":
        return _compatibility_matrix(
            args.project_root,
            args.config,
            args.json,
            args.central_catalog,
            args.trust_store,
        )
    if args.command == "role-conformance":
        return _role_conformance(
            args.project_root,
            args.config,
            args.role,
            args.agent,
            args.model,
            args.agent_version,
            args.timeout,
            args.keep,
            args.json,
        )
    if args.command == "workspace":
        return _workspace_command(args)
    if args.command == "runtime":
        config = load_config(args.config) if args.config else HarnessConfig()
        status = inspect_runtime(args.project_root, config.runtime)
        print(f"ok={status.ok}")
        print(f"python={status.python_path}")
        print(f"python_version={status.python_version}")
        print(f"wheels={status.wheels_path}")
        print(f"wheel_count={status.wheel_count}")
        for finding in status.findings:
            print(f"finding={finding}")
        return 0 if status.ok else 1
    if args.command == "audit":
        config = load_config(args.config) if args.config else HarnessConfig()
        report = run_project_audit(
            args.project_root,
            tuple(args.check),
            audit_config=config.audit,
            command_timeout_seconds=args.command_timeout,
        )
        if args.json:
            print(
                dump_json(
                    protocol_envelope(
                        "audit.result",
                        ok=report.ok,
                        data=project_audit_payload(report),
                    )
                ),
                end="",
            )
        elif args.markdown:
            print(report.to_markdown(), end="")
        else:
            print(f"ok={report.ok}")
            print(f"findings={len(report.findings)}")
            print(f"commands={len(report.command_results)}")
            for finding in report.findings:
                location = f" path={finding.path}" if finding.path else ""
                print(f"finding={finding.code}{location} message={finding.message}")
        return 0 if report.ok else 1
    if args.command == "init-project":
        return _init_project(args.project_root, args.config, args.env_file, args.force, args.skip_env)
    if args.command == "notify":
        config = load_config(args.config) if args.config else HarnessConfig()
        notifier = build_notifier(config.telegram)
        if not isinstance(notifier, TelegramBotNotifier):
            # The stub notifier appends to an in-memory list nobody reads. Reporting
            # success for that would tell the operator a message was delivered.
            print("ok=False")
            print("notifier=none")
            print("delivered=False")
            print(
                "error=No notification channel is configured. Enable [telegram] and set "
                "bot_token_env/chat_id_env before using notify."
            )
            return 1
        notifier.notify_user_action_required(args.message)
        print("ok=True")
        print("notifier=telegram")
        print("delivered=True")
        return 0
    if args.command == "telegram-poll":
        return _telegram_poll(
            args.config,
            args.project_root,
            args.state_root,
            args.limit,
            args.timeout,
            args.stale_minutes,
        )
    if args.command == "telegram-watch":
        return _telegram_watch(
            args.config,
            args.project_root,
            args.state_root,
            args.limit,
            args.timeout,
            args.interval_seconds,
            args.iterations,
            args.stale_minutes,
        )
    if args.command == "operator-command":
        return _operator_command(args.project_root, args.state_root, args.text, args.stale_minutes)
    if args.command == "supervisor-status":
        return _supervisor_status(args.project_root, args.state_root, args.stale_minutes, args.json)
    if args.command == "lock-status":
        return _lock_status(args.project_root, args.state_root, args.config, args.json)
    if args.command == "lock-recover":
        return _lock_recover(
            args.project_root,
            args.state_root,
            args.config,
            args.target,
            args.json,
        )
    if args.command == "reconcile-status":
        return _reconcile_status(args.project_root, args.json)
    if args.command == "reconcile-apply":
        return _reconcile_apply(
            args.project_root,
            args.operation_id,
            args.action,
            args.config,
            args.json,
        )
    if args.command == "review-export":
        return _review_export(args.project_root, args.state_root, args.run_id, args.output, args.config, args.json)
    if args.command == "review-import":
        return _review_import(args.project_root, args.state_root, args.decision, args.config, args.json)
    if args.command == "critic-run":
        return _critic_run(args.project_root, args.state_root, args.bundle_id, args.config, args.json)
    if args.command == "review-list":
        return _review_list(args.project_root, args.state_root, args.json)
    if args.command == "journal-list":
        return _journal_list(args.project_root, args.state_root, args.task_id, args.event_type, args.limit, args.json)
    if args.command == "journal-export":
        return _journal_export(args.project_root, args.state_root, args.output, args.format, args.task_id, args.json)
    if args.command == "journal-record":
        return _journal_record(args.project_root, args.state_root, args.event, args.json)
    if args.command == "metrics-summary":
        return _metrics_summary(args.project_root, args.state_root, args.json)
    if args.command == "metrics-list":
        return _metrics_list(args.project_root, args.state_root, args.limit, args.json)
    if args.command == "review-rework":
        return _review_rework(args.project_root, args.state_root, args.decision_id, args.config, args.json)
    if args.command == "protocol-conformance":
        return _protocol_conformance(args.distribution_root, args.keep, args.json)
    if args.command == "three-head-conformance":
        return _three_head_conformance(args.keep, args.json)
    if args.command == "project-plan":
        return _project_plan(
            args.project_root,
            args.state_root,
            args.spec,
            args.requirements,
            args.config,
            args.write,
            args.enqueue,
            args.json,
        )
    if args.command == "doctor":
        config = load_effective_config(args.project_root, args.config)
        report = run_doctor(args.project_root, config)
        print(report.to_text(), end="")
        return 0 if report.ok else 1
    if args.command == "model-smoke":
        return _model_smoke(args.project_root, args.config, args.role, args.live, args.json)
    if args.command == "hermes-check":
        status = check_hermes_acp(args.hermes_command)
        print(f"ok={status.ok}")
        print(f"command={' '.join(status.command)}")
        print(f"return_code={status.return_code}")
        if status.stdout.strip():
            print(f"stdout={status.stdout.strip()}")
        if status.stderr.strip():
            print(f"stderr={status.stderr.strip()}")
        return 0 if status.ok else 1
    if args.command == "hermes-dry-run":
        work_item = WorkItem(
            id=args.task_id,
            title=args.title,
            objective=args.objective,
            acceptance_criteria=tuple(args.acceptance),
            verification_commands=tuple(args.check),
            allowed_paths=tuple(args.allowed_path),
            non_goals=tuple(args.non_goal),
        )
        print(HermesAcpAdapter().build_prompt(args.project_root, work_item))
        return 0
    if args.command == "hermes-smoke":
        return _hermes_smoke(args.keep, args.timeout)
    if args.command == "hermes-run":
        try:
            result = _run_hermes_task(args.project_root, args.task, args.timeout)
        except TaskLoadError as exc:
            print(f"ok=False")
            print(f"error={exc}")
            return 1
        _print_result(result)
        return 0 if result.ok else 1
    if args.command == "worker-run":
        try:
            result = _run_worker_task(args.project_root, args.task, args.config, args.timeout)
        except (TaskLoadError, WorkerAdapterError) as exc:
            print(f"ok=False")
            print(f"error={exc}")
            return 1
        _print_result(result)
        return 0 if result.ok else 1
    if args.command == "worker-conformance":
        return _worker_conformance(args.config, args.timeout, args.keep, args.json)
    if args.command == "worker-smoke":
        return _worker_smoke(
            args.config,
            args.timeout,
            args.live,
            args.keep,
            args.json,
        )
    if args.command == "queue-add":
        return _queue_add(args.project_root, args.task, args.state_root, args.json)
    if args.command == "queue-list":
        return _queue_list(args.project_root, args.state_root, args.json)
    if args.command == "queue-history":
        return _queue_history(args.project_root, args.state_root, args.json)
    if args.command == "audit-history":
        return _audit_history(args.project_root, args.state_root, args.json)
    if args.command == "rollback-plan":
        return _rollback_plan(
            args.project_root,
            args.state_root,
            args.task_id,
            args.expected_commit,
            args.json,
        )
    if args.command == "rollback-apply":
        return _rollback_apply(
            args.project_root,
            args.state_root,
            args.task_id,
            args.expected_commit,
            args.reason,
            tuple(args.check),
            args.timeout,
            args.config,
            args.json,
        )
    if args.command == "rollback-history":
        return _rollback_history(args.project_root, args.state_root, args.json)
    if args.command == "queue-retry":
        return _queue_retry(args.project_root, args.state_root, args.task_id, args.json)
    if args.command == "queue-recover-running":
        return _queue_recover_running(args.project_root, args.state_root, args.task_id, args.reason, args.json)
    if args.command == "queue-resolve-escalation":
        return _queue_resolve_escalation(
            args.project_root,
            args.state_root,
            args.task_id,
            args.resolution,
            args.reason,
            args.json,
        )
    if args.command == "queue-stale":
        return _queue_stale(args.project_root, args.state_root, args.max_age_minutes, args.json)
    if args.command == "queue-run-next":
        return _queue_run_next(args.project_root, args.state_root, args.timeout, args.config, args.json)
    if args.command == "queue-run-loop":
        return _queue_run_loop(
            args.project_root,
            args.state_root,
            args.timeout,
            args.max_tasks,
            args.config,
            args.parallelism,
            args.stale_minutes,
            args.final_audit,
            tuple(args.final_check),
            args.json,
        )
    if args.command == "demo":
        return _demo(args.keep)
    if args.command == "workflow-smoke":
        return _workflow_smoke(args.keep, args.mode)
    return 2


def _demo(keep: bool) -> int:
    if keep:
        repository = Path(tempfile.mkdtemp(prefix="llm-harness-demo-"))
        result = _run_demo(repository)
        print(f"repository={repository}")
    else:
        with tempfile.TemporaryDirectory(prefix="llm-harness-demo-") as tmp:
            result = _run_demo(Path(tmp))

    print(f"ok={result.ok}")
    print(f"commit={result.commit}")
    for command_result in result.command_results:
        print(f"command={command_result.command} return_code={command_result.return_code}")
    return 0 if result.ok else 1


def _workflow_smoke(keep: bool, mode: str) -> int:
    if keep:
        repository = Path(tempfile.mkdtemp(prefix="llm-harness-workflow-smoke-"))
        report = run_supervised_workflow_smoke(repository, mode=mode)
    else:
        with tempfile.TemporaryDirectory(prefix="llm-harness-workflow-smoke-") as tmp:
            report = run_supervised_workflow_smoke(Path(tmp) / "repo", mode=mode)

    print(f"ok={report.ok}")
    print(f"workflow_status={report.workflow_status}")
    print(f"repository={report.repository}")
    print(f"state_root={report.state_root}")
    print(f"spec={report.spec_path}")
    print(f"brief={report.brief_path}")
    print(f"roadmap={report.roadmap_path}")
    print(f"tasks={len(report.task_paths)}")
    print(f"queued={','.join(report.queued_task_ids)}")
    print(f"completed={report.completed_tasks}")
    print(f"history={report.history_count}")
    print(f"journal_events={report.journal_events}")
    print(f"commits={len(report.commits)}")
    if report.audit_ready is not None:
        print(f"final_audit_ready={report.audit_ready}")
    if report.audit_report_path is not None:
        print(f"audit_report={report.audit_report_path}")
    if report.blocker_task_id:
        print(f"blocked_task={report.blocker_task_id}")
    if report.blocker_reason:
        print(f"blocker_reason={report.blocker_reason}")
    print(f"notifications={len(report.notifier_messages)}")
    for index, message in enumerate(report.notifier_messages, 1):
        first_line = message.splitlines()[0] if message.splitlines() else ""
        print(f"notification={index} {first_line}")
    return 0 if report.ok else 1


def _run_demo(repository: Path):
    _git(repository, "init")
    _git(repository, "config", "user.email", "demo@example.local")
    _git(repository, "config", "user.name", "LLM Harness Demo")
    (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
    _git(repository, "add", ".gitignore")
    _git(repository, "commit", "-m", "Initial commit")

    work_item = WorkItem(
        id="demo-001",
        title="Create harness demo artifact",
        objective="Demonstrate supervisor-owned patch application and commit.",
        acceptance_criteria=("HARNESS_DEMO.md exists after patch application.",),
        verification_commands=(
            f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'HARNESS_DEMO.md\').exists()"',
        ),
    )
    supervisor = Supervisor(PolicyVerifier(trust_policy=WorkerTrustPolicy()))
    return supervisor.execute_work_item(repository, work_item, StubPatchWorker())


def _hermes_smoke(keep: bool, timeout_seconds: float) -> int:
    if keep:
        repository = Path(tempfile.mkdtemp(prefix="llm-harness-hermes-smoke-"))
        result = _run_hermes_smoke(repository, timeout_seconds)
        print(f"repository={repository}")
    else:
        with tempfile.TemporaryDirectory(prefix="llm-harness-hermes-smoke-") as tmp:
            result = _run_hermes_smoke(Path(tmp), timeout_seconds)

    print(f"ok={result.ok}")
    print(f"commit={result.commit}")
    for command_result in result.command_results:
        print(f"command={command_result.command} return_code={command_result.return_code}")
    return 0 if result.ok else 1


def _worker_conformance(
    config_path: Path | None,
    timeout_seconds: float | None,
    keep: bool,
    json_output: bool = False,
) -> int:
    config = load_config(config_path) if config_path else HarnessConfig()
    if keep:
        repository = Path(tempfile.mkdtemp(prefix="llm-harness-worker-conformance-"))
        report = run_worker_conformance(repository, config, timeout_seconds)
        print(f"repository={repository}")
    else:
        with tempfile.TemporaryDirectory(prefix="llm-harness-worker-conformance-") as tmp:
            report = run_worker_conformance(Path(tmp), config, timeout_seconds)

    if json_output:
        _print_json_envelope(
            "worker.conformance",
            ok=report.ok,
            data=_worker_conformance_payload(report),
        )
    else:
        _print_worker_conformance(report)
    return 0 if report.ok else 1


def _worker_smoke(
    config_path: Path,
    timeout_seconds: float | None,
    live: bool,
    keep: bool,
    json_output: bool,
) -> int:
    try:
        config = load_config(config_path)
    except (OSError, ValueError) as exc:
        if json_output:
            _print_json_envelope("worker.smoke", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    manifest = resolve_driver(config.worker.driver or config.worker.type, "worker")
    if manifest.adapter in {"claude_code", "openclaw"}:
        probe = probe_provider_worker(config.worker)
        available, safe, version, detail = probe.available, probe.safe, probe.version, probe.detail
    elif manifest.adapter == "stub":
        available, safe, version, detail = True, True, "in-process", "test-only driver"
    elif manifest.adapter == "hermes_acp":
        status = check_hermes_acp(config.worker.command)
        available, safe = status.ok, True
        version = status.stdout.strip().splitlines()[0][:200] if status.stdout.strip() else None
        detail = status.stderr.strip() or "ACP availability probe completed"
    else:
        from shutil import which

        executable = which(config.worker.command) if config.worker.command else None
        available, safe, version = executable is not None, True, None
        detail = f"path={executable}" if executable else f"missing command={config.worker.command}"
    data: dict[str, object] = {
        "worker_driver": manifest.driver_id,
        "command": config.worker.command,
        "available": available,
        "safe": safe,
        "version": version,
        "detail": detail,
        "live": live,
    }
    ok = available and safe
    if ok and live:
        if keep:
            repository = Path(tempfile.mkdtemp(prefix="llm-harness-worker-smoke-"))
            report = run_worker_conformance(repository, config, timeout_seconds)
            data["repository"] = str(repository)
        else:
            with tempfile.TemporaryDirectory(prefix="llm-harness-worker-smoke-") as tmp:
                report = run_worker_conformance(Path(tmp), config, timeout_seconds)
        data["conformance"] = _worker_conformance_payload(report)
        ok = report.ok
    if json_output:
        _print_json_envelope("worker.smoke", ok=ok, data=data)
    else:
        print(f"ok={ok}")
        print(f"worker_driver={manifest.driver_id}")
        print(f"available={available}")
        print(f"safe={safe}")
        print(f"version={version or ''}")
        print(f"live={live}")
        print(f"detail={detail}")
        if live and "conformance" in data:
            print(f"conformance_ok={ok}")
    return 0 if ok else 1


def _worker_conformance_payload(report) -> dict[str, object]:
    return {
        "repository": str(report.repository),
        "worker_type": report.worker_type,
        "worker_driver": report.worker_type,
        "adapter": report.adapter_name,
        "ok": report.ok,
        "checks": [
            {"name": item.name, "ok": item.ok, "detail": item.detail}
            for item in report.checks
        ],
        "result": (
            harness_run_result_payload(report.run_result)
            if report.run_result is not None
            else None
        ),
    }


def _print_worker_conformance(report) -> None:
    print(f"ok={report.ok}")
    print(f"worker_type={report.worker_type}")
    print(f"worker_driver={report.worker_type}")
    print(f"adapter={report.adapter_name}")
    for check in report.checks:
        print(f"check={check.name} ok={check.ok} detail={check.detail}")
    if report.run_result is not None:
        print(f"commit={report.run_result.commit}")
        for command_result in report.run_result.command_results:
            print(f"command={command_result.command} return_code={command_result.return_code}")


def _role_catalog(
    repository: Path,
    config_path: Path | None,
    json_output: bool,
    refresh_registry: bool = False,
) -> int:
    try:
        base = load_config(config_path) if config_path else HarnessConfig()
        data = build_role_catalog(repository, base, refresh_registry=refresh_registry)
    except (AcpRegistryError, OSError, ValueError, RoleProfileError) as exc:
        if json_output:
            _print_json_envelope("roles.catalog", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    if json_output:
        _print_json_envelope("roles.catalog", ok=True, data=data)
    else:
        print("ok=True")
        for item in data["agents"]:
            print(
                f"agent={item['name']} available={item['available']} command={item['command']} "
                f"source={item['source']} version={item['version'] or ''} "
                f"supervisor_driver={item['supervisor_driver'] or ''} "
                f"worker_driver={item['worker_driver']} critic_driver={item['critic_driver'] or ''}"
            )
        for question in data["briefing_questions"]:
            print(f"question={question}")
    return 0


def _driver_catalog(role: str | None, json_output: bool) -> int:
    manifests = driver_catalog(role)
    data = {"drivers": [item.to_dict() for item in manifests]}
    if json_output:
        _print_json_envelope("drivers.catalog", ok=True, data=data)
    else:
        print("ok=True")
        for item in manifests:
            print(
                f"driver={item.driver_id} role={item.role} adapter={item.adapter} "
                f"transport={item.task_transport} safety_profile={item.safety_profile}"
            )
    return 0


def _agent_manage(
    action: str,
    agent_id: str | None,
    method_id: str | None,
    refresh: bool,
    json_output: bool,
) -> int:
    try:
        if action == "list":
            records = registry_agent_statuses(refresh=refresh)
            data: object = {
                "agents": [
                    {
                        "id": agent.id,
                        "name": agent.name,
                        "version": agent.version,
                        "description": agent.description,
                        "installed": status.installed,
                        "installable": status.installable,
                        "distribution": status.distribution,
                        "detail": status.detail,
                    }
                    for agent, status in records
                ]
            }
        else:
            if not agent_id:
                raise ValueError(f"agent-manage {action} requires --agent.")
            if action == "install":
                data = {"agent": agent_id, "detail": install_registry_agent(agent_id)}
            elif action == "uninstall":
                data = {"agent": agent_id, "removed": uninstall_registry_agent(agent_id)}
            elif action == "auth":
                inspection = inspect_registry_agent_auth(agent_id)
                data = {
                    "agent": agent_id,
                    "agent_name": inspection.agent_name,
                    "agent_version": inspection.agent_version,
                    "logout_supported": inspection.logout_supported,
                    "methods": [
                        {
                            "id": item.method_id,
                            "name": item.name,
                            "description": item.description,
                            "type": item.method_type,
                            "variables": [
                                {"name": name, "label": label, "secret": secret, "optional": optional}
                                for name, label, secret, optional in item.variables
                            ],
                        }
                        for item in inspection.methods
                    ],
                }
            else:
                result = login_registry_agent(agent_id, method_id)
                data = {"agent": agent_id, "succeeded": result.succeeded, "detail": result.detail}
                if not result.succeeded:
                    raise ValueError(result.detail)
    except (OSError, RuntimeError, ValueError) as exc:
        if json_output:
            _print_json_envelope("agents.manage", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    if json_output:
        _print_json_envelope("agents.manage", ok=True, data=data)
    else:
        print("ok=True")
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _a2a_test(config_path: Path, name: str, json_output: bool) -> int:
    try:
        config = load_config(config_path)
        profile = next(
            (item for item in editable_a2a_agents(config) if item.name.casefold() == name.strip().casefold()),
            None,
        )
        if profile is None:
            raise ValueError(f"Unknown configured A2A agent: {name}")
        available, detail, version = test_a2a_agent(profile)
        data = {"name": profile.name, "available": available, "detail": detail, "version": version}
    except (OSError, RuntimeError, ValueError) as exc:
        if json_output:
            _print_json_envelope("a2a.probe", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    if json_output:
        _print_json_envelope("a2a.probe", ok=available, data=data, error=None if available else detail)
    else:
        print(f"ok={available}")
        print(f"agent={profile.name}")
        print(f"version={version or ''}")
        print(f"detail={detail}")
    return 0 if available else 1


def _a2a_login(config_path: Path, name: str, json_output: bool) -> int:
    try:
        config = load_config(config_path)
        profile = next(
            (item for item in editable_a2a_agents(config) if item.name.casefold() == name.strip().casefold()),
            None,
        )
        if profile is None:
            raise ValueError(f"Unknown configured A2A agent: {name}")

        def show_device_code(authorization) -> None:
            target = authorization.verification_uri_complete or authorization.verification_uri
            print(f"Open: {target}", file=sys.stderr)
            print(f"Code: {authorization.user_code}", file=sys.stderr)

        detail = authorize_a2a_agent(profile, show_device_code)
        data = {"name": profile.name, "authorized": True, "detail": detail}
    except (OSError, RuntimeError, ValueError) as exc:
        if json_output:
            _print_json_envelope("a2a.authorization", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    if json_output:
        _print_json_envelope("a2a.authorization", ok=True, data=data)
    else:
        print("ok=True")
        print(f"agent={profile.name}")
        print(f"detail={detail}")
    return 0


def _compatibility_matrix(
    repository: Path,
    config_path: Path | None,
    json_output: bool,
    central_catalog: str | None = None,
    trust_store: Path | None = None,
) -> int:
    try:
        config = load_effective_config(repository, config_path)
        catalog = build_role_catalog(repository, config)
        for item in catalog.get("agents", []):
            if item.get("available") and not item.get("version") and item.get("command") not in {"npx", "uvx", ""}:
                version_args = tuple(str(value) for value in (item.get("version_args") or ("--version",)))
                item["version"] = probe_command_version(str(item["command"]), version_args)
        data = agent_matrix(catalog)
        if central_catalog:
            central = fetch_signed_catalog(
                central_catalog,
                trust_path=trust_store,
                expected_kind="compatibility",
            )
            records = central.get("records")
            if not isinstance(records, list):
                raise SignedCatalogError("Central compatibility catalog has no records array.")
            data["central"] = {
                "source": central_catalog,
                "generated_at_utc": central.get("generated_at_utc"),
                "records": records,
            }
    except (SignedCatalogError, OSError, ValueError, RoleProfileError) as exc:
        if json_output:
            _print_json_envelope("compatibility.matrix", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    if json_output:
        _print_json_envelope("compatibility.matrix", ok=True, data=data)
    else:
        print("ok=True")
        print(f"platform={data['platform']}")
        print(f"available={data['available_count']}")
        if "central" in data:
            print(f"central_source={data['central']['source']}")
            print(f"central_records={len(data['central']['records'])}")
        for item in data["entries"]:
            print(
                f"agent={item['agent']} role={item['role']} driver={item['driver']} "
                f"version={item['version']} status={item['status']}"
            )
    return 0


def _publisher_command(args: argparse.Namespace) -> int:
    try:
        target = args.trust_store or publisher_trust_path()
        if args.action == "import":
            if args.publisher is None:
                raise SignedCatalogError("--publisher is required for publisher import.")
            key_id = import_publisher(args.publisher, target)
            data = {"action": "import", "key_id": key_id, "trust_store": str(target)}
        else:
            trust = load_trust_store(target)
            data = {"action": "list", "publishers": trust["publishers"], "trust_store": str(target)}
    except (SignedCatalogError, OSError, ValueError) as exc:
        if args.json:
            _print_json_envelope("publisher.result", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    if args.json:
        _print_json_envelope("publisher.result", ok=True, data=data)
    else:
        print("ok=True")
        print(f"trust_store={data['trust_store']}")
        if args.action == "import":
            print(f"key_id={data['key_id']}")
        else:
            for item in data["publishers"]:
                print(f"publisher={item['key_id']} name={item.get('name', '')}")
    return 0


def _catalog_command(args: argparse.Namespace) -> int:
    try:
        payload = fetch_signed_catalog(
            args.source,
            trust_path=args.trust_store,
            expected_kind=args.kind,
        )
        data = {"source": args.source, "kind": args.kind, "payload": payload}
    except (SignedCatalogError, OSError, ValueError) as exc:
        if args.json:
            _print_json_envelope("catalog.result", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    if args.json:
        _print_json_envelope("catalog.result", ok=True, data=data)
    else:
        print("ok=True")
        print(f"kind={args.kind}")
        print(f"generated_at_utc={payload.get('generated_at_utc', '')}")
        entries = payload.get("artifacts") if args.kind == "releases" else payload.get("records")
        print(f"entries={len(entries) if isinstance(entries, list) else 0}")
    return 0


def _update_command(args: argparse.Namespace) -> int:
    try:
        root = (args.install_root or installation_root()).resolve()
        if args.action == "status":
            data = {"action": "status", "install_root": str(root), "current": current_release(root)}
        elif args.action == "rollback":
            version = rollback_release(root)
            data = {"action": "rollback", "install_root": str(root), "current": version}
        else:
            if not args.source:
                raise UpdateError(f"--source is required for update {args.action}.")
            artifact, catalog = check_for_update(
                args.source,
                args.current_version,
                trust_path=args.trust_store,
            )
            if artifact is None:
                data = {
                    "action": args.action,
                    "update_available": False,
                    "current_version": args.current_version,
                    "channel_generated_at_utc": catalog.get("generated_at_utc"),
                }
            elif args.action == "check":
                data = {
                    "action": "check",
                    "update_available": True,
                    "current_version": args.current_version,
                    "version": artifact.version,
                    "platform": artifact.platform,
                    "bytes": artifact.bytes,
                    "sha256": artifact.sha256,
                    "notes_url": artifact.notes_url,
                }
            else:
                package = args.package or args.output or (root / "downloads" / f"HoH-{artifact.version}-{artifact.platform}.zip")
                if args.action == "download":
                    downloaded = download_release(artifact, package)
                    data = {"action": "download", "version": artifact.version, "package": str(downloaded)}
                elif args.action == "install":
                    if not package.exists():
                        download_release(artifact, package)
                    installed = install_release(package, artifact, root=root)
                    data = {"action": "install", "version": artifact.version, "installed": str(installed)}
                else:
                    raise UpdateError(f"Unsupported update action: {args.action}")
    except (UpdateError, SignedCatalogError, OSError, ValueError) as exc:
        if args.json:
            _print_json_envelope("update.result", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    if args.json:
        _print_json_envelope("update.result", ok=True, data=data)
    else:
        print("ok=True")
        for key, value in data.items():
            print(f"{key}={value}")
    return 0


def _role_conformance(
    project_root: Path,
    config_path: Path | None,
    role: str,
    explicit_agent: str | None,
    explicit_model: str | None,
    explicit_version: str | None,
    timeout_seconds: float | None,
    keep: bool,
    json_output: bool,
) -> int:
    try:
        config = load_effective_config(project_root, config_path)
        target = _role_identity(project_root, config, role, explicit_agent, explicit_model, explicit_version)
    except (OSError, ValueError, RoleProfileError) as exc:
        if json_output:
            _print_json_envelope("role.conformance", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1

    def run(repository: Path):
        if role == "worker":
            return run_worker_conformance(repository, config, timeout_seconds)
        if role == "supervisor":
            return run_supervisor_conformance(repository, config)
        return run_critic_conformance(repository, config)

    if keep:
        repository = Path(tempfile.mkdtemp(prefix=f"hoh-{role}-conformance-"))
        report = run(repository)
    else:
        with tempfile.TemporaryDirectory(prefix=f"hoh-{role}-conformance-") as tmp:
            report = run(Path(tmp))
    payload = _role_conformance_payload(report, role)
    payload["identity"] = target
    if json_output:
        _print_json_envelope("role.conformance", ok=report.ok, data=payload)
    else:
        print(f"ok={report.ok}")
        print(f"role={role}")
        print(f"agent={target['agent']}")
        print(f"driver={target['driver']}")
        print(f"model={target['model']}")
        print(f"version={target['version']}")
        for item in report.checks:
            print(f"check={item.name} ok={item.ok} detail={item.detail}")
        if keep:
            print(f"repository={report.repository}")
    return 0 if report.ok else 1


def _is_placeholder_identity(value: str) -> bool:
    """Config keeps names like "external" or "worker-agent" to mean "not stated yet"."""
    normalized = str(value).casefold()
    return normalized in {"external", "agent"} or normalized.startswith(
        ("external-", "configured-")
    ) or normalized.endswith("-agent")


def _role_identity(
    project_root: Path,
    config: HarnessConfig,
    role: str,
    explicit_agent: str | None,
    explicit_model: str | None,
    explicit_version: str | None,
) -> dict[str, str]:
    """Describe which agent and model a role is configured to use.

    Reporting only. It never refuses to run the diagnostic because a version
    could not be inferred: the operator is trying to find out whether the role
    works, and answering "supply --agent-version first" helps nobody.
    """
    profile = load_role_profile(project_root)
    selection = None
    if profile is not None:
        selection = {"supervisor": profile.supervisor, "worker": profile.worker, "critic": profile.critic}[role]
    driver = {
        "supervisor": config.supervisor.driver,
        "worker": config.worker.driver or config.worker.type,
        "critic": config.critic.driver or config.critic.type,
    }[role]
    identity = {
        "supervisor": config.three_head.logic,
        "worker": config.three_head.worker,
        "critic": config.three_head.critic,
    }[role]
    command, args = {
        "supervisor": (config.supervisor.command, config.supervisor.args),
        "worker": (config.worker.command, config.worker.args),
        "critic": (config.critic.command, config.critic.args),
    }[role]

    agent = (explicit_agent or (selection.agent if selection is not None else None) or "").strip()
    if not agent and driver == "model_json":
        agent = {"supervisor": DIRECT_SUPERVISOR_MODEL_AGENT, "critic": DIRECT_CRITIC_MODEL_AGENT}.get(role, "")
    if not agent and not _is_placeholder_identity(identity.provider):
        agent = identity.provider

    model = (explicit_model or (selection.model if selection is not None else None) or "").strip()
    if not model and driver == "model_json":
        model = config.supervisor_model.model if role == "supervisor" else config.verifier_model.model
    if not model and not _is_placeholder_identity(identity.model):
        model = identity.model

    version_args = (
        config.worker.process_profile.version_args
        if role == "worker" and config.worker.process_profile is not None
        else ("--version",)
    )
    version = (
        (explicit_version or "").strip()
        or (infer_declared_version(args) or "")
        or (probe_command_version(command, version_args) or "")
        or "unknown"
    )
    return {"agent": agent or "unknown", "driver": driver, "model": model or "unknown", "version": version}


def _role_conformance_payload(report, role: str) -> dict[str, object]:
    if role == "worker":
        return _worker_conformance_payload(report)
    return {
        "repository": str(report.repository),
        "role": report.role,
        "driver": report.driver,
        "adapter": report.adapter_name,
        "ok": report.ok,
        "checks": [
            {"name": item.name, "ok": item.ok, "detail": item.detail}
            for item in report.checks
        ],
        "evidence": report.evidence,
    }


def _print_result(result) -> None:
    print(f"ok={result.ok}")
    print(f"commit={result.commit}")
    if result.pre_apply.findings:
        for finding in result.pre_apply.findings:
            print(f"pre_apply_finding={finding}")
    if result.post_apply.findings:
        for finding in result.post_apply.findings:
            print(f"post_apply_finding={finding}")
    for command_result in result.command_results:
        print(f"command={command_result.command} return_code={command_result.return_code}")


def _run_hermes_task(repository: Path, task_path: Path, timeout_seconds: float):
    work_item = load_work_item(task_path)
    return _run_hermes_work_item(repository, work_item, timeout_seconds)


def _run_worker_task(repository: Path, task_path: Path, config_path: Path | None, timeout_seconds: float | None):
    work_item = load_work_item(task_path)
    config = load_effective_config(repository, config_path)
    return _run_worker_work_item(repository, work_item, config, timeout_seconds)


def _run_hermes_work_item(repository: Path, work_item: WorkItem, timeout_seconds: float, notifier=None):
    supervisor = Supervisor(PolicyVerifier(trust_policy=WorkerTrustPolicy()), notifier=notifier)
    adapter = HermesAcpAdapter(turn_timeout_seconds=timeout_seconds)
    return supervisor.dispatch_work_item_in_attempt(
        repository,
        work_item,
        adapter.target,
        adapter,
    )


def _run_worker_work_item(
    repository: Path,
    work_item: WorkItem,
    config: HarnessConfig,
    timeout_seconds: float | None,
    notifier=None,
    journal: InteractionJournal | None = None,
    operation_context: OperationContext | None = None,
):
    worker_config = with_timeout(config.worker, timeout_seconds)
    adapter = create_worker_adapter(
        worker_config,
        event_sink=journal.adapter_sink(
            config.three_head.worker.identity,
            work_item.id,
            metric_context=UsageContext(
                role="worker",
                agent=config.three_head.worker.identity,
                provider=config.three_head.worker.provider,
                model=config.three_head.worker.model,
                driver=worker_config.driver,
                task_id=work_item.id,
            ),
            prices=config.metrics.prices,
        ) if journal else None,
    )
    supervisor = Supervisor(
        PolicyVerifier(trust_policy=WorkerTrustPolicy(config.worker_trust_level)),
        notifier=notifier,
        journal=journal,
        semantic_verifier=_semantic_verifier(config),
        coordination=config.coordination,
        command_policy=config.verification.policy(),
    )
    if adapter.isolated_worktree:
        return supervisor.dispatch_work_item_in_attempt(
            repository,
            work_item,
            adapter.target,
            adapter.worker,
            operation_context=operation_context,
        )
    return supervisor.dispatch_work_item(
        repository,
        work_item,
        adapter.target,
        adapter.worker,
        operation_context=operation_context,
    )


def _collect_worker_work_item(
    repository: Path,
    work_item: WorkItem,
    config: HarnessConfig,
    timeout_seconds: float | None,
    notifier=None,
    journal: InteractionJournal | None = None,
):
    worker_config = with_timeout(config.worker, timeout_seconds)
    adapter = create_worker_adapter(
        worker_config,
        event_sink=journal.adapter_sink(
            config.three_head.worker.identity,
            work_item.id,
            metric_context=UsageContext(
                role="worker",
                agent=config.three_head.worker.identity,
                provider=config.three_head.worker.provider,
                model=config.three_head.worker.model,
                driver=worker_config.driver,
                task_id=work_item.id,
            ),
            prices=config.metrics.prices,
        ) if journal else None,
    )
    if not adapter.isolated_worktree:
        raise WorkerAdapterError("Parallel preparation requires an isolated-worktree worker adapter.")
    supervisor = Supervisor(
        PolicyVerifier(trust_policy=WorkerTrustPolicy(config.worker_trust_level)),
        notifier=notifier,
        journal=journal,
        semantic_verifier=_semantic_verifier(config),
        coordination=config.coordination,
        command_policy=config.verification.policy(),
    )
    completion = supervisor.collect_worker_completion_in_attempt(
        repository,
        work_item,
        adapter.target,
        adapter.worker,
    )
    return supervisor, completion


def _semantic_verifier(config: HarnessConfig) -> ModelSemanticVerifier | None:
    if config.verifier_model.provider == "stub":
        return None
    return ModelSemanticVerifier(config.verifier_model)


def _model_smoke(
    repository: Path,
    config_path: Path | None,
    role: str,
    live: bool,
    json_output: bool,
) -> int:
    config = load_effective_config(repository, config_path)
    endpoint = config.supervisor_model if role == "supervisor" else config.verifier_model
    ok, detail = model_endpoint_preflight(endpoint)
    data: dict[str, object] = {
        "role": role,
        "provider": endpoint.provider,
        "model": endpoint.model,
        "live": live,
        "preflight": detail,
    }
    error = None
    if ok and live:
        try:
            response = create_model_provider(endpoint).invoke(
                ModelRequest(
                    instructions="Return the requested JSON health acknowledgement. Do not use tools.",
                    input_text='Return exactly one JSON object with status set to "ok".',
                    output_schema_name="hoh_model_smoke",
                    output_schema={
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["status"],
                        "properties": {"status": {"type": "string", "enum": ["ok"]}},
                    },
                )
            )
            if response.output.get("status") != "ok":
                raise ValueError("Model smoke response did not acknowledge status=ok.")
            data["model_evidence"] = _model_evidence_payload(response.evidence)
        except (ModelProviderError, ValueError) as exc:
            ok = False
            error = str(exc)
    if json_output:
        _print_json_envelope("model.smoke", ok=ok, data=data, error=error)
    else:
        print(f"ok={ok}")
        print(f"role={role}")
        print(f"provider={endpoint.provider}")
        print(f"model={endpoint.model}")
        print(f"live={live}")
        print(f"detail={detail}")
        if error:
            print(f"error={error}")
    return 0 if ok else 1


def _model_evidence_payload(evidence) -> dict[str, object] | None:
    if evidence is None:
        return None
    return {
        "provider": evidence.provider,
        "model": evidence.model,
        "endpoint": evidence.endpoint,
        "request_id": evidence.request_id,
        "attempts": evidence.attempts,
        "latency_ms": evidence.latency_ms,
        "input_tokens": evidence.input_tokens,
        "output_tokens": evidence.output_tokens,
        "total_tokens": evidence.total_tokens,
        "request_sha256": evidence.request_sha256,
        "response_sha256": evidence.response_sha256,
    }


def _operator_command(repository: Path, state_root: Path | None, text: str, stale_minutes: float) -> int:
    store = _state_store(repository, state_root)
    try:
        result = handle_operator_command(
            store,
            text,
            stale_minutes=stale_minutes,
            repository=repository,
        )
    except StateStoreError as exc:
        print("ok=False")
        print(f"state_root={store.root}")
        print("status=lock_or_state_error")
        print(f"error={exc}")
        return 1
    print(f"ok={result.ok}")
    print(f"state_root={store.root}")
    print(f"command={result.command}")
    if result.task_id:
        print(f"task={result.task_id}")
    print(f"should_continue={result.should_continue}")
    print(f"message={result.message}")
    return 0 if result.ok else 1


def _supervisor_status(repository: Path, state_root: Path | None, stale_minutes: float, json_output: bool) -> int:
    try:
        report = build_supervisor_status(repository, state_root, stale_minutes)
    except StateStoreError as exc:
        if json_output:
            _print_json_envelope(
                "supervisor.status",
                ok=False,
                data={"state_root": str(state_root or default_state_root(repository))},
                error=str(exc),
            )
            return 1
        print("ok=False")
        print(f"state_root={state_root or default_state_root(repository)}")
        print(f"error={exc}")
        return 1

    if json_output:
        _print_json_envelope("supervisor.status", ok=report.ok, data=supervisor_status_payload(report))
        return 0 if report.ok else 1

    print(f"ok={report.ok}")
    print(f"repository={report.repository}")
    print(f"state_root={report.state_root}")
    print(f"queued={report.queued}")
    print(f"ready={report.ready}")
    print(f"waiting={report.waiting}")
    print(f"running={report.running}")
    print(f"done={report.done}")
    print(f"failed={report.failed}")
    print(f"rolled_back={report.rolled_back}")
    print(f"review_pending={report.review_pending}")
    print(f"rework_required={report.rework_required}")
    print(f"escalated={report.escalated}")
    print(f"stale={report.stale}")
    print(f"review_bundles={report.review_bundles}")
    print(f"pending_reviews={report.pending_reviews}")
    print(f"approved_reviews={report.approved_reviews}")
    print(f"rejected_reviews={report.rejected_reviews}")
    print(f"rollback_records={report.rollback_records}")
    print(f"reconciliation_pending={report.reconciliation_pending}")
    if report.stale_task_ids:
        print(f"stale_tasks={','.join(report.stale_task_ids)}")
    if report.latest_run is not None:
        print(
            "latest_run="
            f"{report.latest_run.run_id} task={report.latest_run.work_item_id} "
            f"ok={report.latest_run.ok} commit={report.latest_run.commit or ''} "
            f"error={report.latest_run.error or ''}"
        )
    else:
        print("latest_run=")
    if report.latest_audit is not None:
        print(
            "latest_audit="
            f"{report.latest_audit.audit_id} ok={report.latest_audit.ok} "
            f"findings={report.latest_audit.findings_count} "
            f"checks={report.latest_audit.command_results_count} "
            f"path={report.latest_audit.report_path}"
        )
    else:
        print("latest_audit=")
    if report.latest_operator_event is not None:
        print(
            "latest_operator_event="
            f"{report.latest_operator_event.command} ok={report.latest_operator_event.ok} "
            f"task={report.latest_operator_event.task_id or ''} "
            f"message={report.latest_operator_event.message}"
        )
    else:
        print("latest_operator_event=")
    if report.latest_review is not None:
        print(
            "latest_review="
            f"{report.latest_review.decision_id} bundle={report.latest_review.bundle_id} "
            f"decision={report.latest_review.decision} reviewer={report.latest_review.reviewer_id}"
        )
    else:
        print("latest_review=")
    return 0 if report.ok else 1


def _lock_status(
    repository: Path,
    state_root: Path | None,
    config_path: Path | None,
    json_output: bool,
) -> int:
    config = load_effective_config(repository, config_path)
    root = state_root or default_state_root(repository)
    statuses = (
        state_lease(root, config.coordination).status(),
        repository_execution_lease(repository, config.coordination).status(),
    )
    locks = [_lease_status_payload(status) for status in statuses]
    data = {"state_root": str(root), "locks": locks}
    if json_output:
        _print_json_envelope("coordination.lock_status", ok=True, data=data)
    else:
        print("ok=True")
        print(f"state_root={root}")
        for lock in locks:
            print(
                f"lock={lock['kind']} available={lock['available']} "
                f"stale_owner={lock['stale_owner']} path={lock['lock_path']}"
            )
            if lock["owner"] is not None:
                print(f"owner={json.dumps(lock['owner'], sort_keys=True, ensure_ascii=False)}")
            if lock["metadata_error"] is not None:
                print(f"metadata_error={lock['metadata_error']}")
    return 0


def _lock_recover(
    repository: Path,
    state_root: Path | None,
    config_path: Path | None,
    target: str,
    json_output: bool,
) -> int:
    config = load_effective_config(repository, config_path)
    root = state_root or default_state_root(repository)
    lease = (
        state_lease(root, config.coordination)
        if target == "state"
        else repository_execution_lease(repository, config.coordination)
    )
    try:
        owner = lease.recover(command=f"lock-recover {target}")
    except LockContendedError as exc:
        data = {
            "target": target,
            "lock_path": str(exc.lock_path),
            "owner": exc.owner,
        }
        if json_output:
            _print_json_envelope(
                "coordination.lock_recover",
                ok=False,
                data=data,
                error=str(exc),
            )
        else:
            print("ok=False")
            print(f"target={target}")
            print(f"lock_path={exc.lock_path}")
            print(f"owner={json.dumps(exc.owner, sort_keys=True, ensure_ascii=False)}")
            print(f"error={exc}")
        return 1
    data = {
        "target": target,
        "lock_path": str(lease.lock_path),
        "recovered_from": owner.get("recovered_from"),
    }
    if json_output:
        _print_json_envelope("coordination.lock_recover", ok=True, data=data)
    else:
        print("ok=True")
        print(f"target={target}")
        print(f"lock_path={lease.lock_path}")
        print(
            "recovered_from="
            + json.dumps(owner.get("recovered_from"), sort_keys=True, ensure_ascii=False)
        )
    return 0


def _lease_status_payload(status) -> dict[str, object]:
    return {
        "kind": (
            status.owner.get("kind")
            if status.owner is not None and status.owner.get("kind")
            else status.lock_path.stem
        ),
        "lock_path": str(status.lock_path),
        "metadata_path": str(status.metadata_path),
        "available": status.available,
        "stale_owner": status.stale_owner,
        "owner": status.owner,
        "metadata_error": status.metadata_error,
    }


def _reconciliation_payload(report) -> dict[str, object]:
    return {
        "repository": str(report.repository),
        "blocking": len(report.blocking),
        "operations": [
            {
                "operation_id": item.operation_id,
                "action": item.action,
                "classification": item.classification,
                "safe_action": item.safe_action,
                "detail": item.detail,
                "terminal": item.terminal,
            }
            for item in report.items
        ],
    }


def _reconcile_status(repository: Path, json_output: bool) -> int:
    report = OperationLedger(repository).reconcile(auto_complete_state=False)
    data = _reconciliation_payload(report)
    if json_output:
        _print_json_envelope("reconciliation.status", ok=report.ok, data=data)
    else:
        print(f"ok={report.ok}")
        print(f"blocking={len(report.blocking)}")
        for item in report.items:
            print(
                f"operation={item.operation_id} action={item.action} "
                f"classification={item.classification} safe_action={item.safe_action or ''}"
            )
    return 0 if report.ok else 1


def _reconcile_apply(
    repository: Path,
    operation_id: str,
    action: str,
    config_path: Path | None,
    json_output: bool,
) -> int:
    config = load_effective_config(repository, config_path)
    lease = repository_execution_lease(repository, config.coordination)
    try:
        with lease.hold(
            f"reconciliation.apply:{operation_id}",
            command=f"reconcile-apply {operation_id} {action}",
        ):
            item = OperationLedger(repository).guarded_resolve(operation_id, action)
    except (OperationLedgerError, LockContendedError, GitError, StateStoreError) as exc:
        if json_output:
            _print_json_envelope("reconciliation.apply", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    data = {
        "operation_id": item.operation_id,
        "action": item.action,
        "classification": item.classification,
        "terminal": item.terminal,
    }
    if json_output:
        _print_json_envelope("reconciliation.apply", ok=True, data=data)
    else:
        print("ok=True")
        print(f"operation_id={item.operation_id}")
        print(f"status={item.classification}")
    return 0


def _review_export(
    repository: Path,
    state_root: Path | None,
    run_id: str,
    output_path: Path | None,
    config_path: Path | None,
    json_output: bool,
) -> int:
    config = load_effective_config(repository, config_path)
    store = _state_store(repository, state_root, config.coordination)
    try:
        exported = export_review_bundle(
            repository,
            store,
            run_id,
            output_path,
            three_head=config.three_head if config.three_head.required else None,
        )
    except (LockContendedError, ReviewProtocolError, StateStoreError) as exc:
        if json_output:
            _print_json_envelope(
                "verifier.review_bundle.exported",
                ok=False,
                data={"state_root": str(store.root), "run_id": run_id},
                error=str(exc),
            )
        else:
            print("ok=False")
            print(f"state_root={store.root}")
            print(f"error={exc}")
        return 1
    data = {
        "state_root": str(store.root),
        "bundle_id": exported.record.bundle_id,
        "run_id": exported.record.run_id,
        "work_item_id": exported.record.work_item_id,
        "commit": exported.record.commit,
        "bundle_sha256": exported.record.bundle_sha256,
        "bundle_path": str(exported.output_path),
    }
    if json_output:
        _print_json_envelope("verifier.review_bundle.exported", ok=True, data=data)
    else:
        print("ok=True")
        for key, value in data.items():
            print(f"{key}={value}")
    return 0


def _review_import(
    repository: Path,
    state_root: Path | None,
    decision_path: Path,
    config_path: Path | None,
    json_output: bool,
) -> int:
    config = load_effective_config(repository, config_path)
    store = _state_store(repository, state_root, config.coordination)
    try:
        record = import_verifier_decision(repository, store, decision_path)
    except (ReviewProtocolError, StateStoreError) as exc:
        if json_output:
            _print_json_envelope(
                "verifier.decision.imported",
                ok=False,
                data={"state_root": str(store.root), "decision_path": str(decision_path)},
                error=str(exc),
            )
        else:
            print("ok=False")
            print(f"state_root={store.root}")
            print(f"error={exc}")
        return 1
    accepted = record.decision == "approve"
    task = next((item for item in store.list_tasks() if item.work_item.id == record.work_item_id), None)
    data = {
        "state_root": str(store.root),
        **review_decision_payload(record),
        "lifecycle_status": task.status if task is not None else None,
    }
    if record.decision == "escalate":
        try:
            notifier = JournaledNotifier(build_notifier(config.telegram), store.journal)
            request = record.escalation
            details = [
                "HoH critic escalated a task for customer decision.",
                f"Task: {record.work_item_id}",
                f"Reason: {request.reason if request else record.summary}",
                f"Question: {request.question if request else record.summary}",
                "Options:",
            ]
            for option in request.options if request else ("реши сам", "stop", "свой"):
                details.append(f"- {option}")
            details.append(
                "Whatever you choose, the task stays escalated until an operator runs: "
                f"queue-resolve-escalation --task-id {record.work_item_id} "
                "--resolution requeue|fail --reason <why>"
            )
            notifier.notify_user_action_required("\n".join(details))
        except Exception as exc:
            data["notification_error"] = str(exc)
    if json_output:
        _print_json_envelope("verifier.decision.imported", ok=accepted, data=data)
    else:
        print(f"ok={accepted}")
        print(f"state_root={store.root}")
        print(f"decision_id={record.decision_id}")
        print(f"bundle_id={record.bundle_id}")
        print(f"work_item_id={record.work_item_id}")
        print(f"commit={record.commit}")
        print(f"decision={record.decision}")
        print(f"findings={len(record.findings)}")
    return 0 if accepted else 1


def _critic_run(
    repository: Path,
    state_root: Path | None,
    bundle_id: str | None,
    config_path: Path | None,
    json_output: bool,
) -> int:
    config = load_effective_config(repository, config_path)
    store = _state_store(repository, state_root, config.coordination)
    selected_bundle_id = bundle_id
    if selected_bundle_id is None:
        pending = [
            task
            for task in store.list_tasks()
            if task.status == "review_pending" and task.pending_review_bundle_id
        ]
        if len(pending) != 1:
            error = f"Expected exactly one review-pending task; found {len(pending)}."
            if json_output:
                _print_json_envelope(
                    "critic.run",
                    ok=False,
                    data={"state_root": str(store.root)},
                    error=error,
                )
            else:
                print("ok=False")
                print(f"state_root={store.root}")
                print(f"error={error}")
            return 1
        selected_bundle_id = pending[0].pending_review_bundle_id

    try:
        result = run_critic_review(
            repository,
            store,
            config,
            bundle_id=selected_bundle_id,
        )
    except (CriticAdapterError, StateStoreError) as exc:
        if json_output:
            _print_json_envelope(
                "critic.run",
                ok=False,
                data={"state_root": str(store.root), "bundle_id": selected_bundle_id},
                error=str(exc),
            )
        else:
            print("ok=False")
            print(f"state_root={store.root}")
            print(f"bundle_id={selected_bundle_id}")
            print(f"error={exc}")
        return 1

    accepted = result.decision.decision == "approve"
    data = {
        "state_root": str(store.root),
        **review_decision_payload(result.decision),
        "decision_path": str(result.decision_path),
        "lifecycle_status": result.task_status,
    }
    if json_output:
        _print_json_envelope("critic.run", ok=accepted, data=data)
    else:
        print(f"ok={accepted}")
        print(f"state_root={store.root}")
        print(f"bundle_id={result.decision.bundle_id}")
        print(f"decision_id={result.decision.decision_id}")
        print(f"decision={result.decision.decision}")
        print(f"status={result.task_status}")
        print(f"decision_path={result.decision_path}")
    return 0 if accepted else 1


def _review_rework(
    repository: Path,
    state_root: Path | None,
    decision_id: str,
    config_path: Path | None,
    json_output: bool,
) -> int:
    config = load_effective_config(repository, config_path)
    store = _state_store(repository, state_root, config.coordination)
    try:
        decision = next(
            (item for item in store.review_decisions() if item.decision_id == decision_id),
            None,
        )
        if decision is None:
            raise ReviewProtocolError(f"Unknown review decision: {decision_id}")
        if decision.protocol_version != "2.0" or decision.decision != "reject":
            raise ReviewProtocolError("Only a protocol v2 critic rejection can be confirmed for rework.")
        if decision.correction_brief is None:
            raise ReviewProtocolError("Critic rejection has no correction brief.")
        bundle = load_review_bundle(store, decision.bundle_id)
        max_attempts = int(bundle["three_head"]["max_attempts"])
        task = store.confirm_rework(
            decision.work_item_id,
            decision.decision_id,
            decision.correction_brief.instructions,
            max_attempts,
        )
    except (ReviewProtocolError, StateStoreError, KeyError, TypeError, ValueError) as exc:
        if json_output:
            _print_json_envelope(
                "critic.rework.confirmed",
                ok=False,
                data={"state_root": str(store.root), "decision_id": decision_id},
                error=str(exc),
            )
        else:
            print("ok=False")
            print(f"state_root={store.root}")
            print(f"error={exc}")
        return 1
    data = {
        "state_root": str(store.root),
        "decision_id": decision_id,
        "task": queue_task_payload(task),
        "max_attempts": max_attempts,
    }
    if task.status == "escalated":
        try:
            notifier = JournaledNotifier(build_notifier(config.telegram), store.journal)
            notifier.notify_user_action_required(
                "HoH rework attempt limit reached.\n"
                f"Task: {task.work_item.id}\n"
                f"Reason: {task.last_error}\n"
                "Resolve it with: queue-resolve-escalation --task-id "
                f"{task.work_item.id} --resolution requeue|fail --reason <why>"
            )
        except Exception as exc:
            data["notification_error"] = str(exc)
    ok = task.status == "queued"
    if json_output:
        _print_json_envelope("critic.rework.confirmed", ok=ok, data=data)
    else:
        print(f"ok={ok}")
        print(f"state_root={store.root}")
        print(f"decision_id={decision_id}")
        print(f"task_id={task.work_item.id}")
        print(f"status={task.status}")
        print(f"attempts={task.attempts}")
        print(f"max_attempts={max_attempts}")
        if "notification_error" in data:
            print(f"notification_error={data['notification_error']}")
    return 0 if ok else 1


def _review_list(repository: Path, state_root: Path | None, json_output: bool) -> int:
    store = _state_store(repository, state_root)
    bundles = store.review_bundles()
    decisions = store.review_decisions()
    bundle_data = [
        {
            "bundle_id": item.bundle_id,
            "created_at_utc": item.created_at_utc,
            "run_id": item.run_id,
            "work_item_id": item.work_item_id,
            "commit": item.commit,
            "bundle_sha256": item.bundle_sha256,
            "bundle_path": str(item.bundle_path),
        }
        for item in bundles
    ]
    decision_data = [review_decision_payload(item) for item in decisions]
    if json_output:
        _print_json_envelope(
            "verifier.review_evidence",
            ok=True,
            data={"state_root": str(store.root), "bundles": bundle_data, "decisions": decision_data},
        )
        return 0
    print("ok=True")
    print(f"state_root={store.root}")
    print(f"bundles={len(bundles)}")
    print(f"decisions={len(decisions)}")
    for item in bundles:
        print(
            f"bundle={item.bundle_id} run={item.run_id} task={item.work_item_id} "
            f"commit={item.commit} sha256={item.bundle_sha256} path={item.bundle_path}"
        )
    for item in decisions:
        print(
            f"decision={item.decision_id} bundle={item.bundle_id} result={item.decision} "
            f"reviewer={item.reviewer_id} findings={len(item.findings)}"
        )
    return 0


def _journal_list(
    repository: Path,
    state_root: Path | None,
    task_id: str | None,
    event_type: str | None,
    limit: int,
    json_output: bool,
) -> int:
    store = _state_store(repository, state_root)
    events = _filtered_journal_events(store.journal, task_id, event_type, limit)
    payload = [journal_event_payload(item) for item in events]
    if json_output:
        _print_json_envelope(
            "journal.events",
            ok=True,
            data={"state_root": str(store.root), "count": len(payload), "events": payload},
        )
    else:
        print("ok=True")
        print(f"state_root={store.root}")
        print(f"events={len(payload)}")
        for item in events:
            print(
                f"event={item.created_at_utc} type={item.event_type} actor={item.actor} "
                f"recipient={item.recipient or ''} action={item.action} task={item.task_id or ''}"
            )
    return 0


def _journal_record(
    repository: Path,
    state_root: Path | None,
    event_path: Path,
    json_output: bool,
) -> int:
    store = _state_store(repository, state_root)
    try:
        payload = json.loads(event_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Journal ingress file must contain an object.")
        event = store.journal.record_payload(payload)
    except (LockContendedError, OSError, json.JSONDecodeError, ValueError) as exc:
        if json_output:
            _print_json_envelope("journal.recorded", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    data = {"state_root": str(store.root), "event": journal_event_payload(event)}
    if json_output:
        _print_json_envelope("journal.recorded", ok=True, data=data)
    else:
        print("ok=True")
        print(f"state_root={store.root}")
        print(f"event_id={event.event_id}")
    return 0


def _metrics_summary(repository: Path, state_root: Path | None, json_output: bool) -> int:
    store = _state_store(repository, state_root)
    summary = store.metrics.summary()
    data = {"state_root": str(store.root), **summary}
    if json_output:
        _print_json_envelope("metrics.summary", ok=True, data=data)
        return 0
    print("ok=True")
    print(f"state_root={store.root}")
    for item in summary["groups"]:
        print(
            f"role={item['role']} agent={item['agent']} model={item['model']} "
            f"invocations={item['invocations']} success={item['successful_invocations']} "
            f"tokens={item['total_tokens']} duration_ms={item['duration_ms']} "
            f"quality={item['quality_score']} costs={json.dumps(item['costs'], sort_keys=True)}"
        )
    return 0


def _workspace_command(args: argparse.Namespace) -> int:
    registry = WorkspaceRegistry(args.registry)
    try:
        if args.action == "register":
            if args.project_root is None:
                raise ValueError("workspace register requires --project-root.")
            project = registry.register(args.project_root, args.name)
            data: dict[str, object] = {"project": next(
                item for item in workspace_payload(registry.load())["projects"]
                if item["project_id"] == project.project_id
            )}
            message_type = "workspace.registered"
        elif args.action == "remove":
            if not args.project_id:
                raise ValueError("workspace remove requires --project-id.")
            project = registry.remove(args.project_id)
            data = {"project_id": project.project_id, "root": project.root}
            message_type = "workspace.removed"
        elif args.action == "schedule":
            if not args.project_id:
                raise ValueError("workspace schedule requires --project-id.")
            project = registry.configure_schedule(
                args.project_id,
                enabled=True if args.enabled is None else args.enabled,
                interval_minutes=args.interval_minutes,
                final_audit=args.final_audit,
                notify_windows=args.notify_windows,
                notify_telegram=args.notify_telegram,
                start_immediately=args.start_immediately,
            )
            data = {"project": next(
                item for item in workspace_payload(registry.load())["projects"]
                if item["project_id"] == project.project_id
            )}
            message_type = "workspace.scheduled"
        elif args.action == "summary":
            summaries = registry.summaries()
            data = {
                "registry": str(registry.path),
                "count": len(summaries),
                "projects": [summary_payload(item) for item in summaries],
            }
            message_type = "workspace.summary"
        elif args.action == "run-due":
            results = run_due_projects(
                registry,
                timeout_seconds=args.timeout,
                desktop_notifier=DesktopNotifier(),
            )
            data = {
                "registry": str(registry.path),
                "count": len(results),
                "results": [scheduled_result_payload(item) for item in results],
            }
            message_type = "workspace.run_due"
            ok = all(item.ok for item in results)
            if args.json:
                _print_json_envelope(message_type, ok=ok, data=data)
            else:
                print(f"ok={ok}")
                print(f"registry={registry.path}")
                for item in results:
                    print(
                        f"project={item.project_id} name={item.name} status={item.status} "
                        f"completed={item.completed} ok={item.ok}"
                    )
            return 0 if ok else 1
        elif args.action == "notify-test":
            record = DesktopNotifier().notify("HoH", args.message)
            data = dict(record.__dict__)
            message_type = "workspace.notification_test"
            if args.json:
                _print_json_envelope(message_type, ok=record.delivered, data=data)
            else:
                print(f"ok={record.delivered}")
                print(f"platform={record.platform}")
                print(f"notification_id={record.notification_id}")
                if record.error:
                    print(f"error={record.error}")
            return 0 if record.delivered else 1
        elif args.action.startswith("service-"):
            service_action = args.action.removeprefix("service-")
            result = manage_scheduler_service(
                service_action,
                every_minutes=args.interval_minutes,
                run_now=args.start_immediately,
            )
            data = {
                "action": result.action,
                "command": list(result.command),
                "return_code": result.return_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
            message_type = "workspace.scheduler_service"
            if args.json:
                _print_json_envelope(message_type, ok=result.ok, data=data)
            else:
                print(f"ok={result.ok}")
                print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
                if result.stderr:
                    print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
            return 0 if result.ok else 1
        else:
            snapshot = registry.load()
            data = {"registry": str(registry.path), **workspace_payload(snapshot)}
            message_type = "workspace.projects"
    except (OSError, RuntimeError, ValueError) as exc:
        if args.json:
            _print_json_envelope("workspace.error", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    if args.json:
        _print_json_envelope(message_type, ok=True, data=data)
    else:
        print("ok=True")
        print(f"registry={registry.path}")
        for key, value in data.items():
            if key != "registry":
                print(f"{key}={json.dumps(value, ensure_ascii=False)}")
    return 0


def _metrics_list(
    repository: Path,
    state_root: Path | None,
    limit: int,
    json_output: bool,
) -> int:
    if limit < 1:
        if json_output:
            _print_json_envelope("metrics.events", ok=False, error="--limit must be at least 1.")
        else:
            print("ok=False")
            print("error=--limit must be at least 1.")
        return 1
    store = _state_store(repository, state_root)
    events = store.metrics.events()[-limit:]
    data = {
        "state_root": str(store.root),
        "count": len(events),
        "events": [usage_event_payload(item) for item in events],
    }
    if json_output:
        _print_json_envelope("metrics.events", ok=True, data=data)
    else:
        print("ok=True")
        print(f"state_root={store.root}")
        for item in events:
            print(
                f"event={item.created_at_utc} kind={item.event_kind} role={item.role} "
                f"agent={item.agent} model={item.model} tokens={item.total_tokens or ''} "
                f"cost={item.cost_amount or ''}{item.cost_currency or ''} outcome={item.outcome}"
            )
    return 0


def _journal_export(
    repository: Path,
    state_root: Path | None,
    output: Path,
    output_format: str,
    task_id: str | None,
    json_output: bool,
) -> int:
    store = _state_store(repository, state_root)
    events = _filtered_journal_events(store.journal, task_id, None, 0)
    destination = output.resolve()
    if output_format == "jsonl":
        text = "".join(json.dumps(journal_event_payload(item), ensure_ascii=False) + "\n" for item in events)
    else:
        text = render_journal_markdown(events)
    atomic_write_text(destination, text)
    data = {
        "state_root": str(store.root),
        "output": str(destination),
        "format": output_format,
        "events": len(events),
    }
    if json_output:
        _print_json_envelope("journal.exported", ok=True, data=data)
    else:
        print("ok=True")
        for key, value in data.items():
            print(f"{key}={value}")
    return 0


def _filtered_journal_events(
    journal: InteractionJournal,
    task_id: str | None,
    event_type: str | None,
    limit: int,
):
    events = tuple(
        item
        for item in journal.events()
        if (task_id is None or item.task_id == task_id)
        and (event_type is None or item.event_type == event_type)
    )
    return events[-limit:] if limit > 0 else events


def _protocol_conformance(distribution_root: Path, keep: bool, json_output: bool) -> int:
    def run(repository: Path):
        return run_protocol_conformance(distribution_root, repository)

    try:
        if keep:
            base = Path(tempfile.mkdtemp(prefix="hoh-protocol-conformance-"))
            report = run(base / "repo")
        else:
            with tempfile.TemporaryDirectory(prefix="hoh-protocol-conformance-") as tmp:
                report = run(Path(tmp) / "repo")
                return _print_protocol_conformance(report, json_output, kept=False)
    except ProtocolConformanceError as exc:
        if json_output:
            _print_json_envelope("protocol.conformance", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    return _print_protocol_conformance(report, json_output, kept=True)


def _print_protocol_conformance(report, json_output: bool, kept: bool) -> int:
    data = {
        "distribution_root": str(report.distribution_root),
        "repository": str(report.repository) if kept else None,
        "state_root": str(report.state_root) if kept else None,
        "assets_checked": report.assets_checked,
        "run_id": report.run_id,
        "bundle_id": report.bundle_id,
        "bundle_sha256": report.bundle_sha256,
        "decision_id": report.decision_id,
        "commit": report.commit,
        "canonical_git_clean": report.canonical_git_clean,
    }
    if json_output:
        _print_json_envelope("protocol.conformance", ok=report.ok, data=data)
    else:
        print(f"ok={report.ok}")
        for key, value in data.items():
            if value is not None:
                print(f"{key}={value}")
    return 0 if report.ok else 1


def _three_head_conformance(keep: bool, json_output: bool) -> int:
    try:
        if keep:
            root = Path(tempfile.mkdtemp(prefix="hoh-three-head-conformance-"))
            report = run_three_head_conformance(root)
            kept_root: str | None = str(root)
        else:
            with tempfile.TemporaryDirectory(prefix="hoh-three-head-conformance-") as tmp:
                report = run_three_head_conformance(Path(tmp))
                kept_root = None
    except ThreeHeadConformanceError as exc:
        if json_output:
            _print_json_envelope("three_head.conformance", ok=False, error=str(exc))
        else:
            print("ok=False")
            print(f"error={exc}")
        return 1
    data = {
        "root": kept_root,
        "approve": report.approve,
        "reject": report.reject,
        "rework": report.rework,
        "escalate": report.escalate,
        "critic_disabled": report.critic_disabled,
        "git_clean": report.git_clean,
    }
    if json_output:
        _print_json_envelope("three_head.conformance", ok=report.ok, data=data)
    else:
        print(f"ok={report.ok}")
        for key, value in data.items():
            if value is not None:
                print(f"{key}={value}")
    return 0 if report.ok else 1


def _telegram_poll(
    config_path: Path,
    repository: Path,
    state_root: Path | None,
    limit: int,
    poll_timeout_seconds: int,
    stale_minutes: float,
) -> int:
    context = _telegram_poll_context(config_path, repository, state_root, command_name="telegram-poll")
    if context is None:
        return 1
    store, notifier, allowed_user_id, allowed_chat_id = context
    try:
        result = poll_operator_commands(
            store=store,
            bot=notifier,
            allowed_user_id=allowed_user_id,
            allowed_chat_id=allowed_chat_id,
            stale_minutes=stale_minutes,
            limit=limit,
            poll_timeout_seconds=poll_timeout_seconds,
            repository=repository,
        )
    except LockContendedError as exc:
        print("ok=False")
        print(f"state_root={store.root}")
        print("status=lock_contended")
        print(f"owner={json.dumps(exc.owner, sort_keys=True, ensure_ascii=False)}")
        print(f"error={exc}")
        return 1
    print("ok=True")
    print(f"state_root={store.root}")
    print(f"processed={result.processed}")
    print(f"ignored={result.ignored}")
    print(f"next_offset={result.next_offset if result.next_offset is not None else ''}")
    return 0


def _telegram_watch(
    config_path: Path,
    repository: Path,
    state_root: Path | None,
    limit: int,
    poll_timeout_seconds: int,
    interval_seconds: float,
    iterations: int,
    stale_minutes: float,
) -> int:
    if iterations < 0:
        print("ok=False")
        print("error=--iterations must be 0 or greater.")
        return 1
    if interval_seconds < 0:
        print("ok=False")
        print("error=--interval-seconds must be 0 or greater.")
        return 1

    context = _telegram_poll_context(config_path, repository, state_root, command_name="telegram-watch")
    if context is None:
        return 1
    store, notifier, allowed_user_id, allowed_chat_id = context

    completed = 0
    total_processed = 0
    total_ignored = 0
    try:
        while iterations == 0 or completed < iterations:
            result = poll_operator_commands(
                store=store,
                bot=notifier,
                allowed_user_id=allowed_user_id,
                allowed_chat_id=allowed_chat_id,
                stale_minutes=stale_minutes,
                limit=limit,
                poll_timeout_seconds=poll_timeout_seconds,
                repository=repository,
            )
            completed += 1
            total_processed += result.processed
            total_ignored += result.ignored
            print(
                f"iteration={completed} processed={result.processed} ignored={result.ignored} "
                f"next_offset={result.next_offset if result.next_offset is not None else ''}"
            )
            if iterations != 0 and completed >= iterations:
                break
            if interval_seconds:
                time.sleep(interval_seconds)
    except LockContendedError as exc:
        print("ok=False")
        print(f"state_root={store.root}")
        print(f"iterations={completed}")
        print("status=lock_contended")
        print(f"owner={json.dumps(exc.owner, sort_keys=True, ensure_ascii=False)}")
        print(f"error={exc}")
        return 1
    except KeyboardInterrupt:
        print("ok=True")
        print(f"state_root={store.root}")
        print(f"iterations={completed}")
        print(f"processed={total_processed}")
        print(f"ignored={total_ignored}")
        print("status=interrupted")
        return 0

    print("ok=True")
    print(f"state_root={store.root}")
    print(f"iterations={completed}")
    print(f"processed={total_processed}")
    print(f"ignored={total_ignored}")
    print("status=completed" if iterations else "status=stopped")
    return 0


def _telegram_poll_context(
    config_path: Path,
    repository: Path,
    state_root: Path | None,
    command_name: str,
):
    config = load_config(config_path)
    if not config.telegram.enabled:
        print("ok=False")
        print("error=Telegram is disabled in config.")
        return None
    if not config.telegram.user_id_env:
        print("ok=False")
        print("error=telegram.user_id_env is required for inbound operator commands.")
        return None
    try:
        notifier = build_notifier(config.telegram)
    except ValueError as exc:
        print("ok=False")
        print(f"error={exc}")
        return None
    if not isinstance(notifier, TelegramBotNotifier):
        print("ok=False")
        print(f"error={command_name} requires a TelegramBotNotifier.")
        return None
    allowed_user_id = os.environ.get(config.telegram.user_id_env)
    allowed_chat_id = os.environ.get(config.telegram.chat_id_env or "")
    if not allowed_user_id:
        print("ok=False")
        print(f"error=Environment variable {config.telegram.user_id_env} is required for Telegram user id.")
        return None
    if not allowed_chat_id:
        print("ok=False")
        print(f"error=Environment variable {config.telegram.chat_id_env} is required for Telegram chat id.")
        return None

    store = _state_store(repository, state_root)
    return store, notifier, allowed_user_id, allowed_chat_id


def _project_plan(
    repository: Path,
    state_root: Path | None,
    spec_path: Path | None,
    requirements_path: Path | None,
    config_path: Path | None,
    write: bool,
    enqueue: bool,
    json_output: bool,
) -> int:
    if enqueue and not write:
        if json_output:
            _print_json_envelope(
                "project.plan",
                ok=False,
                error="--enqueue requires --write because queue source paths must exist.",
            )
            return 1
        print("ok=False")
        print("error=--enqueue requires --write because queue source paths must exist.")
        return 1
    try:
        config = load_effective_config(repository, config_path)
        store = (
            _state_store(repository, state_root, config.coordination)
            if write or enqueue
            else None
        )
        model_evidence = None
        if spec_path is not None:
            spec = load_project_spec(spec_path)
        else:
            assert requirements_path is not None
            requirements_text = requirements_path.read_text(encoding="utf-8")
            planner = create_supervisor_adapter(
                config.supervisor,
                config.supervisor_model,
                config.three_head.logic,
                event_sink=store.journal.adapter_sink(
                    config.three_head.logic.identity,
                    metric_context=UsageContext(
                        role="supervisor",
                        agent=config.three_head.logic.identity,
                        provider=config.three_head.logic.provider,
                        model=config.three_head.logic.model,
                        driver=config.supervisor.driver,
                    ),
                    prices=config.metrics.prices,
                ) if store is not None else None,
            )
            spec, model_evidence = planner.plan(repository, requirements_text)
        if spec.orchestration is not None:
            apply_role_profile(config, spec.orchestration, spec.id)
        result = materialize_project_plan(repository, spec, write=write, queue=store if enqueue else None)
        if store is not None:
            store.journal.record(
                "interaction",
                "supervisor",
                "project.spec_generated" if model_evidence is not None else "project.spec_submitted",
                recipient="hoh",
                content=(
                    {"model_evidence": _model_evidence_payload(model_evidence)}
                    if model_evidence is not None
                    else json.loads(spec_path.read_text(encoding="utf-8"))
                ),
                metadata={
                    "brief_path": str(result.brief_path),
                    "roadmap_path": str(result.roadmap_path),
                    "role_profile_path": str(result.role_profile_path) if result.role_profile_path else None,
                },
            )
    except (
        ModelProviderError,
        ProjectSpecError,
        StateStoreError,
        SupervisorAdapterError,
        OSError,
        ValueError,
    ) as exc:
        if json_output:
            _print_json_envelope("project.plan", ok=False, error=str(exc))
            return 1
        print("ok=False")
        print(f"error={exc}")
        return 1

    if json_output:
        _print_json_envelope(
            "project.plan",
            ok=True,
            data={
                "project_id": spec.id,
                "title": spec.title,
                "task_count": len(spec.tasks),
                "brief_path": str(result.brief_path),
                "roadmap_path": str(result.roadmap_path),
                "role_profile_path": str(result.role_profile_path) if result.role_profile_path else None,
                "model_evidence": _model_evidence_payload(model_evidence),
                "critic_enabled": spec.orchestration.critic_enabled if spec.orchestration else None,
                "task_paths": [str(path) for path in result.task_paths],
                "state_root": str(store.root) if store is not None else None,
                "enqueued_task_ids": list(result.enqueued_task_ids),
            },
        )
        return 0

    print("ok=True")
    print(f"project={spec.id}")
    print(f"title={spec.title}")
    print(f"tasks={len(spec.tasks)}")
    print(f"brief={result.brief_path}")
    print(f"roadmap={result.roadmap_path}")
    if result.role_profile_path is not None:
        print(f"role_profile={result.role_profile_path}")
        print(f"critic_enabled={spec.orchestration.critic_enabled if spec.orchestration else False}")
    for task_path in result.task_paths:
        print(f"task_file={task_path}")
    if enqueue:
        print(f"state_root={store.root if store is not None else ''}")
        print(f"enqueued={len(result.enqueued_task_ids)}")
        for task_id in result.enqueued_task_ids:
            print(f"enqueued_task={task_id}")
    return 0


def _init_project(
    project_root: Path,
    config_path: Path,
    env_path: Path | None,
    force: bool,
    skip_env: bool,
) -> int:
    windows = os.name == "nt"
    project = project_root.resolve()
    source_config = project / "config.example.toml"
    source_env = project / "scripts" / ("hoh-env.template.ps1" if windows else "hoh-env.template.sh")
    target_config = _resolve_project_path(project, config_path)
    target_env = _resolve_project_path(
        project, env_path or Path("hoh-env.local.ps1" if windows else "hoh-env.local.sh")
    )

    if not source_config.exists():
        print("ok=False")
        print(f"error=Template missing: {source_config}")
        return 1
    if not skip_env and not source_env.exists():
        print("ok=False")
        print(f"error=Template missing: {source_env}")
        return 1
    existing_targets = [str(target_config)] if target_config.exists() else []
    if not skip_env and target_env.exists():
        existing_targets.append(str(target_env))
    if existing_targets and not force:
        print("ok=False")
        print("error=Target file(s) already exist; rerun with --force to overwrite.")
        for target in existing_targets:
            print(f"existing={target}")
        return 1

    try:
        config_action = _copy_template(source_config, target_config, force)
        env_action = "skipped"
        if not skip_env:
            env_action = _copy_template(source_env, target_env, force)
    except OSError as exc:
        print("ok=False")
        print(f"error={exc}")
        return 1

    print("ok=True")
    print(f"project_root={project}")
    print(f"config={target_config}")
    print(f"config_action={config_action}")
    if skip_env:
        print("env_file=")
        print("env_action=skipped_by_option")
    else:
        print(f"env_file={target_env}")
        print(f"env_action={env_action}")
    print(f"next=Edit {target_config.name} for the worker adapter.")
    if not skip_env:
        print(f"next=Edit {target_env.name} with real Telegram values when Telegram is enabled.")
        if windows:
            print(f"next=Load environment with: . .\\{target_env.name}")
        else:
            print(f"next=Load environment with: . ./{target_env.name}")
    if windows:
        print(f"next=Run: .\\scripts\\hoh.ps1 doctor --config .\\{target_config.name}")
    else:
        print(f"next=Run: ./scripts/hoh.sh doctor --config ./{target_config.name}")
    return 0


def _resolve_project_path(project: Path, path: Path) -> Path:
    return path if path.is_absolute() else project / path


def _copy_template(source: Path, target: Path, force: bool) -> str:
    existed = target.exists()
    if existed and not force:
        return "exists"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return "overwritten" if existed else "created"


def _queue_add(repository: Path, task_path: Path, state_root: Path | None, json_output: bool) -> int:
    try:
        work_item = load_work_item(task_path)
        store = _state_store(repository, state_root)
        task = store.enqueue(work_item, source=str(task_path))
    except (TaskLoadError, StateStoreError) as exc:
        if json_output:
            _print_json_envelope("queue.add", ok=False, error=str(exc))
            return 1
        print("ok=False")
        print(f"error={exc}")
        return 1
    if json_output:
        _print_json_envelope(
            "queue.add",
            ok=True,
            data={"state_root": str(store.root), "task": queue_task_payload(task)},
        )
        return 0
    print("ok=True")
    print(f"state_root={store.root}")
    print(f"task={task.work_item.id}")
    print(f"status={task.status}")
    return 0


def _queue_list(repository: Path, state_root: Path | None, json_output: bool) -> int:
    store = _state_store(repository, state_root)
    tasks = store.list_tasks()
    schedule = store.schedule()
    readiness = {
        item.task.work_item.id: item
        for item in (*schedule.ready, *schedule.waiting)
    }
    if json_output:
        _print_json_envelope(
            "queue.list",
            ok=True,
            data={
                "state_root": str(store.root),
                "tasks": [queue_task_payload(task) for task in tasks],
                "schedule": queue_schedule_payload(schedule),
            },
        )
        return 0
    print(f"state_root={store.root}")
    print(f"tasks={len(tasks)}")
    print(f"ready={len(schedule.ready)}")
    print(f"waiting={len(schedule.waiting)}")
    print(f"selected={schedule.selected.work_item.id if schedule.selected else ''}")
    for task in tasks:
        item = readiness.get(task.work_item.id)
        blockers = (
            ",".join(f"{blocker.task_id}={blocker.status}" for blocker in item.blockers)
            if item is not None
            else ""
        )
        print(
            "task="
            f"{task.work_item.id} status={task.status} attempts={task.attempts} "
            f"priority={task.work_item.priority} ready={item.ready if item is not None else ''} "
            f"blockers={blockers} "
            f"commit={task.last_commit or ''} error={task.last_error or ''}"
        )
    return 0


def _queue_history(repository: Path, state_root: Path | None, json_output: bool) -> int:
    store = _state_store(repository, state_root)
    records = store.history()
    if json_output:
        _print_json_envelope(
            "queue.history",
            ok=True,
            data={"state_root": str(store.root), "runs": [run_record_payload(record) for record in records]},
        )
        return 0
    print(f"state_root={store.root}")
    print(f"runs={len(records)}")
    for record in records:
        print(
            "run="
            f"{record.run_id} task={record.work_item_id} ok={record.ok} "
            f"commit={record.commit or ''} error={record.error or ''}"
        )
    return 0


def _audit_history(repository: Path, state_root: Path | None, json_output: bool) -> int:
    store = _state_store(repository, state_root)
    records = store.audit_reports()
    if json_output:
        _print_json_envelope(
            "audit.history",
            ok=True,
            data={"state_root": str(store.root), "audits": [audit_record_payload(record) for record in records]},
        )
        return 0
    print(f"state_root={store.root}")
    print(f"audits={len(records)}")
    if records:
        print(f"latest={records[-1].report_path}")
    for record in records:
        print(
            "audit="
            f"{record.audit_id} ok={record.ok} findings={record.findings_count} "
            f"checks={record.command_results_count} path={record.report_path}"
        )
    return 0


def _rollback_plan(
    repository: Path,
    state_root: Path | None,
    task_id: str,
    expected_commit: str | None,
    json_output: bool,
) -> int:
    store = _state_store(repository, state_root)
    try:
        plan = plan_rollback(repository, store, task_id, expected_commit)
    except (GitError, StateStoreError) as exc:
        if json_output:
            _print_json_envelope(
                "rollback.plan",
                ok=False,
                data={"state_root": str(store.root), "task_id": task_id},
                error=str(exc),
            )
        else:
            print("ok=False")
            print(f"state_root={store.root}")
            print(f"error={exc}")
        return 1
    data = {
        "state_root": str(store.root),
        "task_id": task_id,
        "eligible": plan.eligible,
        "run_id": plan.run_id,
        "target_commit": plan.target_commit,
        "changed_files": list(plan.changed_files),
        "downstream_task_ids": list(plan.downstream_task_ids),
        "blockers": [
            {"code": item.code, "message": item.message, "task_id": item.task_id}
            for item in plan.blockers
        ],
    }
    if json_output:
        _print_json_envelope(
            "rollback.plan",
            ok=plan.eligible,
            data=data,
            error=None if plan.eligible else "Rollback is blocked by policy.",
        )
    else:
        print(f"ok={plan.eligible}")
        print(f"state_root={store.root}")
        print(f"task={task_id}")
        print(f"eligible={plan.eligible}")
        print(f"run={plan.run_id or ''}")
        print(f"target_commit={plan.target_commit or ''}")
        print(f"changed_files={len(plan.changed_files)}")
        print(f"downstream={','.join(plan.downstream_task_ids)}")
        for blocker in plan.blockers:
            print(
                f"blocker={blocker.code} task={blocker.task_id or ''} message={blocker.message}"
            )
    return 0 if plan.eligible else 1


def _rollback_apply(
    repository: Path,
    state_root: Path | None,
    task_id: str,
    expected_commit: str,
    reason: str,
    checks: tuple[str, ...],
    timeout_seconds: float,
    config_path: Path | None,
    json_output: bool,
) -> int:
    config = load_effective_config(repository, config_path)
    store = _state_store(repository, state_root, config.coordination)
    try:
        record = apply_rollback(
            repository,
            store,
            task_id,
            expected_commit,
            reason,
            checks,
            timeout_seconds=timeout_seconds,
            coordination=config.coordination,
        )
    except (RollbackError, GitError, StateStoreError) as exc:
        if json_output:
            _print_json_envelope(
                "rollback.apply",
                ok=False,
                data={"state_root": str(store.root), "task_id": task_id},
                error=str(exc),
            )
        else:
            print("ok=False")
            print(f"state_root={store.root}")
            print(f"task={task_id}")
            print(f"error={exc}")
        return 1
    if json_output:
        _print_json_envelope(
            "rollback.apply",
            ok=True,
            data={
                "state_root": str(store.root),
                "status": "applied",
                "rollback": rollback_record_payload(record),
            },
        )
    else:
        print("ok=True")
        print(f"state_root={store.root}")
        print(f"task={task_id}")
        print(f"rollback_id={record.rollback_id}")
        print(f"target_commit={record.target_commit}")
        print(f"rollback_commit={record.rollback_commit}")
        print("status=applied")
    return 0


def _rollback_history(repository: Path, state_root: Path | None, json_output: bool) -> int:
    store = _state_store(repository, state_root)
    records = store.rollback_records()
    if json_output:
        _print_json_envelope(
            "rollback.history",
            ok=True,
            data={
                "state_root": str(store.root),
                "rollbacks": [rollback_record_payload(record) for record in records],
            },
        )
        return 0
    print(f"state_root={store.root}")
    print(f"rollbacks={len(records)}")
    for record in records:
        print(
            "rollback="
            f"{record.rollback_id} task={record.work_item_id} ok={record.ok} "
            f"target={record.target_commit} commit={record.rollback_commit or ''} "
            f"error={record.error or ''}"
        )
    return 0


def _queue_retry(repository: Path, state_root: Path | None, task_id: str, json_output: bool) -> int:
    store = _state_store(repository, state_root)
    try:
        task = store.requeue_failed(task_id)
    except StateStoreError as exc:
        if json_output:
            _print_json_envelope(
                "queue.retry",
                ok=False,
                data={"state_root": str(store.root), "task_id": task_id},
                error=str(exc),
            )
            return 1
        print("ok=False")
        print(f"state_root={store.root}")
        print(f"error={exc}")
        return 1
    if json_output:
        _print_json_envelope(
            "queue.retry",
            ok=True,
            data={"state_root": str(store.root), "task": queue_task_payload(task)},
        )
        return 0
    print("ok=True")
    print(f"state_root={store.root}")
    print(f"task={task.work_item.id}")
    print(f"status={task.status}")
    print(f"attempts={task.attempts}")
    return 0


def _queue_resolve_escalation(
    repository: Path,
    state_root: Path | None,
    task_id: str,
    resolution: str,
    reason: str,
    json_output: bool,
) -> int:
    store = _state_store(repository, state_root)
    try:
        task = store.resolve_escalation(task_id, resolution, reason)
    except StateStoreError as exc:
        if json_output:
            _print_json_envelope(
                "queue.resolve_escalation",
                ok=False,
                data={"state_root": str(store.root), "task_id": task_id, "resolution": resolution},
                error=str(exc),
            )
            return 1
        print("ok=False")
        print(f"state_root={store.root}")
        print(f"error={exc}")
        return 1
    if json_output:
        _print_json_envelope(
            "queue.resolve_escalation",
            ok=True,
            data={
                "state_root": str(store.root),
                "resolution": resolution,
                "task": queue_task_payload(task),
            },
        )
        return 0
    print("ok=True")
    print(f"state_root={store.root}")
    print(f"task={task.work_item.id}")
    print(f"status={task.status}")
    print(f"attempts={task.attempts}")
    return 0


def _queue_recover_running(
    repository: Path,
    state_root: Path | None,
    task_id: str,
    reason: str,
    json_output: bool,
) -> int:
    store = _state_store(repository, state_root)
    try:
        ledger = OperationLedger(repository)
        blocking_ids = {item.operation_id for item in ledger.reconcile().blocking}
        if any(
            record.get("operation_id") in blocking_ids
            and record.get("task_id") == task_id
            for record in ledger.records()
        ):
            raise StateStoreError(
                f"Task {task_id} has unresolved canonical operation evidence; reconcile it first."
            )
        task = store.recover_running(task_id, reason)
    except StateStoreError as exc:
        if json_output:
            _print_json_envelope(
                "queue.recover",
                ok=False,
                data={"state_root": str(store.root), "task_id": task_id},
                error=str(exc),
            )
            return 1
        print("ok=False")
        print(f"state_root={store.root}")
        print(f"error={exc}")
        return 1
    if json_output:
        _print_json_envelope(
            "queue.recover",
            ok=True,
            data={"state_root": str(store.root), "task": queue_task_payload(task)},
        )
        return 0
    print("ok=True")
    print(f"state_root={store.root}")
    print(f"task={task.work_item.id}")
    print(f"status={task.status}")
    print(f"attempts={task.attempts}")
    print(f"last_error={task.last_error or ''}")
    return 0


def _queue_stale(repository: Path, state_root: Path | None, max_age_minutes: float, json_output: bool) -> int:
    store = _state_store(repository, state_root)
    try:
        stale_tasks = store.stale_running_tasks(max_age_minutes)
    except StateStoreError as exc:
        if json_output:
            _print_json_envelope(
                "queue.stale",
                ok=False,
                data={"state_root": str(store.root), "max_age_minutes": max_age_minutes},
                error=str(exc),
            )
            return 1
        print("ok=False")
        print(f"state_root={store.root}")
        print(f"error={exc}")
        return 1

    if json_output:
        _print_json_envelope(
            "queue.stale",
            ok=not stale_tasks,
            data={
                "state_root": str(store.root),
                "max_age_minutes": max_age_minutes,
                "tasks": [
                    {**queue_task_payload(task), "age_minutes": round(_age_minutes(task.updated_at_utc), 1)}
                    for task in stale_tasks
                ],
            },
        )
        return 0 if not stale_tasks else 1

    print(f"ok={len(stale_tasks) == 0}")
    print(f"state_root={store.root}")
    print(f"stale={len(stale_tasks)}")
    print(f"max_age_minutes={max_age_minutes:g}")
    for task in stale_tasks:
        print(
            "task="
            f"{task.work_item.id} status={task.status} attempts={task.attempts} "
            f"updated_at={task.updated_at_utc} age_minutes={_age_minutes(task.updated_at_utc):.1f} "
            f"error={task.last_error or ''}"
        )
    return 0 if not stale_tasks else 1


def _queue_run_next(
    repository: Path,
    state_root: Path | None,
    timeout_seconds: float,
    config_path: Path | None,
    json_output: bool,
) -> int:
    config = load_effective_config(repository, config_path)
    outcome = _run_next_queued_task(repository, state_root, timeout_seconds, config)
    if outcome["empty"]:
        if json_output:
            _print_json_envelope(
                "queue.run_next",
                ok=False,
                data={"state_root": str(outcome["state_root"]), "status": "empty"},
                error="No queued tasks.",
            )
            return 1
        print("ok=False")
        print(f"state_root={outcome['state_root']}")
        print("error=No queued tasks.")
        return 1
    if outcome.get("dependency_blocked"):
        if json_output:
            _print_json_envelope(
                "queue.run_next",
                ok=False,
                data={
                    "state_root": str(outcome["state_root"]),
                    "status": "dependency_blocked",
                    "schedule": outcome["schedule"],
                },
                error=str(outcome["exception"]),
            )
            return 1
        print("ok=False")
        print(f"state_root={outcome['state_root']}")
        print("status=dependency_blocked")
        print(f"error={outcome['exception']}")
        return 1
    if outcome["exception"] is not None:
        if json_output:
            _print_json_envelope(
                "queue.run_next",
                ok=False,
                data={
                    "state_root": str(outcome["state_root"]),
                    "run_id": outcome["run_id"],
                    "task_id": outcome["task_id"],
                    "status": outcome.get("lifecycle_status") or "blocked",
                },
                error=str(outcome["exception"]),
            )
            return 1
        print("ok=False")
        print(f"state_root={outcome['state_root']}")
        print(f"run={outcome['run_id']}")
        print(f"task={outcome['task_id']}")
        print(f"status={outcome.get('lifecycle_status') or 'blocked'}")
        print(f"error={outcome['exception']}")
        return 1
    if json_output:
        result = outcome["result"]
        exported = outcome.get("review_bundle")
        critic_review = outcome.get("critic_review")
        lifecycle_status = outcome.get("lifecycle_status") or ("completed" if result.ok else "blocked")
        command_ok = result.ok and lifecycle_status not in {"rework_required", "escalated"}
        _print_json_envelope(
            "queue.run_next",
            ok=command_ok,
            data={
                "state_root": str(outcome["state_root"]),
                "run_id": outcome["run_id"],
                "task_id": outcome["task_id"],
                "status": lifecycle_status,
                "result": harness_run_result_payload(result),
                "review_bundle": (
                    {
                        "bundle_id": exported.record.bundle_id,
                        "bundle_sha256": exported.record.bundle_sha256,
                        "bundle_path": str(exported.output_path),
                    }
                    if exported is not None
                    else None
                ),
                "critic_decision": (
                    review_decision_payload(critic_review.decision)
                    if critic_review is not None
                    else None
                ),
            },
        )
        return 0 if command_ok else 1
    print(f"state_root={outcome['state_root']}")
    print(f"run={outcome['run_id']}")
    print(f"task={outcome['task_id']}")
    _print_result(outcome["result"])
    if outcome.get("review_bundle") is not None:
        exported = outcome["review_bundle"]
        print(f"review_bundle={exported.record.bundle_id}")
        print(f"review_bundle_path={exported.output_path}")
    if outcome.get("critic_review") is not None:
        critic_review = outcome["critic_review"]
        print(f"critic_decision={critic_review.decision.decision}")
        print(f"critic_decision_path={critic_review.decision_path}")
    lifecycle_status = outcome.get("lifecycle_status") or ("done" if outcome["result"].ok else "failed")
    print(f"status={lifecycle_status}")
    command_ok = outcome["result"].ok and lifecycle_status not in {"rework_required", "escalated"}
    return 0 if command_ok else 1


def _reconcile_pending_review_setup(
    repository: Path,
    store: HohStateStore,
    config: HarnessConfig,
) -> None:
    if not config.three_head.required:
        return
    runs = store.history()
    for task in store.list_tasks():
        if task.status != "review_pending" or task.pending_review_bundle_id is not None:
            continue
        run = next(
            (
                item
                for item in reversed(runs)
                if item.work_item_id == task.work_item.id and item.ok and item.commit is not None
            ),
            None,
        )
        if run is None:
            raise ReconciliationBlockedError(
                f"Review-pending task has no successful run evidence: {task.work_item.id}"
            )
        exported = export_review_bundle(
            repository,
            store,
            run.run_id,
            three_head=config.three_head,
        )
        store.attach_review_bundle(task.work_item.id, exported.record.bundle_id)


def _startup_reconcile(repository: Path, config: HarnessConfig):
    lease = repository_execution_lease(repository, config.coordination)
    with lease.hold(
        "reconciliation.startup",
        command="queue startup reconciliation",
    ):
        return OperationLedger(repository).reconcile(auto_complete_state=True)


def _run_next_queued_task(
    repository: Path,
    state_root: Path | None,
    timeout_seconds: float,
    config: HarnessConfig,
    notifier=None,
) -> dict:
    store = HohStateStore(
        state_root or default_state_root(repository),
        coordination=config.coordination,
    )
    try:
        reconciliation = _startup_reconcile(repository, config)
    except LockContendedError as exc:
        return {
            "empty": False,
            "dependency_blocked": False,
            "state_root": store.root,
            "task_id": None,
            "run_id": None,
            "result": None,
            "exception": exc,
            "review_bundle": None,
            "critic_review": None,
            "lifecycle_status": "claim_contended",
        }
    if reconciliation.blocking:
        return {
            "empty": False,
            "dependency_blocked": False,
            "state_root": store.root,
            "task_id": None,
            "run_id": None,
            "result": None,
            "exception": ReconciliationBlockedError(
                "Unresolved canonical operation blocks Worker execution."
            ),
            "review_bundle": None,
            "critic_review": None,
            "lifecycle_status": "reconciliation_blocked",
            "reconciliation": reconciliation,
        }
    _reconcile_pending_review_setup(repository, store, config)
    schedule = store.schedule()
    selected = schedule.selected
    if selected is None:
        if schedule.waiting:
            return {
                "empty": False,
                "dependency_blocked": True,
                "state_root": store.root,
                "task_id": None,
                "run_id": None,
                "result": None,
                "exception": StateStoreError(
                    "Queued tasks are waiting for dependencies; no task is runnable."
                ),
                "review_bundle": None,
                "critic_review": None,
                "lifecycle_status": "dependency_blocked",
                "schedule": queue_schedule_payload(schedule),
            }
        return {
            "empty": True,
            "dependency_blocked": False,
            "state_root": store.root,
            "task_id": None,
            "run_id": None,
            "result": None,
            "exception": None,
            "review_bundle": None,
            "critic_review": None,
            "lifecycle_status": "empty",
            "schedule": queue_schedule_payload(schedule),
        }

    try:
        task = store.claim_next(
            config.three_head.max_attempts if config.three_head.required else None
        )
    except StateStoreError as exc:
        return {
            "empty": False,
            "state_root": store.root,
            "task_id": selected.work_item.id,
            "run_id": None,
            "result": None,
            "exception": exc,
            "review_bundle": None,
            "critic_review": None,
            "lifecycle_status": (
                "escalated"
                if any(
                    item.work_item.id == selected.work_item.id and item.status == "escalated"
                    for item in store.list_tasks()
                )
                else "claim_contended"
            ),
        }
    if task is None:
        return {
            "empty": True,
            "dependency_blocked": False,
            "state_root": store.root,
            "task_id": None,
            "run_id": None,
            "result": None,
            "exception": None,
            "review_bundle": None,
            "critic_review": None,
            "lifecycle_status": "empty",
            "schedule": queue_schedule_payload(store.schedule()),
        }

    started_at = _utc_now_for_cli()
    operation_context = OperationContext.create(
        store.root,
        started_at,
        review_required=config.three_head.required,
    )
    try:
        result = _run_worker_work_item(
            repository,
            task.work_item,
            config,
            timeout_seconds,
            notifier=notifier,
            journal=store.journal,
            operation_context=operation_context,
        )
    except LockContendedError as exc:
        store.recover_running(
            task.work_item.id,
            f"Repository execution lease contention; task was safely requeued: {exc}",
        )
        return {
            "empty": False,
            "state_root": store.root,
            "task_id": task.work_item.id,
            "run_id": None,
            "result": None,
            "exception": exc,
            "review_bundle": None,
            "critic_review": None,
            "lifecycle_status": "claim_contended",
        }
    except Exception as exc:
        reconciliation = OperationLedger(repository).reconcile(auto_complete_state=True)
        if reconciliation.blocking:
            return {
                "empty": False,
                "state_root": store.root,
                "task_id": task.work_item.id,
                "run_id": operation_context.run_id,
                "result": None,
                "exception": exc,
                "review_bundle": None,
                "critic_review": None,
                "lifecycle_status": "reconciliation_blocked",
                "reconciliation": reconciliation,
            }
        record = store.record_failure(task.work_item.id, started_at, str(exc))
        return {
            "empty": False,
            "state_root": store.root,
            "task_id": task.work_item.id,
            "run_id": record.run_id,
            "result": None,
            "exception": exc,
            "review_bundle": None,
            "critic_review": None,
            "lifecycle_status": "failed",
        }

    return _record_worker_result(
        repository,
        store,
        task.work_item.id,
        started_at,
        result,
        config,
        operation_context,
    )


def _record_worker_result(
    repository: Path,
    store: HohStateStore,
    task_id: str,
    started_at: str,
    result,
    config: HarnessConfig,
    operation_context: OperationContext | None = None,
) -> dict:
    crash_point("task.before_state")
    record = store.record_result(
        task_id,
        started_at,
        result,
        review_required=config.three_head.required,
        run_id=operation_context.run_id if operation_context is not None else None,
    )
    if operation_context is not None:
        ledger = OperationLedger(repository)
        if ledger.exists(operation_context.operation_id):
            ledger.phase(operation_context.operation_id, "state_recorded")
            crash_point("task.after_state")
            ledger.terminal(
                operation_context.operation_id,
                "complete",
                "Canonical commit and StateStore run evidence are durable.",
            )
    exported = None
    critic_review = None
    if config.three_head.required and result.ok:
        try:
            exported = export_review_bundle(
                repository,
                store,
                record.run_id,
                three_head=config.three_head,
            )
            store.attach_review_bundle(task_id, exported.record.bundle_id)
            if config.critic.automatic:
                critic_review = run_critic_review(
                    repository,
                    store,
                    config,
                    bundle=exported,
                )
        except Exception as exc:
            current = next(
                item for item in store.list_tasks() if item.work_item.id == task_id
            )
            if current.pending_review_bundle_id is None:
                current = store.fail_review_setup(
                    task_id,
                    f"Review bundle setup failed: {exc}",
                )
            return {
                "empty": False,
                "state_root": store.root,
                "task_id": task_id,
                "run_id": record.run_id,
                "result": result,
                "exception": exc,
                "review_bundle": exported,
                "critic_review": None,
                "lifecycle_status": current.status,
            }
    lifecycle_status = (
        critic_review.task_status
        if critic_review is not None
        else ("review_pending" if exported is not None else ("done" if result.ok else "failed"))
    )
    if lifecycle_status != "review_pending":
        try:
            store.metrics.record_quality(
                UsageContext(
                    role="worker",
                    agent=config.three_head.worker.identity,
                    provider=config.three_head.worker.provider,
                    model=config.three_head.worker.model,
                    driver=config.worker.driver,
                    task_id=task_id,
                    run_id=record.run_id,
                ),
                1.0 if lifecycle_status == "done" else 0.0,
                lifecycle_status,
            )
        except Exception:
            pass
    return {
        "empty": False,
        "state_root": store.root,
        "task_id": task_id,
        "run_id": record.run_id,
        "result": result,
        "exception": None,
        "review_bundle": exported,
        "critic_review": critic_review,
        "lifecycle_status": lifecycle_status,
    }


def _run_ready_batch(
    repository: Path,
    state_root: Path | None,
    timeout_seconds: float,
    config: HarnessConfig,
    notifier,
    limit: int,
) -> list[dict]:
    if limit <= 1:
        return [_run_next_queued_task(repository, state_root, timeout_seconds, config, notifier=notifier)]

    store = HohStateStore(
        state_root or default_state_root(repository),
        coordination=config.coordination,
    )
    try:
        reconciliation = _startup_reconcile(repository, config)
    except LockContendedError as exc:
        return [
            {
                "empty": False,
                "dependency_blocked": False,
                "state_root": store.root,
                "task_id": None,
                "run_id": None,
                "result": None,
                "exception": exc,
                "review_bundle": None,
                "critic_review": None,
                "lifecycle_status": "claim_contended",
            }
        ]
    if reconciliation.blocking:
        return [
            {
                "empty": False,
                "dependency_blocked": False,
                "state_root": store.root,
                "task_id": None,
                "run_id": None,
                "result": None,
                "exception": ReconciliationBlockedError(
                    "Unresolved canonical operation blocks Worker execution."
                ),
                "review_bundle": None,
                "critic_review": None,
                "lifecycle_status": "reconciliation_blocked",
                "reconciliation": reconciliation,
            }
        ]
    _reconcile_pending_review_setup(repository, store, config)
    scheduled = store.schedule_batch(limit)
    if len(scheduled) <= 1:
        return [_run_next_queued_task(repository, state_root, timeout_seconds, config, notifier=notifier)]
    batch = store.claim_batch(limit)

    starts: dict[str, str] = {}
    contexts: dict[str, OperationContext] = {}
    for task in batch:
        starts[task.work_item.id] = _utc_now_for_cli()
        contexts[task.work_item.id] = OperationContext.create(
            store.root,
            starts[task.work_item.id],
            review_required=config.three_head.required,
        )
    store.journal.record(
        "decision",
        "supervisor",
        "scheduler.batch_started",
        content={
            "task_ids": [task.work_item.id for task in batch],
            "parallelism": len(batch),
            "resource_policy": "non-overlapping allowed_paths",
        },
    )

    prepared: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=len(batch), thread_name_prefix="hoh-worker") as executor:
        futures = {
            task.work_item.id: executor.submit(
                _collect_worker_work_item,
                repository,
                task.work_item,
                config,
                timeout_seconds,
                notifier,
                store.journal,
            )
            for task in batch
        }
        for task in batch:
            task_id = task.work_item.id
            try:
                prepared[task_id] = futures[task_id].result()
            except Exception as exc:
                prepared[task_id] = exc

    outcomes: list[dict] = []
    for task in batch:
        task_id = task.work_item.id
        value = prepared[task_id]
        if isinstance(value, Exception):
            record = store.record_failure(task_id, starts[task_id], str(value))
            outcomes.append(
                {
                    "empty": False,
                    "state_root": store.root,
                    "task_id": task_id,
                    "run_id": record.run_id,
                    "result": None,
                    "exception": value,
                    "review_bundle": None,
                    "critic_review": None,
                    "lifecycle_status": "failed",
                }
            )
            continue
        supervisor, completion = value
        try:
            result = supervisor.execute_worker_completion(
                repository,
                task.work_item,
                completion,
                operation_context=contexts[task_id],
            )
        except LockContendedError as exc:
            store.recover_running(
                task_id,
                f"Repository execution lease contention; task was safely requeued: {exc}",
            )
            outcomes.append(
                {
                    "empty": False,
                    "state_root": store.root,
                    "task_id": task_id,
                    "run_id": None,
                    "result": None,
                    "exception": exc,
                    "review_bundle": None,
                    "critic_review": None,
                    "lifecycle_status": "claim_contended",
                }
            )
            continue
        except Exception as exc:
            reconciliation = OperationLedger(repository).reconcile(auto_complete_state=True)
            if reconciliation.blocking:
                outcomes.append(
                    {
                        "empty": False,
                        "state_root": store.root,
                        "task_id": task_id,
                        "run_id": contexts[task_id].run_id,
                        "result": None,
                        "exception": exc,
                        "review_bundle": None,
                        "critic_review": None,
                        "lifecycle_status": "reconciliation_blocked",
                        "reconciliation": reconciliation,
                    }
                )
                continue
            record = store.record_failure(task_id, starts[task_id], str(exc))
            outcomes.append(
                {
                    "empty": False,
                    "state_root": store.root,
                    "task_id": task_id,
                    "run_id": record.run_id,
                    "result": None,
                    "exception": exc,
                    "review_bundle": None,
                    "critic_review": None,
                    "lifecycle_status": "failed",
                }
            )
            continue
        outcomes.append(
            _record_worker_result(
                repository,
                store,
                task_id,
                starts[task_id],
                result,
                config,
                contexts[task_id],
            )
        )
    return outcomes


def _queue_run_loop(
    repository: Path,
    state_root: Path | None,
    timeout_seconds: float,
    max_tasks: int,
    config_path: Path | None,
    parallelism: int | None,
    stale_minutes: float,
    final_audit: bool,
    final_check_commands: tuple[str, ...],
    json_output: bool,
) -> int:
    config = load_effective_config(repository, config_path)
    requested_parallelism = parallelism if parallelism is not None else config.scheduler.max_parallel_tasks
    if requested_parallelism < 1:
        message = "Scheduler parallelism must be at least 1."
        if json_output:
            _print_json_envelope("queue.run_loop", ok=False, error=message)
        else:
            print("ok=False")
            print(f"error={message}")
        return 1
    effective_parallelism = (
        requested_parallelism
        if config.worker.capabilities.requires_isolated_worktree and not config.three_head.required
        else 1
    )
    try:
        notifier = build_notifier(config.telegram)
    except ValueError as exc:
        if json_output:
            _print_json_envelope("queue.run_loop", ok=False, error=str(exc))
            return 1
        print("ok=False")
        print(f"error={exc}")
        return 1
    store = _state_store(repository, state_root)
    notifier = JournaledNotifier(notifier, store.journal)
    try:
        stale_tasks = store.stale_running_tasks(stale_minutes)
    except StateStoreError as exc:
        if json_output:
            _print_json_envelope(
                "queue.run_loop",
                ok=False,
                data={"state_root": str(store.root), "status": "state_error"},
                error=str(exc),
            )
            return 1
        print("ok=False")
        print(f"state_root={store.root}")
        print(f"error={exc}")
        return 1
    if stale_tasks:
        notification_error = _try_notify_stale_running(notifier, stale_tasks, stale_minutes)
        if json_output:
            data = {
                "state_root": str(store.root),
                "status": "blocked",
                "completed": 0,
                "stale_tasks": [
                    {**queue_task_payload(task), "age_minutes": round(_age_minutes(task.updated_at_utc), 1)}
                    for task in stale_tasks
                ],
                "runs": [],
            }
            if notification_error:
                data["notification_error"] = notification_error
            _print_json_envelope("queue.run_loop", ok=False, data=data)
            return 1
        print("ok=False")
        print(f"state_root={store.root}")
        print("completed=0")
        print(f"stale_running={len(stale_tasks)}")
        for task in stale_tasks:
            print(
                "stale_task="
                f"{task.work_item.id} attempts={task.attempts} "
                f"updated_at={task.updated_at_utc} age_minutes={_age_minutes(task.updated_at_utc):.1f}"
            )
        if notification_error:
            print(f"notification_error={notification_error}")
        print("status=blocked")
        return 1
    if config.three_head.required:
        waiting = [
            task
            for task in store.list_tasks()
            if task.status in {"review_pending", "rework_required", "escalated"}
        ]
        if waiting:
            status = next(
                candidate
                for candidate in ("escalated", "rework_required", "review_pending")
                if any(task.status == candidate for task in waiting)
            )
            notification_error = None
            if status == "escalated":
                notification_error = _try_notify_three_head_escalation(notifier, waiting)
            data = {
                "state_root": str(store.root),
                "status": status,
                "completed": 0,
                "tasks": [queue_task_payload(task) for task in waiting],
                "runs": [],
            }
            if notification_error:
                data["notification_error"] = notification_error
            if json_output:
                _print_json_envelope("queue.run_loop", ok=status == "review_pending", data=data)
            else:
                print(f"ok={status == 'review_pending'}")
                print(f"state_root={store.root}")
                print(f"status={status}")
                for task in waiting:
                    print(f"waiting_task={task.work_item.id} state={task.status} attempts={task.attempts}")
                if notification_error:
                    print(f"notification_error={notification_error}")
            return 0 if status == "review_pending" else 1
    completed = 0
    blocked = False
    first_blocked_task: str | None = None
    run_events: list[dict[str, object]] = []
    pending_outcomes: list[dict] = []
    while max_tasks <= 0 or completed < max_tasks:
        if not pending_outcomes:
            remaining = effective_parallelism
            if max_tasks > 0:
                remaining = min(remaining, max_tasks - completed)
            pending_outcomes.extend(
                _run_ready_batch(
                    repository,
                    state_root,
                    timeout_seconds,
                    config,
                    notifier,
                    remaining,
                )
            )
        outcome = pending_outcomes.pop(0)
        if outcome["empty"]:
            if final_audit:
                return _final_audit_handoff(
                    repository,
                    outcome["state_root"],
                    completed,
                    final_check_commands,
                    notifier,
                    json_output,
                    run_events,
                    config.audit,
                )
            if json_output:
                _print_json_envelope(
                    "queue.run_loop",
                    ok=True,
                    data={
                        "state_root": str(outcome["state_root"]),
                        "completed": completed,
                        "status": "empty",
                        "runs": run_events,
                    },
                )
                return 0
            print("ok=True")
            print(f"state_root={outcome['state_root']}")
            print(f"completed={completed}")
            print("status=empty")
            return 0
        if outcome.get("dependency_blocked"):
            if json_output:
                _print_json_envelope(
                    "queue.run_loop",
                    ok=False,
                    data={
                        "state_root": str(outcome["state_root"]),
                        "completed": completed,
                        "status": "dependency_blocked",
                        "schedule": outcome["schedule"],
                        "runs": run_events,
                    },
                    error=str(outcome["exception"]),
                )
            else:
                print("ok=False")
                print(f"state_root={outcome['state_root']}")
                print(f"completed={completed}")
                print("status=dependency_blocked")
                print(f"error={outcome['exception']}")
            return 1
        outcome_blocked = False
        if outcome["exception"] is not None:
            outcome_blocked = True
            notification_error = _try_notify_queue_blocker(notifier, outcome["task_id"], str(outcome["exception"]))
        elif not outcome["result"].ok:
            outcome_blocked = True
            notification_error = _try_notify_queue_blocker(
                notifier,
                outcome["task_id"],
                _result_blocker_error(outcome["result"]),
            )
        else:
            notification_error = None

        run_event: dict[str, object] = {
            "run_id": outcome["run_id"],
            "task_id": outcome["task_id"],
            "ok": not outcome_blocked,
            "result": harness_run_result_payload(outcome["result"]) if outcome["result"] is not None else None,
            "error": str(outcome["exception"]) if outcome["exception"] is not None else None,
            "critic_decision": (
                review_decision_payload(outcome["critic_review"].decision)
                if outcome.get("critic_review") is not None
                else None
            ),
        }
        run_events.append(run_event)
        if not json_output:
            print(f"run={outcome['run_id']} task={outcome['task_id']} ok={not blocked}")
        if outcome.get("critic_review") is not None and not outcome_blocked:
            completed += 1
            critic_review = outcome["critic_review"]
            if critic_review.task_status == "done":
                if not json_output:
                    print(
                        f"critic_decision={critic_review.decision.decision} "
                        f"status={critic_review.task_status}"
                    )
                continue
            notification_error = None
            if critic_review.task_status == "escalated":
                waiting = [
                    task
                    for task in store.list_tasks()
                    if task.work_item.id == outcome["task_id"]
                ]
                notification_error = _try_notify_three_head_escalation(notifier, waiting)
            data = {
                "state_root": str(outcome["state_root"]),
                "completed": completed,
                "status": critic_review.task_status,
                "task_id": outcome["task_id"],
                "decision": review_decision_payload(critic_review.decision),
                "decision_path": str(critic_review.decision_path),
                "runs": run_events,
            }
            if notification_error:
                data["notification_error"] = notification_error
            if json_output:
                _print_json_envelope("queue.run_loop", ok=False, data=data)
            else:
                print("ok=False")
                print(f"state_root={outcome['state_root']}")
                print(f"completed={completed}")
                print(f"critic_decision={critic_review.decision.decision}")
                print(f"status={critic_review.task_status}")
                if notification_error:
                    print(f"notification_error={notification_error}")
            return 1
        if (
            outcome.get("review_bundle") is not None
            and outcome.get("critic_review") is None
            and not outcome_blocked
        ):
            completed += 1
            exported = outcome["review_bundle"]
            data = {
                "state_root": str(outcome["state_root"]),
                "completed": completed,
                "status": "review_pending",
                "task_id": outcome["task_id"],
                "bundle_id": exported.record.bundle_id,
                "bundle_sha256": exported.record.bundle_sha256,
                "bundle_path": str(exported.output_path),
                "runs": run_events,
            }
            if json_output:
                _print_json_envelope("queue.run_loop", ok=True, data=data)
            else:
                print(f"state_root={outcome['state_root']}")
                print(f"completed={completed}")
                print(f"review_bundle={exported.record.bundle_id}")
                print(f"review_bundle_path={exported.output_path}")
                print("status=review_pending")
            return 0
        if outcome_blocked:
            blocked = True
            first_blocked_task = first_blocked_task or outcome["task_id"]
            if pending_outcomes:
                continue
            if json_output:
                data = {
                    "state_root": str(outcome["state_root"]),
                    "completed": completed,
                    "status": "blocked",
                    "blocked_task": first_blocked_task,
                    "runs": run_events,
                }
                if notification_error:
                    data["notification_error"] = notification_error
                _print_json_envelope("queue.run_loop", ok=False, data=data)
                return 1
            print("ok=False")
            print(f"state_root={outcome['state_root']}")
            print(f"completed={completed}")
            print(f"blocked_task={first_blocked_task}")
            if notification_error:
                print(f"notification_error={notification_error}")
            print("status=blocked")
            return 1
        completed += 1

        if blocked and not pending_outcomes:
            if json_output:
                _print_json_envelope(
                    "queue.run_loop",
                    ok=False,
                    data={
                        "state_root": str(outcome["state_root"]),
                        "completed": completed,
                        "status": "blocked",
                        "blocked_task": first_blocked_task,
                        "runs": run_events,
                    },
                )
            else:
                print("ok=False")
                print(f"state_root={outcome['state_root']}")
                print(f"completed={completed}")
                print(f"blocked_task={first_blocked_task}")
                print("status=blocked")
            return 1

    if json_output:
        _print_json_envelope(
            "queue.run_loop",
            ok=True,
            data={
                "state_root": str(_state_store(repository, state_root).root),
                "completed": completed,
                "status": "max_tasks_reached",
                "runs": run_events,
            },
        )
        return 0
    print("ok=True")
    print(f"state_root={_state_store(repository, state_root).root}")
    print(f"completed={completed}")
    print("status=max_tasks_reached")
    return 0


def _final_audit_handoff(
    repository: Path,
    state_root: Path,
    completed: int,
    check_commands: tuple[str, ...],
    notifier,
    json_output: bool,
    run_events: list[dict[str, object]],
    audit_config: AuditConfig,
) -> int:
    review_status = build_supervisor_status(repository, state_root)
    if review_status.rolled_back:
        message = (
            "HoH final handoff blocked by rolled-back task state.\n"
            f"Rolled-back tasks: {review_status.rolled_back}\n"
            "Queue a corrected replacement lifecycle for the same task id and complete its "
            "verification before final handoff."
        )
        try:
            notifier.notify_user_action_required(message)
            notification_error = None
        except Exception as exc:
            notification_error = str(exc)
        data = {
            "state_root": str(state_root),
            "completed": completed,
            "status": "rollback_blocked",
            "rolled_back": review_status.rolled_back,
            "runs": run_events,
        }
        if notification_error:
            data["notification_error"] = notification_error
        if json_output:
            _print_json_envelope("queue.run_loop", ok=False, data=data)
        else:
            print("ok=False")
            print(f"state_root={state_root}")
            print(f"completed={completed}")
            print(f"rolled_back={review_status.rolled_back}")
            if notification_error:
                print(f"notification_error={notification_error}")
            print("status=rollback_blocked")
        return 1
    if review_status.pending_reviews or review_status.rejected_reviews:
        message = (
            "HoH final handoff blocked by external review evidence.\n"
            f"Pending reviews: {review_status.pending_reviews}\n"
            f"Rejected reviews: {review_status.rejected_reviews}\n"
            "Resolve it with one of:\n"
            "- review: import a conforming verifier decision, or create a new reviewed run.\n"
            "- stop: change nothing and inspect the state yourself.\n"
            "HoH does not choose for you; free-form replies are only recorded as operator notes."
        )
        try:
            notifier.notify_user_action_required(message)
            notification_error = None
        except Exception as exc:
            notification_error = str(exc)
        if json_output:
            data = {
                "state_root": str(state_root),
                "completed": completed,
                "status": "review_blocked",
                "pending_reviews": review_status.pending_reviews,
                "rejected_reviews": review_status.rejected_reviews,
                "runs": run_events,
            }
            if notification_error:
                data["notification_error"] = notification_error
            _print_json_envelope("queue.run_loop", ok=False, data=data)
            return 1
        print("ok=False")
        print(f"state_root={state_root}")
        print(f"completed={completed}")
        print(f"pending_reviews={review_status.pending_reviews}")
        print(f"rejected_reviews={review_status.rejected_reviews}")
        if notification_error:
            print(f"notification_error={notification_error}")
        print("status=review_blocked")
        return 1

    blocking_states = {
        "queued": review_status.queued,
        "running": review_status.running,
        "failed": review_status.failed,
        "review_pending": review_status.review_pending,
        "rework_required": review_status.rework_required,
        "escalated": review_status.escalated,
        "stale": review_status.stale,
        "reconciliation_pending": review_status.reconciliation_pending,
    }
    blocking_states = {name: count for name, count in blocking_states.items() if count}
    if blocking_states:
        summary = ", ".join(f"{name}={count}" for name, count in blocking_states.items())
        message = (
            "HoH final handoff blocked by unresolved supervisor state.\n"
            f"Blocking state: {summary}\n"
            "Inspect queue-list and supervisor-status. Retry reviewed failed tasks, recover only "
            "confirmed interrupted tasks, and resolve review, escalation, or reconciliation "
            "blockers before final handoff."
        )
        try:
            notifier.notify_user_action_required(message)
            notification_error = None
        except Exception as exc:
            notification_error = str(exc)
        data = {
            "state_root": str(state_root),
            "completed": completed,
            "status": "task_state_blocked",
            "blocking_states": blocking_states,
            "runs": run_events,
        }
        if notification_error:
            data["notification_error"] = notification_error
        if json_output:
            _print_json_envelope("queue.run_loop", ok=False, data=data)
        else:
            print("ok=False")
            print(f"state_root={state_root}")
            print(f"completed={completed}")
            for name, count in blocking_states.items():
                print(f"blocking_state={name} count={count}")
            if notification_error:
                print(f"notification_error={notification_error}")
            print("status=task_state_blocked")
        return 1

    report = run_project_audit(repository, check_commands, audit_config=audit_config)
    audit_record = HohStateStore(state_root).record_audit_report(
        report.to_markdown(),
        ok=report.ok,
        findings_count=len(report.findings),
        command_results_count=len(report.command_results),
    )
    if report.ok:
        notification_error = _try_notify_project_ready(notifier, report, audit_record.report_path)
        status = "ready"
    else:
        notification_error = _try_notify_audit_failed(notifier, report, audit_record.report_path)
        status = "audit_failed"
    if json_output:
        data = {
            "state_root": str(state_root),
            "completed": completed,
            "status": status,
            "runs": run_events,
            "audit": project_audit_payload(report),
            "audit_record": audit_record_payload(audit_record),
        }
        if notification_error:
            data["notification_error"] = notification_error
        _print_json_envelope("queue.run_loop", ok=report.ok, data=data)
        return 0 if report.ok else 1
    print(f"ok={report.ok}")
    print(f"state_root={state_root}")
    print(f"completed={completed}")
    print(f"final_audit_ready={report.ok}")
    print(f"final_audit_findings={len(report.findings)}")
    print(f"final_audit_checks={len(report.command_results)}")
    print(f"audit_report={audit_record.report_path}")
    if report.ok:
        if notification_error:
            print(f"notification_error={notification_error}")
        print("status=ready")
        return 0

    if notification_error:
        print(f"notification_error={notification_error}")
    for finding in report.findings:
        location = f" path={finding.path}" if finding.path else ""
        print(f"finding={finding.code}{location}")
    print("status=audit_failed")
    return 1


def _try_notify_project_ready(notifier, report, report_path: Path) -> str | None:
    try:
        notifier.notify_project_ready(_project_ready_message(report, report_path))
        return None
    except Exception as exc:
        return str(exc)


def _try_notify_audit_failed(notifier, report, report_path: Path) -> str | None:
    try:
        notifier.notify_audit_failed(_audit_failed_message(report, report_path))
        return None
    except Exception as exc:
        return str(exc)


def _try_notify_three_head_escalation(notifier, tasks) -> str | None:
    lines = [
        "HoH three-head loop escalated.",
        "The critic or bounded attempt policy requires a customer/supervisor decision.",
    ]
    for task in tasks:
        if task.status == "escalated":
            lines.append(
                f"Task: {task.work_item.id}; attempts: {task.attempts}; reason: {task.last_error or 'unspecified'}"
            )
    lines.extend(
        [
            "Resolve each one with:",
            "  queue-resolve-escalation --task-id <task-id> --resolution requeue|fail --reason <why>",
            "- requeue: return the task to the queue with a fresh attempt budget.",
            "- fail: close the task as failed.",
            "Leaving it escalated keeps the queue blocked on that task.",
        ]
    )
    try:
        notifier.notify_user_action_required("\n".join(lines))
        return None
    except Exception as exc:
        return str(exc)


def _project_ready_message(report, report_path: Path) -> str:
    return (
        "HoH project ready for handoff.\n"
        f"Project: {report.project_root}\n"
        f"Audit evidence: {report_path}\n"
        f"Verification commands: {len(report.command_results)}\n"
        "Final audit: passed."
    )


def _audit_failed_message(report, report_path: Path) -> str:
    findings = "\n".join(
        f"- {finding.code}: {finding.message}" + (f" ({finding.path})" if finding.path else "")
        for finding in report.findings[:10]
    )
    if len(report.findings) > 10:
        findings += f"\n- ... and {len(report.findings) - 10} more finding(s)"
    return (
        "HoH final audit failed.\n"
        f"Project: {report.project_root}\n"
        f"Audit evidence: {report_path}\n"
        f"Findings: {len(report.findings)}\n"
        f"{findings or '- none'}"
    )


def _try_notify_queue_blocker(notifier, task_id: str, error: str) -> str | None:
    try:
        _notify_queue_blocker(notifier, task_id, error)
        return None
    except Exception as exc:
        return str(exc)


def _try_notify_stale_running(notifier, stale_tasks, stale_minutes: float) -> str | None:
    try:
        _notify_stale_running(notifier, stale_tasks, stale_minutes)
        return None
    except Exception as exc:
        return str(exc)


def _notify_stale_running(notifier, stale_tasks, stale_minutes: float) -> None:
    task_ids = ", ".join(task.work_item.id for task in stale_tasks)
    question = f"HoH found stale running task(s): {task_ids}. How should HoH proceed?"
    task_lines = "\n".join(
        f"- {task.work_item.id}: attempts={task.attempts}, updated_at={task.updated_at_utc}, "
        f"age_minutes={_age_minutes(task.updated_at_utc):.1f}, error={task.last_error or ''}"
        for task in stale_tasks
    )
    message = (
        "HoH queue has stale running task(s).\n"
        f"Threshold minutes: {stale_minutes:g}\n"
        f"{task_lines}\n"
        "\n"
        f"Question: {question}\n"
        "Reply with one of:\n"
        "- /recover <task-id> [reason]: requeue the stale task after you confirm the previous "
        "process is no longer running.\n"
        "- /stop: change nothing and inspect the task yourself.\n"
        "HoH does not choose for you; free-form replies are only recorded as operator notes."
    )
    notifier.notify_user_action_required(message)


def _notify_queue_blocker(notifier, task_id: str, error: str) -> None:
    message = (
        "HoH queue blocked.\n"
        f"Task: {task_id}\n"
        f"Reason: {error}\n"
        "\n"
        f"Question: Queue task '{task_id}' is blocked. How should HoH proceed?\n"
        "Reply with one of:\n"
        "- /retry <task-id>: requeue the task after you confirm it should be attempted again.\n"
        "- /stop: change nothing and inspect the task yourself.\n"
        "HoH does not choose for you; free-form replies are only recorded as operator notes."
    )
    notifier.notify_user_action_required(message)


def _result_blocker_error(result) -> str:
    findings = result.pre_apply.findings + result.post_apply.findings
    if findings:
        return "; ".join(findings)
    failed_commands = [item for item in result.command_results if not item.ok]
    if failed_commands:
        return "; ".join(f"{item.command} exited {item.return_code}" for item in failed_commands)
    return "Task did not produce an accepted supervisor commit."


def _state_store(
    repository: Path,
    state_root: Path | None,
    coordination=None,
) -> HohStateStore:
    root = state_root or default_state_root(repository)
    return HohStateStore(root) if coordination is None else HohStateStore(root, coordination)


def _age_minutes(timestamp: str) -> float:
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - parsed.astimezone(UTC)
    return max(delta.total_seconds() / 60.0, 0.0)


def _print_json_envelope(
    message_type: str,
    *,
    ok: bool,
    data: dict[str, object] | None = None,
    error: str | None = None,
) -> None:
    print(dump_json(protocol_envelope(message_type, ok=ok, data=data, error=error)), end="")


def _run_hermes_smoke(repository: Path, timeout_seconds: float):
    _git(repository, "init")
    _git(repository, "config", "user.email", "demo@example.local")
    _git(repository, "config", "user.name", "LLM Harness Demo")
    (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
    _git(repository, "add", ".gitignore")
    _git(repository, "commit", "-m", "Initial commit")

    work_item = WorkItem(
        id="hermes-smoke",
        title="Create Hermes smoke artifact",
        objective="Create HARNESS_DEMO.md with one short sentence proving Hermes edited the isolated worktree.",
        acceptance_criteria=("HARNESS_DEMO.md exists after Hermes worktree attempt is accepted.",),
        verification_commands=(
            f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'HARNESS_DEMO.md\').exists()"',
        ),
        allowed_paths=("HARNESS_DEMO.md",),
        non_goals=("Do not edit README, docs, scripts, runtime, vendor, or git metadata.",),
    )
    supervisor = Supervisor(PolicyVerifier(trust_policy=WorkerTrustPolicy()))
    adapter = HermesAcpAdapter(turn_timeout_seconds=timeout_seconds)
    return supervisor.dispatch_work_item_in_attempt(
        repository,
        work_item,
        adapter.target,
        adapter,
    )


def _git(repository: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip())


def _utc_now_for_cli() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
