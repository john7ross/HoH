from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from queue import Empty, Queue
from threading import Event
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Sequence
import webbrowser

from .gui_widgets import (
    ButtonStyle,
    ElevatedCard,
    ModernSelect,
    RoundedButton,
    SelectStyle,
    SurfaceStyle,
)
from .gui_display import (
    MIN_WINDOW as MINIMUM_WINDOW_SIZE,
    apply_tk_scaling,
    enable_dpi_awareness,
    format_window_geometry,
    parse_window_geometry,
    preferred_window_geometry,
    resolve_font_scheme,
)
from .gui_services import (
    EditableA2AAgent,
    EditableModelPrice,
    EditableMcpServer,
    EditableProcessProfile,
    EditableProjectSettings,
    GuiSettings,
    application_update_status,
    authorize_a2a_agent,
    configure_workspace_schedule,
    editable_a2a_agents,
    editable_model_prices,
    editable_role_mcp_policy,
    editable_process_profiles,
    editable_project_settings,
    environment_variable_status,
    forget_credential,
    gui_settings_path,
    inspect_registry_agent_auth,
    install_registry_agent,
    install_application_update,
    login_registry_agent,
    load_gui_settings,
    load_project_snapshot,
    project_config_text,
    queue_task_choices,
    register_workspace_project,
    registry_agent_statuses,
    save_credential,
    stored_credential_names,
    remove_a2a_agent,
    remove_model_price,
    remove_mcp_server,
    remove_process_profile,
    remove_workspace_project,
    run_gui_command,
    run_due_workspace_projects,
    run_workspace_project,
    save_a2a_agent,
    save_model_price,
    save_mcp_server,
    save_role_mcp_policy,
    save_editable_project_settings,
    save_gui_settings,
    save_process_profile,
    save_project_config_text,
    save_project_roles,
    set_session_auth_variables,
    set_session_environment_variables,
    test_a2a_agent,
    trust_publisher_identity,
    uninstall_registry_agent,
    usage_metrics_summary,
    workspace_project_summaries,
    workspace_scheduler_service,
)
from .mcp import ACP_TOOL_KINDS
from .agent_matrix import agent_matrix
from .onboarding import ROLE_PRESETS, onboarding_progress, role_preset
from .supervisor_chat import (
    clear_supervisor_chat,
    load_supervisor_chat,
    materialize_supervisor_chat_plan,
    send_supervisor_message,
    supervisor_chat_path,
)


TEXT = {
    "en": {
        "app_title": "HoH — universal agent harness",
        "project": "Project",
        "browse": "Browse…",
        "load": "Load",
        "language": "Language",
        "theme": "Theme",
        "roles": "Roles",
        "setup": "First-run setup",
        "chat": "Supervisor chat",
        "config": "Project settings",
        "operations": "Operations",
        "metrics": "Metrics",
        "projects": "Projects",
        "agents_setup": "Agents & connections",
        "tools": "MCP policies",
        "mcp_title": "Tools by role",
        "mcp_desc": "Choose which ACP tool categories and MCP servers each role may use. Unknown or unlisted tools are denied before the agent can execute them.",
        "mcp_role": "Role policy",
        "mcp_mode": "Permission mode",
        "mcp_mode_help": "reject blocks every permission request. allow_once permits only the checked categories and never grants a permanent approval.",
        "mcp_kinds": "Allowed ACP tool categories",
        "mcp_save_policy": "Save role policy",
        "mcp_server": "MCP server",
        "mcp_server_name": "Server name",
        "mcp_transport": "Transport",
        "mcp_command": "Executable command (stdio)",
        "mcp_url": "HTTPS URL (HTTP/SSE)",
        "mcp_args": "Arguments, one per line",
        "mcp_env": "Process variables: TARGET=SOURCE_ENV",
        "mcp_headers": "HTTP headers: Header=SOURCE_ENV",
        "mcp_secret_help": "The project stores environment-variable names only. Values are resolved for the session and redacted from the protocol journal.",
        "mcp_new": "New server",
        "mcp_save_server": "Save server",
        "mcp_remove_server": "Remove server",
        "mcp_confirm_remove": "Remove MCP server '{name}' from the {role} policy?",
        "mcp_saved": "MCP policy saved.",
        "updates_title": "Trusted application updates",
        "updates_desc": "HoH accepts only an HTTPS release catalog signed by a publisher you imported explicitly. The package hash is checked before a new user-scope version is activated; the running version is never overwritten.",
        "updates_url": "Signed release catalog URL",
        "updates_check": "Check for update",
        "updates_install": "Download and install",
        "updates_trust": "Import publisher key",
        "updates_idle": "Current version is ready. Configure a channel to check for updates.",
        "updates_current": "Current {current}; no newer trusted release.",
        "updates_available": "Trusted update {version} is available ({size}).",
        "updates_installed": "HoH {version} was installed side-by-side at {path}. Restart from the installed shortcut when ready.",
        "updates_publisher": "Publisher {key_id} is now trusted for signed catalogs.",
        "updates_confirm": "Download, verify, and install the newer release beside the current version?",
        "updates_auto": "Automatically install trusted updates side-by-side at startup",
        "projects_title": "Project control center",
        "projects_desc": "One user-scoped view of every registered Git project, its real queue, schedule, and latest background run.",
        "projects_registered": "Registered projects",
        "projects_register_current": "Register open project",
        "projects_open": "Open selected",
        "projects_remove": "Remove from list",
        "projects_refresh": "Refresh summary",
        "projects_run_now": "Run selected now",
        "projects_run_due": "Run due schedules",
        "projects_empty": "No projects are registered yet. Open a Git project above and register it.",
        "projects_queue_line": "queued {queued} · ready {ready} · running {running} · attention {attention} · failed {failed} · done {done}",
        "projects_next_task": "Next: {task}",
        "projects_no_next_task": "No ready task",
        "projects_schedule": "Background schedule",
        "projects_schedule_desc": "Run this project's existing guarded queue loop at a fixed interval. Project locks still prevent overlapping Git work.",
        "projects_schedule_enabled": "Enable schedule",
        "projects_interval": "Every, minutes",
        "projects_final_audit": "Final audit when queue is empty",
        "projects_notify_windows": "Desktop notification",
        "projects_notify_telegram": "Telegram summary",
        "projects_schedule_save": "Save schedule",
        "projects_service_install": "Enable background runner",
        "projects_service_remove": "Disable background runner",
        "projects_service_status": "Check background runner",
        "projects_service_result": "Background runner: {detail}",
        "projects_schedule_saved": "Project schedule saved.",
        "projects_registered_status": "Project registered.",
        "projects_removed_status": "Project removed from the list; its files and queue were not deleted.",
        "projects_run_result": "{name}: status={status}, completed={completed}",
        "projects_confirm_remove": "Remove {name} from the project list? Project files and queue evidence will remain untouched.",
        "setup_title": "Get HoH ready for real work",
        "setup_desc": "Three steps to a first task. Everything else can wait until you need it.",
        "setup_step_project": "1. Project",
        "setup_step_project_desc": "Choose the Git repository HoH will work in. Nothing outside it is touched.",
        "open_project": "Open",
        "setup_step_team": "2. Team",
        "setup_step_team_desc": "Pick who does the work. HoH installs the agents this team needs and assigns the three roles for you. Sign in with the vendor if the agent asks for it.",
        "setup_prepare_team": "Prepare the team",
        "setup_step_task": "3. Task",
        "setup_step_task_desc": "Describe what you want done. Supervisor asks what it needs, then turns the conversation into a plan and a queue.",
        "setup_open_chat": "Describe a task",
        "setup_optional": "Everything else, when you need it",
        "setup_optional_desc": "None of this is required to start. It stays available for the projects and setups that need it.",
        "setup_team_ready": "Team is ready. Installed now: {installed}. Already present: {already}.",
        "setup_preset": "Team preset",
        "setup_required_agents": "Required agents: {names}",
        "setup_open_login": "Sign in with the vendor",
        "setup_finish": "Finish first-run setup",
        "setup_complete": "First-run setup is complete. The team is installed and assigned. Run a task to see it work.",
        "setup_incomplete": "Setup is not complete yet: {detail}",
        "setup_confirm_install": "Install or update these managed agents for the current user?\n\n{names}",
        "setup_roles_saved": "Preset applied and the three-role profile was saved.",
        "setup_status_ready": "Ready",
        "setup_status_pending": "Pending",
        "driver_a2a": "A2A · remote agent",
        "agents_setup_title": "Agents and connections",
        "agents_setup_desc": "Install ACP agents for the current OS user, sign in through the vendor, or connect a remote A2A v1 agent.",
        "managed_agents": "Managed ACP agents",
        "managed_agents_desc": "HoH installs an exact Registry version under the current user profile. Direct downloads require a published SHA-256; unsafe packages stay blocked.",
        "registry_agent": "Registry agent",
        "agent_description": "Description",
        "install_agent": "Install / update",
        "remove_agent": "Remove local copy",
        "inspect_login": "Check login methods",
        "login_agent": "Sign in",
        "auth_method": "Login method",
        "refresh_agents": "Refresh Registry and status",
        "managed_agent_home": "Managed files: %LOCALAPPDATA%\\HoH\\agents. Vendor credentials stay in the vendor's own secure store.",
        "agent_installed": "Installed · {detail}",
        "agent_installable": "Not installed · can be installed safely · {detail}",
        "agent_blocked_install": "Not installed · automatic install blocked · {detail}",
        "agent_removed": "Managed copy removed.",
        "auth_none": "The agent advertises no ACP login method. Sign in uses a known vendor CLI flow when available.",
        "auth_variables": "Credentials for this window only",
        "a2a_agents": "Remote A2A v1 agents",
        "a2a_agents_desc": "Connect a service through its public Agent Card. HoH supports Supervisor, Worker, and Critic; remote Worker receives only explicitly allowed text files.",
        "a2a_profile": "Connection name",
        "a2a_card_url": "Agent Card URL",
        "a2a_roles": "Allowed roles",
        "a2a_auth": "Authentication",
        "a2a_credential_env": "Credential variable",
        "a2a_header": "API-key header",
        "a2a_timeout": "Task timeout, seconds",
        "a2a_poll": "Poll interval, seconds",
        "a2a_oauth_flow": "OAuth flow",
        "a2a_client_id_env": "Client ID variable",
        "a2a_client_secret_env": "Client secret variable",
        "a2a_token_url": "OAuth token URL",
        "a2a_device_url": "Device authorization URL",
        "a2a_oidc_url": "OIDC discovery URL",
        "a2a_scopes": "OAuth scopes (space separated)",
        "a2a_client_auth": "Client authentication",
        "a2a_streaming": "Prefer SSE streaming when advertised",
        "a2a_push_url": "Push callback URL",
        "a2a_push_token_env": "Push token variable",
        "a2a_login": "OAuth / OIDC sign in",
        "a2a_device_prompt": "Open {url} and enter code {code}. HoH will continue waiting in this window.",
        "a2a_oauth_help": "OAuth/OIDC stores only environment-variable names in the project. Device tokens use Windows DPAPI or a mode-0600 user cache on Linux/macOS, so background runs can refresh them.",
        "a2a_delivery_help": "SSE is used before polling. Push callbacks require an authenticated HoH server endpoint reachable by the remote agent.",
        "new_a2a": "New connection",
        "save_a2a": "Save connection",
        "test_a2a": "Test connection",
        "remove_a2a": "Remove connection",
        "confirm_remove_a2a": "Remove A2A connection '{name}' from this project?",
        "a2a_security": "Use HTTPS except for localhost. Store only the environment-variable name here; the token value stays in this HoH process or OS user environment.",
        "select_registry_agent": "Select a Registry agent first.",
        "agent": "Agent / harness",
        "model": "Model (optional)",
        "driver": "Connection method (automatic)",
        "driver_help": "Selected from the agent's declared, role-safe transport; incompatible methods are not offered.",
        "driver_acp": "ACP · agent protocol",
        "driver_model_json": "Model API · strict JSON",
        "driver_command_json": "CLI · stdin / JSON stdout",
        "driver_command": "CLI · stdin / isolated worktree",
        "driver_process": "CLI profile · safe one-shot process",
        "driver_hermes_acp": "Hermes · ACP",
        "driver_claude_code": "Claude Code CLI · safe mode",
        "driver_openclaw": "OpenClaw CLI · safe workspace",
        "driver_manual": "Manual · review files",
        "driver_stub": "Built-in test stub",
        "supervisor": "Supervisor",
        "worker": "Worker",
        "critic": "Critic",
        "critic_enabled": "Enable independent Critic",
        "attempts": "Worker attempt limit",
        "save_roles": "Save three-role profile",
        "refresh": "Refresh ACP Registry",
        "save_config": "Validate and save project settings",
        "process_profiles_title": "Headless CLI profiles",
        "process_profiles_desc": "Connect one-shot coding CLIs without writing a Python adapter. Aider is bundled as a safe Worker profile.",
        "process_profiles_help": "Availability is checked from PATH. Use the form below for custom profiles; Advanced JSON is only the expert escape hatch. API keys stay in the process environment.",
        "process_profile_name": "Profile name",
        "process_command": "Executable command",
        "process_args": "Base arguments",
        "prompt_transport": "Prompt delivery",
        "prompt_argument": "Prompt-file flag",
        "required_args": "Required safe arguments",
        "forbidden_args": "Forbidden arguments",
        "model_argument": "Model flag",
        "version_args": "Version-check arguments",
        "args_help": "Enter one argument per line. HoH rejects secret-bearing, conflicting, and prompt-owning flags.",
        "save_process_profile": "Save CLI profile",
        "remove_process_profile": "Remove profile",
        "new_process_profile": "New profile",
        "confirm_remove_profile": "Remove process profile '{name}' from this project?",
        "reload_config": "Reload settings",
        "doctor": "Doctor",
        "status": "Supervisor status",
        "queue": "Queue",
        "history": "Run history",
        "audit_history": "Audit history",
        "run_next": "Run next task",
        "run_all": "Run queue",
        "retry": "Retry failed task",
        "recover": "Recover interrupted task",
        "requeue_escalated": "Requeue escalated task",
        "fail_escalated": "Close escalated task as failed",
        "audit": "Final audit",
        "compatibility": "Compatibility matrix",
        "check_role": "Check this role",
        "confirm_check_role": "Run the selected {role} once against a throwaway Git repository to see whether it works? This calls the configured agent or model. Nothing in your project is touched.",
        "output": "Operation output",
        "ready": "Ready",
        "readiness_checking": "Checking the project…",
        "readiness_ready": "Ready to run tasks.",
        "readiness_warning": "Runs, but not ready for real work: {detail}",
        "readiness_blocked": "Cannot run a task yet: {detail}",
        "readiness_unknown": "Could not check the project.",
        "loading": "Loading…",
        "running": "Running…",
        "saved": "Saved",
        "error": "Error",
        "error_detail": "Technical detail:",
        "error_permission": "HoH is not allowed to read or write that location. Check the file permissions, or pick a different directory.",
        "error_missing": "A file or directory HoH expected is not there. It may have been moved, renamed, or deleted since it was last used.",
        "error_value": "One of the values is not valid, so HoH refused to continue rather than save a broken configuration.",
        "error_timeout": "The operation took longer than its timeout and was stopped. Nothing was left half-applied.",
        "error_unexpected": "HoH stopped this operation because something unexpected happened. Nothing was applied.",
        "confirm_run": "Run the next queued Worker task? This may change and commit the project.",
        "confirm_run_all": "Run queued tasks until the queue is empty or blocked? This may change and commit the project.",
        "confirm_retry": "Requeue the selected failed task after operator review?",
        "confirm_recover": "Recover the selected running task only after confirming its previous process is no longer active?",
        "confirm_requeue_escalated": "Return the selected escalated task to the queue with a fresh attempt budget? State the reason in the reason field.",
        "confirm_fail_escalated": "Close the selected escalated task as failed? State the reason in the reason field.",
        "confirm_audit": "Run the deterministic final project audit?",
        "registry": "ACP Registry: {count} agents, version {version}",
        "queue_summary": "Queued {queued} · Running {running} · Review {review} · Failed {failed} · Done {done}",
        "no_project": "Select a Git project first.",
        "config_note": "Secrets are never stored here. Enter environment-variable names only.",
        "workspace": "WORKSPACE",
        "control_center": "CONTROL CENTER",
        "role_team": "Agent team",
        "role_team_desc": "Assign an independent harness and model to each responsibility.",
        "supervisor_desc": "Plans work and owns the final decision",
        "worker_desc": "Implements constrained project changes",
        "critic_desc": "Reviews immutable evidence independently",
        "required": "REQUIRED",
        "optional": "OPTIONAL",
        "policy": "Execution policy",
        "policy_desc": "Bound retries and keep final review independent.",
        "appearance": "APPEARANCE",
        "project_placeholder": "Select a Git repository",
        "config_desc": "Advanced runtime and safety policy in .hoh/harness.json",
        "operations_desc": "Diagnostics, live queue state, guarded execution, and release audit.",
        "metrics_title": "Agent performance and cost",
        "metrics_desc": "Immutable local totals by role, agent, and model. Provider-reported cost wins; configured model prices fill gaps without hidden rates.",
        "refresh_metrics": "Refresh metrics",
        "metrics_empty": "No agent invocations have been recorded for this project yet.",
        "metric_invocations": "Invocations",
        "metric_tokens": "Tokens",
        "metric_cost": "Cost",
        "metric_time": "Agent time",
        "metric_quality": "Quality",
        "metric_success": "success",
        "metric_unknown_tokens": "without token data",
        "metric_quality_na": "not rated",
        "metric_group_line": "{success}/{invocations} success · {tokens} tokens · {duration} · {cost} · quality {quality}",
        "pricing_title": "Model prices",
        "pricing_desc": "Optional price per one million tokens. Use the exact provider/model id or model '*' as a provider fallback. Provider-reported cost always wins.",
        "pricing_profile": "Configured rate",
        "pricing_provider": "Provider",
        "pricing_model": "Model",
        "pricing_input": "Input / 1M",
        "pricing_output": "Output / 1M",
        "pricing_currency": "Currency",
        "pricing_new": "New rate",
        "pricing_save": "Save rate",
        "pricing_remove": "Remove rate",
        "pricing_saved": "Model rate saved.",
        "pricing_removed": "Model rate removed.",
        "pricing_confirm_remove": "Remove the configured rate for {provider}/{model}?",
        "chat_title": "Talk to Supervisor",
        "chat_desc": "Describe the task, answer clarifying questions, and create the project plan without leaving HoH.",
        "chat_empty": "Start by describing what you want to build or change.",
        "chat_placeholder": "Write a task or answer Supervisor…",
        "send": "Send",
        "create_plan": "Create and enqueue plan",
        "new_chat": "New conversation",
        "confirm_plan": "Create project documentation, task files, and enqueue the agreed plan?",
        "confirm_clear_chat": "Clear this project's local Supervisor conversation?",
        "plan_created": "Plan created: {count} tasks enqueued.",
        "chat_not_ready": "Supervisor still has open questions. You can continue or create a draft plan explicitly.",
        "storage_title": "Where settings are stored",
        "storage_project": "Project settings",
        "storage_roles": "Role selection",
        "storage_chat": "Private chat history (outside Git)",
        "storage_ui": "UI preferences",
        "storage_secrets": "Secret values",
        "storage_secrets_value": "Environment, or the user profile under OS protection when you press Save — never the project",
        "session_secrets": "Provider credentials",
        "session_secrets_desc": "Paste an API key to use it. Use for this session keeps it in memory only, until this window closes. Save keeps it for next time in your user profile, protected by the operating system — never in the project, never in Git, never in operation output. An exported environment variable always wins over a saved one.",
        "set_session_secrets": "Use for this session",
        "save_credentials": "Save",
        "forget_credentials": "Forget saved",
        "credentials_saved": "Saved credentials: {count}.",
        "credentials_forgotten": "Forgotten credentials: {count}.",
        "credential_source_env": "environment",
        "credential_source_stored": "saved",
        "no_session_secrets": "No API-key variables are required by the current model endpoints.",
        "session_secrets_set": "Session credentials loaded: {count}.",
        "guided_settings": "Guided settings",
        "guided_settings_desc": "Common project options with validation and explanations. Unlisted options remain unchanged.",
        "supervisor_endpoint": "Supervisor model endpoint",
        "verifier_endpoint": "Verifier / direct Critic endpoint",
        "provider": "Provider",
        "base_url": "Base URL",
        "api_key_env": "API key variable name",
        "provider_help": "stub is offline; OpenAI and DeepSeek use HTTPS; openai_compatible requires an explicit URL.",
        "model_help": "Exact provider model id. Select direct-supervisor-model or direct-critic-model to use it as that role.",
        "base_url_help": "Leave empty for the provider default. HTTP is allowed only on localhost.",
        "api_key_env_help": "Variable name only, for example OPENAI_API_KEY. Its secret value stays in the OS user environment.",
        "runtime_safety": "Runtime and safety",
        "embedded_python": "Require bundled Python runtime",
        "python_path": "Bundled Python path",
        "wheels_path": "Offline wheelhouse path",
        "require_tests_label": "Require task verification commands",
        "trust_level": "Worker trust level",
        "parallel_tasks": "Maximum parallel tasks",
        "trust_help": "patch_only is safest: Worker cannot own canonical Git commits.",
        "check_env": "Check variables",
        "env_all_set": "All required environment variables are defined.",
        "env_missing": "Missing: {names}. Define them in the OS user environment or before launching HoH.",
        "advanced_json": "Advanced raw JSON",
        "save_guided": "Save guided settings",
        "save_raw": "Validate and save raw JSON",
        "agent_ready": "Ready · {detail}",
        "agent_missing": "Unavailable · {detail}",
        "agent_unknown": "Not found in current catalog",
        "available_agents": "{count} runnable choices",
        "you": "You",
        "supervisor_speaker": "Supervisor",
        "ready_badge": "Ready to create a plan",
        "save_roles_first": "Save the Supervisor/Worker role profile before starting the chat.",
        "roles_not_ready": "One or more selected roles are no longer available. Reopen Roles, choose available agents, and save the profile again.",
        "critic_not_enabled": "Enable and save an independent Critic before checking that role.",
        "operator_task": "Task",
        "recovery_reason": "Recovery reason",
        "recovery_reason_default": "Previous Worker process was confirmed stopped by the operator.",
    },
    "ru": {
        "app_title": "HoH — универсальный агентный харнесс",
        "project": "Проект",
        "browse": "Обзор…",
        "load": "Открыть",
        "language": "Язык",
        "theme": "Тема",
        "roles": "Роли",
        "setup": "Первый запуск",
        "chat": "Чат с Supervisor",
        "config": "Настройки проекта",
        "operations": "Операции",
        "metrics": "Метрики",
        "projects": "Проекты",
        "agents_setup": "Агенты и подключения",
        "tools": "Политики MCP",
        "mcp_title": "Инструменты по ролям",
        "mcp_desc": "Задайте, какие категории ACP-инструментов и MCP-серверы доступны каждой роли. Неизвестные и неразрешённые инструменты блокируются до выполнения агентом.",
        "mcp_role": "Политика роли",
        "mcp_mode": "Режим разрешений",
        "mcp_mode_help": "reject блокирует все запросы. allow_once разрешает только отмеченные категории и никогда не выдаёт постоянное разрешение.",
        "mcp_kinds": "Разрешённые категории ACP",
        "mcp_save_policy": "Сохранить политику роли",
        "mcp_server": "MCP-сервер",
        "mcp_server_name": "Имя сервера",
        "mcp_transport": "Транспорт",
        "mcp_command": "Команда запуска (stdio)",
        "mcp_url": "HTTPS URL (HTTP/SSE)",
        "mcp_args": "Аргументы, по одному в строке",
        "mcp_env": "Переменные процесса: TARGET=SOURCE_ENV",
        "mcp_headers": "HTTP-заголовки: Header=SOURCE_ENV",
        "mcp_secret_help": "В проекте хранятся только имена переменных. Значения подставляются на время сессии и скрываются в журнале протокола.",
        "mcp_new": "Новый сервер",
        "mcp_save_server": "Сохранить сервер",
        "mcp_remove_server": "Удалить сервер",
        "mcp_confirm_remove": "Удалить MCP-сервер «{name}» из политики {role}?",
        "mcp_saved": "Политика MCP сохранена.",
        "updates_title": "Доверенные обновления приложения",
        "updates_desc": "HoH принимает только HTTPS-каталог релизов, подписанный явно импортированным издателем. Хеш пакета проверяется до активации новой пользовательской версии; запущенная версия не перезаписывается.",
        "updates_url": "URL подписанного каталога релизов",
        "updates_check": "Проверить обновление",
        "updates_install": "Скачать и установить",
        "updates_trust": "Импортировать ключ издателя",
        "updates_idle": "Текущая версия готова. Укажите канал, чтобы проверять обновления.",
        "updates_current": "Текущая версия {current}; более нового доверенного релиза нет.",
        "updates_available": "Доступно доверенное обновление {version} ({size}).",
        "updates_installed": "HoH {version} установлен рядом с текущей версией: {path}. Перезапустите приложение через установленный ярлык.",
        "updates_publisher": "Издатель {key_id} добавлен в доверенные для подписанных каталогов.",
        "updates_confirm": "Скачать, проверить и установить новый релиз рядом с текущей версией?",
        "updates_auto": "Автоматически ставить доверенные обновления рядом с текущей версией при запуске",
        "projects_title": "Центр управления проектами",
        "projects_desc": "Единая пользовательская панель зарегистрированных Git-проектов, их реальных очередей, расписаний и последних фоновых запусков.",
        "projects_registered": "Зарегистрированные проекты",
        "projects_register_current": "Добавить открытый проект",
        "projects_open": "Открыть выбранный",
        "projects_remove": "Убрать из списка",
        "projects_refresh": "Обновить сводку",
        "projects_run_now": "Запустить выбранный",
        "projects_run_due": "Запустить по расписанию",
        "projects_empty": "Проекты ещё не зарегистрированы. Откройте Git-проект выше и добавьте его.",
        "projects_queue_line": "в очереди {queued} · готовы {ready} · выполняются {running} · требуют внимания {attention} · ошибки {failed} · готово {done}",
        "projects_next_task": "Следующая: {task}",
        "projects_no_next_task": "Нет готовой задачи",
        "projects_schedule": "Фоновое расписание",
        "projects_schedule_desc": "Запускает существующий защищённый цикл очереди проекта с заданным интервалом. Блокировки проекта по-прежнему исключают параллельные Git-операции.",
        "projects_schedule_enabled": "Включить расписание",
        "projects_interval": "Интервал, минут",
        "projects_final_audit": "Финальный аудит при пустой очереди",
        "projects_notify_windows": "Системное уведомление",
        "projects_notify_telegram": "Сводка в Telegram",
        "projects_schedule_save": "Сохранить расписание",
        "projects_service_install": "Включить фоновый запуск",
        "projects_service_remove": "Отключить фоновый запуск",
        "projects_service_status": "Проверить фоновый запуск",
        "projects_service_result": "Фоновый запуск: {detail}",
        "projects_schedule_saved": "Расписание проекта сохранено.",
        "projects_registered_status": "Проект добавлен.",
        "projects_removed_status": "Проект убран из списка; файлы и очередь не удалены.",
        "projects_run_result": "{name}: статус={status}, выполнено={completed}",
        "projects_confirm_remove": "Убрать {name} из списка проектов? Файлы проекта и история очереди останутся на месте.",
        "setup_title": "Подготовка HoH к реальной работе",
        "setup_desc": "Три шага до первой задачи. Всё остальное может подождать до того момента, когда понадобится.",
        "setup_step_project": "1. Проект",
        "setup_step_project_desc": "Выберите Git-репозиторий, в котором будет работать HoH. Ничего за его пределами не затрагивается.",
        "open_project": "Открыть",
        "setup_step_team": "2. Команда",
        "setup_step_team_desc": "Выберите, кто будет работать. HoH сам установит нужных агентов и назначит три роли. Войдите у вендора, если агент этого попросит.",
        "setup_prepare_team": "Подготовить команду",
        "setup_step_task": "3. Задача",
        "setup_step_task_desc": "Опишите, что нужно сделать. Supervisor задаст уточняющие вопросы и превратит разговор в план и очередь.",
        "setup_open_chat": "Описать задачу",
        "setup_optional": "Всё остальное — когда понадобится",
        "setup_optional_desc": "Ничего из этого не нужно для старта. Оно остаётся доступным для проектов и конфигураций, которым это нужно.",
        "setup_team_ready": "Команда готова. Установлено сейчас: {installed}. Уже было: {already}.",
        "setup_preset": "Готовый состав команды",
        "setup_required_agents": "Нужные агенты: {names}",
        "setup_open_login": "Войти у вендора",
        "setup_finish": "Завершить первый запуск",
        "setup_complete": "Первый запуск завершён. Команда установлена, назначена и live-сертифицирована на этом компьютере.",
        "setup_incomplete": "Настройка ещё не завершена: {detail}",
        "setup_confirm_install": "Установить или обновить этих управляемых агентов для текущего пользователя?\n\n{names}",
        "setup_roles_saved": "Пресет применён, профиль трёх ролей сохранён.",
        "setup_status_ready": "Готово",
        "setup_status_pending": "Ожидается",
        "driver_a2a": "A2A · удалённый агент",
        "agents_setup_title": "Агенты и подключения",
        "agents_setup_desc": "Устанавливайте ACP-агентов для текущего пользователя ОС, входите через вендора или подключайте удалённого агента A2A v1.",
        "managed_agents": "Управляемые ACP-агенты",
        "managed_agents_desc": "HoH ставит точную версию из Registry в профиль текущего пользователя. Для прямой загрузки обязателен опубликованный SHA-256; небезопасные пакеты блокируются.",
        "registry_agent": "Агент из Registry",
        "agent_description": "Описание",
        "install_agent": "Установить / обновить",
        "remove_agent": "Удалить локальную копию",
        "inspect_login": "Проверить способы входа",
        "login_agent": "Войти",
        "auth_method": "Способ входа",
        "refresh_agents": "Обновить Registry и статусы",
        "managed_agent_home": "Файлы агентов: %LOCALAPPDATA%\\HoH\\agents. Учётные данные остаются в защищённом хранилище самого вендора.",
        "agent_installed": "Установлен · {detail}",
        "agent_installable": "Не установлен · безопасная установка доступна · {detail}",
        "agent_blocked_install": "Не установлен · автоустановка заблокирована · {detail}",
        "agent_removed": "Управляемая копия удалена.",
        "auth_none": "Агент не объявил способ входа ACP. Кнопка входа использует известный CLI-сценарий вендора, если он поддерживается.",
        "auth_variables": "Ключи только для этого окна",
        "a2a_agents": "Удалённые агенты A2A v1",
        "a2a_agents_desc": "Подключите сервис через публичную Agent Card. Доступны Supervisor, Worker и Critic; удалённый Worker получает только явно разрешённые текстовые файлы.",
        "a2a_profile": "Имя подключения",
        "a2a_card_url": "URL Agent Card",
        "a2a_roles": "Разрешённые роли",
        "a2a_auth": "Аутентификация",
        "a2a_credential_env": "Переменная с ключом",
        "a2a_header": "Заголовок API-ключа",
        "a2a_timeout": "Таймаут задачи, секунд",
        "a2a_poll": "Интервал опроса, секунд",
        "a2a_oauth_flow": "OAuth flow",
        "a2a_client_id_env": "Переменная Client ID",
        "a2a_client_secret_env": "Переменная client secret",
        "a2a_token_url": "URL OAuth token",
        "a2a_device_url": "URL device authorization",
        "a2a_oidc_url": "URL OIDC discovery",
        "a2a_scopes": "OAuth scopes через пробел",
        "a2a_client_auth": "Аутентификация клиента",
        "a2a_streaming": "Предпочитать SSE streaming, если он объявлен",
        "a2a_push_url": "URL push callback",
        "a2a_push_token_env": "Переменная push-токена",
        "a2a_login": "Вход OAuth / OIDC",
        "a2a_device_prompt": "Откройте {url} и введите код {code}. HoH продолжит ждать в этом окне.",
        "a2a_oauth_help": "В проекте сохраняются только имена переменных. Device-токены защищены Windows DPAPI либо правами 0600 в Linux/macOS, поэтому фоновые запуски могут их обновлять.",
        "a2a_delivery_help": "SSE используется раньше polling. Для push нужен аутентифицированный endpoint HoH, доступный удалённому агенту.",
        "new_a2a": "Новое подключение",
        "save_a2a": "Сохранить подключение",
        "test_a2a": "Проверить соединение",
        "remove_a2a": "Удалить подключение",
        "confirm_remove_a2a": "Удалить A2A-подключение «{name}» из проекта?",
        "a2a_security": "Используйте HTTPS, кроме localhost. Здесь хранится только имя переменной окружения; токен остаётся в процессе HoH или пользовательских переменных ОС.",
        "select_registry_agent": "Сначала выберите агента из Registry.",
        "agent": "Агент / харнесс",
        "model": "Модель (необязательно)",
        "driver": "Способ связи (автоматически)",
        "driver_help": "Берётся из безопасного транспорта агента для этой роли; несовместимые варианты не предлагаются.",
        "driver_acp": "ACP · протокол агентов",
        "driver_model_json": "API модели · строгий JSON",
        "driver_command_json": "CLI · stdin / JSON stdout",
        "driver_command": "CLI · stdin / изолированный worktree",
        "driver_process": "CLI-профиль · безопасный one-shot процесс",
        "driver_hermes_acp": "Hermes · ACP",
        "driver_claude_code": "Claude Code CLI · безопасный режим",
        "driver_openclaw": "OpenClaw CLI · безопасный workspace",
        "driver_manual": "Вручную · файлы ревью",
        "driver_stub": "Встроенный тестовый stub",
        "supervisor": "Supervisor",
        "worker": "Worker",
        "critic": "Critic",
        "critic_enabled": "Включить независимого Critic",
        "attempts": "Лимит попыток Worker",
        "save_roles": "Сохранить профиль трёх ролей",
        "refresh": "Обновить ACP Registry",
        "save_config": "Проверить и сохранить настройки",
        "process_profiles_title": "Профили headless CLI",
        "process_profiles_desc": "Подключайте one-shot coding CLI без отдельного Python-адаптера. Aider уже описан безопасным профилем Worker.",
        "process_profiles_help": "Доступность проверяется через PATH. Свои профили создавайте формой ниже; расширенный JSON нужен только экспертам. API-ключи остаются в окружении процесса.",
        "process_profile_name": "Имя профиля",
        "process_command": "Исполняемая команда",
        "process_args": "Базовые аргументы",
        "prompt_transport": "Передача задания",
        "prompt_argument": "Флаг файла задания",
        "required_args": "Обязательные безопасные аргументы",
        "forbidden_args": "Запрещённые аргументы",
        "model_argument": "Флаг модели",
        "version_args": "Аргументы проверки версии",
        "args_help": "Один аргумент на строку. HoH отклоняет секреты, конфликты и флаги, которыми должен владеть сам.",
        "save_process_profile": "Сохранить CLI-профиль",
        "remove_process_profile": "Удалить профиль",
        "new_process_profile": "Новый профиль",
        "confirm_remove_profile": "Удалить process-профиль «{name}» из этого проекта?",
        "reload_config": "Перечитать настройки",
        "doctor": "Диагностика",
        "status": "Статус Supervisor",
        "queue": "Очередь",
        "history": "История запусков",
        "audit_history": "История аудитов",
        "run_next": "Запустить следующую задачу",
        "run_all": "Выполнить очередь",
        "retry": "Повторить ошибочную задачу",
        "recover": "Восстановить прерванную задачу",
        "requeue_escalated": "Вернуть эскалированную в очередь",
        "fail_escalated": "Закрыть эскалированную как ошибочную",
        "audit": "Финальный аудит",
        "compatibility": "Матрица совместимости",
        "check_role": "Проверить роль",
        "confirm_check_role": "Один раз запустить роль {role} в одноразовом Git-репозитории, чтобы увидеть, работает ли она? Будет вызван настроенный агент или модель. Ваш проект не затрагивается.",
        "output": "Вывод операции",
        "ready": "Готово",
        "readiness_checking": "Проверяю проект…",
        "readiness_ready": "Готов выполнять задачи.",
        "readiness_warning": "Запустится, но к реальной работе не готов: {detail}",
        "readiness_blocked": "Задачу пока выполнить нельзя: {detail}",
        "readiness_unknown": "Не удалось проверить проект.",
        "loading": "Загрузка…",
        "running": "Выполняется…",
        "saved": "Сохранено",
        "error": "Ошибка",
        "error_detail": "Техническая подробность:",
        "error_permission": "У HoH нет прав на чтение или запись по этому пути. Проверьте права доступа или выберите другой каталог.",
        "error_missing": "Файла или каталога, который ожидал HoH, нет на месте. Возможно, его переместили, переименовали или удалили.",
        "error_value": "Одно из значений некорректно, и HoH отказался продолжать, чтобы не сохранить сломанную конфигурацию.",
        "error_timeout": "Операция превысила таймаут и была остановлена. Ничего не осталось применённым наполовину.",
        "error_unexpected": "HoH остановил операцию из-за непредвиденной ошибки. Ничего не применено.",
        "confirm_run": "Запустить следующую задачу Worker? Операция может изменить проект и создать коммит.",
        "confirm_run_all": "Выполнять задачи до опустошения или блокировки очереди? Операции могут изменить проект и создать коммиты.",
        "confirm_retry": "Вернуть выбранную ошибочную задачу в очередь после проверки оператором?",
        "confirm_recover": "Восстановить выбранную выполняющуюся задачу только после подтверждения, что прежний процесс уже остановлен?",
        "confirm_requeue_escalated": "Вернуть выбранную эскалированную задачу в очередь с обнулённым счётчиком попыток? Укажите причину в поле причины.",
        "confirm_fail_escalated": "Закрыть выбранную эскалированную задачу как ошибочную? Укажите причину в поле причины.",
        "confirm_audit": "Запустить детерминированный финальный аудит проекта?",
        "registry": "ACP Registry: агентов {count}, версия {version}",
        "queue_summary": "В очереди {queued} · Выполняется {running} · На ревью {review} · Ошибок {failed} · Готово {done}",
        "no_project": "Сначала выберите Git-проект.",
        "config_note": "Секреты здесь не хранятся. Указывайте только имена переменных окружения.",
        "workspace": "РАБОЧАЯ ОБЛАСТЬ",
        "control_center": "ЦЕНТР УПРАВЛЕНИЯ",
        "role_team": "Команда агентов",
        "role_team_desc": "Назначьте независимый харнесс и модель для каждой ответственности.",
        "supervisor_desc": "Планирует работу и принимает итоговое решение",
        "worker_desc": "Выполняет ограниченные изменения проекта",
        "critic_desc": "Независимо проверяет неизменяемые доказательства",
        "required": "ОБЯЗАТЕЛЬНО",
        "optional": "ОПЦИОНАЛЬНО",
        "policy": "Политика выполнения",
        "policy_desc": "Ограничьте повторы и сохраните независимость финальной проверки.",
        "appearance": "ОФОРМЛЕНИЕ",
        "project_placeholder": "Выберите Git-репозиторий",
        "config_desc": "Расширенные настройки runtime и безопасности в .hoh/harness.json",
        "operations_desc": "Диагностика, очередь, управляемый запуск и релизный аудит.",
        "metrics_title": "Эффективность и стоимость агентов",
        "metrics_desc": "Неизменяемая локальная статистика по ролям, агентам и моделям. Стоимость провайдера приоритетна; заданные пользователем тарифы заполняют пробелы без скрытых цен.",
        "refresh_metrics": "Обновить метрики",
        "metrics_empty": "В этом проекте пока не записано ни одного вызова агента.",
        "metric_invocations": "Вызовы",
        "metric_tokens": "Токены",
        "metric_cost": "Стоимость",
        "metric_time": "Время агентов",
        "metric_quality": "Качество",
        "metric_success": "успешно",
        "metric_unknown_tokens": "без данных о токенах",
        "metric_quality_na": "нет оценки",
        "metric_group_line": "успешно {success}/{invocations} · {tokens} токенов · {duration} · {cost} · качество {quality}",
        "pricing_title": "Тарифы моделей",
        "pricing_desc": "Необязательная цена за миллион токенов. Укажите точные provider/model или модель '*' как резерв для провайдера. Стоимость, сообщённая провайдером, всегда приоритетна.",
        "pricing_profile": "Настроенный тариф",
        "pricing_provider": "Провайдер",
        "pricing_model": "Модель",
        "pricing_input": "Вход / 1 млн",
        "pricing_output": "Выход / 1 млн",
        "pricing_currency": "Валюта",
        "pricing_new": "Новый тариф",
        "pricing_save": "Сохранить тариф",
        "pricing_remove": "Удалить тариф",
        "pricing_saved": "Тариф модели сохранён.",
        "pricing_removed": "Тариф модели удалён.",
        "pricing_confirm_remove": "Удалить тариф для {provider}/{model}?",
        "chat_title": "Диалог с Supervisor",
        "chat_desc": "Опишите задачу, ответьте на уточнения и создайте план проекта, не выходя из HoH.",
        "chat_empty": "Начните с описания того, что нужно создать или изменить.",
        "chat_placeholder": "Напишите задачу или ответ Supervisor…",
        "send": "Отправить",
        "create_plan": "Создать план и очередь",
        "new_chat": "Новый диалог",
        "confirm_plan": "Создать документацию проекта, файлы задач и поставить согласованный план в очередь?",
        "confirm_clear_chat": "Очистить локальную переписку с Supervisor для этого проекта?",
        "plan_created": "План создан: задач в очереди — {count}.",
        "chat_not_ready": "У Supervisor ещё есть вопросы. Можно продолжить диалог или явно создать черновой план.",
        "storage_title": "Где хранятся настройки",
        "storage_project": "Настройки проекта",
        "storage_roles": "Выбор ролей",
        "storage_chat": "Приватная история чата (вне Git)",
        "storage_ui": "Настройки интерфейса",
        "storage_secrets": "Секретные значения",
        "storage_secrets_value": "Переменные окружения или профиль пользователя под защитой ОС по кнопке «Сохранить» — но не проект",
        "session_secrets": "Ключи провайдеров",
        "session_secrets_desc": "Вставьте API-ключ, чтобы им пользоваться. «Использовать в этом сеансе» держит его только в памяти до закрытия окна. «Сохранить» оставляет его на следующий раз в вашем профиле под защитой ОС — не в проекте, не в Git и не в выводе операций. Переменная окружения всегда имеет приоритет над сохранённым значением.",
        "set_session_secrets": "Использовать в этом сеансе",
        "save_credentials": "Сохранить",
        "forget_credentials": "Удалить сохранённые",
        "credentials_saved": "Сохранено ключей: {count}.",
        "credentials_forgotten": "Удалено ключей: {count}.",
        "credential_source_env": "окружение",
        "credential_source_stored": "сохранён",
        "no_session_secrets": "Текущим endpoints моделей API-ключи не требуются.",
        "session_secrets_set": "Ключи сеанса загружены: {count}.",
        "guided_settings": "Понятные настройки",
        "guided_settings_desc": "Основные параметры проекта с проверкой и объяснениями. Остальные параметры не изменяются.",
        "supervisor_endpoint": "Модель для Supervisor",
        "verifier_endpoint": "Модель для Verifier / прямого Critic",
        "provider": "Провайдер",
        "base_url": "Базовый URL",
        "api_key_env": "Имя переменной API-ключа",
        "provider_help": "stub работает без сети; OpenAI и DeepSeek используют HTTPS; openai_compatible требует URL.",
        "model_help": "Точный ID модели. Выберите direct-supervisor-model или direct-critic-model, чтобы назначить её на эту роль.",
        "base_url_help": "Оставьте пустым для стандартного адреса. HTTP разрешён только для localhost.",
        "api_key_env_help": "Только имя, например OPENAI_API_KEY. Сам секрет остаётся в пользовательских переменных ОС.",
        "runtime_safety": "Runtime и безопасность",
        "embedded_python": "Требовать встроенный Python runtime",
        "python_path": "Путь к встроенному Python",
        "wheels_path": "Путь к offline wheelhouse",
        "require_tests_label": "Требовать команды проверки задач",
        "trust_level": "Уровень доверия Worker",
        "parallel_tasks": "Максимум параллельных задач",
        "trust_help": "patch_only — самый безопасный режим: Worker не владеет коммитами основного Git.",
        "check_env": "Проверить переменные",
        "env_all_set": "Все обязательные переменные окружения определены.",
        "env_missing": "Не найдены: {names}. Задайте их в пользовательских переменных ОС или до запуска HoH.",
        "advanced_json": "Дополнительно: raw JSON",
        "save_guided": "Сохранить понятные настройки",
        "save_raw": "Проверить и сохранить raw JSON",
        "agent_ready": "Готов · {detail}",
        "agent_missing": "Недоступен · {detail}",
        "agent_unknown": "Не найден в текущем каталоге",
        "available_agents": "Доступных вариантов: {count}",
        "you": "Вы",
        "supervisor_speaker": "Supervisor",
        "ready_badge": "Можно создавать план",
        "save_roles_first": "Перед началом диалога сохраните профиль ролей Supervisor/Worker.",
        "roles_not_ready": "Одна или несколько выбранных ролей больше недоступны. Откройте «Роли», выберите доступных агентов и снова сохраните профиль.",
        "critic_not_enabled": "Включите и сохраните независимого Critic перед проверкой этой роли.",
        "operator_task": "Задача",
        "recovery_reason": "Причина восстановления",
        "recovery_reason_default": "Оператор подтвердил, что предыдущий процесс Worker остановлен.",
    },
}


def driver_display_name(locale: str, driver_id: str) -> str:
    """Return a user-facing transport label while preserving the internal id visibly."""
    normalized = driver_id.strip().casefold()
    if not normalized:
        return "—"
    translations = TEXT.get(locale, TEXT["en"])
    label = translations.get(f"driver_{normalized}")
    return f"{label} [{normalized}]" if label else normalized


PALETTES = {
    "dark": {
        "background": "#0b1020",
        "sidebar": "#0f172a",
        "surface": "#151d31",
        "surface_alt": "#1b2640",
        "foreground": "#f8fafc",
        "muted": "#94a3b8",
        "accent": "#4f7cff",
        "accent_hover": "#638bff",
        "selection": "#263b77",
        "border": "#273550",
        "success": "#34d399",
        "warning": "#fbbf24",
        "shadow": "#050814",
        "surface_hover": "#1a2340",
        "popup": "#1a2340",
        "popup_hover": "#243056",
        "field": "#111a2e",
        "field_hover": "#16203a",
    },
    "light": {
        "background": "#f4f7fb",
        "sidebar": "#ffffff",
        "surface": "#ffffff",
        "surface_alt": "#eef3fa",
        "foreground": "#111827",
        "muted": "#64748b",
        "accent": "#315efb",
        "accent_hover": "#244bd4",
        "selection": "#dbe5ff",
        "border": "#dbe3ef",
        "success": "#059669",
        "warning": "#d97706",
        "shadow": "#cbd5e1",
        "surface_hover": "#fbfdff",
        "popup": "#ffffff",
        "popup_hover": "#eef3fa",
        "field": "#f7f9fd",
        "field_hover": "#ffffff",
    },
}


MINIMUM_WRAP_WIDTH = 220
LABEL_WRAP_PADDING = 24


class HohDesktopApp:
    def __init__(self, root: tk.Tk, settings: GuiSettings, *, auto_load: bool = True) -> None:
        self.root = root
        self.settings = settings
        self.display_scaling = apply_tk_scaling(root)
        self._wrapping_labels: list[tuple[ttk.Label, int]] = []
        self._syncing_geometry = False
        self._geometry_sync_pending: str | None = None
        self.fonts = resolve_font_scheme(root)
        self.snapshot = None
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hoh-gui")
        self.project_var = tk.StringVar(value=settings.last_project_root)
        self.locale_var = tk.StringVar(value=settings.locale)
        self.theme_var = tk.StringVar(value=settings.theme)
        self.critic_enabled_var = tk.BooleanVar(value=True)
        self.attempts_var = tk.StringVar(value="3")
        self.role_vars = {
            role: {
                "agent": tk.StringVar(),
                "model": tk.StringVar(),
                "driver": tk.StringVar(),
            }
            for role in ("supervisor", "worker", "critic")
        }
        self.role_driver_display_vars = {
            role: tk.StringVar() for role in ("supervisor", "worker", "critic")
        }
        self.role_status_vars = {role: tk.StringVar() for role in ("supervisor", "worker", "critic")}
        self.role_status_labels: dict[str, ttk.Label] = {}
        self.settings_vars = {
            "supervisor_provider": tk.StringVar(value="stub"),
            "supervisor_model": tk.StringVar(value="supervisor-stub"),
            "supervisor_base_url": tk.StringVar(),
            "supervisor_api_key_env": tk.StringVar(),
            "verifier_provider": tk.StringVar(value="stub"),
            "verifier_model": tk.StringVar(value="verifier-stub"),
            "verifier_base_url": tk.StringVar(),
            "verifier_api_key_env": tk.StringVar(),
            "require_embedded_python": tk.BooleanVar(value=True),
            "embedded_python_path": tk.StringVar(value="runtime/python/python.exe"),
            "wheels_path": tk.StringVar(value="vendor/wheels"),
            "require_tests": tk.BooleanVar(value=True),
            "worker_trust_level": tk.StringVar(value="patch_only"),
            "max_parallel_tasks": tk.StringVar(value="1"),
        }
        self.chat_ready_var = tk.BooleanVar(value=False)
        self.preset_var = tk.StringVar()
        self.preset_description_var = tk.StringVar()
        self.preset_agents_var = tk.StringVar()
        self.preset_display_ids: dict[str, str] = {}
        self.setup_status_vars = {
            key: tk.StringVar(value=self.tr("setup_status_pending"))
            for key in ("project", "team", "task")
        }
        self.setup_progress = None
        self.process_profiles_var = tk.StringVar(value="—")
        self.process_vars = {
            "name": tk.StringVar(),
            "command": tk.StringVar(),
            "prompt_transport": tk.StringVar(value="stdin"),
            "prompt_argument": tk.StringVar(),
            "model_argument": tk.StringVar(),
        }
        self.process_profile_box: ttk.Combobox | None = None
        self.process_arg_editors: dict[str, tk.Text] = {}
        self.registry_agent_var = tk.StringVar()
        self.registry_agent_status_var = tk.StringVar(value="—")
        self.registry_agent_description_var = tk.StringVar(value="—")
        self.registry_agent_box: ttk.Combobox | None = None
        self.registry_agent_ids: dict[str, str] = {}
        self.registry_agent_records: dict[str, tuple[Any, Any]] = {}
        self.auth_method_var = tk.StringVar()
        self.auth_method_box: ttk.Combobox | None = None
        self.auth_methods: dict[str, Any] = {}
        self.auth_variables_frame: ttk.Frame | None = None
        self.auth_variable_vars: dict[str, tk.StringVar] = {}
        self.a2a_profile_var = tk.StringVar()
        self.a2a_profile_box: ttk.Combobox | None = None
        self.a2a_vars = {
            "name": tk.StringVar(),
            "card_url": tk.StringVar(),
            "auth_kind": tk.StringVar(value="none"),
            "credential_env": tk.StringVar(),
            "api_key_header": tk.StringVar(value="X-API-Key"),
            "oauth_flow": tk.StringVar(value="client_credentials"),
            "client_id_env": tk.StringVar(),
            "client_secret_env": tk.StringVar(),
            "token_url": tk.StringVar(),
            "device_authorization_url": tk.StringVar(),
            "oidc_discovery_url": tk.StringVar(),
            "scopes": tk.StringVar(),
            "client_auth_method": tk.StringVar(value="basic"),
            "prefer_streaming": tk.BooleanVar(value=True),
            "push_callback_url": tk.StringVar(),
            "push_token_env": tk.StringVar(),
            "timeout_seconds": tk.StringVar(value="300"),
            "poll_interval_seconds": tk.StringVar(value="1"),
        }
        self.a2a_role_vars = {
            role: tk.BooleanVar(value=True) for role in ("supervisor", "worker", "critic")
        }
        self.mcp_role_var = tk.StringVar(value="worker")
        self.mcp_mode_var = tk.StringVar(value="allow_once")
        self.mcp_kind_vars = {
            kind: tk.BooleanVar(value=True) for kind in ACP_TOOL_KINDS
        }
        self.mcp_server_var = tk.StringVar()
        self.mcp_server_box: ttk.Combobox | None = None
        self.mcp_server_vars = {
            "name": tk.StringVar(),
            "transport": tk.StringVar(value="stdio"),
            "command": tk.StringVar(),
            "url": tk.StringVar(),
        }
        self.mcp_text_editors: dict[str, tk.Text] = {}
        self.session_secrets_frame: ttk.Frame | None = None
        self.session_secret_vars: dict[str, tk.StringVar] = {}
        self.operator_task_var = tk.StringVar()
        self.operator_task_box: ttk.Combobox | None = None
        self.operator_task_ids: dict[str, str] = {}
        self.recovery_reason_var = tk.StringVar(value=self.tr("recovery_reason_default"))
        self.metrics_kpi_vars = {
            key: tk.StringVar(value="—")
            for key in ("invocations", "tokens", "cost", "time", "quality")
        }
        self.metrics_groups_frame: ttk.Frame | None = None
        self.price_profile_var = tk.StringVar()
        self.price_profile_box: ttk.Combobox | None = None
        self.price_profile_keys: dict[str, tuple[str, str]] = {}
        self.price_vars = {
            "provider": tk.StringVar(),
            "model": tk.StringVar(),
            "input_per_million": tk.StringVar(value="0"),
            "output_per_million": tk.StringVar(value="0"),
            "currency": tk.StringVar(value="USD"),
        }
        self.workspace_project_var = tk.StringVar()
        self.workspace_project_box: ttk.Combobox | None = None
        self.workspace_project_ids: dict[str, str] = {}
        self.workspace_project_roots: dict[str, str] = {}
        self.workspace_project_records: dict[str, Any] = {}
        self.workspace_cards_frame: ttk.Frame | None = None
        self.workspace_schedule_vars = {
            "enabled": tk.BooleanVar(value=False),
            "interval_minutes": tk.StringVar(value="60"),
            "final_audit": tk.BooleanVar(value=False),
            "notify_windows": tk.BooleanVar(value=True),
            "notify_telegram": tk.BooleanVar(value=False),
        }
        self.workspace_service_var = tk.StringVar(value="—")
        self.update_catalog_var = tk.StringVar(value=settings.update_catalog_url)
        self.automatic_updates_var = tk.BooleanVar(value=settings.automatic_updates)
        self.update_status_var = tk.StringVar(value=self.tr("updates_idle"))
        self.available_update_version: str | None = None
        self._pending_operation_output: str | None = None
        self.storage_vars = {
            key: tk.StringVar(value="—")
            for key in ("project", "roles", "chat", "ui")
        }
        self.status_var = tk.StringVar(value="")
        self.readiness_var = tk.StringVar(value="")
        self.role_boxes: dict[str, ttk.Combobox] = {}
        self.config_editor: tk.Text | None = None
        self.output_editor: tk.Text | None = None
        self.chat_editor: tk.Text | None = None
        self.chat_input: tk.Text | None = None
        self.advanced_config_shell: tk.Frame | None = None
        self.save_roles_button: ttk.Button | None = None
        self.pages: dict[str, ttk.Frame] = {}
        self.nav_buttons: dict[str, ttk.Button] = {}
        self.current_page = "chat" if settings.onboarding_completed else "setup"
        self._icon_image: tk.PhotoImage | None = None
        self.sidebar: ttk.Frame | None = None
        self.brand_copy: ttk.Frame | None = None
        self.appearance_panel: ttk.Frame | None = None
        self.appearance_label: ttk.Label | None = None
        self.content_canvas: tk.Canvas | None = None
        self.content_host_window: int | None = None
        self.cards_frame: ttk.Frame | None = None
        self.role_card_shells: dict[str, tk.Frame] = {}
        self.connection_cards_frame: ttk.Frame | None = None
        self.connection_card_shells: tuple[tk.Frame, tk.Frame] | None = None
        self._resize_after: str | None = None
        self._sidebar_compact = False
        self._full_sidebar_width = 248
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        if auto_load and self.project_var.get():
            self.load_project()
        if auto_load and settings.automatic_updates and settings.update_catalog_url.strip():
            self.root.after(1200, self._run_automatic_update)

    def _apply_window_geometry(self) -> None:
        """Size the window from this screen, or restore the size the user last chose."""
        width, height = preferred_window_geometry(
            self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        )
        remembered = parse_window_geometry(self.settings.window_geometry)
        if remembered is not None:
            width, height = remembered
        self.root.geometry(format_window_geometry(width, height))
        # The minimum yields to a small screen for the same reason the preferred size does.
        self.root.minsize(min(MINIMUM_WINDOW_SIZE[0], width), min(MINIMUM_WINDOW_SIZE[1], height))

    def _current_window_geometry(self) -> str:
        try:
            width = self.root.winfo_width()
            height = self.root.winfo_height()
        except tk.TclError:
            return self.settings.window_geometry
        if width < MINIMUM_WINDOW_SIZE[0] or height < MINIMUM_WINDOW_SIZE[1]:
            return self.settings.window_geometry
        return format_window_geometry(width, height)

    def _show_error(self, exc: BaseException) -> None:
        """Explain what happened in words, then show the technical detail underneath."""
        messagebox.showerror(self.tr("error"), self._error_message(exc))

    def _error_message(self, exc: BaseException) -> str:
        if isinstance(exc, PermissionError):
            explanation = self.tr("error_permission")
        elif isinstance(exc, FileNotFoundError):
            explanation = self.tr("error_missing")
        elif isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutExpired":
            explanation = self.tr("error_timeout")
        elif isinstance(exc, ValueError):
            explanation = self.tr("error_value")
        elif isinstance(exc, OSError):
            explanation = self.tr("error_missing")
        else:
            explanation = self.tr("error_unexpected")
        detail = str(exc).strip() or type(exc).__name__
        return f"{explanation}\n\n{self.tr('error_detail')} {detail}"

    def _font(self, size: int) -> tuple:
        return self.fonts.regular_font(size)

    def _font_bold(self, size: int) -> tuple:
        return self.fonts.emphasis_font(size)

    def tr(self, key: str, **values: Any) -> str:
        return TEXT[self.locale_var.get()][key].format(**values)

    def _build(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()
        self.root.title(self.tr("app_title"))
        self._apply_window_geometry()
        self._apply_theme()
        self._set_window_icon()

        shell = ttk.Frame(self.root, style="App.TFrame")
        shell.pack(fill="both", expand=True)
        sidebar = ttk.Frame(shell, style="Sidebar.TFrame", width=248, padding=(20, 24))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self.sidebar = sidebar
        brand = ttk.Frame(sidebar, style="Sidebar.TFrame")
        brand.pack(fill="x", pady=(0, 34))
        ttk.Label(brand, text="H", style="BrandMark.TLabel", anchor="center").pack(side="left")
        brand_copy = ttk.Frame(brand, style="Sidebar.TFrame")
        brand_copy.pack(side="left", padx=(10, 0))
        self.brand_copy = brand_copy
        ttk.Label(brand_copy, text="HoH", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(brand_copy, text=self.tr("control_center"), style="Eyebrow.TLabel").pack(anchor="w")

        for page, icon in (("setup", "✓"), ("projects", "▤"), ("chat", "✦"), ("roles", "◆"), ("agents_setup", "⬡"), ("tools", "⌘"), ("config", "▦"), ("metrics", "◉"), ("operations", "▶")):
            button = self._nav_button(
                sidebar,
                text=f"  {icon}   {self.tr(page)}",
                command=lambda selected=page: self._show_page(selected),
            )
            button.pack(fill="x", pady=3)
            self.nav_buttons[page] = button
        self._full_sidebar_width = max(
            248,
            max(button.winfo_reqwidth() for button in self.nav_buttons.values()) + 40,
        )
        sidebar.configure(width=self._full_sidebar_width)
        ttk.Frame(sidebar, style="Sidebar.TFrame").pack(fill="both", expand=True)
        appearance_label = ttk.Label(sidebar, text=self.tr("appearance"), style="Eyebrow.TLabel")
        appearance_label.pack(anchor="w", pady=(0, 8))
        self.appearance_label = appearance_label
        appearance = ttk.Frame(sidebar, style="Sidebar.TFrame")
        appearance.pack(fill="x")
        self.appearance_panel = appearance
        locale = self._select(appearance, textvariable=self.locale_var, values=("ru", "en"), width=5, state="readonly")
        locale.pack(side="left", fill="x", expand=True, padx=(0, 6))
        locale.bind("<<ComboboxSelected>>", self._presentation_changed)
        theme = self._select(appearance, textvariable=self.theme_var, values=("dark", "light"), width=8, state="readonly")
        theme.pack(side="left", fill="x", expand=True)
        theme.bind("<<ComboboxSelected>>", self._presentation_changed)

        main = ttk.Frame(shell, style="App.TFrame", padding=(28, 22, 28, 18))
        main.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(main, style="App.TFrame")
        header.pack(fill="x", pady=(0, 20))
        header_copy = ttk.Frame(header, style="App.TFrame")
        header_copy.pack(side="left")
        ttk.Label(header_copy, text=self.tr("workspace"), style="Eyebrow.App.TLabel").pack(anchor="w")
        ttk.Label(header_copy, text=self.tr("project"), style="PageTitle.TLabel").pack(anchor="w", pady=(2, 0))
        project_box = ttk.Frame(header, style="App.TFrame")
        project_box.pack(side="right", fill="x", expand=True, padx=(42, 0))
        ttk.Entry(project_box, textvariable=self.project_var, style="Modern.TEntry").pack(side="left", fill="x", expand=True)
        self._button(project_box, text=self.tr("browse"), variant="secondary", command=self.browse_project).pack(side="left", padx=(8, 0))
        self._button(project_box, text=self.tr("load"), variant="primary", command=self.load_project).pack(side="left", padx=(8, 0))

        palette = PALETTES[self.theme_var.get()]
        content_shell = ttk.Frame(main, style="App.TFrame")
        content_shell.pack(fill="both", expand=True)
        content = tk.Canvas(content_shell, background=palette["background"], highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(content_shell, orient="vertical", command=content.yview, style="Modern.Vertical.TScrollbar")
        # Keep the rail visible: hiding it made mouse-wheel scrolling look like
        # layout drift and removed the only visual cue on long settings pages.
        content.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y", padx=(6, 0))
        content.pack(side="left", fill="both", expand=True)
        pages_host = ttk.Frame(content, style="App.TFrame")
        host_window = content.create_window((0, 0), window=pages_host, anchor="nw")
        pages_host.bind("<Configure>", lambda _event: content.configure(scrollregion=content.bbox("all")))
        content.bind("<Configure>", self._content_resized)
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        self.content_canvas = content
        self.content_host_window = host_window
        for page in ("setup", "projects", "chat", "roles", "agents_setup", "tools", "config", "metrics", "operations"):
            frame = ttk.Frame(pages_host, style="App.TFrame")
            self.pages[page] = frame
        self._build_setup(self.pages["setup"])
        self._build_projects(self.pages["projects"])
        self._build_chat(self.pages["chat"])
        self._build_roles(self.pages["roles"])
        self._build_agents_setup(self.pages["agents_setup"])
        self._build_mcp_tools(self.pages["tools"])
        self._build_config(self.pages["config"])
        self._build_metrics(self.pages["metrics"])
        self._build_operations(self.pages["operations"])
        self._style_text_widgets()
        status_bar = ttk.Frame(main, style="Status.TFrame", padding=(12, 8))
        status_bar.pack(fill="x", pady=(12, 0))
        ttk.Label(status_bar, text="●", style="StatusDot.TLabel").pack(side="left")
        ttk.Label(status_bar, textvariable=self.status_var, style="Status.TLabel").pack(side="left", padx=(7, 0))
        ttk.Label(status_bar, textvariable=self.readiness_var, style="Status.TLabel", wraplength=820).pack(
            side="right"
        )
        self._show_page(self.current_page)
        self._register_wrapping_labels()
        self.root.bind("<Configure>", self._on_resize)
        if self.snapshot is not None:
            self._populate_snapshot()

    def _build_setup(self, parent: ttk.Frame) -> None:
        """Three steps: pick the project, prepare the team, describe the task.

        Everything else the product can do stays reachable from the sidebar and is
        listed below, but nothing else is required before the first task. Installing
        agents and assigning roles are consequences of choosing a team, not separate
        chores for the operator to sequence.
        """
        ttk.Label(parent, text=self.tr("setup_title"), style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(
            parent, text=self.tr("setup_desc"), style="Subtitle.TLabel", wraplength=900
        ).pack(anchor="w", pady=(3, 14))

        project_shell, project_card = self._elevated_frame(parent, padding=(18, 16))
        project_shell.pack(fill="x")
        self._setup_step_header(project_card, "setup_step_project", "project")
        ttk.Label(
            project_card, text=self.tr("setup_step_project_desc"), style="CardMuted.TLabel", wraplength=880
        ).pack(anchor="w", pady=(6, 10))
        project_row = ttk.Frame(project_card, style="Card.TFrame")
        project_row.pack(fill="x")
        ttk.Entry(project_row, textvariable=self.project_var, style="Modern.TEntry").pack(
            side="left", fill="x", expand=True
        )
        self._button(
            project_row, text=self.tr("browse"), variant="secondary", command=self.browse_project
        ).pack(side="left", padx=(8, 0))
        self._button(
            project_row, text=self.tr("open_project"), variant="primary", command=lambda: self.load_project(False)
        ).pack(side="left", padx=(8, 0))

        team_shell, team_card = self._elevated_frame(parent, padding=(18, 16))
        team_shell.pack(fill="x", pady=(12, 0))
        self._setup_step_header(team_card, "setup_step_team", "team")
        ttk.Label(
            team_card, text=self.tr("setup_step_team_desc"), style="CardMuted.TLabel", wraplength=880
        ).pack(anchor="w", pady=(6, 10))
        self.preset_display_ids = {preset.title(self.locale_var.get()): preset.preset_id for preset in ROLE_PRESETS}
        titles = tuple(self.preset_display_ids)
        if not self.preset_var.get() or self.preset_var.get() not in titles:
            self.preset_var.set(titles[1] if len(titles) > 1 else titles[0])
        preset_box = self._select(
            team_card, textvariable=self.preset_var, values=titles, state="readonly"
        )
        preset_box.pack(fill="x", pady=(0, 8))
        preset_box.bind("<<ComboboxSelected>>", lambda _event: self._preset_changed())
        ttk.Label(
            team_card, textvariable=self.preset_description_var, style="CardMuted.TLabel", wraplength=880
        ).pack(anchor="w")
        ttk.Label(
            team_card, textvariable=self.preset_agents_var, style="FieldLabel.TLabel", wraplength=880
        ).pack(anchor="w", pady=(7, 10))
        self._preset_changed()
        team_actions = ttk.Frame(team_card, style="Card.TFrame")
        team_actions.pack(fill="x")
        self._button(
            team_actions, text=self.tr("setup_prepare_team"), variant="primary", command=self.prepare_team
        ).pack(side="right")
        self._button(
            team_actions,
            text=self.tr("setup_open_login"),
            variant="secondary",
            command=lambda: self._show_page("agents_setup"),
        ).pack(side="right", padx=(0, 8))

        task_shell, task_card = self._elevated_frame(parent, padding=(18, 16))
        task_shell.pack(fill="x", pady=(12, 0))
        self._setup_step_header(task_card, "setup_step_task", "task")
        ttk.Label(
            task_card, text=self.tr("setup_step_task_desc"), style="CardMuted.TLabel", wraplength=880
        ).pack(anchor="w", pady=(6, 10))
        self._button(
            task_card, text=self.tr("setup_open_chat"), variant="primary", command=self.start_first_task
        ).pack(anchor="e")

        optional_shell, optional_card = self._elevated_frame(parent, padding=(18, 16))
        optional_shell.pack(fill="x", pady=(12, 0))
        ttk.Label(optional_card, text=self.tr("setup_optional"), style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            optional_card, text=self.tr("setup_optional_desc"), style="CardMuted.TLabel", wraplength=880
        ).pack(anchor="w", pady=(6, 10))
        optional_row = ttk.Frame(optional_card, style="Card.TFrame")
        optional_row.pack(fill="x")
        for page in ("agents_setup", "tools", "config", "projects", "metrics", "operations"):
            self._button(
                optional_row,
                text=self.tr(page),
                variant="secondary",
                command=lambda selected=page: self._show_page(selected),
            ).pack(side="left", padx=(0, 8), pady=(0, 4))

    def _setup_step_header(self, card: ttk.Frame, title_key: str, status_key: str) -> None:
        header = ttk.Frame(card, style="Card.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text=self.tr(title_key), style="CardTitle.TLabel").pack(side="left")
        ttk.Label(
            header, textvariable=self.setup_status_vars[status_key], style="RoleStatus.TLabel"
        ).pack(side="right")

    def _build_projects(self, parent: ttk.Frame) -> None:
        heading = ttk.Frame(parent, style="App.TFrame")
        heading.pack(fill="x", pady=(0, 14))
        copy = ttk.Frame(heading, style="App.TFrame")
        copy.pack(side="left", fill="x", expand=True)
        ttk.Label(copy, text=self.tr("projects_title"), style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(
            copy,
            text=self.tr("projects_desc"),
            style="Subtitle.TLabel",
            wraplength=760,
        ).pack(anchor="w", pady=(3, 0))
        self._button(
            heading,
            text=self.tr("projects_refresh"),
            variant="secondary",
            command=self.refresh_workspace_projects,
        ).pack(side="right", padx=(12, 0))

        selector_shell, selector = self._elevated_frame(parent, padding=(16, 14))
        selector_shell.pack(fill="x", pady=(0, 14))
        ttk.Label(selector, text=self.tr("projects_registered"), style="FieldLabel.TLabel").pack(anchor="w")
        self.workspace_project_box = self._select(
            selector,
            textvariable=self.workspace_project_var,
            state="readonly",
        )
        self.workspace_project_box.pack(fill="x", pady=(5, 10))
        self.workspace_project_box.bind("<<ComboboxSelected>>", lambda _event: self.workspace_project_changed())
        actions = ttk.Frame(selector, style="Card.TFrame")
        actions.pack(fill="x")
        self._button(actions, text=self.tr("projects_register_current"), variant="primary", command=self.register_current_workspace_project).pack(side="left")
        self._button(actions, text=self.tr("projects_open"), variant="secondary", command=self.open_selected_workspace_project).pack(side="left", padx=(8, 0))
        self._button(actions, text=self.tr("projects_remove"), variant="secondary", command=self.delete_workspace_project).pack(side="left", padx=(8, 0))
        self._button(actions, text=self.tr("projects_run_now"), variant="primary", command=self.run_selected_workspace_project).pack(side="right")
        self._button(actions, text=self.tr("projects_run_due"), variant="secondary", command=self.run_due_workspace_schedules).pack(side="right", padx=(0, 8))

        schedule_shell, schedule = self._elevated_frame(parent, padding=(16, 14))
        schedule_shell.pack(fill="x", pady=(0, 14))
        ttk.Label(schedule, text=self.tr("projects_schedule"), style="CardTitle.TLabel").grid(row=0, column=0, columnspan=5, sticky="w")
        ttk.Label(schedule, text=self.tr("projects_schedule_desc"), style="CardMuted.TLabel", wraplength=940).grid(row=1, column=0, columnspan=5, sticky="w", pady=(4, 10))
        ttk.Checkbutton(schedule, text=self.tr("projects_schedule_enabled"), variable=self.workspace_schedule_vars["enabled"], style="Modern.TCheckbutton").grid(row=2, column=0, sticky="w")
        ttk.Label(schedule, text=self.tr("projects_interval"), style="FieldLabel.TLabel").grid(row=2, column=1, sticky="w", padx=(16, 0))
        ttk.Spinbox(schedule, from_=1, to=10080, textvariable=self.workspace_schedule_vars["interval_minutes"], width=8, style="Modern.TSpinbox").grid(row=2, column=2, sticky="w", padx=(8, 0))
        ttk.Checkbutton(schedule, text=self.tr("projects_final_audit"), variable=self.workspace_schedule_vars["final_audit"], style="Modern.TCheckbutton").grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(schedule, text=self.tr("projects_notify_windows"), variable=self.workspace_schedule_vars["notify_windows"], style="Modern.TCheckbutton").grid(row=3, column=2, sticky="w", padx=(8, 0), pady=(8, 0))
        ttk.Checkbutton(schedule, text=self.tr("projects_notify_telegram"), variable=self.workspace_schedule_vars["notify_telegram"], style="Modern.TCheckbutton").grid(row=3, column=3, sticky="w", padx=(8, 0), pady=(8, 0))
        self._button(schedule, text=self.tr("projects_schedule_save"), variant="primary", command=self.save_workspace_schedule).grid(row=2, column=4, rowspan=2, sticky="e", padx=(18, 0))
        service_actions = ttk.Frame(schedule, style="Card.TFrame")
        service_actions.grid(row=4, column=0, columnspan=5, sticky="ew", pady=(12, 0))
        self._button(service_actions, text=self.tr("projects_service_install"), variant="primary", command=lambda: self.manage_workspace_scheduler("install")).pack(side="left")
        self._button(service_actions, text=self.tr("projects_service_remove"), variant="secondary", command=lambda: self.manage_workspace_scheduler("uninstall")).pack(side="left", padx=(8, 0))
        self._button(service_actions, text=self.tr("projects_service_status"), variant="secondary", command=lambda: self.manage_workspace_scheduler("status")).pack(side="left", padx=(8, 0))
        ttk.Label(service_actions, textvariable=self.workspace_service_var, style="CardMuted.TLabel").pack(side="right", padx=(12, 0))
        schedule.columnconfigure(3, weight=1)

        self.workspace_cards_frame = ttk.Frame(parent, style="App.TFrame")
        self.workspace_cards_frame.pack(fill="x")
        self.refresh_workspace_projects()

    def _build_chat(self, parent: ttk.Frame) -> None:
        heading = ttk.Frame(parent, style="App.TFrame")
        heading.pack(fill="x", pady=(0, 14))
        copy = ttk.Frame(heading, style="App.TFrame")
        copy.pack(side="left")
        ttk.Label(copy, text=self.tr("chat_title"), style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(copy, text=self.tr("chat_desc"), style="Subtitle.TLabel").pack(anchor="w", pady=(3, 0))
        self._button(heading, text=self.tr("new_chat"), variant="secondary", command=self.clear_chat).pack(side="right")

        transcript_shell, transcript = self._elevated_frame(parent, padding=(18, 16))
        transcript_shell.pack(fill="both", expand=True)
        # Keep the requested height below the viewport so the transcript grows into
        # available space instead of forcing the composer below the fold.
        self.chat_editor = tk.Text(transcript, wrap="word", state="disabled", height=8, font=self._font(10), spacing1=4, spacing3=8)
        self.chat_editor.pack(fill="both", expand=True)

        composer_shell, composer = self._elevated_frame(parent, padding=(14, 12))
        composer_shell.pack(fill="x", pady=(14, 0))
        self.chat_input = tk.Text(composer, wrap="word", height=4, font=self._font(10), spacing1=3)
        self.chat_input.pack(side="left", fill="both", expand=True, padx=(0, 12))
        self.chat_input.insert("1.0", "")
        composer_actions = ttk.Frame(composer, style="Card.TFrame")
        composer_actions.pack(side="right", fill="y")
        self._button(composer_actions, text=self.tr("send"), variant="primary", command=self.send_chat_message).pack(fill="x")
        self._button(composer_actions, text=self.tr("create_plan"), variant="secondary", command=self.create_chat_plan).pack(fill="x", pady=(8, 0))

    def _build_roles(self, parent: ttk.Frame) -> None:
        heading = ttk.Frame(parent, style="App.TFrame")
        heading.pack(fill="x", pady=(0, 12))
        copy = ttk.Frame(heading, style="App.TFrame")
        copy.pack(side="left")
        ttk.Label(copy, text=self.tr("role_team"), style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(copy, text=self.tr("role_team_desc"), style="Subtitle.TLabel").pack(anchor="w", pady=(3, 0))
        self._button(heading, text="↻  " + self.tr("refresh"), variant="secondary", command=lambda: self.load_project(True)).pack(side="right")

        cards = ttk.Frame(parent, style="App.TFrame")
        cards.pack(fill="x")
        self.cards_frame = cards
        for column in range(3):
            cards.columnconfigure(column, weight=1, uniform="roles")
        for column, role in enumerate(("supervisor", "worker", "critic")):
            self._create_role_card(cards, role, column)

        policy_shell, policy = self._elevated_frame(parent, padding=(18, 16))
        policy_shell.pack(fill="x", pady=(12, 0))
        policy_copy = ttk.Frame(policy, style="Card.TFrame")
        policy_copy.pack(side="left", fill="x", expand=True)
        ttk.Label(policy_copy, text=self.tr("policy"), style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(policy_copy, text=self.tr("policy_desc"), style="CardMuted.TLabel").pack(anchor="w", pady=(3, 0))
        controls = ttk.Frame(policy, style="Card.TFrame")
        controls.pack(side="right")
        ttk.Checkbutton(
            controls,
            text=self.tr("critic_enabled"),
            variable=self.critic_enabled_var,
            style="Modern.TCheckbutton",
            command=self._update_role_save_state,
        ).pack(side="left", padx=(0, 24))
        ttk.Label(controls, text=self.tr("attempts"), style="FieldLabel.TLabel").pack(side="left", padx=(0, 8))
        ttk.Spinbox(controls, from_=1, to=99, textvariable=self.attempts_var, width=6, style="Modern.TSpinbox").pack(side="left")

        actions = ttk.Frame(parent, style="App.TFrame")
        actions.pack(fill="x", pady=(12, 0))
        save_button = self._button(actions, text=self.tr("save_roles"), variant="primary", command=self.save_roles)
        save_button.pack(side="right")
        self.save_roles_button = save_button

    def _build_agents_setup(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text=self.tr("agents_setup_title"), style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(parent, text=self.tr("agents_setup_desc"), style="Subtitle.TLabel", wraplength=920).pack(anchor="w", pady=(3, 14))

        columns = ttk.Frame(parent, style="App.TFrame")
        columns.pack(fill="x")
        self.connection_cards_frame = columns
        columns.columnconfigure(0, weight=1, uniform="connections")
        columns.columnconfigure(1, weight=1, uniform="connections")

        managed_shell, managed = self._elevated_frame(columns, padding=(18, 16))
        managed_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        ttk.Label(managed, text=self.tr("managed_agents"), style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(managed, text=self.tr("managed_agents_desc"), style="CardMuted.TLabel", wraplength=430).pack(anchor="w", pady=(4, 12))
        ttk.Label(managed, text=self.tr("registry_agent"), style="FieldLabel.TLabel").pack(anchor="w")
        self.registry_agent_box = self._select(managed, textvariable=self.registry_agent_var, state="readonly")
        self.registry_agent_box.pack(fill="x", pady=(4, 8))
        self.registry_agent_box.bind("<<ComboboxSelected>>", lambda _event: self._registry_agent_changed())
        ttk.Label(managed, textvariable=self.registry_agent_description_var, style="CardMuted.TLabel", wraplength=430).pack(anchor="w")
        ttk.Label(managed, textvariable=self.registry_agent_status_var, style="RoleStatus.TLabel", wraplength=430).pack(anchor="w", pady=(8, 10))
        install_actions = ttk.Frame(managed, style="Card.TFrame")
        install_actions.pack(fill="x")
        self._button(install_actions, text=self.tr("install_agent"), variant="primary", command=self.install_selected_agent).pack(side="left")
        self._button(install_actions, text=self.tr("remove_agent"), variant="secondary", command=self.uninstall_selected_agent).pack(side="left", padx=(8, 0))
        self._button(managed, text=self.tr("refresh_agents"), variant="secondary", command=lambda: self.refresh_managed_agents(True)).pack(fill="x", pady=(8, 0))
        ttk.Label(managed, text=self.tr("auth_method"), style="FieldLabel.TLabel").pack(anchor="w", pady=(14, 4))
        self.auth_method_box = self._select(managed, textvariable=self.auth_method_var, state="readonly")
        self.auth_method_box.pack(fill="x")
        self.auth_method_box.bind("<<ComboboxSelected>>", lambda _event: self._render_auth_variables())
        self.auth_variables_frame = ttk.Frame(managed, style="Card.TFrame")
        self.auth_variables_frame.pack(fill="x", pady=(6, 0))
        auth_actions = ttk.Frame(managed, style="Card.TFrame")
        auth_actions.pack(fill="x", pady=(8, 0))
        self._button(auth_actions, text=self.tr("inspect_login"), variant="secondary", command=self.inspect_selected_agent_auth).pack(side="left")
        self._button(auth_actions, text=self.tr("login_agent"), variant="primary", command=self.login_selected_agent).pack(side="right")
        ttk.Label(managed, text=self.tr("managed_agent_home"), style="CardMuted.TLabel", wraplength=430).pack(anchor="w", pady=(12, 0))

        remote_shell, remote = self._elevated_frame(columns, padding=(18, 16))
        remote_shell.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self.connection_card_shells = (managed_shell, remote_shell)
        ttk.Label(remote, text=self.tr("a2a_agents"), style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(remote, text=self.tr("a2a_agents_desc"), style="CardMuted.TLabel", wraplength=430).pack(anchor="w", pady=(4, 12))
        ttk.Label(remote, text=self.tr("a2a_profile"), style="FieldLabel.TLabel").pack(anchor="w")
        self.a2a_profile_box = self._select(remote, textvariable=self.a2a_profile_var, state="readonly")
        self.a2a_profile_box.pack(fill="x", pady=(4, 8))
        self.a2a_profile_box.bind("<<ComboboxSelected>>", lambda _event: self.load_selected_a2a_profile())
        form = ttk.Frame(remote, style="Card.TFrame")
        form.pack(fill="x")
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        self._a2a_field(form, "a2a_profile", "name", 0, 0)
        self._a2a_field(form, "a2a_card_url", "card_url", 0, 1)
        ttk.Label(form, text=self.tr("a2a_roles"), style="FieldLabel.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(9, 3))
        roles = ttk.Frame(form, style="Card.TFrame")
        roles.grid(row=3, column=0, columnspan=2, sticky="w")
        for role in ("supervisor", "worker", "critic"):
            ttk.Checkbutton(roles, text=self.tr(role), variable=self.a2a_role_vars[role], style="Modern.TCheckbutton").pack(side="left", padx=(0, 8))
        ttk.Label(form, text=self.tr("a2a_auth"), style="FieldLabel.TLabel").grid(row=4, column=0, sticky="w", pady=(9, 3), padx=(0, 6))
        self._select(
            form,
            textvariable=self.a2a_vars["auth_kind"],
            values=("none", "bearer", "api_key", "oauth2", "oidc"),
            height=5,
            state="readonly",
        ).grid(row=5, column=0, sticky="ew", padx=(0, 6))
        self._a2a_field(form, "a2a_credential_env", "credential_env", 4, 1)
        ttk.Label(form, text=self.tr("a2a_oauth_flow"), style="FieldLabel.TLabel").grid(row=6, column=0, sticky="w", pady=(9, 3), padx=(0, 6))
        self._select(form, textvariable=self.a2a_vars["oauth_flow"], values=("client_credentials", "device_code"), state="readonly").grid(row=7, column=0, sticky="ew", padx=(0, 6))
        ttk.Label(form, text=self.tr("a2a_client_auth"), style="FieldLabel.TLabel").grid(row=6, column=1, sticky="w", pady=(9, 3), padx=(6, 0))
        self._select(form, textvariable=self.a2a_vars["client_auth_method"], values=("basic", "post"), state="readonly").grid(row=7, column=1, sticky="ew", padx=(6, 0))
        self._a2a_field(form, "a2a_client_id_env", "client_id_env", 8, 0)
        self._a2a_field(form, "a2a_client_secret_env", "client_secret_env", 8, 1)
        self._a2a_field(form, "a2a_token_url", "token_url", 10, 0)
        self._a2a_field(form, "a2a_oidc_url", "oidc_discovery_url", 10, 1)
        self._a2a_field(form, "a2a_device_url", "device_authorization_url", 12, 0)
        self._a2a_field(form, "a2a_scopes", "scopes", 12, 1)
        self._a2a_field(form, "a2a_header", "api_key_header", 14, 0)
        self._a2a_field(form, "a2a_timeout", "timeout_seconds", 14, 1)
        self._a2a_field(form, "a2a_poll", "poll_interval_seconds", 16, 0)
        self._a2a_field(form, "a2a_push_url", "push_callback_url", 16, 1)
        self._a2a_field(form, "a2a_push_token_env", "push_token_env", 18, 0)
        ttk.Checkbutton(form, text=self.tr("a2a_streaming"), variable=self.a2a_vars["prefer_streaming"], style="Modern.TCheckbutton").grid(row=19, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(remote, text=self.tr("a2a_oauth_help"), style="CardMuted.TLabel", wraplength=430).pack(anchor="w", pady=(10, 0))
        ttk.Label(remote, text=self.tr("a2a_delivery_help"), style="CardMuted.TLabel", wraplength=430).pack(anchor="w", pady=(4, 0))
        ttk.Label(remote, text=self.tr("a2a_security"), style="CardMuted.TLabel", wraplength=430).pack(anchor="w", pady=(4, 0))
        remote_actions = ttk.Frame(remote, style="Card.TFrame")
        remote_actions.pack(fill="x", pady=(12, 0))
        for column in range(2):
            remote_actions.columnconfigure(column, weight=1, uniform="a2a-actions")
        self._button(remote_actions, text=self.tr("new_a2a"), variant="secondary", command=self.clear_a2a_form).grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=(0, 6))
        self._button(remote_actions, text=self.tr("test_a2a"), variant="secondary", command=self.test_a2a_form).grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=(0, 6))
        self._button(remote_actions, text=self.tr("remove_a2a"), variant="secondary", command=self.delete_a2a_profile).grid(row=1, column=0, sticky="ew", padx=(0, 4))
        self._button(remote_actions, text=self.tr("save_a2a"), variant="primary", command=self.save_a2a_form).grid(row=1, column=1, sticky="ew", padx=(4, 0))
        self._button(remote_actions, text=self.tr("a2a_login"), variant="secondary", command=self.login_a2a_form).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    def _a2a_field(self, parent: ttk.Frame, label: str, key: str, row: int, column: int) -> None:
        padx = (0, 6) if column == 0 else (6, 0)
        ttk.Label(parent, text=self.tr(label), style="FieldLabel.TLabel").grid(row=row, column=column, sticky="w", padx=padx, pady=(9, 3))
        ttk.Entry(parent, textvariable=self.a2a_vars[key], style="Modern.TEntry").grid(row=row + 1, column=column, sticky="ew", padx=padx)

    def _build_mcp_tools(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text=self.tr("mcp_title"), style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(
            parent, text=self.tr("mcp_desc"), style="Subtitle.TLabel", wraplength=960
        ).pack(anchor="w", pady=(3, 14))

        policy_shell, policy = self._elevated_frame(parent, padding=(18, 15))
        policy_shell.pack(fill="x")
        policy.columnconfigure(0, weight=1)
        policy.columnconfigure(1, weight=1)
        ttk.Label(policy, text=self.tr("mcp_role"), style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        role_box = self._select(
            policy,
            textvariable=self.mcp_role_var,
            values=("supervisor", "worker", "critic"),
            state="readonly",
        )
        role_box.grid(row=1, column=0, sticky="ew", padx=(0, 7), pady=(4, 0))
        role_box.bind("<<ComboboxSelected>>", lambda _event: self.load_mcp_role())
        ttk.Label(policy, text=self.tr("mcp_mode"), style="FieldLabel.TLabel").grid(row=0, column=1, sticky="w", padx=(7, 0))
        self._select(
            policy,
            textvariable=self.mcp_mode_var,
            values=("reject", "allow_once"),
            state="readonly",
        ).grid(row=1, column=1, sticky="ew", padx=(7, 0), pady=(4, 0))
        ttk.Label(
            policy, text=self.tr("mcp_mode_help"), style="CardMuted.TLabel", wraplength=900
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(7, 0))
        ttk.Label(policy, text=self.tr("mcp_kinds"), style="CardTitle.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(14, 7)
        )
        kinds = ttk.Frame(policy, style="Card.TFrame")
        kinds.grid(row=4, column=0, columnspan=2, sticky="ew")
        for column in range(5):
            kinds.columnconfigure(column, weight=1)
        for index, kind in enumerate(ACP_TOOL_KINDS):
            ttk.Checkbutton(
                kinds,
                text=kind,
                variable=self.mcp_kind_vars[kind],
                style="Modern.TCheckbutton",
            ).grid(row=index // 5, column=index % 5, sticky="w", pady=3)
        self._button(
            policy,
            text=self.tr("mcp_save_policy"),
            variant="primary",
            command=self.save_mcp_policy_form,
        ).grid(row=5, column=1, sticky="e", pady=(12, 0))

        server_shell, server = self._elevated_frame(parent, padding=(18, 15))
        server_shell.pack(fill="x", pady=(14, 0))
        for column in range(2):
            server.columnconfigure(column, weight=1)
        ttk.Label(server, text=self.tr("mcp_server"), style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        self.mcp_server_box = self._select(
            server, textvariable=self.mcp_server_var, state="readonly"
        )
        self.mcp_server_box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(7, 10))
        self.mcp_server_box.bind("<<ComboboxSelected>>", lambda _event: self.load_selected_mcp_server())
        self._mcp_entry(server, "mcp_server_name", "name", 2, 0)
        self._mcp_entry(server, "mcp_transport", "transport", 2, 1, choices=("stdio", "http", "sse"))
        self._mcp_entry(server, "mcp_command", "command", 4, 0)
        self._mcp_entry(server, "mcp_url", "url", 4, 1)
        for key, label, column in (
            ("args", "mcp_args", 0),
            ("environment", "mcp_env", 1),
            ("headers", "mcp_headers", 0),
        ):
            row = 6 if key != "headers" else 8
            ttk.Label(server, text=self.tr(label), style="FieldLabel.TLabel").grid(
                row=row, column=column, sticky="w", padx=(0, 7) if column == 0 else (7, 0), pady=(9, 3)
            )
            editor = tk.Text(server, height=4, wrap="none", font=("Consolas", 10))
            editor.grid(
                row=row + 1,
                column=column,
                columnspan=2 if key == "headers" else 1,
                sticky="ew",
                padx=(0, 7) if column == 0 and key != "headers" else ((7, 0) if column else 0),
            )
            self.mcp_text_editors[key] = editor
        ttk.Label(
            server, text=self.tr("mcp_secret_help"), style="CardMuted.TLabel", wraplength=900
        ).grid(row=10, column=0, columnspan=2, sticky="w", pady=(8, 0))
        actions = ttk.Frame(server, style="Card.TFrame")
        actions.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self._button(actions, text=self.tr("mcp_new"), variant="secondary", command=self.clear_mcp_server_form).pack(side="left")
        self._button(actions, text=self.tr("mcp_remove_server"), variant="secondary", command=self.delete_mcp_server).pack(side="right", padx=(8, 0))
        self._button(actions, text=self.tr("mcp_save_server"), variant="primary", command=self.save_mcp_server_form).pack(side="right")

    def _mcp_entry(
        self,
        parent: ttk.Frame,
        label: str,
        key: str,
        row: int,
        column: int,
        *,
        choices: tuple[str, ...] = (),
    ) -> None:
        padx = (0, 7) if column == 0 else (7, 0)
        ttk.Label(parent, text=self.tr(label), style="FieldLabel.TLabel").grid(
            row=row, column=column, sticky="w", padx=padx, pady=(7, 3)
        )
        widget = (
            self._select(
                parent,
                textvariable=self.mcp_server_vars[key],
                values=choices,
                state="readonly",
            )
            if choices
            else ttk.Entry(parent, textvariable=self.mcp_server_vars[key], style="Modern.TEntry")
        )
        widget.grid(row=row + 1, column=column, sticky="ew", padx=padx)

    def _build_config(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text=self.tr("config"), style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(parent, text=self.tr("guided_settings_desc"), style="Subtitle.TLabel").pack(anchor="w", pady=(3, 14))

        storage_shell, storage = self._elevated_frame(parent, padding=(18, 15))
        storage_shell.pack(fill="x")
        ttk.Label(storage, text=self.tr("storage_title"), style="CardTitle.TLabel").pack(anchor="w", pady=(0, 8))
        for label, key in (
            ("storage_project", "project"),
            ("storage_roles", "roles"),
            ("storage_chat", "chat"),
            ("storage_ui", "ui"),
        ):
            row = ttk.Frame(storage, style="Card.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=self.tr(label), style="FieldLabel.TLabel", width=32).pack(side="left")
            ttk.Label(row, textvariable=self.storage_vars[key], style="CardMuted.TLabel").pack(side="left")
        secret_row = ttk.Frame(storage, style="Card.TFrame")
        secret_row.pack(fill="x", pady=(6, 0))
        ttk.Label(secret_row, text=self.tr("storage_secrets"), style="FieldLabel.TLabel", width=32).pack(side="left")
        ttk.Label(secret_row, text=self.tr("storage_secrets_value"), style="CardMuted.TLabel", wraplength=700).pack(side="left")

        secrets_shell, secrets = self._elevated_frame(parent, padding=(18, 15))
        secrets_shell.pack(fill="x", pady=(14, 0))
        ttk.Label(secrets, text=self.tr("session_secrets"), style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(secrets, text=self.tr("session_secrets_desc"), style="CardMuted.TLabel", wraplength=900).pack(anchor="w", pady=(4, 9))
        self.session_secrets_frame = ttk.Frame(secrets, style="Card.TFrame")
        self.session_secrets_frame.pack(fill="x")

        models = ttk.Frame(parent, style="App.TFrame")
        models.pack(fill="x", pady=(14, 0))
        models.columnconfigure(0, weight=1, uniform="models")
        models.columnconfigure(1, weight=1, uniform="models")
        self._build_endpoint_card(models, "supervisor", 0)
        self._build_endpoint_card(models, "verifier", 1)

        process_shell, process_card = self._elevated_frame(parent, padding=(18, 15))
        process_shell.pack(fill="x", pady=(14, 0))
        ttk.Label(process_card, text=self.tr("process_profiles_title"), style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(process_card, text=self.tr("process_profiles_desc"), style="CardMuted.TLabel", wraplength=900).pack(anchor="w", pady=(4, 7))
        ttk.Label(process_card, textvariable=self.process_profiles_var, style="FieldLabel.TLabel", wraplength=900).pack(anchor="w")
        ttk.Label(process_card, text=self.tr("process_profiles_help"), style="CardMuted.TLabel", wraplength=900).pack(anchor="w", pady=(5, 10))
        editor = ttk.Frame(process_card, style="Card.TFrame")
        editor.pack(fill="x")
        for column in range(2):
            editor.columnconfigure(column, weight=1, uniform="process")
        ttk.Label(editor, text=self.tr("process_profile_name"), style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 7))
        ttk.Label(editor, text=self.tr("process_command"), style="FieldLabel.TLabel").grid(row=0, column=1, sticky="w", padx=(7, 0))
        self.process_profile_box = self._select(editor, textvariable=self.process_vars["name"])
        self.process_profile_box.grid(row=1, column=0, sticky="ew", padx=(0, 7), pady=(4, 0))
        self.process_profile_box.bind("<<ComboboxSelected>>", lambda _event: self.load_selected_process_profile())
        ttk.Entry(editor, textvariable=self.process_vars["command"], style="Modern.TEntry").grid(row=1, column=1, sticky="ew", padx=(7, 0), pady=(4, 0))
        ttk.Label(editor, text=self.tr("prompt_transport"), style="FieldLabel.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 7), pady=(9, 0))
        ttk.Label(editor, text=self.tr("prompt_argument"), style="FieldLabel.TLabel").grid(row=2, column=1, sticky="w", padx=(7, 0), pady=(9, 0))
        self._select(editor, textvariable=self.process_vars["prompt_transport"], values=("stdin", "file"), state="readonly").grid(row=3, column=0, sticky="ew", padx=(0, 7), pady=(4, 0))
        ttk.Entry(editor, textvariable=self.process_vars["prompt_argument"], style="Modern.TEntry").grid(row=3, column=1, sticky="ew", padx=(7, 0), pady=(4, 0))
        ttk.Label(editor, text=self.tr("model_argument"), style="FieldLabel.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 7), pady=(9, 0))
        ttk.Entry(editor, textvariable=self.process_vars["model_argument"], style="Modern.TEntry").grid(row=5, column=0, sticky="ew", padx=(0, 7), pady=(4, 0))
        for index, (key, label) in enumerate((("args", "process_args"), ("required_args", "required_args"), ("forbidden_args", "forbidden_args"), ("version_args", "version_args"))):
            column = index % 2
            row = 6 + (index // 2) * 2
            ttk.Label(editor, text=self.tr(label), style="FieldLabel.TLabel").grid(row=row, column=column, sticky="w", padx=(0, 7) if column == 0 else (7, 0), pady=(9, 0))
            text_widget = tk.Text(editor, height=3, wrap="none", font=("Consolas", 9))
            text_widget.grid(row=row + 1, column=column, sticky="ew", padx=(0, 7) if column == 0 else (7, 0), pady=(4, 0))
            self.process_arg_editors[key] = text_widget
        ttk.Label(editor, text=self.tr("args_help"), style="CardMuted.TLabel", wraplength=860).grid(row=10, column=0, columnspan=2, sticky="w", pady=(7, 0))
        process_actions = ttk.Frame(editor, style="Card.TFrame")
        process_actions.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._button(process_actions, text=self.tr("new_process_profile"), variant="secondary", command=self.clear_process_profile_form).pack(side="left")
        self._button(process_actions, text=self.tr("remove_process_profile"), variant="secondary", command=self.delete_process_profile).pack(side="right", padx=(8, 0))
        self._button(process_actions, text=self.tr("save_process_profile"), variant="primary", command=self.save_process_profile_form).pack(side="right")

        runtime_shell, runtime = self._elevated_frame(parent, padding=(18, 15))
        runtime_shell.pack(fill="x", pady=(14, 0))
        ttk.Label(runtime, text=self.tr("runtime_safety"), style="CardTitle.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Checkbutton(runtime, text=self.tr("embedded_python"), variable=self.settings_vars["require_embedded_python"], style="Modern.TCheckbutton").grid(row=1, column=0, sticky="w", padx=(0, 18))
        ttk.Checkbutton(runtime, text=self.tr("require_tests_label"), variable=self.settings_vars["require_tests"], style="Modern.TCheckbutton").grid(row=1, column=1, sticky="w", padx=(0, 18))
        self._compact_field(runtime, "python_path", "embedded_python_path", 2, 0)
        self._compact_field(runtime, "wheels_path", "wheels_path", 2, 1)
        ttk.Label(runtime, text=self.tr("trust_level"), style="FieldLabel.TLabel").grid(row=2, column=2, sticky="w", pady=(8, 5))
        self._select(runtime, textvariable=self.settings_vars["worker_trust_level"], values=("patch_only", "branch_only", "branch_and_commit"), state="readonly").grid(row=3, column=2, sticky="ew", padx=(8, 0))
        ttk.Label(runtime, text=self.tr("trust_help"), style="CardMuted.TLabel", wraplength=360).grid(row=4, column=2, sticky="w", padx=(8, 0), pady=(4, 0))
        ttk.Label(runtime, text=self.tr("parallel_tasks"), style="FieldLabel.TLabel").grid(row=4, column=0, sticky="w", pady=(10, 5))
        ttk.Spinbox(runtime, from_=1, to=32, textvariable=self.settings_vars["max_parallel_tasks"], style="Modern.TSpinbox", width=8).grid(row=5, column=0, sticky="w")
        for column in range(3):
            runtime.columnconfigure(column, weight=1)

        update_shell, update_card = self._elevated_frame(parent, padding=(18, 15))
        update_shell.pack(fill="x", pady=(14, 0))
        update_card.columnconfigure(0, weight=1)
        ttk.Label(update_card, text=self.tr("updates_title"), style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            update_card,
            text=self.tr("updates_desc"),
            style="CardMuted.TLabel",
            wraplength=900,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 9))
        ttk.Label(update_card, text=self.tr("updates_url"), style="FieldLabel.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w"
        )
        ttk.Entry(update_card, textvariable=self.update_catalog_var, style="Modern.TEntry").grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(4, 8)
        )
        ttk.Label(update_card, textvariable=self.update_status_var, style="CardMuted.TLabel", wraplength=900).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(0, 9)
        )
        ttk.Checkbutton(
            update_card,
            text=self.tr("updates_auto"),
            variable=self.automatic_updates_var,
            style="Modern.TCheckbutton",
            command=self._persist_settings,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 9))
        self._button(
            update_card, text=self.tr("updates_trust"), variant="secondary", command=self.import_update_publisher
        ).grid(row=6, column=0, sticky="w")
        self._button(
            update_card, text=self.tr("updates_check"), variant="secondary", command=self.check_application_update
        ).grid(row=6, column=1, sticky="e", padx=(8, 0))
        self._button(
            update_card, text=self.tr("updates_install"), variant="primary", command=self.install_application_update
        ).grid(row=6, column=2, sticky="e", padx=(8, 0))

        actions = ttk.Frame(parent, style="App.TFrame")
        actions.pack(fill="x", pady=(14, 0))
        self._button(actions, text=self.tr("save_guided"), variant="primary", command=self.save_guided_config).pack(side="right")
        self._button(actions, text=self.tr("check_env"), variant="secondary", command=self.check_environment).pack(side="right", padx=8)
        self._button(actions, text=self.tr("advanced_json"), variant="secondary", command=self.toggle_advanced_config).pack(side="left")

        advanced_shell, advanced = self._elevated_frame(parent, padding=(14, 12))
        self.advanced_config_shell = advanced_shell
        self.config_editor = tk.Text(advanced, wrap="none", undo=True, height=18, font=("Consolas", 10))
        self.config_editor.pack(fill="both", expand=True)
        raw_actions = ttk.Frame(advanced, style="Card.TFrame")
        raw_actions.pack(fill="x", pady=(8, 0))
        self._button(raw_actions, text=self.tr("save_raw"), variant="primary", command=self.save_raw_config).pack(side="right")
        self._button(raw_actions, text=self.tr("reload_config"), variant="secondary", command=self.reload_config).pack(side="right", padx=8)

    def _build_endpoint_card(self, parent: ttk.Frame, prefix: str, column: int) -> None:
        shell, card = self._elevated_frame(parent, padding=(18, 15))
        shell.grid(row=0, column=column, sticky="nsew", padx=(0, 7) if column == 0 else (7, 0))
        ttk.Label(card, text=self.tr(f"{prefix}_endpoint"), style="CardTitle.TLabel").pack(anchor="w", pady=(0, 10))
        self._stacked_setting(card, "provider", f"{prefix}_provider", "provider_help", ("stub", "openai", "deepseek", "openai_compatible"))
        self._stacked_setting(card, "model", f"{prefix}_model", "model_help")
        self._stacked_setting(card, "base_url", f"{prefix}_base_url", "base_url_help")
        self._stacked_setting(card, "api_key_env", f"{prefix}_api_key_env", "api_key_env_help")

    def import_update_publisher(self) -> None:
        path = filedialog.askopenfilename(
            title=self.tr("updates_trust"),
            filetypes=(("JSON", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            key_id = trust_publisher_identity(Path(path))
        except (OSError, RuntimeError, ValueError) as exc:
            self._show_error(exc)
            return
        messagebox.showinfo(self.tr("updates_trust"), self.tr("updates_publisher", key_id=key_id))

    def check_application_update(self) -> None:
        source = self.update_catalog_var.get().strip()
        self.status_var.set(self.tr("running"))
        self._background(lambda: application_update_status(source), self._application_update_checked)

    def _application_update_checked(self, result: Any) -> None:
        self.available_update_version = str(result["version"]) if result["update_available"] else None
        if result["update_available"]:
            self.update_status_var.set(
                self.tr("updates_available", version=result["version"], size=self._format_bytes(int(result["bytes"])))
            )
        else:
            self.update_status_var.set(self.tr("updates_current", current=result["current_version"]))
        self.status_var.set(self.tr("ready"))
        self._persist_settings()

    def install_application_update(self) -> None:
        source = self.update_catalog_var.get().strip()
        if not messagebox.askyesno(self.tr("updates_install"), self.tr("updates_confirm")):
            return
        self.status_var.set(self.tr("running"))
        self._background(lambda: install_application_update(source), self._application_update_installed)

    def _application_update_installed(self, path: Any) -> None:
        version = self.available_update_version or Path(path).name
        messagebox.showinfo(
            self.tr("updates_install"),
            self.tr("updates_installed", version=version, path=path),
        )
        self.status_var.set(self.tr("ready"))

    def _run_automatic_update(self) -> None:
        source = self.update_catalog_var.get().strip()
        if not self.automatic_updates_var.get() or not source:
            return

        def task() -> dict[str, Any]:
            status = application_update_status(source)
            installed = install_application_update(source) if status["update_available"] else None
            return {**status, "installed": installed}

        self._background(task, self._automatic_update_finished)

    def _automatic_update_finished(self, result: Any) -> None:
        if result["installed"] is None:
            self.update_status_var.set(self.tr("updates_current", current=result["current_version"]))
            return
        self.available_update_version = str(result["version"])
        self._application_update_installed(result["installed"])

    @staticmethod
    def _format_bytes(value: int) -> str:
        size = float(max(0, value))
        units = ("B", "KiB", "MiB", "GiB")
        unit = units[0]
        for unit in units:
            if size < 1024 or unit == units[-1]:
                break
            size /= 1024
        return f"{size:.1f} {unit}"

    def _stacked_setting(self, parent: ttk.Frame, label: str, variable: str, help_key: str, choices: tuple[str, ...] = ()) -> None:
        ttk.Label(parent, text=self.tr(label), style="FieldLabel.TLabel").pack(anchor="w", pady=(8, 4))
        if choices:
            widget = self._select(parent, textvariable=self.settings_vars[variable], values=choices, state="readonly")
        else:
            widget = ttk.Entry(parent, textvariable=self.settings_vars[variable], style="Modern.TEntry")
        widget.pack(fill="x")
        ttk.Label(parent, text=self.tr(help_key), style="CardMuted.TLabel", wraplength=440).pack(anchor="w", pady=(3, 0))

    def _compact_field(self, parent: ttk.Frame, label: str, variable: str, row: int, column: int) -> None:
        ttk.Label(parent, text=self.tr(label), style="FieldLabel.TLabel").grid(row=row, column=column, sticky="w", pady=(8, 5), padx=(0, 8))
        ttk.Entry(parent, textvariable=self.settings_vars[variable], style="Modern.TEntry").grid(row=row + 1, column=column, sticky="ew", padx=(0, 8))

    def refresh_workspace_projects(self) -> None:
        try:
            records = workspace_project_summaries()
        except (OSError, RuntimeError, ValueError) as exc:
            self.status_var.set(f"{self.tr('error')}: {exc}")
            records = ()
        previous_id = self.workspace_project_ids.get(self.workspace_project_var.get())
        labels = tuple(f"{item.name} · {item.root}" for item in records)
        self.workspace_project_ids = {label: item.project_id for label, item in zip(labels, records)}
        self.workspace_project_roots = {label: item.root for label, item in zip(labels, records)}
        self.workspace_project_records = {item.project_id: item for item in records}
        if self.workspace_project_box is not None:
            self.workspace_project_box.configure(values=labels)
        selected = next((label for label, project_id in self.workspace_project_ids.items() if project_id == previous_id), "")
        if not selected and labels:
            selected = labels[0]
        self.workspace_project_var.set(selected)
        self.workspace_project_changed()
        self._render_workspace_projects(records)

    def _render_workspace_projects(self, records: tuple[Any, ...]) -> None:
        if self.workspace_cards_frame is None:
            return
        for child in self.workspace_cards_frame.winfo_children():
            child.destroy()
        if not records:
            ttk.Label(self.workspace_cards_frame, text=self.tr("projects_empty"), style="Subtitle.TLabel").pack(anchor="w", pady=(8, 0))
            return
        for item in records:
            shell, card = self._elevated_frame(self.workspace_cards_frame, padding=(16, 13))
            shell.pack(fill="x", pady=(0, 9))
            header = ttk.Frame(card, style="Card.TFrame")
            header.pack(fill="x")
            ttk.Label(header, text=item.name, style="CardTitle.TLabel").pack(side="left")
            state = item.error or item.last_run_status or "—"
            ttk.Label(header, text=state, style="RoleBlocked.TLabel" if item.error else "RoleStatus.TLabel").pack(side="right")
            ttk.Label(card, text=item.root, style="CardMuted.TLabel", wraplength=980).pack(anchor="w", pady=(3, 7))
            ttk.Label(
                card,
                text=self.tr(
                    "projects_queue_line",
                    queued=item.queued,
                    ready=item.ready,
                    running=item.running,
                    attention=item.review_pending + item.rework_required + item.escalated + item.rolled_back,
                    failed=item.failed,
                    done=item.done,
                ),
                style="FieldLabel.TLabel",
            ).pack(anchor="w")
            next_task = (
                self.tr("projects_next_task", task=f"{item.next_task_id} · {item.next_task_title}")
                if item.next_task_id else self.tr("projects_no_next_task")
            )
            ttk.Label(card, text=next_task, style="CardMuted.TLabel").pack(anchor="w", pady=(4, 0))

    def workspace_project_changed(self) -> None:
        project_id = self.workspace_project_ids.get(self.workspace_project_var.get())
        record = self.workspace_project_records.get(project_id or "")
        if record is None:
            self.workspace_schedule_vars["enabled"].set(False)
            self.workspace_schedule_vars["interval_minutes"].set("60")
            self.workspace_schedule_vars["final_audit"].set(False)
            self.workspace_schedule_vars["notify_windows"].set(True)
            self.workspace_schedule_vars["notify_telegram"].set(False)
            return
        self.workspace_schedule_vars["enabled"].set(record.schedule_enabled)
        self.workspace_schedule_vars["interval_minutes"].set(str(record.schedule_interval_minutes))
        self.workspace_schedule_vars["final_audit"].set(record.schedule_final_audit)
        self.workspace_schedule_vars["notify_windows"].set(record.schedule_notify_windows)
        self.workspace_schedule_vars["notify_telegram"].set(record.schedule_notify_telegram)

    def register_current_workspace_project(self) -> None:
        root = Path(self.project_var.get()) if self.project_var.get().strip() else None
        if root is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        try:
            project = register_workspace_project(root)
        except (OSError, RuntimeError, ValueError) as exc:
            self._show_error(exc)
            return
        self.status_var.set(self.tr("projects_registered_status"))
        self.refresh_workspace_projects()
        selected = next((label for label, project_id in self.workspace_project_ids.items() if project_id == project.project_id), "")
        if selected:
            self.workspace_project_var.set(selected)
            self.workspace_project_changed()

    def open_selected_workspace_project(self) -> None:
        root = self.workspace_project_roots.get(self.workspace_project_var.get())
        if not root:
            return
        self.project_var.set(root)
        self.load_project()

    def delete_workspace_project(self) -> None:
        label = self.workspace_project_var.get()
        project_id = self.workspace_project_ids.get(label)
        if not project_id:
            return
        record = self.workspace_project_records.get(project_id)
        name = record.name if record is not None else label
        if not messagebox.askyesno(self.tr("projects_remove"), self.tr("projects_confirm_remove", name=name)):
            return
        try:
            remove_workspace_project(project_id)
        except (OSError, RuntimeError, ValueError) as exc:
            self._show_error(exc)
            return
        self.status_var.set(self.tr("projects_removed_status"))
        self.refresh_workspace_projects()

    def save_workspace_schedule(self) -> None:
        project_id = self.workspace_project_ids.get(self.workspace_project_var.get())
        if not project_id:
            return
        try:
            configure_workspace_schedule(
                project_id,
                enabled=self.workspace_schedule_vars["enabled"].get(),
                interval_minutes=int(self.workspace_schedule_vars["interval_minutes"].get()),
                final_audit=self.workspace_schedule_vars["final_audit"].get(),
                notify_windows=self.workspace_schedule_vars["notify_windows"].get(),
                notify_telegram=self.workspace_schedule_vars["notify_telegram"].get(),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._show_error(exc)
            return
        self.status_var.set(self.tr("projects_schedule_saved"))
        self.refresh_workspace_projects()

    def run_selected_workspace_project(self) -> None:
        project_id = self.workspace_project_ids.get(self.workspace_project_var.get())
        if not project_id:
            return
        self._background(lambda: run_workspace_project(project_id), self._workspace_run_finished)

    def run_due_workspace_schedules(self) -> None:
        self._background(run_due_workspace_projects, self._workspace_runs_finished)

    def manage_workspace_scheduler(self, action: str) -> None:
        self._background(
            lambda: workspace_scheduler_service(action),
            self._workspace_scheduler_finished,
        )

    def _workspace_scheduler_finished(self, result: Any) -> None:
        detail = (result.stdout or result.stderr or f"exit {result.return_code}").strip().replace("\n", " · ")
        self.workspace_service_var.set(detail)
        self.status_var.set(self.tr("projects_service_result", detail=detail))

    def _workspace_run_finished(self, result: Any) -> None:
        self.status_var.set(self.tr("projects_run_result", name=result.name, status=result.status, completed=result.completed))
        self.refresh_workspace_projects()

    def _workspace_runs_finished(self, results: tuple[Any, ...]) -> None:
        if results:
            self.status_var.set(" · ".join(
                self.tr("projects_run_result", name=item.name, status=item.status, completed=item.completed)
                for item in results
            ))
        else:
            self.status_var.set(self.tr("ready"))
        self.refresh_workspace_projects()

    def _build_metrics(self, parent: ttk.Frame) -> None:
        heading = ttk.Frame(parent, style="App.TFrame")
        heading.pack(fill="x")
        self._button(
            heading,
            text=self.tr("refresh_metrics"),
            variant="secondary",
            command=self.refresh_usage_metrics,
        ).pack(side="right", padx=(12, 0))
        copy = ttk.Frame(heading, style="App.TFrame")
        copy.pack(side="left", fill="x", expand=True)
        ttk.Label(copy, text=self.tr("metrics_title"), style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(
            copy,
            text=self.tr("metrics_desc"),
            style="Subtitle.TLabel",
            wraplength=840,
        ).pack(anchor="w", pady=(3, 0))
        kpis = ttk.Frame(parent, style="App.TFrame")
        kpis.pack(fill="x", pady=(16, 14))
        definitions = (
            ("invocations", "metric_invocations"),
            ("tokens", "metric_tokens"),
            ("cost", "metric_cost"),
            ("time", "metric_time"),
            ("quality", "metric_quality"),
        )
        for column in range(3):
            kpis.columnconfigure(column, weight=1, uniform="metrics")
        for index, (key, label) in enumerate(definitions):
            row, column = divmod(index, 3)
            shell, card = self._elevated_frame(kpis, padding=(16, 13))
            shell.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 5, 0 if column == 2 else 5),
                pady=(0, 8) if row == 0 else 0,
            )
            ttk.Label(card, text=self.tr(label), style="FieldLabel.TLabel").pack(anchor="w")
            ttk.Label(card, textvariable=self.metrics_kpi_vars[key], style="RoleTitle.TLabel").pack(
                anchor="w", pady=(5, 0)
            )

        pricing_shell, pricing = self._elevated_frame(parent, padding=(16, 14))
        pricing_shell.pack(fill="x", pady=(0, 14))
        ttk.Label(pricing, text=self.tr("pricing_title"), style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=5, sticky="w"
        )
        ttk.Label(
            pricing,
            text=self.tr("pricing_desc"),
            style="CardMuted.TLabel",
            wraplength=980,
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(4, 10))
        ttk.Label(pricing, text=self.tr("pricing_profile"), style="FieldLabel.TLabel").grid(
            row=2, column=0, columnspan=5, sticky="w"
        )
        self.price_profile_box = self._select(
            pricing,
            textvariable=self.price_profile_var,
            state="readonly",
        )
        self.price_profile_box.grid(row=3, column=0, columnspan=5, sticky="ew", pady=(4, 8))
        self.price_profile_box.bind("<<ComboboxSelected>>", lambda _event: self.load_selected_model_price())
        fields = (
            ("pricing_provider", "provider"),
            ("pricing_model", "model"),
            ("pricing_input", "input_per_million"),
            ("pricing_output", "output_per_million"),
            ("pricing_currency", "currency"),
        )
        for column, (label, variable) in enumerate(fields):
            ttk.Label(pricing, text=self.tr(label), style="FieldLabel.TLabel").grid(
                row=4, column=column, sticky="w", padx=(0 if column == 0 else 5, 0)
            )
            ttk.Entry(pricing, textvariable=self.price_vars[variable], style="Modern.TEntry").grid(
                row=5, column=column, sticky="ew", padx=(0 if column == 0 else 5, 0), pady=(4, 0)
            )
            pricing.columnconfigure(column, weight=2 if column in {0, 1} else 1)
        pricing_actions = ttk.Frame(pricing, style="Card.TFrame")
        pricing_actions.grid(row=6, column=0, columnspan=5, sticky="e", pady=(10, 0))
        self._button(
            pricing_actions,
            text=self.tr("pricing_new"),
            variant="secondary",
            command=self.clear_model_price_form,
        ).pack(side="left")
        self._button(
            pricing_actions,
            text=self.tr("pricing_remove"),
            variant="secondary",
            command=self.delete_model_price,
        ).pack(side="left", padx=(8, 0))
        self._button(
            pricing_actions,
            text=self.tr("pricing_save"),
            variant="primary",
            command=self.save_model_price_form,
        ).pack(side="left", padx=(8, 0))

        self.metrics_groups_frame = ttk.Frame(parent, style="App.TFrame")
        self.metrics_groups_frame.pack(fill="x")
        self._render_usage_metrics({"groups": []})

    def refresh_usage_metrics(self) -> None:
        if self.snapshot is None:
            self._render_usage_metrics({"groups": []})
            return
        try:
            summary = usage_metrics_summary(self.snapshot.root)
        except (OSError, RuntimeError, ValueError) as exc:
            self.status_var.set(f"{self.tr('error')}: {exc}")
            return
        self._render_usage_metrics(summary)

    def _render_usage_metrics(self, summary: dict[str, Any]) -> None:
        groups = tuple(summary.get("groups") or ())
        invocations = sum(int(item.get("invocations", 0)) for item in groups)
        successful = sum(int(item.get("successful_invocations", 0)) for item in groups)
        tokens = sum(int(item.get("total_tokens", 0)) for item in groups)
        duration_ms = sum(int(item.get("duration_ms", 0)) for item in groups)
        costs: dict[str, float] = {}
        quality_total = 0.0
        quality_samples = 0
        for item in groups:
            for currency, amount in dict(item.get("costs") or {}).items():
                costs[str(currency)] = costs.get(str(currency), 0.0) + float(amount)
            samples = int(item.get("quality_samples", 0))
            score = item.get("quality_score")
            if samples and score is not None:
                quality_total += float(score) * samples
                quality_samples += samples
        self.metrics_kpi_vars["invocations"].set(f"{invocations} · {successful} {self.tr('metric_success')}")
        self.metrics_kpi_vars["tokens"].set(f"{tokens:,}".replace(",", " "))
        self.metrics_kpi_vars["cost"].set(self._format_costs(costs))
        self.metrics_kpi_vars["time"].set(self._format_duration(duration_ms))
        self.metrics_kpi_vars["quality"].set(
            f"{quality_total / quality_samples:.0%}" if quality_samples else self.tr("metric_quality_na")
        )
        if self.metrics_groups_frame is None:
            return
        for child in self.metrics_groups_frame.winfo_children():
            child.destroy()
        if not groups:
            ttk.Label(
                self.metrics_groups_frame,
                text=self.tr("metrics_empty"),
                style="Subtitle.TLabel",
            ).pack(anchor="w", pady=(8, 0))
            return
        for item in groups:
            shell, card = self._elevated_frame(self.metrics_groups_frame, padding=(16, 13))
            shell.pack(fill="x", pady=(0, 9))
            title = f"{str(item.get('role', '')).upper()}  ·  {item.get('agent', '—')}  ·  {item.get('model', '—')}"
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
            quality = (
                f"{float(item['quality_score']):.0%}"
                if item.get("quality_score") is not None
                else self.tr("metric_quality_na")
            )
            line = self.tr(
                "metric_group_line",
                success=item.get("successful_invocations", 0),
                invocations=item.get("invocations", 0),
                tokens=f"{int(item.get('total_tokens', 0)):,}".replace(",", " "),
                duration=self._format_duration(int(item.get("duration_ms", 0))),
                cost=self._format_costs(dict(item.get("costs") or {})),
                quality=quality,
            )
            unknown = int(item.get("unknown_token_invocations", 0))
            if unknown:
                line += f" · {unknown} {self.tr('metric_unknown_tokens')}"
            ttk.Label(card, text=line, style="CardMuted.TLabel", wraplength=980).pack(
                anchor="w", pady=(5, 0)
            )

    @staticmethod
    def _format_duration(duration_ms: int) -> str:
        seconds = max(0, duration_ms) / 1000
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes, seconds = divmod(int(seconds), 60)
        if minutes < 60:
            return f"{minutes}m {seconds:02d}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes:02d}m"

    @staticmethod
    def _format_costs(costs: dict[str, float]) -> str:
        if not costs:
            return "—"
        return " + ".join(f"{amount:.4f} {currency}" for currency, amount in sorted(costs.items()))

    def _build_operations(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text=self.tr("operations"), style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(parent, text=self.tr("operations_desc"), style="Subtitle.TLabel").pack(anchor="w", pady=(3, 14))
        actions = ttk.Frame(parent, style="App.TFrame")
        actions.pack(fill="x", pady=(0, 12))
        for column in range(3):
            actions.columnconfigure(column, weight=1)
        for index, operation in enumerate(("doctor", "status", "queue", "history", "audit_history", "compatibility", "run_next", "run_all", "audit")):
            variant = "primary" if operation in {"run_next", "run_all"} else "secondary"
            self._button(
                actions,
                text=self.tr(operation),
                variant=variant,
                command=lambda name=operation: self.run_operation(name),
            ).grid(
                row=index // 3,
                column=index % 3,
                sticky="ew",
                padx=(0 if index % 3 == 0 else 5, 0 if index % 3 == 2 else 5),
                pady=(0 if index < 3 else 6, 0),
            )
        operator_shell, operator = self._elevated_frame(parent, padding=(16, 14))
        operator_shell.pack(fill="x", pady=(0, 12))
        operator.columnconfigure(0, weight=1)
        operator.columnconfigure(1, weight=2)
        ttk.Label(operator, text=self.tr("operator_task"), style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(operator, text=self.tr("recovery_reason"), style="FieldLabel.TLabel").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.operator_task_box = self._select(operator, textvariable=self.operator_task_var, state="readonly")
        self.operator_task_box.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(4, 0))
        ttk.Entry(operator, textvariable=self.recovery_reason_var, style="Modern.TEntry").grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))
        operator_actions = ttk.Frame(operator, style="Card.TFrame")
        operator_actions.grid(row=2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        self._button(operator_actions, text=self.tr("retry"), variant="secondary", command=lambda: self.run_operation("retry")).pack(side="left")
        self._button(operator_actions, text=self.tr("recover"), variant="secondary", command=lambda: self.run_operation("recover")).pack(side="left", padx=(8, 0))
        self._button(operator_actions, text=self.tr("requeue_escalated"), variant="secondary", command=lambda: self.run_operation("requeue_escalated")).pack(side="left", padx=(8, 0))
        self._button(operator_actions, text=self.tr("fail_escalated"), variant="secondary", command=lambda: self.run_operation("fail_escalated")).pack(side="left", padx=(8, 0))
        output_shell, output_card = self._elevated_frame(parent, padding=(16, 14))
        output_shell.pack(fill="both", expand=True)
        ttk.Label(output_card, text=self.tr("output"), style="CardTitle.TLabel").pack(anchor="w", pady=(0, 9))
        self.output_editor = tk.Text(output_card, wrap="word", state="disabled", font=("Consolas", 10))
        self.output_editor.pack(fill="both", expand=True)

    def _create_role_card(self, parent: ttk.Frame, role: str, column: int) -> None:
        shell, card = self._elevated_frame(parent, padding=(18, 16))
        shell.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0 if column == 2 else 6))
        self.role_card_shells[role] = shell
        title_row = ttk.Frame(card, style="Card.TFrame")
        title_row.pack(fill="x")
        badge_style = "Optional.Badge.TLabel" if role == "critic" else "Required.Badge.TLabel"
        ttk.Label(title_row, text=self.tr(role), style="RoleTitle.TLabel").pack(side="left")
        ttk.Label(title_row, text=self.tr("optional" if role == "critic" else "required"), style=badge_style).pack(side="right")
        ttk.Label(card, text=self.tr(f"{role}_desc"), style="CardMuted.TLabel", wraplength=255).pack(anchor="w", pady=(4, 10))
        for key in ("agent", "model", "driver"):
            ttk.Label(card, text=self.tr(key), style="FieldLabel.TLabel").pack(anchor="w", pady=(8 if key != "agent" else 0, 5))
            if key == "agent":
                widget = self._select(card, textvariable=self.role_vars[role][key], state="readonly")
                widget.bind("<<ComboboxSelected>>", lambda _event, selected=role: self._agent_changed(selected))
                self.role_boxes[role] = widget
            else:
                widget = ttk.Entry(
                    card,
                    textvariable=(
                        self.role_driver_display_vars[role]
                        if key == "driver"
                        else self.role_vars[role][key]
                    ),
                    style="Modern.TEntry",
                    state="readonly" if key == "driver" else "normal",
                )
            widget.pack(fill="x")
            if key == "driver":
                ttk.Label(
                    card,
                    text=self.tr("driver_help"),
                    style="CardMuted.TLabel",
                    wraplength=255,
                ).pack(anchor="w", pady=(4, 0))
        status = ttk.Label(card, textvariable=self.role_status_vars[role], style="RoleStatus.TLabel", wraplength=260)
        status.pack(anchor="w", pady=(12, 0))
        self.role_status_labels[role] = status
        self._button(
            card,
            text=self.tr("check_role"),
            variant="secondary",
            command=lambda selected=role: self.run_role_check(selected),
        ).pack(fill="x", pady=(12, 0))

    def _surface_style(self, parent: tk.Misc) -> SurfaceStyle:
        palette = PALETTES[self.theme_var.get()]
        return SurfaceStyle(
            background=self._behind(parent),
            surface=palette["surface"],
            surface_hover=palette["surface_hover"],
            shadow=palette["shadow"],
        )

    def _behind(self, parent: tk.Misc) -> str:
        """The colour a drawn widget sits on, so its blended corners disappear into it."""
        palette = PALETTES[self.theme_var.get()]
        try:
            style_name = parent.cget("style")
        except tk.TclError:
            style_name = ""
        if style_name == "Sidebar.TFrame":
            return palette["sidebar"]
        if style_name in {"Card.TFrame", "Status.TFrame"}:
            return palette["surface"] if style_name == "Card.TFrame" else palette["surface_alt"]
        if isinstance(parent, (tk.Canvas, tk.Frame)):
            try:
                return str(parent.cget("background"))
            except tk.TclError:
                return palette["background"]
        return palette["background"]

    def _elevated_frame(self, parent: tk.Misc, *, padding: Any) -> tuple[tk.Widget, ttk.Frame]:
        card = ElevatedCard(
            parent,
            self._surface_style(parent),
            lambda host: ttk.Frame(host, style="Card.TFrame", padding=padding),
        )
        return card, card.body

    def _button(
        self,
        parent: tk.Misc,
        *,
        text: str,
        command: Callable[[], None] | None = None,
        variant: str = "secondary",
        width: int | None = None,
    ) -> RoundedButton:
        palette = PALETTES[self.theme_var.get()]
        background = self._behind(parent)
        if variant == "primary":
            style = ButtonStyle(
                background=background,
                fill=palette["accent"],
                fill_hover=palette["accent_hover"],
                fill_active=_mix_color(palette["accent"], palette["shadow"], 0.25),
                foreground="#ffffff",
            )
        else:
            style = ButtonStyle(
                background=background,
                fill=palette["surface_alt"],
                fill_hover=palette["selection"],
                fill_active=_mix_color(palette["surface_alt"], palette["shadow"], 0.3),
                foreground=palette["foreground"],
                padding=(16, 10),
            )
        return RoundedButton(
            parent,
            text=text,
            command=command,
            style=style,
            font=self._font_bold(9),
            width=width,
        )

    def _nav_button(
        self, parent: tk.Misc, *, text: str, command: Callable[[], None]
    ) -> RoundedButton:
        palette = PALETTES[self.theme_var.get()]
        base = ButtonStyle(
            background=palette["sidebar"],
            fill=palette["sidebar"],
            fill_hover=_mix_color(palette["sidebar"], palette["selection"], 0.55),
            fill_active=palette["selection"],
            foreground=palette["muted"],
            radius=10,
            padding=(14, 11),
            anchor="w",
        )
        selected = ButtonStyle(
            background=palette["sidebar"],
            fill=palette["selection"],
            fill_hover=_mix_color(palette["selection"], palette["accent"], 0.2),
            fill_active=palette["selection"],
            foreground=palette["foreground"],
            radius=10,
            padding=(14, 11),
            anchor="w",
        )
        return RoundedButton(
            parent,
            text=text,
            command=command,
            style=base,
            selected_style=selected,
            font=self._font_bold(10),
        )

    def _select(
        self,
        parent: tk.Misc,
        *,
        textvariable: tk.StringVar,
        values: Sequence[str] = (),
        width: int | None = None,
        height: int = 9,
        state: str = "readonly",
    ) -> ModernSelect:
        palette = PALETTES[self.theme_var.get()]
        style = SelectStyle(
            background=self._behind(parent),
            field=palette["field"],
            field_hover=palette["field_hover"],
            border=palette["border"],
            foreground=palette["foreground"],
            muted=palette["muted"],
            popup=palette["popup"],
            popup_hover=palette["popup_hover"],
            accent=palette["accent"],
        )
        widget = ModernSelect(
            parent,
            textvariable=textvariable,
            values=values,
            style=style,
            font=self._font(10),
            width=width,
            height=height,
            editable=state == "normal",
        )
        return widget

    def _apply_theme(self) -> None:
        palette = PALETTES[self.theme_var.get()]
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.root.configure(background=palette["background"])
        default_font = self._font(10)
        style.configure(".", font=default_font, background=palette["background"], foreground=palette["foreground"], borderwidth=0)
        style.configure("App.TFrame", background=palette["background"])
        style.configure("Sidebar.TFrame", background=palette["sidebar"])
        style.configure("Card.TFrame", background=palette["surface"], relief="flat", borderwidth=0)
        style.configure("Status.TFrame", background=palette["surface_alt"])
        style.configure("TLabel", background=palette["background"], foreground=palette["foreground"])
        style.configure("BrandMark.TLabel", background=palette["accent"], foreground="#ffffff", font=self._font_bold(15), padding=(9, 5))
        style.configure("Brand.TLabel", background=palette["sidebar"], foreground=palette["foreground"], font=self._font_bold(16))
        style.configure("Eyebrow.TLabel", background=palette["sidebar"], foreground=palette["muted"], font=self._font_bold(8))
        style.configure("Eyebrow.App.TLabel", background=palette["background"], foreground=palette["accent"], font=self._font_bold(8))
        style.configure("PageTitle.TLabel", background=palette["background"], foreground=palette["foreground"], font=self._font_bold(21))
        style.configure("SectionTitle.TLabel", background=palette["background"], foreground=palette["foreground"], font=self._font_bold(18))
        style.configure("Subtitle.TLabel", background=palette["background"], foreground=palette["muted"], font=default_font)
        style.configure("RoleTitle.TLabel", background=palette["surface"], foreground=palette["foreground"], font=self._font_bold(14))
        style.configure("CardTitle.TLabel", background=palette["surface"], foreground=palette["foreground"], font=self._font_bold(11))
        style.configure("CardMuted.TLabel", background=palette["surface"], foreground=palette["muted"], font=self._font(9))
        style.configure("FieldLabel.TLabel", background=palette["surface"], foreground=palette["muted"], font=self._font_bold(9))
        style.configure("RoleStatus.TLabel", background=palette["surface"], foreground=palette["muted"], font=self._font(8))
        style.configure("RoleReady.TLabel", background=palette["surface"], foreground=palette["success"], font=self._font(8))
        style.configure("RoleBlocked.TLabel", background=palette["surface"], foreground="#f87171", font=self._font(8))
        style.configure("Status.TLabel", background=palette["surface_alt"], foreground=palette["muted"], font=self._font(9))
        style.configure("StatusDot.TLabel", background=palette["surface_alt"], foreground=palette["success"], font=self._font(9))
        style.configure("Required.Badge.TLabel", background=palette["selection"], foreground=palette["accent"], font=self._font_bold(7), padding=(7, 3))
        style.configure("Optional.Badge.TLabel", background=palette["surface_alt"], foreground=palette["muted"], font=self._font_bold(7), padding=(7, 3))
        # Buttons, drop-downs and cards are drawn in gui_widgets; only the plain
        # text fields and the check indicator are still ttk.
        field_options = dict(
            fieldbackground=palette["field"],
            foreground=palette["foreground"],
            insertcolor=palette["accent"],
            bordercolor=palette["border"],
            lightcolor=palette["border"],
            darkcolor=palette["border"],
            padding=(11, 9),
            relief="flat",
        )
        style.configure("Modern.TEntry", **field_options)
        style.map(
            "Modern.TEntry",
            fieldbackground=[("focus", palette["field_hover"])],
            bordercolor=[("focus", palette["accent"])],
            lightcolor=[("focus", palette["accent"])],
            darkcolor=[("focus", palette["accent"])],
        )
        style.configure(
            "Modern.TSpinbox",
            fieldbackground=palette["field"],
            foreground=palette["foreground"],
            arrowcolor=palette["muted"],
            bordercolor=palette["border"],
            padding=(9, 7),
            relief="flat",
        )
        style.configure(
            "Modern.TCheckbutton",
            background=palette["surface"],
            foreground=palette["foreground"],
            indicatorcolor=palette["field"],
            indicatorrelief="flat",
            indicatormargin=(0, 0, 8, 0),
            focuscolor=palette["accent"],
            padding=5,
        )
        style.map(
            "Modern.TCheckbutton",
            background=[("active", palette["surface"])],
            indicatorcolor=[("selected", palette["accent"]), ("active", palette["field_hover"])],
            foreground=[("disabled", palette["muted"])],
        )
        style.layout(
            "Modern.Vertical.TScrollbar",
            [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})],
        )
        style.configure(
            "Modern.Vertical.TScrollbar",
            troughcolor=palette["background"],
            background=palette["border"],
            bordercolor=palette["background"],
            lightcolor=palette["border"],
            darkcolor=palette["border"],
            relief="flat",
            borderwidth=0,
            width=8,
        )
        style.map("Modern.Vertical.TScrollbar", background=[("active", palette["muted"]), ("pressed", palette["accent"])])

    def _show_page(self, page: str) -> None:
        self.current_page = page
        for name, frame in self.pages.items():
            if name == page:
                frame.pack(fill="both", expand=True, padx=(16, 0))
            else:
                frame.pack_forget()
        for name, button in self.nav_buttons.items():
            button.set_selected(name == page)
        if self.content_canvas is not None:
            self.content_canvas.yview_moveto(0)
            self.root.after_idle(self._sync_content_geometry)
        self._animate_page(self.pages[page])
        if page == "metrics":
            self.refresh_usage_metrics()

    def _content_resized(self, event: tk.Event) -> None:
        if self.content_canvas is None or self.content_host_window is None:
            return
        self.content_canvas.itemconfigure(self.content_host_window, width=event.width)
        if self._geometry_sync_pending is None:
            self._geometry_sync_pending = self.root.after_idle(self._sync_content_geometry)

    def _sync_content_geometry(self) -> None:
        """Size the canvas window from the visible page, not a previously shown page.

        Guarded against re-entry: it calls update_idletasks, which runs pending
        <Configure> handlers, which schedule this again. Drawn cards make that chain
        long enough that an unguarded version never drains the idle queue.
        """
        self._geometry_sync_pending = None
        if self._syncing_geometry:
            return
        if self.content_canvas is None or self.content_host_window is None:
            return
        frame = self.pages.get(self.current_page)
        if frame is None or not frame.winfo_exists():
            return
        self._syncing_geometry = True
        try:
            self._apply_content_geometry(frame)
        finally:
            self._syncing_geometry = False

    def _apply_content_geometry(self, frame: ttk.Frame) -> None:
        assert self.content_canvas is not None and self.content_host_window is not None
        if self.current_page == "roles" and self.cards_frame is not None:
            # Measure from the canvas, not from the cards. The cards are sized by the
            # column they sit in, so asking them how wide they are and then choosing
            # the column count from that answer is a loop that never settles.
            self._layout_role_cards(self.content_canvas.winfo_width())
        frame.update_idletasks()
        width = max(self.content_canvas.winfo_width(), 1)
        height = max(frame.winfo_reqheight(), self.content_canvas.winfo_height(), 1)
        self.content_canvas.itemconfigure(self.content_host_window, width=width, height=height)
        self.content_canvas.configure(scrollregion=(0, 0, width, height))
        self.content_canvas.yview_moveto(0)

    def _animate_page(self, frame: ttk.Frame, step: int = 0) -> None:
        offsets = (16, 11, 7, 4, 2, 0)
        if step >= len(offsets) or not frame.winfo_exists():
            return
        frame.pack_configure(padx=(offsets[step], 0))
        self.root.after(18, lambda: self._animate_page(frame, step + 1))

    def _animate_shell(self, shell: tk.Frame, start: str, end: str, step: int = 0) -> None:
        if not shell.winfo_exists() or step > 6:
            return
        ratio = step / 6
        color = _mix_color(start, end, ratio)
        shell.configure(background=color)
        shell.after(18, lambda: self._animate_shell(shell, start, end, step + 1))

    def _on_resize(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        if self._resize_after is not None:
            self.root.after_cancel(self._resize_after)
        self._resize_after = self.root.after(70, lambda: self._apply_responsive_layout(event.width))

    def _register_wrapping_labels(self) -> None:
        """Remember every label's designed wrap width so it can be reduced to fit."""
        self._wrapping_labels = []
        stack = [self.root]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            if not isinstance(widget, ttk.Label):
                continue
            try:
                base = int(widget.cget("wraplength"))
            except (tk.TclError, ValueError):
                continue
            if base > 0:
                self._wrapping_labels.append((widget, base))

    def _apply_label_wrapping(self) -> None:
        """Wrap long text at the container's real width.

        The designed widths were measured against English strings; Russian runs
        15-30% longer, and a label that asks for more room than its card has is
        clipped rather than wrapped.
        """
        for label, base in self._wrapping_labels:
            try:
                parent = label.nametowidget(label.winfo_parent())
                available = parent.winfo_width()
                if available <= 1:
                    continue
                target = max(MINIMUM_WRAP_WIDTH, min(base, available - LABEL_WRAP_PADDING))
                if int(label.cget("wraplength")) != target:
                    label.configure(wraplength=target)
            except tk.TclError:
                continue

    def _apply_responsive_layout(self, window_width: int) -> None:
        self._resize_after = None
        self._apply_label_wrapping()
        compact = window_width < 1000
        if compact != self._sidebar_compact and self.sidebar is not None:
            self._sidebar_compact = compact
            self.sidebar.configure(
                width=82 if compact else self._full_sidebar_width,
                padding=(12 if compact else 20, 24),
            )
            if self.brand_copy is not None:
                (self.brand_copy.pack_forget() if compact else self.brand_copy.pack(side="left", padx=(10, 0)))
            if self.appearance_panel is not None:
                (self.appearance_panel.pack_forget() if compact else self.appearance_panel.pack(fill="x"))
            if self.appearance_label is not None:
                (self.appearance_label.pack_forget() if compact else self.appearance_label.pack(anchor="w", pady=(0, 8), before=self.appearance_panel))
            icons = {"setup": "✓", "projects": "▤", "chat": "✦", "roles": "◆", "agents_setup": "⬡", "tools": "⌘", "config": "▦", "metrics": "◉", "operations": "▶"}
            for name, button in self.nav_buttons.items():
                button.configure(text=f"  {icons[name]}" if compact else f"  {icons[name]}   {self.tr(name)}")
        if self.cards_frame is None:
            self._layout_connection_cards(window_width)
            return
        self._layout_role_cards(self.cards_frame.winfo_width())
        self._layout_connection_cards(window_width)

    def _layout_connection_cards(self, window_width: int) -> None:
        if self.connection_cards_frame is None or self.connection_card_shells is None:
            return
        stacked = window_width < 1180
        managed, remote = self.connection_card_shells
        if stacked:
            self.connection_cards_frame.columnconfigure(0, weight=1, uniform="")
            self.connection_cards_frame.columnconfigure(1, weight=0, uniform="")
            managed.grid_configure(row=0, column=0, padx=0, pady=(0, 12))
            remote.grid_configure(row=1, column=0, padx=0, pady=0)
        else:
            self.connection_cards_frame.columnconfigure(0, weight=1, uniform="connections")
            self.connection_cards_frame.columnconfigure(1, weight=1, uniform="connections")
            managed.grid_configure(row=0, column=0, padx=(0, 7), pady=0)
            remote.grid_configure(row=0, column=1, padx=(7, 0), pady=0)

    def _layout_role_cards(self, available: int) -> None:
        if self.cards_frame is None:
            return
        columns = 3 if available >= 760 else 2 if available >= 520 else 1
        for column in range(3):
            self.cards_frame.columnconfigure(column, weight=1 if column < columns else 0, uniform="roles")
        for index, role in enumerate(("supervisor", "worker", "critic")):
            shell = self.role_card_shells.get(role)
            if shell is None:
                continue
            row, column = divmod(index, columns)
            left = 0 if column == 0 else 6
            right = 0 if column == columns - 1 else 6
            shell.grid_configure(row=row, column=column, padx=(left, right), pady=(0 if row == 0 else 12, 0))

    def _set_window_icon(self) -> None:
        palette = PALETTES[self.theme_var.get()]
        icon = tk.PhotoImage(width=32, height=32)
        icon.put(palette["accent"], to=(0, 0, 32, 32))
        icon.put("#ffffff", to=(7, 7, 12, 25))
        icon.put("#ffffff", to=(20, 7, 25, 25))
        icon.put("#ffffff", to=(12, 14, 20, 19))
        self._icon_image = icon
        self.root.iconphoto(True, icon)

    def _on_mousewheel(self, event: tk.Event) -> str | None:
        if self.content_canvas is None or isinstance(event.widget, tk.Text):
            return None
        delta = -1 if event.delta > 0 else 1
        self.content_canvas.yview_scroll(delta * 3, "units")
        return "break"

    def browse_project(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.project_var.get() or str(Path.cwd()))
        if selected:
            self.project_var.set(selected)
            self.load_project()

    def load_project(self, refresh_registry: bool = False) -> None:
        if not self.project_var.get().strip():
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        self._background(
            lambda: load_project_snapshot(Path(self.project_var.get()), refresh_registry=refresh_registry),
            self._project_loaded,
        )

    def _project_loaded(self, snapshot: Any) -> None:
        self.snapshot = snapshot
        self.project_var.set(str(snapshot.root))
        self._populate_snapshot()
        self._persist_settings()
        self._refresh_readiness()

    def _refresh_readiness(self) -> None:
        """Run Doctor by itself and say, in one line, what still needs doing.

        Doctor used to be a button the operator had to remember to press, which meant
        a project could sit in a state that cannot run a task with nothing on screen
        saying so.
        """
        if self.snapshot is None:
            self.readiness_var.set("")
            return
        self.readiness_var.set(self.tr("readiness_checking"))
        self._background(
            lambda: run_doctor(self.snapshot.root, self.snapshot.config),
            self._readiness_finished,
        )

    def _readiness_finished(self, report: Any) -> None:
        if isinstance(report, BaseException):
            self.readiness_var.set(self.tr("readiness_unknown"))
            return
        failed = [check for check in report.checks if not check.ok]
        if failed:
            step = next((check.next_step for check in failed if check.next_step), "")
            detail = step or ", ".join(check.name for check in failed[:3])
            self.readiness_var.set(self.tr("readiness_blocked", detail=detail))
        elif report.warnings:
            step = report.next_steps[0] if report.next_steps else report.warnings[0].name
            self.readiness_var.set(self.tr("readiness_warning", detail=step))
        else:
            self.readiness_var.set(self.tr("readiness_ready"))

    def _populate_snapshot(self) -> None:
        if self.snapshot is None:
            return
        agents = self.snapshot.catalog["agents"]
        for role in ("supervisor", "worker", "critic"):
            supported = [
                item["name"]
                for item in agents
                if item.get(f"{role}_driver") and item.get("available")
            ]
            self.role_boxes[role].configure(values=supported)
        profile = self.snapshot.profile
        if profile is not None:
            selections = {"supervisor": profile.supervisor, "worker": profile.worker, "critic": profile.critic}
            self.critic_enabled_var.set(profile.critic_enabled)
            self.attempts_var.set(str(profile.max_attempts))
            for role, selection in selections.items():
                self.role_vars[role]["agent"].set(selection.agent if selection else "")
                self.role_vars[role]["model"].set(selection.model if selection and selection.model else "")
                self.role_vars[role]["driver"].set(selection.driver if selection and selection.driver else "")
                self._agent_changed(role)
        else:
            for role in ("supervisor", "worker"):
                values = self.role_boxes[role].cget("values")
                if values and not self.role_vars[role]["agent"].get():
                    self.role_vars[role]["agent"].set(values[0])
                    self._agent_changed(role)
        self._update_role_save_state()
        values = editable_project_settings(self.snapshot.config)
        for key in self.settings_vars:
            value = getattr(values, key)
            self.settings_vars[key].set(value)
        self.storage_vars["project"].set(str(self.snapshot.config_path))
        self.storage_vars["roles"].set(str(self.snapshot.root / ".hoh" / "role-profile.json"))
        self.storage_vars["chat"].set(str(supervisor_chat_path(self.snapshot.root)))
        self.storage_vars["ui"].set(str(gui_settings_path()))
        process_agents = [item for item in agents if item.get("process_profile")]
        self.process_profiles_var.set(
            "; ".join(
                f"{item['display_name']} [{item['process_profile']}] — {item.get('detail') or '—'}"
                for item in process_agents
            )
            or "—"
        )
        profiles = editable_process_profiles(self.snapshot.config)
        if self.process_profile_box is not None:
            self.process_profile_box.configure(values=tuple(item.name for item in profiles))
            selected = self.process_vars["name"].get().strip()
            if selected and any(item.name == selected for item in profiles):
                self.load_selected_process_profile()
        self._populate_model_prices()
        self._populate_session_secrets()
        self._populate_a2a_profiles()
        self.load_mcp_role()
        if not self.registry_agent_records:
            self.refresh_managed_agents(False)
        tasks = queue_task_choices(self.snapshot.root)
        labels = tuple(f"{item.task_id} · {item.status} · {item.title}" for item in tasks)
        self.operator_task_ids = {label: item.task_id for label, item in zip(labels, tasks)}
        if self.operator_task_box is not None:
            self.operator_task_box.configure(values=labels)
            if self.operator_task_var.get() not in labels:
                preferred = next((label for label, item in zip(labels, tasks) if item.status in {"failed", "running"}), "")
                self.operator_task_var.set(preferred)
        registry = self.snapshot.catalog["registry"]
        self.status_var.set(self.tr("registry", count=registry["agent_count"], version=registry["version"] or "—"))
        self.reload_config()
        self.reload_chat()
        self.refresh_usage_metrics()
        self._refresh_onboarding_progress()
        status = self.snapshot.status
        if self._pending_operation_output is not None:
            self._set_output(self._pending_operation_output)
            self._pending_operation_output = None
        else:
            self._set_output(self.tr("queue_summary", queued=status.queued, running=status.running, review=status.review_pending, failed=status.failed, done=status.done))

    def _selected_preset(self):
        preset_id = self.preset_display_ids.get(self.preset_var.get())
        if not preset_id:
            preset_id = ROLE_PRESETS[0].preset_id
        return role_preset(preset_id)

    def _preset_changed(self) -> None:
        preset = self._selected_preset()
        self.preset_description_var.set(preset.description(self.locale_var.get()))
        self.preset_agents_var.set(
            self.tr("setup_required_agents", names=", ".join(preset.required_agents))
        )
        self._refresh_onboarding_progress()

    def _refresh_onboarding_progress(self) -> None:
        if self.snapshot is None:
            for variable in self.setup_status_vars.values():
                variable.set(self.tr("setup_status_pending"))
            self.setup_progress = None
            return
        progress = onboarding_progress(self.snapshot.profile, self.snapshot.catalog)
        self.setup_progress = progress
        preset = self._selected_preset()
        installed_ids = {
            agent_id
            for agent_id, (_agent, status) in self.registry_agent_records.items()
            if status.installed
        }
        preset_installed = all(agent in installed_ids for agent in preset.required_agents)
        ready = self.tr("setup_status_ready")
        pending = self.tr("setup_status_pending")
        self.setup_status_vars["project"].set(ready if self.snapshot is not None else pending)
        # "Team ready" means the agents this preset needs are installed and the roles
        # are saved. Whether the vendor login works cannot be known without calling
        # the agent, and the first task is what answers that.
        team_ready = preset_installed and progress.agents_ready and progress.roles_ready
        self.setup_status_vars["team"].set(ready if team_ready else pending)
        self.setup_status_vars["task"].set(ready if team_ready else pending)

    def prepare_team(self) -> None:
        """One action for what used to be three chores: install, then assign the roles.

        The operator picked a team; installing the agents that team needs and saving
        the role assignment are consequences of that choice, not decisions of their own.
        """
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        preset = self._selected_preset()
        names = "\n".join(f"• {name}" for name in preset.required_agents)
        if not messagebox.askyesno(
            self.tr("setup_step_team"), self.tr("setup_confirm_install", names=names)
        ):
            return

        def prepare() -> tuple[int, int]:
            # fetch_if_missing: on a machine that has never cached the Registry this
            # step used to fail with "refresh the registry first", which is exactly
            # the detour the three-step first run exists to remove.
            records = {
                agent.id: status
                for agent, status in registry_agent_statuses(refresh=False, fetch_if_missing=True)
            }
            installed = 0
            already = 0
            for agent_id in preset.required_agents:
                status = records.get(agent_id)
                if status is None:
                    raise ValueError(f"Agent is absent from the current ACP Registry: {agent_id}")
                if status.installed:
                    already += 1
                    continue
                if not status.installable:
                    raise ValueError(
                        f"Agent cannot be installed automatically: {agent_id} · {status.detail}"
                    )
                install_registry_agent(agent_id)
                installed += 1
            return installed, already

        self.status_var.set(self.tr("running"))
        self._background(prepare, self._team_prepared)

    def _team_prepared(self, result: Any) -> None:
        if isinstance(result, BaseException):
            self._show_error(result)
            return
        installed, already = result
        self.refresh_managed_agents(False)
        self.apply_selected_preset()
        self.status_var.set(
            self.tr("setup_team_ready", installed=installed, already=already)
        )

    def start_first_task(self) -> None:
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        self._show_page("chat")

    def apply_selected_preset(self) -> None:
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        preset = self._selected_preset()
        profile = preset.profile(max_attempts=int(self.attempts_var.get()))
        try:
            save_project_roles(
                self.snapshot.root,
                supervisor_agent=profile.supervisor.agent,
                supervisor_model=profile.supervisor.model,
                supervisor_driver=profile.supervisor.driver,
                worker_agent=profile.worker.agent,
                worker_model=profile.worker.model,
                worker_driver=profile.worker.driver,
                critic_enabled=profile.critic is not None,
                critic_agent=profile.critic.agent if profile.critic else None,
                critic_model=profile.critic.model if profile.critic else None,
                critic_driver=profile.critic.driver if profile.critic else None,
                max_attempts=profile.max_attempts,
            )
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            self._show_page("agents_setup")
            return
        messagebox.showinfo(self.tr("setup_step_roles"), self.tr("setup_roles_saved"))
        self.load_project()

    def finish_onboarding(self) -> None:
        self._refresh_onboarding_progress()
        if self.setup_progress is None or not self.setup_progress.complete:
            detail = (
                ", ".join(self.setup_progress.missing_agents)
                if self.setup_progress is not None
                else self.tr("no_project")
            )
            messagebox.showwarning(
                self.tr("setup_finish"), self.tr("setup_incomplete", detail=detail or self.tr("setup_status_pending"))
            )
            return
        self.settings = GuiSettings(
            locale=self.locale_var.get(),
            theme=self.theme_var.get(),
            last_project_root=self.project_var.get(),
            onboarding_completed=True,
            update_catalog_url=self.update_catalog_var.get().strip(),
            automatic_updates=self.automatic_updates_var.get(),
            window_geometry=self._current_window_geometry(),
        )
        save_gui_settings(self.settings)
        messagebox.showinfo(self.tr("setup_finish"), self.tr("setup_complete"))
        self._show_page("chat")

    def refresh_managed_agents(self, refresh: bool = False) -> None:
        self._background(lambda: registry_agent_statuses(refresh=refresh), self._managed_agents_loaded)

    def _managed_agents_loaded(self, records: Any) -> None:
        previous_id = self._selected_registry_agent_id()
        labels: list[str] = []
        self.registry_agent_ids = {}
        self.registry_agent_records = {}
        for agent, status in records:
            marker = "✓" if status.installed else ("+" if status.installable else "!")
            label = f"{marker} {agent.name} · {agent.version} [{agent.id}]"
            labels.append(label)
            self.registry_agent_ids[label] = agent.id
            self.registry_agent_records[agent.id] = (agent, status)
        if self.registry_agent_box is not None:
            self.registry_agent_box.configure(values=tuple(labels))
        selected = next((label for label in labels if self.registry_agent_ids[label] == previous_id), labels[0] if labels else "")
        self.registry_agent_var.set(selected)
        self._registry_agent_changed()
        self._refresh_onboarding_progress()
        self.status_var.set(self.tr("ready"))

    def _selected_registry_agent_id(self) -> str:
        return self.registry_agent_ids.get(self.registry_agent_var.get(), "")

    def _registry_agent_changed(self) -> None:
        record = self.registry_agent_records.get(self._selected_registry_agent_id())
        self.auth_method_var.set("")
        self.auth_methods = {}
        if self.auth_method_box is not None:
            self.auth_method_box.configure(values=())
        self._render_auth_variables()
        if record is None:
            self.registry_agent_description_var.set("—")
            self.registry_agent_status_var.set("—")
            return
        agent, status = record
        self.registry_agent_description_var.set(agent.description or "—")
        key = "agent_installed" if status.installed else ("agent_installable" if status.installable else "agent_blocked_install")
        self.registry_agent_status_var.set(self.tr(key, detail=status.detail))

    def install_selected_agent(self) -> None:
        agent_id = self._selected_registry_agent_id()
        if not agent_id:
            messagebox.showwarning(self.tr("managed_agents"), self.tr("select_registry_agent"))
            return
        self._background(lambda: install_registry_agent(agent_id), lambda detail: self._agent_action_finished(detail))

    def uninstall_selected_agent(self) -> None:
        agent_id = self._selected_registry_agent_id()
        if not agent_id:
            messagebox.showwarning(self.tr("managed_agents"), self.tr("select_registry_agent"))
            return
        self._background(lambda: uninstall_registry_agent(agent_id), lambda _removed: self._agent_action_finished(self.tr("agent_removed")))

    def _agent_action_finished(self, detail: str) -> None:
        self.status_var.set(detail)
        self.refresh_managed_agents(False)
        if self.snapshot is not None:
            self.load_project()

    def inspect_selected_agent_auth(self) -> None:
        agent_id = self._selected_registry_agent_id()
        if not agent_id:
            messagebox.showwarning(self.tr("managed_agents"), self.tr("select_registry_agent"))
            return
        self._background(lambda: inspect_registry_agent_auth(agent_id), self._auth_inspected)

    def _auth_inspected(self, inspection: Any) -> None:
        labels = [f"{item.name} [{item.method_id}]" for item in inspection.methods]
        self.auth_methods = {label: item for label, item in zip(labels, inspection.methods)}
        if self.auth_method_box is not None:
            self.auth_method_box.configure(values=tuple(labels))
        self.auth_method_var.set(labels[0] if labels else "")
        self._render_auth_variables()
        self.registry_agent_status_var.set(
            f"{inspection.agent_name or 'ACP agent'} {inspection.agent_version or ''} · "
            + (f"{len(labels)} auth method(s)" if labels else self.tr("auth_none"))
        )
        self.status_var.set(self.tr("ready"))

    def _render_auth_variables(self) -> None:
        frame = self.auth_variables_frame
        if frame is None:
            return
        for child in frame.winfo_children():
            child.destroy()
        self.auth_variable_vars = {}
        method = self.auth_methods.get(self.auth_method_var.get())
        if method is None or not method.variables:
            return
        ttk.Label(frame, text=self.tr("auth_variables"), style="FieldLabel.TLabel").pack(anchor="w", pady=(2, 4))
        for name, label, secret, optional in method.variables:
            row = ttk.Frame(frame, style="Card.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{label}{' (optional)' if optional else ''}", style="FieldLabel.TLabel", width=20).pack(side="left")
            value = tk.StringVar()
            self.auth_variable_vars[name] = value
            ttk.Entry(row, textvariable=value, show="•" if secret else "", style="Modern.TEntry").pack(side="left", fill="x", expand=True)

    def login_selected_agent(self) -> None:
        agent_id = self._selected_registry_agent_id()
        if not agent_id:
            messagebox.showwarning(self.tr("managed_agents"), self.tr("select_registry_agent"))
            return
        method = self.auth_methods.get(self.auth_method_var.get())
        if method is not None and self.auth_variable_vars:
            try:
                set_session_auth_variables(method, {name: value.get() for name, value in self.auth_variable_vars.items()})
            except ValueError as exc:
                self._show_error(exc)
                return
        method_id = method.method_id if method is not None else None
        self._background(lambda: login_registry_agent(agent_id, method_id), self._agent_login_finished)

    def _agent_login_finished(self, result: Any) -> None:
        self.registry_agent_status_var.set(result.detail)
        self.status_var.set(self.tr("ready") if result.succeeded else self.tr("error"))
        (messagebox.showinfo if result.succeeded else messagebox.showwarning)(self.tr("login_agent"), result.detail)

    def _populate_a2a_profiles(self) -> None:
        if self.snapshot is None:
            return
        profiles = editable_a2a_agents(self.snapshot.config)
        names = tuple(item.name for item in profiles)
        if self.a2a_profile_box is not None:
            self.a2a_profile_box.configure(values=names)
        if self.a2a_profile_var.get() in names:
            self.load_selected_a2a_profile()

    def _a2a_values(self) -> EditableA2AAgent:
        auth_kind = self.a2a_vars["auth_kind"].get()
        return EditableA2AAgent(
            name=self.a2a_vars["name"].get(),
            card_url=self.a2a_vars["card_url"].get(),
            roles=tuple(role for role, selected in self.a2a_role_vars.items() if selected.get()),
            auth_kind=auth_kind,
            credential_env=self.a2a_vars["credential_env"].get(),
            api_key_header=self.a2a_vars["api_key_header"].get() if auth_kind == "api_key" else "",
            oauth_flow=self.a2a_vars["oauth_flow"].get(),
            client_id_env=self.a2a_vars["client_id_env"].get(),
            client_secret_env=self.a2a_vars["client_secret_env"].get(),
            token_url=self.a2a_vars["token_url"].get(),
            device_authorization_url=self.a2a_vars["device_authorization_url"].get(),
            oidc_discovery_url=self.a2a_vars["oidc_discovery_url"].get(),
            scopes=tuple(self.a2a_vars["scopes"].get().split()),
            client_auth_method=self.a2a_vars["client_auth_method"].get(),
            prefer_streaming=bool(self.a2a_vars["prefer_streaming"].get()),
            push_callback_url=self.a2a_vars["push_callback_url"].get(),
            push_token_env=self.a2a_vars["push_token_env"].get(),
            timeout_seconds=float(self.a2a_vars["timeout_seconds"].get()),
            poll_interval_seconds=float(self.a2a_vars["poll_interval_seconds"].get()),
        )

    def clear_a2a_form(self) -> None:
        self.a2a_profile_var.set("")
        for key, default in (
            ("name", ""), ("card_url", ""), ("auth_kind", "none"),
            ("credential_env", ""), ("api_key_header", "X-API-Key"),
            ("oauth_flow", "client_credentials"), ("client_id_env", ""),
            ("client_secret_env", ""), ("token_url", ""),
            ("device_authorization_url", ""), ("oidc_discovery_url", ""),
            ("scopes", ""), ("client_auth_method", "basic"),
            ("prefer_streaming", True), ("push_callback_url", ""),
            ("push_token_env", ""), ("timeout_seconds", "300"),
            ("poll_interval_seconds", "1"),
        ):
            self.a2a_vars[key].set(default)
        for variable in self.a2a_role_vars.values():
            variable.set(True)

    def load_selected_a2a_profile(self) -> None:
        if self.snapshot is None:
            return
        selected = self.a2a_profile_var.get().casefold()
        profile = next((item for item in editable_a2a_agents(self.snapshot.config) if item.name.casefold() == selected), None)
        if profile is None:
            return
        for key in (
            "name", "card_url", "auth_kind", "credential_env", "api_key_header", "oauth_flow",
            "client_id_env", "client_secret_env", "token_url", "device_authorization_url",
            "oidc_discovery_url", "client_auth_method", "prefer_streaming", "push_callback_url",
            "push_token_env", "timeout_seconds", "poll_interval_seconds",
        ):
            self.a2a_vars[key].set(getattr(profile, key))
        self.a2a_vars["scopes"].set(" ".join(profile.scopes))
        for role, variable in self.a2a_role_vars.items():
            variable.set(role in profile.roles)

    def save_a2a_form(self) -> None:
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        try:
            values = self._a2a_values()
            save_a2a_agent(self.snapshot.root, values)
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self.a2a_profile_var.set(values.name.strip())
        self.status_var.set(self.tr("saved"))
        self.load_project()

    def test_a2a_form(self) -> None:
        try:
            values = self._a2a_values()
        except ValueError as exc:
            self._show_error(exc)
            return
        self._background(lambda: test_a2a_agent(values), self._a2a_test_finished)

    def _a2a_test_finished(self, result: Any) -> None:
        available, detail, _name = result
        self.status_var.set(self.tr("ready") if available else self.tr("error"))
        (messagebox.showinfo if available else messagebox.showwarning)(self.tr("test_a2a"), detail)

    def login_a2a_form(self) -> None:
        try:
            values = self._a2a_values()
        except ValueError as exc:
            self._show_error(exc)
            return

        prompts: Queue[Any] = Queue()
        finished = Event()

        def show_device(authorization: Any) -> None:
            prompts.put(authorization)

        def poll_device_prompt() -> None:
            try:
                authorization = prompts.get_nowait()
            except Empty:
                if not finished.is_set():
                    self.root.after(100, poll_device_prompt)
                return
            url = authorization.verification_uri_complete or authorization.verification_uri
            messagebox.showinfo(
                self.tr("a2a_login"),
                self.tr("a2a_device_prompt", url=url, code=authorization.user_code),
            )
            webbrowser.open(url)
            if not finished.is_set():
                self.root.after(100, poll_device_prompt)

        def authorize() -> str:
            try:
                return authorize_a2a_agent(values, show_device)
            finally:
                finished.set()

        self.status_var.set(self.tr("running"))
        self.root.after(100, poll_device_prompt)
        self._background(
            authorize,
            lambda detail: self._a2a_login_finished(detail),
        )

    def _a2a_login_finished(self, detail: str) -> None:
        self.status_var.set(self.tr("ready"))
        messagebox.showinfo(self.tr("a2a_login"), detail)

    def delete_a2a_profile(self) -> None:
        if self.snapshot is None:
            return
        name = self.a2a_vars["name"].get().strip()
        if not name or not messagebox.askyesno(self.tr("remove_a2a"), self.tr("confirm_remove_a2a", name=name)):
            return
        try:
            remove_a2a_agent(self.snapshot.root, name)
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self.clear_a2a_form()
        self.status_var.set(self.tr("saved"))
        self.load_project()

    def load_mcp_role(self) -> None:
        if self.snapshot is None:
            return
        policy = editable_role_mcp_policy(self.snapshot.config, self.mcp_role_var.get())
        self.mcp_mode_var.set(policy.permission_mode)
        allowed = set(policy.allowed_tool_kinds)
        for kind, variable in self.mcp_kind_vars.items():
            variable.set(kind in allowed)
        names = tuple(item.name for item in policy.servers)
        if self.mcp_server_box is not None:
            self.mcp_server_box.configure(values=names)
        if self.mcp_server_var.get() in names:
            self.load_selected_mcp_server()
        else:
            self.clear_mcp_server_form()

    def save_mcp_policy_form(self) -> None:
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        mode = self.mcp_mode_var.get()
        kinds = tuple(
            kind for kind, selected in self.mcp_kind_vars.items() if selected.get()
        ) if mode == "allow_once" else ()
        try:
            save_role_mcp_policy(
                self.snapshot.root,
                self.mcp_role_var.get(),
                permission_mode=mode,
                allowed_tool_kinds=kinds,
            )
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self.status_var.set(self.tr("mcp_saved"))
        self.load_project()

    def clear_mcp_server_form(self) -> None:
        self.mcp_server_var.set("")
        for key, default in (("name", ""), ("transport", "stdio"), ("command", ""), ("url", "")):
            self.mcp_server_vars[key].set(default)
        for editor in self.mcp_text_editors.values():
            editor.delete("1.0", "end")

    def load_selected_mcp_server(self) -> None:
        if self.snapshot is None:
            return
        policy = editable_role_mcp_policy(self.snapshot.config, self.mcp_role_var.get())
        selected = self.mcp_server_var.get().casefold()
        server = next((item for item in policy.servers if item.name.casefold() == selected), None)
        if server is None:
            return
        self.mcp_server_vars["name"].set(server.name)
        self.mcp_server_vars["transport"].set(server.transport)
        self.mcp_server_vars["command"].set(server.command)
        self.mcp_server_vars["url"].set(server.url)
        values = {
            "args": server.args,
            "environment": tuple(f"{name}={source}" for name, source in server.environment),
            "headers": tuple(f"{name}={source}" for name, source in server.headers),
        }
        for key, lines in values.items():
            editor = self.mcp_text_editors[key]
            editor.delete("1.0", "end")
            editor.insert("1.0", "\n".join(lines))

    def _mcp_bindings(self, key: str) -> tuple[tuple[str, str], ...]:
        bindings: list[tuple[str, str]] = []
        for line in self._argument_lines(self.mcp_text_editors[key]):
            name, separator, source = line.partition("=")
            if not separator or not name.strip() or not source.strip():
                raise ValueError(f"Invalid MCP binding '{line}'. Expected NAME=SOURCE_ENV.")
            bindings.append((name.strip(), source.strip()))
        return tuple(bindings)

    def _mcp_server_values(self) -> EditableMcpServer:
        transport = self.mcp_server_vars["transport"].get()
        return EditableMcpServer(
            name=self.mcp_server_vars["name"].get(),
            transport=transport,
            command=self.mcp_server_vars["command"].get() if transport == "stdio" else "",
            args=self._argument_lines(self.mcp_text_editors["args"]) if transport == "stdio" else (),
            url=self.mcp_server_vars["url"].get() if transport in {"http", "sse"} else "",
            environment=self._mcp_bindings("environment") if transport == "stdio" else (),
            headers=self._mcp_bindings("headers") if transport in {"http", "sse"} else (),
        )

    def save_mcp_server_form(self) -> None:
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        try:
            values = self._mcp_server_values()
            save_mcp_server(self.snapshot.root, self.mcp_role_var.get(), values)
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self.mcp_server_var.set(values.name.strip())
        self.status_var.set(self.tr("mcp_saved"))
        self.load_project()

    def delete_mcp_server(self) -> None:
        if self.snapshot is None:
            return
        name = self.mcp_server_vars["name"].get().strip()
        role = self.mcp_role_var.get()
        if not name or not messagebox.askyesno(
            self.tr("mcp_remove_server"), self.tr("mcp_confirm_remove", name=name, role=role)
        ):
            return
        try:
            remove_mcp_server(self.snapshot.root, role, name)
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self.clear_mcp_server_form()
        self.status_var.set(self.tr("mcp_saved"))
        self.load_project()

    def _agent_changed(self, role: str) -> None:
        if self.snapshot is None:
            return
        name = self.role_vars[role]["agent"].get()
        agent = next((item for item in self.snapshot.catalog["agents"] if item["name"] == name), None)
        if agent is not None:
            driver = agent.get(f"{role}_driver") or ""
            self.role_vars[role]["driver"].set(driver)
            self.role_driver_display_vars[role].set(driver_display_name(self.locale_var.get(), driver))
            available = bool(agent.get("available"))
            key = "agent_ready" if available else "agent_missing"
            self.role_status_vars[role].set(self.tr(key, detail=agent.get("detail") or "—"))
            self.role_status_labels[role].configure(style="RoleReady.TLabel" if available else "RoleBlocked.TLabel")
        else:
            self.role_driver_display_vars[role].set(
                driver_display_name(self.locale_var.get(), self.role_vars[role]["driver"].get())
            )
            self.role_status_vars[role].set(self.tr("agent_unknown"))
            self.role_status_labels[role].configure(style="RoleBlocked.TLabel")
        self._update_role_save_state()

    def _update_role_save_state(self) -> None:
        if self.save_roles_button is None or self.snapshot is None:
            return
        required = ("supervisor", "worker") + (("critic",) if self.critic_enabled_var.get() else ())
        agents = self.snapshot.catalog["agents"]
        ready = True
        for role in required:
            name = self.role_vars[role]["agent"].get()
            item = next((entry for entry in agents if entry["name"] == name), None)
            ready = ready and bool(item and item.get("available") and item.get(f"{role}_driver"))
        self.save_roles_button.configure(state="normal" if ready else "disabled")

    def save_roles(self) -> None:
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        try:
            save_project_roles(
                self.snapshot.root,
                supervisor_agent=self.role_vars["supervisor"]["agent"].get(),
                supervisor_model=self.role_vars["supervisor"]["model"].get(),
                supervisor_driver=self.role_vars["supervisor"]["driver"].get(),
                worker_agent=self.role_vars["worker"]["agent"].get(),
                worker_model=self.role_vars["worker"]["model"].get(),
                worker_driver=self.role_vars["worker"]["driver"].get(),
                critic_enabled=self.critic_enabled_var.get(),
                critic_agent=self.role_vars["critic"]["agent"].get(),
                critic_model=self.role_vars["critic"]["model"].get(),
                critic_driver=self.role_vars["critic"]["driver"].get(),
                max_attempts=int(self.attempts_var.get()),
            )
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self.status_var.set(self.tr("saved"))
        self.load_project()

    def reload_config(self) -> None:
        if self.snapshot is None or self.config_editor is None:
            return
        try:
            text = project_config_text(self.snapshot.root)
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self.config_editor.delete("1.0", "end")
        self.config_editor.insert("1.0", text)
        self._style_text_widgets()

    @staticmethod
    def _argument_lines(widget: tk.Text) -> tuple[str, ...]:
        return tuple(line.strip() for line in widget.get("1.0", "end").splitlines() if line.strip())

    def clear_process_profile_form(self) -> None:
        for variable in self.process_vars.values():
            variable.set("")
        self.process_vars["prompt_transport"].set("stdin")
        for editor in self.process_arg_editors.values():
            editor.delete("1.0", "end")
        version_editor = self.process_arg_editors.get("version_args")
        if version_editor is not None:
            version_editor.insert("1.0", "--version")

    def _populate_model_prices(self) -> None:
        if self.snapshot is None or self.price_profile_box is None:
            return
        prices = editable_model_prices(self.snapshot.config)
        labels = tuple(f"{item.provider} / {item.model}" for item in prices)
        self.price_profile_keys = {
            label: (item.provider, item.model) for label, item in zip(labels, prices)
        }
        self.price_profile_box.configure(values=labels)
        selected = self.price_profile_var.get()
        if selected in self.price_profile_keys:
            self.load_selected_model_price()
        elif labels:
            self.price_profile_var.set(labels[0])
            self.load_selected_model_price()
        else:
            self.clear_model_price_form()

    def clear_model_price_form(self) -> None:
        self.price_profile_var.set("")
        for variable in self.price_vars.values():
            variable.set("")
        self.price_vars["currency"].set("USD")

    def load_selected_model_price(self) -> None:
        if self.snapshot is None:
            return
        key = self.price_profile_keys.get(self.price_profile_var.get())
        if key is None:
            return
        price = next(
            (
                item
                for item in editable_model_prices(self.snapshot.config)
                if (item.provider, item.model) == key
            ),
            None,
        )
        if price is None:
            return
        self.price_vars["provider"].set(price.provider)
        self.price_vars["model"].set(price.model)
        self.price_vars["input_per_million"].set(str(price.input_per_million))
        self.price_vars["output_per_million"].set(str(price.output_per_million))
        self.price_vars["currency"].set(price.currency)

    def save_model_price_form(self) -> None:
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        try:
            values = EditableModelPrice(
                provider=self.price_vars["provider"].get(),
                model=self.price_vars["model"].get(),
                input_per_million=float(self.price_vars["input_per_million"].get()),
                output_per_million=float(self.price_vars["output_per_million"].get()),
                currency=self.price_vars["currency"].get(),
            )
            save_model_price(self.snapshot.root, values)
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self.status_var.set(self.tr("pricing_saved"))
        self.load_project()

    def delete_model_price(self) -> None:
        if self.snapshot is None:
            return
        provider = self.price_vars["provider"].get().strip()
        model = self.price_vars["model"].get().strip()
        if not provider or not model:
            return
        if not messagebox.askyesno(
            self.tr("pricing_remove"),
            self.tr("pricing_confirm_remove", provider=provider, model=model),
        ):
            return
        try:
            remove_model_price(self.snapshot.root, provider, model)
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self.clear_model_price_form()
        self.status_var.set(self.tr("pricing_removed"))
        self.load_project()

    def load_selected_process_profile(self) -> None:
        if self.snapshot is None:
            return
        selected = self.process_vars["name"].get().strip().casefold()
        profile = next(
            (item for item in editable_process_profiles(self.snapshot.config) if item.name.casefold() == selected),
            None,
        )
        if profile is None:
            return
        self.process_vars["name"].set(profile.name)
        self.process_vars["command"].set(profile.command)
        self.process_vars["prompt_transport"].set(profile.prompt_transport)
        self.process_vars["prompt_argument"].set(profile.prompt_argument)
        self.process_vars["model_argument"].set(profile.model_argument)
        for key in ("args", "required_args", "forbidden_args", "version_args"):
            editor = self.process_arg_editors.get(key)
            if editor is None:
                continue
            editor.delete("1.0", "end")
            editor.insert("1.0", "\n".join(getattr(profile, key)))

    def save_process_profile_form(self) -> None:
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        try:
            values = EditableProcessProfile(
                name=self.process_vars["name"].get(),
                command=self.process_vars["command"].get(),
                args=self._argument_lines(self.process_arg_editors["args"]),
                prompt_transport=self.process_vars["prompt_transport"].get(),
                prompt_argument=self.process_vars["prompt_argument"].get(),
                required_args=self._argument_lines(self.process_arg_editors["required_args"]),
                forbidden_args=self._argument_lines(self.process_arg_editors["forbidden_args"]),
                model_argument=self.process_vars["model_argument"].get(),
                version_args=self._argument_lines(self.process_arg_editors["version_args"]),
            )
            save_process_profile(self.snapshot.root, values)
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self.status_var.set(self.tr("saved"))
        self.load_project()

    def delete_process_profile(self) -> None:
        if self.snapshot is None:
            return
        name = self.process_vars["name"].get().strip()
        if not name:
            return
        if not messagebox.askyesno(
            self.tr("remove_process_profile"),
            self.tr("confirm_remove_profile", name=name),
        ):
            return
        try:
            remove_process_profile(self.snapshot.root, name)
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self.clear_process_profile_form()
        self.status_var.set(self.tr("saved"))
        self.load_project()

    def _populate_session_secrets(self) -> None:
        if self.snapshot is None or self.session_secrets_frame is None:
            return
        for child in self.session_secrets_frame.winfo_children():
            child.destroy()
        self.session_secret_vars = {}
        records = environment_variable_status(self.snapshot.config)
        if not records:
            ttk.Label(self.session_secrets_frame, text=self.tr("no_session_secrets"), style="CardMuted.TLabel").pack(anchor="w")
            return
        stored = set(stored_credential_names())
        for name, available, label in records:
            row = ttk.Frame(self.session_secrets_frame, style="Card.TFrame")
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=f"{label}: {name}", style="FieldLabel.TLabel", width=32).pack(side="left")
            variable = tk.StringVar()
            self.session_secret_vars[name] = variable
            ttk.Entry(row, textvariable=variable, show="•", style="Modern.TEntry").pack(side="left", fill="x", expand=True)
            if available:
                source = self.tr("credential_source_stored") if name in stored else self.tr("credential_source_env")
                if os.environ.get(name):
                    source = self.tr("credential_source_env")
                ttk.Label(row, text=f"✓ {source}", style="RoleReady.TLabel").pack(side="left", padx=(8, 0))
            else:
                ttk.Label(row, text="!", style="RoleBlocked.TLabel").pack(side="left", padx=(8, 0))
        actions = ttk.Frame(self.session_secrets_frame, style="Card.TFrame")
        actions.pack(fill="x", pady=(7, 0))
        self._button(
            actions,
            text=self.tr("save_credentials"),
            variant="primary",
            command=self.save_credentials,
        ).pack(side="right")
        self._button(
            actions,
            text=self.tr("set_session_secrets"),
            variant="secondary",
            command=self.apply_session_secrets,
        ).pack(side="right", padx=(0, 8))
        if stored:
            self._button(
                actions,
                text=self.tr("forget_credentials"),
                variant="secondary",
                command=self.forget_credentials,
            ).pack(side="left")

    def save_credentials(self) -> None:
        """Keep the pasted keys for next time, in the user profile rather than the project."""
        values = {name: variable.get().strip() for name, variable in self.session_secret_vars.items()}
        values = {name: value for name, value in values.items() if value}
        if not values:
            self.check_environment()
            return
        try:
            for name, value in values.items():
                save_credential(name, value)
        except (SecretStoreError, OSError, ValueError) as exc:
            self._show_error(exc)
            return
        for variable in self.session_secret_vars.values():
            variable.set("")
        self.status_var.set(self.tr("credentials_saved", count=len(values)))
        self.load_project()

    def forget_credentials(self) -> None:
        removed = 0
        try:
            for name in stored_credential_names():
                removed += 1 if forget_credential(name) else 0
        except (SecretStoreError, OSError) as exc:
            self._show_error(exc)
            return
        self.status_var.set(self.tr("credentials_forgotten", count=removed))
        self.load_project()

    def apply_session_secrets(self) -> None:
        if self.snapshot is None:
            return
        values = {name: variable.get() for name, variable in self.session_secret_vars.items() if variable.get()}
        if not values:
            self.check_environment()
            return
        try:
            loaded = set_session_environment_variables(self.snapshot.config, values)
        except ValueError as exc:
            self._show_error(exc)
            return
        for variable in self.session_secret_vars.values():
            variable.set("")
        self.status_var.set(self.tr("session_secrets_set", count=len(loaded)))
        self.load_project()

    def save_guided_config(self) -> None:
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        try:
            values = EditableProjectSettings(
                supervisor_provider=self.settings_vars["supervisor_provider"].get(),
                supervisor_model=self.settings_vars["supervisor_model"].get(),
                supervisor_base_url=self.settings_vars["supervisor_base_url"].get(),
                supervisor_api_key_env=self.settings_vars["supervisor_api_key_env"].get(),
                verifier_provider=self.settings_vars["verifier_provider"].get(),
                verifier_model=self.settings_vars["verifier_model"].get(),
                verifier_base_url=self.settings_vars["verifier_base_url"].get(),
                verifier_api_key_env=self.settings_vars["verifier_api_key_env"].get(),
                require_embedded_python=self.settings_vars["require_embedded_python"].get(),
                embedded_python_path=self.settings_vars["embedded_python_path"].get(),
                wheels_path=self.settings_vars["wheels_path"].get(),
                require_tests=self.settings_vars["require_tests"].get(),
                worker_trust_level=self.settings_vars["worker_trust_level"].get(),
                max_parallel_tasks=int(self.settings_vars["max_parallel_tasks"].get()),
            )
            save_editable_project_settings(self.snapshot.root, values)
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self.status_var.set(self.tr("saved"))
        self.load_project()

    def save_raw_config(self) -> None:
        if self.snapshot is None or self.config_editor is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        try:
            save_project_config_text(self.snapshot.root, self.config_editor.get("1.0", "end"))
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self.status_var.set(self.tr("saved"))
        self.load_project()

    def toggle_advanced_config(self) -> None:
        if self.advanced_config_shell is None:
            return
        if self.advanced_config_shell.winfo_ismapped():
            self.advanced_config_shell.pack_forget()
        else:
            self.reload_config()
            self.advanced_config_shell.pack(fill="both", expand=True, pady=(14, 0))

    def check_environment(self) -> None:
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        records = environment_variable_status(self.snapshot.config)
        missing = [name for name, available, _label in records if not available]
        message = self.tr("env_missing", names=", ".join(missing)) if missing else self.tr("env_all_set")
        (messagebox.showwarning if missing else messagebox.showinfo)(self.tr("check_env"), message)

    def reload_chat(self) -> None:
        if self.snapshot is None or self.chat_editor is None:
            return
        try:
            history = load_supervisor_chat(self.snapshot.root)
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        palette = PALETTES[self.theme_var.get()]
        editor = self.chat_editor
        editor.configure(state="normal")
        editor.delete("1.0", "end")
        editor.tag_configure("user_name", foreground=palette["accent"], font=self._font_bold(9))
        editor.tag_configure("supervisor_name", foreground=palette["success"], font=self._font_bold(9))
        editor.tag_configure("message", foreground=palette["foreground"], lmargin1=8, lmargin2=8, rmargin=8)
        editor.tag_configure("ready", foreground=palette["success"], font=self._font_bold(9))
        if not history:
            editor.insert("end", self.tr("chat_empty"), "message")
            self.chat_ready_var.set(False)
        else:
            for item in history:
                speaker = self.tr("you") if item.role == "user" else self.tr("supervisor_speaker")
                name_tag = "user_name" if item.role == "user" else "supervisor_name"
                editor.insert("end", speaker + "\n", name_tag)
                editor.insert("end", item.text + "\n\n", "message")
                if item.role == "supervisor" and item.ready_to_plan:
                    editor.insert("end", "✓ " + self.tr("ready_badge") + "\n\n", "ready")
            self.chat_ready_var.set(history[-1].role == "supervisor" and history[-1].ready_to_plan)
        editor.configure(state="disabled")
        editor.see("end")
        self._style_text_widgets()

    def send_chat_message(self) -> None:
        if self.snapshot is None or self.chat_input is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        if self.snapshot.profile is None:
            messagebox.showwarning(self.tr("chat"), self.tr("save_roles_first"))
            self._show_page("roles")
            return
        if not self._active_roles_ready():
            messagebox.showwarning(self.tr("chat"), self.tr("roles_not_ready"))
            self._show_page("roles")
            return
        text = self.chat_input.get("1.0", "end").strip()
        if not text:
            return
        self._background(
            lambda: send_supervisor_message(self.snapshot.root, text),
            self._chat_message_sent,
        )

    def _chat_message_sent(self, _turn: Any) -> None:
        if self.chat_input is not None:
            self.chat_input.delete("1.0", "end")
        self.reload_chat()
        self.status_var.set(self.tr("ready"))

    def create_chat_plan(self) -> None:
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        if self.snapshot.profile is None:
            messagebox.showwarning(self.tr("chat"), self.tr("save_roles_first"))
            self._show_page("roles")
            return
        if not self._active_roles_ready():
            messagebox.showwarning(self.tr("chat"), self.tr("roles_not_ready"))
            self._show_page("roles")
            return
        if not self.chat_ready_var.get():
            if not messagebox.askyesno(self.tr("create_plan"), self.tr("chat_not_ready")):
                return
        if not messagebox.askyesno(self.tr("create_plan"), self.tr("confirm_plan")):
            return
        self._background(
            lambda: materialize_supervisor_chat_plan(self.snapshot.root),
            self._chat_plan_created,
        )

    def _chat_plan_created(self, result: Any) -> None:
        self.status_var.set(self.tr("plan_created", count=len(result.enqueued_task_ids)))
        self._show_page("operations")
        self.load_project()

    def clear_chat(self) -> None:
        if self.snapshot is None:
            return
        if not messagebox.askyesno(self.tr("new_chat"), self.tr("confirm_clear_chat")):
            return
        try:
            clear_supervisor_chat(self.snapshot.root)
        except OSError as exc:
            self._show_error(exc)
            return
        self.reload_chat()

    def _active_roles_ready(self) -> bool:
        if self.snapshot is None or self.snapshot.profile is None:
            return False
        profile = self.snapshot.profile
        selections = [
            ("supervisor", profile.supervisor),
            ("worker", profile.worker),
        ]
        if profile.critic is not None:
            selections.append(("critic", profile.critic))
        agents = self.snapshot.catalog["agents"]
        for role, selection in selections:
            agent = next(
                (item for item in agents if item["name"].casefold() == selection.agent.casefold()),
                None,
            )
            declared_driver = agent.get(f"{role}_driver") if agent else None
            if not agent or not agent.get("available") or not declared_driver:
                return False
            if selection.driver and selection.driver != declared_driver:
                return False
        return True

    def run_operation(self, operation: str) -> None:
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        if operation in {"run_next", "run_all"} and self.snapshot.profile is None:
            messagebox.showwarning(self.tr("operations"), self.tr("save_roles_first"))
            self._show_page("roles")
            return
        if operation in {"run_next", "run_all"} and not self._active_roles_ready():
            messagebox.showwarning(self.tr("operations"), self.tr("roles_not_ready"))
            self._show_page("roles")
            return
        if operation == "run_next" and not messagebox.askyesno(self.tr("operations"), self.tr("confirm_run")):
            return
        if operation == "run_all" and not messagebox.askyesno(self.tr("operations"), self.tr("confirm_run_all")):
            return
        task_id = self.operator_task_ids.get(self.operator_task_var.get(), "")
        if operation == "retry" and not messagebox.askyesno(self.tr("operations"), self.tr("confirm_retry")):
            return
        if operation == "recover" and not messagebox.askyesno(self.tr("operations"), self.tr("confirm_recover")):
            return
        if operation in {"requeue_escalated", "fail_escalated"} and not messagebox.askyesno(
            self.tr("operations"), self.tr(f"confirm_{operation}")
        ):
            return
        if operation == "audit" and not messagebox.askyesno(self.tr("operations"), self.tr("confirm_audit")):
            return
        self._background(
            lambda: run_gui_command(
                self.snapshot.root,
                operation,
                task_id=task_id,
                reason=self.recovery_reason_var.get(),
            ),
            self._command_finished,
        )

    def run_role_check(self, role: str) -> None:
        if self.snapshot is None:
            messagebox.showerror(self.tr("error"), self.tr("no_project"))
            return
        if self.snapshot.profile is None:
            messagebox.showwarning(self.tr("roles"), self.tr("save_roles_first"))
            return
        if role == "critic" and not self.snapshot.profile.critic_enabled:
            messagebox.showwarning(self.tr("critic"), self.tr("critic_not_enabled"))
            return
        if not self._active_roles_ready():
            messagebox.showwarning(self.tr("roles"), self.tr("roles_not_ready"))
            return
        if not messagebox.askyesno(
            self.tr("check_role"),
            self.tr("confirm_check_role", role=self.tr(role)),
        ):
            return
        self._show_page("operations")
        self._background(
            lambda: run_gui_command(self.snapshot.root, f"check_role_{role}"),
            self._command_finished,
        )

    def _command_finished(self, result: Any) -> None:
        output = result.stdout
        if result.stderr:
            output += ("\n" if output else "") + result.stderr
        rendered = output or f"return_code={result.return_code}"
        self._set_output(rendered)
        self._pending_operation_output = rendered
        self.status_var.set(self.tr("ready") if result.ok else f"{self.tr('error')}: {result.return_code}")
        if self.snapshot is not None:
            self.load_project()

    def _background(self, work: Callable[[], Any], success: Callable[[Any], None]) -> None:
        self.status_var.set(self.tr("running"))
        future = self.executor.submit(work)

        def poll() -> None:
            if not future.done():
                self.root.after(50, poll)
                return
            try:
                result = future.result()
            except Exception as exc:  # Tk callbacks must convert service failures to user-visible errors.
                self.status_var.set(self.tr("error"))
                self._show_error(exc)
            else:
                success(result)

        self.root.after(50, poll)

    def _set_output(self, text: str) -> None:
        if self.output_editor is None:
            return
        self.output_editor.configure(state="normal")
        self.output_editor.delete("1.0", "end")
        self.output_editor.insert("1.0", text)
        self.output_editor.configure(state="disabled")
        self._style_text_widgets()

    def _style_text_widgets(self) -> None:
        palette = PALETTES[self.theme_var.get()]
        widgets = (
            self.config_editor,
            self.output_editor,
            self.chat_editor,
            self.chat_input,
            *self.process_arg_editors.values(),
            *self.mcp_text_editors.values(),
        )
        for widget in widgets:
            if widget is not None:
                widget.configure(
                    background=palette["surface"],
                    foreground=palette["foreground"],
                    insertbackground=palette["foreground"],
                    selectbackground=palette["selection"],
                    highlightbackground=palette["border"],
                )

    def _presentation_changed(self, _event: Any = None) -> None:
        self.settings = GuiSettings(
            locale=self.locale_var.get(),
            theme=self.theme_var.get(),
            last_project_root=self.project_var.get(),
            onboarding_completed=self.settings.onboarding_completed,
            update_catalog_url=self.update_catalog_var.get().strip(),
            automatic_updates=self.automatic_updates_var.get(),
            window_geometry=self.settings.window_geometry,
        )
        self._persist_settings()
        self._build()

    def _persist_settings(self) -> None:
        self.settings = GuiSettings(
            locale=self.locale_var.get(),
            theme=self.theme_var.get(),
            last_project_root=self.project_var.get(),
            onboarding_completed=self.settings.onboarding_completed,
            update_catalog_url=self.update_catalog_var.get().strip(),
            automatic_updates=self.automatic_updates_var.get(),
        )
        save_gui_settings(self.settings)

    def close(self) -> None:
        self._persist_settings()
        self.root.unbind_all("<MouseWheel>")
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def validate_translations() -> None:
    expected = set(TEXT["en"])
    for locale, translations in TEXT.items():
        missing = expected - set(translations)
        extra = set(translations) - expected
        if missing or extra:
            raise ValueError(f"Translation catalog mismatch for {locale}: missing={sorted(missing)} extra={sorted(extra)}")


def _mix_color(start: str, end: str, ratio: float) -> str:
    start_rgb = tuple(int(start[index:index + 2], 16) for index in (1, 3, 5))
    end_rgb = tuple(int(end[index:index + 2], 16) for index in (1, 3, 5))
    mixed = tuple(round(left + (right - left) * ratio) for left, right in zip(start_rgb, end_rgb))
    return "#" + "".join(f"{value:02x}" for value in mixed)


def validate_responsive_layout(app: HohDesktopApp) -> None:
    # Pin a known desktop size: the real window is sized from the screen, and the
    # assertions below are about layout, not about the machine running the smoke.
    # Two different claims, checked separately.
    #
    # First: the app lays out at whatever size this display actually gives it. That
    # has to hold everywhere, including a small screen.
    opened = preferred_window_geometry(app.root.winfo_screenwidth(), app.root.winfo_screenheight())
    app.root.geometry(format_window_geometry(*opened))
    app.root.update()
    for page in app.pages:
        app._show_page(page)
        app.root.update()
        app._sync_content_geometry()

    # Second: on a normal desktop viewport the chat composer fits without scrolling.
    # That is a design claim about the layout, so it is measured at a reference size
    # rather than at whatever this machine happens to have.
    #
    # The reference is in points, not pixels, and is converted using the display's own
    # scaling. A pixel constant here silently assumes 100%: at 200% the same numbers
    # describe a window with half the usable room, and the check fails on a layout
    # that is perfectly fine. A display too small to host the reference cannot answer
    # the question either way.
    scale = max(1.0, app.display_scaling)
    reference = (int(1180 * scale), int(760 * scale))
    if app.root.winfo_screenwidth() < reference[0] or app.root.winfo_screenheight() < reference[1]:
        return
    app.root.geometry(format_window_geometry(*reference))
    app.root.update()
    app._show_page("chat")
    app.root.update()
    app._sync_content_geometry()
    if app.content_canvas is None or app.content_canvas.yview() != (0.0, 1.0):
        raise ValueError("Chat composer does not fit in the default desktop viewport.")
    app._show_page("roles")
    app.root.update()
    app._sync_content_geometry()
    if app.content_canvas is None:
        raise ValueError("Roles page content canvas is missing.")
    roles_yview = app.content_canvas.yview()
    if len(roles_yview) != 2 or not (0.0 <= roles_yview[0] <= roles_yview[1] <= 1.0):
        raise ValueError("Roles page has an invalid vertical scroll state.")
    app._apply_responsive_layout(1180)
    app.root.update_idletasks()
    if app._sidebar_compact:
        raise ValueError("Desktop sidebar unexpectedly collapsed at the default width.")
    if app.nav_buttons["config"].cget("text").strip() != f"▦   {app.tr('config')}":
        raise ValueError("Full desktop navigation label is not present.")
    clipped = [
        name
        for name, button in app.nav_buttons.items()
        if button.winfo_reqwidth() > button.winfo_width()
    ]
    if clipped:
        raise ValueError(f"Desktop navigation labels are clipped: {', '.join(clipped)}")
    if "agents_setup" not in app.pages or app.registry_agent_box is None or app.a2a_profile_box is None:
        raise ValueError("Agent installation and A2A setup controls are missing from the desktop UI.")
    app._layout_connection_cards(1100)
    if app.connection_card_shells is None or int(app.connection_card_shells[1].grid_info()["row"]) != 1:
        raise ValueError("Connection cards did not stack in the narrow desktop layout.")
    app._apply_responsive_layout(900)
    if not app._sidebar_compact or app.nav_buttons["config"].cget("text").strip() != "▦":
        raise ValueError("Compact desktop navigation did not replace labels with icons.")
    app._layout_role_cards(700)
    if int(app.role_card_shells["critic"].grid_info()["row"]) != 1:
        raise ValueError("Two-column role card reflow failed.")
    app._layout_role_cards(480)
    if int(app.role_card_shells["critic"].grid_info()["row"]) != 2:
        raise ValueError("One-column role card reflow failed.")


def main(
    project_root: Path | None = None,
    *,
    smoke: bool = False,
    locale: str | None = None,
    theme: str | None = None,
) -> int:
    validate_translations()
    settings = load_gui_settings()
    settings = GuiSettings(
        locale=locale or settings.locale,
        theme=theme or settings.theme,
        last_project_root=str(project_root.resolve()) if project_root else settings.last_project_root,
        onboarding_completed=settings.onboarding_completed,
        update_catalog_url=settings.update_catalog_url,
        automatic_updates=settings.automatic_updates,
        window_geometry=settings.window_geometry,
    )
    enable_dpi_awareness()
    root = tk.Tk()
    app = HohDesktopApp(root, settings, auto_load=not smoke)
    if smoke:
        root.update_idletasks()
        validate_responsive_layout(app)
        app.close()
        return 0
    root.mainloop()
    return 0
