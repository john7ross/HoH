import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from support import write_architecture_docs

from llm_harness.cli import main
from llm_harness.critic_adapters import build_critic_decision
from llm_harness.config import A2AAgentConfig, HarnessConfig, RoleIdentityConfig, SupervisorConfig, ThreeHeadConfig, write_config
from llm_harness.domain import HarnessRunResult, ModelInvocationEvidence, VerificationReport, WorkItem
from llm_harness.hermes import HermesAcpStatus
from llm_harness.tasks import TaskLoadError
from llm_harness.telegram import StubTelegramNotifier, TelegramBotNotifier
from llm_harness.metrics import UsageContext
from llm_harness.state import HohStateStore


class CliTests(unittest.TestCase):
    def test_workspace_cli_registers_and_summarizes_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            registry = base / "workspace.json"
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            register_stdout = io.StringIO()
            with contextlib.redirect_stdout(register_stdout):
                register_code = main([
                    "workspace", "register", "--registry", str(registry),
                    "--project-root", str(root), "--name", "CLI project", "--json",
                ])
            project_id = json.loads(register_stdout.getvalue())["data"]["project"]["project_id"]
            schedule_stdout = io.StringIO()
            with contextlib.redirect_stdout(schedule_stdout):
                schedule_code = main([
                    "workspace", "schedule", "--registry", str(registry),
                    "--project-id", project_id, "--enabled", "--interval-minutes", "5",
                    "--start-immediately", "--json",
                ])
            summary_stdout = io.StringIO()
            with contextlib.redirect_stdout(summary_stdout):
                summary_code = main(["workspace", "summary", "--registry", str(registry), "--json"])

        summary = json.loads(summary_stdout.getvalue())
        self.assertEqual((register_code, schedule_code, summary_code), (0, 0, 0))
        self.assertEqual(summary["data"]["projects"][0]["name"], "CLI project")
        self.assertTrue(summary["data"]["projects"][0]["schedule_enabled"])

    def test_metrics_cli_reports_summary_and_immutable_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_root = root / "state"
            store = HohStateStore(state_root)
            store.metrics.record_model_evidence(
                UsageContext("worker", "codex", "openai", "gpt-test", "acp", "task-1", "run-1"),
                ModelInvocationEvidence(
                    provider="openai",
                    model="gpt-test",
                    endpoint="https://api.example.test/v1/responses",
                    request_id="request-1",
                    attempts=1,
                    latency_ms=125,
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                ),
            )

            summary_stdout = io.StringIO()
            with contextlib.redirect_stdout(summary_stdout):
                summary_code = main([
                    "metrics-summary", "--project-root", str(root),
                    "--state-root", str(state_root), "--json",
                ])
            events_stdout = io.StringIO()
            with contextlib.redirect_stdout(events_stdout):
                events_code = main([
                    "metrics-list", "--project-root", str(root),
                    "--state-root", str(state_root), "--limit", "1", "--json",
                ])

        summary = json.loads(summary_stdout.getvalue())
        events = json.loads(events_stdout.getvalue())
        self.assertEqual(summary_code, 0)
        self.assertEqual(summary["data"]["groups"][0]["total_tokens"], 15)
        self.assertEqual(events_code, 0)
        self.assertEqual(events["data"]["events"][0]["invocation_id"], "request-1")

    def test_agent_manage_lists_safe_install_status(self):
        agent = SimpleNamespace(id="demo", name="Demo", version="1.2.3", description="demo agent")
        status = SimpleNamespace(installed=False, installable=True, distribution="npx", detail="Ready")
        stdout = io.StringIO()
        with patch("llm_harness.cli.registry_agent_statuses", return_value=((agent, status),)):
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["agent-manage", "list", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["data"]["agents"][0]["installable"])

    def test_a2a_test_reports_configured_connection(self):
        config = HarnessConfig(a2a_agents=(A2AAgentConfig("remote", "http://127.0.0.1/card"),))
        stdout = io.StringIO()
        with (
            patch("llm_harness.cli.load_config", return_value=config),
            patch("llm_harness.cli.test_a2a_agent", return_value=(True, "A2A 1.0 JSONRPC", "1.0")),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(["a2a-test", "--name", "remote", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["data"]["available"])

    def test_compatibility_matrix_reports_what_is_on_this_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["compatibility-matrix", "--project-root", tmp, "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["data"]["statuses"], ["available", "unavailable"])
        self.assertTrue(payload["data"]["entries"])
        for entry in payload["data"]["entries"]:
            self.assertIn(entry["status"], {"available", "unavailable"})
            self.assertIn(entry["role"], {"supervisor", "worker", "critic"})

    def test_role_conformance_reports_the_run_without_writing_a_record(self):
        spec = {
            "spec_version": "1.0",
            "id": "certified-plan",
            "title": "Certified plan",
            "goal": "Create HARNESS_DEMO.md.",
            "customer": "Conformance",
            "business_requirements": ["One bounded artifact."],
            "definition_of_done": ["Verification passes."],
            "non_functional_requirements": [],
            "documentation_requirements": ["Document the artifact."],
            "constraints": ["No Git authority."],
            "open_questions": [],
            "tasks": [{
                "id": "task-1",
                "title": "Create demo",
                "objective": "Create HARNESS_DEMO.md.",
                "acceptance_criteria": ["HARNESS_DEMO.md exists."],
                "verification_commands": ["test HARNESS_DEMO.md"],
                "allowed_paths": ["HARNESS_DEMO.md"],
                "non_goals": [],
                "depends_on": [],
                "priority": 1,
            }],
        }
        script = f"import sys; sys.stdin.read(); print({json.dumps(json.dumps(spec))})"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "harness.json"
            write_config(
                config_path,
                HarnessConfig(
                    supervisor=SupervisorConfig(
                        driver="command_json",
                        command=sys.executable,
                        args=("-c", script),
                    ),
                    three_head=ThreeHeadConfig(
                        logic=RoleIdentityConfig("logic", "future-supervisor", "planner-model"),
                    ),
                ),
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "role-conformance",
                        "--project-root", str(root),
                        "--config", str(config_path),
                        "--role", "supervisor",
                        "--agent", "future-supervisor",
                        "--model", "planner-model",
                        "--agent-version", "1.2.3",
                        "--json",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertTrue(payload["ok"])
        # A diagnostic, not a certificate: it reports what ran and leaves nothing behind.
        self.assertEqual(payload["data"]["identity"]["agent"], "future-supervisor")
        self.assertEqual(payload["data"]["identity"]["model"], "planner-model")
        self.assertNotIn("certification", payload["data"])

    def test_role_conformance_runs_even_when_the_model_is_unknown(self):
        # The operator is asking whether the role works. Refusing to answer until
        # they supply --model helps nobody, so an unknown identity is reported, not fatal.
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(
                    [
                        "role-conformance",
                        "--project-root", tmp,
                        "--role", "worker",
                        "--agent", "codex",
                        "--agent-version", "1.7.0",
                        "--json",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["data"]["identity"]["agent"], "codex")
        self.assertEqual(payload["data"]["identity"]["version"], "1.7.0")
        self.assertIn("checks", payload["data"])

    def test_scan_command_returns_success(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["scan"])

        self.assertEqual(exit_code, 0)
        self.assertIn("agent codex:", stdout.getvalue())

    def test_role_catalog_exposes_plain_language_briefing_questions(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["role-catalog", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertIn("Supervisor", payload["data"]["briefing_questions"][0])
        self.assertIn("Critic", payload["data"]["briefing_questions"][2])
        self.assertTrue(any(item["name"] == "hermes" for item in payload["data"]["agents"]))
        self.assertTrue(any(item["name"] == "direct-supervisor-model" for item in payload["data"]["agents"]))
        self.assertTrue(any(item["name"] == "direct-critic-model" for item in payload["data"]["agents"]))

    def test_driver_catalog_exposes_versioned_neutral_contracts(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["driver-catalog", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        manifests = payload["data"]["drivers"]
        self.assertTrue(any(item["driver_id"] == "command" and item["role"] == "worker" for item in manifests))
        self.assertTrue(any(item["driver_id"] == "process" and item["role"] == "worker" for item in manifests))
        self.assertTrue(any(item["driver_id"] == "command_json" and item["role"] == "critic" for item in manifests))
        self.assertTrue(all(item["protocol"] == "hoh.driver" for item in manifests))

    def test_worker_smoke_accepts_unknown_cli_through_command_driver(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "harness.toml"
            executable = sys.executable.replace("\\", "\\\\")
            config.write_text(
                f'[worker]\ndriver = "command"\ncommand = "{executable}"\nargs = []\n',
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["worker-smoke", "--config", str(config), "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["data"]["worker_driver"], "command")
        self.assertTrue(payload["data"]["available"])

    def test_runtime_command_reports_the_installation_not_the_project(self):
        """This used to assert exit 1 for a project with no runtime directory.

        That expectation was the bug: the runtime belongs to the HoH installation,
        so an installed user running this in their own repository was told the
        interpreter they were running on was missing.
        """
        with tempfile.TemporaryDirectory() as tmp:
            installation = Path(tmp) / "installation"
            project = Path(tmp) / "project"
            (installation / "vendor" / "wheels").mkdir(parents=True)
            (installation / "vendor" / "wheels" / "llm_harness-0.1.0-py3-none-any.whl").write_text(
                "", encoding="utf-8"
            )
            project.mkdir()
            stdout = io.StringIO()
            with patch.dict(os.environ, {"HOH_DISTRIBUTION_ROOT": str(installation)}):
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(["runtime", "--project-root", str(project)])

        output = stdout.getvalue()
        # Windows requires an embedded interpreter, and this fixture has none, so
        # the command still reports a finding. What matters is which tree it names.
        self.assertIn(str(installation), output)
        self.assertNotIn(str(project), output)

    def test_audit_command_returns_markdown_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            write_architecture_docs(root / "docs")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Initial ready project")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "audit",
                        "--project-root",
                        str(root),
                        "--check",
                        f'"{sys.executable}" -c "print(\'ok\')"',
                        "--markdown",
                    ]
                )

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertIn("# Project audit report", stdout.getvalue())

    def test_notify_reports_failure_when_no_channel_is_configured(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["notify", "--message", "Project ready"])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("ok=False", output)
        self.assertIn("delivered=False", output)
        self.assertIn("No notification channel is configured", output)

    def test_init_project_writes_local_config_and_env_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "config.example.toml").write_text("[telegram]\nenabled = false\n", encoding="utf-8")
            (root / "scripts" / "hoh-env.template.ps1").write_text(
                "$env:HOH_TELEGRAM_BOT_TOKEN = \"<bot-token>\"\n",
                encoding="utf-8",
            )
            (root / "scripts" / "hoh-env.template.sh").write_text(
                'export HOH_TELEGRAM_BOT_TOKEN="<bot-token>"\n',
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main(["init-project", "--project-root", str(root)])

            config = root / "harness.toml"
            env_file = root / ("hoh-env.local.ps1" if os.name == "nt" else "hoh-env.local.sh")
            config_exists = config.exists()
            env_exists = env_file.exists()

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertTrue(config_exists)
        self.assertTrue(env_exists)
        self.assertIn("config=", stdout.getvalue())
        self.assertIn("env_file=", stdout.getvalue())

    def test_init_project_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "config.example.toml").write_text("new\n", encoding="utf-8")
            (root / "scripts" / "hoh-env.template.ps1").write_text("env\n", encoding="utf-8")
            (root / "scripts" / "hoh-env.template.sh").write_text("env\n", encoding="utf-8")
            (root / "harness.toml").write_text("existing\n", encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main(["init-project", "--project-root", str(root)])

            config_text = (root / "harness.toml").read_text(encoding="utf-8")

        self.assertEqual(exit_code, 1)
        self.assertEqual(config_text, "existing\n")
        self.assertIn("Target file(s) already exist", stdout.getvalue())

    def test_init_project_force_can_skip_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "config.example.toml").write_text("new\n", encoding="utf-8")
            (root / "scripts" / "hoh-env.template.ps1").write_text("env\n", encoding="utf-8")
            (root / "scripts" / "hoh-env.template.sh").write_text("env\n", encoding="utf-8")
            (root / "harness.toml").write_text("existing\n", encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main(["init-project", "--project-root", str(root), "--force", "--skip-env"])

            config_text = (root / "harness.toml").read_text(encoding="utf-8")
            env_exists = (root / ("hoh-env.local.ps1" if os.name == "nt" else "hoh-env.local.sh")).exists()

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertEqual(config_text, "new\n")
        self.assertFalse(env_exists)
        self.assertIn("env_file=", stdout.getvalue())

    def test_operator_command_reports_status_and_records_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "operator-command",
                        "--project-root",
                        str(root),
                        "--state-root",
                        str(state),
                        "--text",
                        "/status",
                    ]
                )
            from llm_harness.state import HohStateStore

            events = HohStateStore(state).operator_events()

        self.assertEqual(exit_code, 0)
        self.assertIn("command=status", stdout.getvalue())
        self.assertIn("queued=0", stdout.getvalue())
        self.assertEqual(events[0].command, "status")

    def test_operator_command_retry_requeues_failed_task(self):
        failed = HarnessRunResult(
            repository=Path("C:/tmp/repo"),
            work_item_id="cli-task",
            commit=None,
            pre_apply=VerificationReport(ok=False, findings=("worker failed",)),
            post_apply=VerificationReport(ok=False, findings=("worker failed",)),
            command_results=(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self._task_file(root)
            state = root / "state"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["queue-add", "--project-root", str(root), "--state-root", str(state), "--task", str(task)])
            with patch("llm_harness.cli._run_worker_work_item", return_value=failed):
                with contextlib.redirect_stdout(io.StringIO()):
                    main(["queue-run-next", "--project-root", str(root), "--state-root", str(state)])
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "operator-command",
                        "--project-root",
                        str(root),
                        "--state-root",
                        str(state),
                        "--text",
                        "/retry cli-task",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("command=retry", stdout.getvalue())
        self.assertIn("task=cli-task", stdout.getvalue())

    def test_project_plan_writes_docs_tasks_and_enqueues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            spec = root / "project.json"
            spec.write_text(json.dumps(self._project_spec_payload()), encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "project-plan",
                        "--project-root",
                        str(root),
                        "--state-root",
                        str(state),
                        "--spec",
                        str(spec),
                        "--write",
                        "--enqueue",
                    ]
                )
            from llm_harness.state import HohStateStore

            queued = HohStateStore(state).list_tasks()
            brief_exists = (root / "docs" / "hoh" / "project-brief.md").exists()
            task_exists = (root / "tasks" / "hoh" / "001-cli-plan-task.json").exists()

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertIn("tasks=1", stdout.getvalue())
        self.assertIn("enqueued=1", stdout.getvalue())
        self.assertEqual([task.work_item.id for task in queued], ["cli-plan-task"])
        self.assertTrue(brief_exists)
        self.assertTrue(task_exists)

    def test_project_plan_rejects_enqueue_without_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = root / "project.json"
            spec.write_text(json.dumps(self._project_spec_payload()), encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main(["project-plan", "--project-root", str(root), "--spec", str(spec), "--enqueue"])

        self.assertEqual(exit_code, 1)
        self.assertIn("--enqueue requires --write", stdout.getvalue())

    def test_telegram_poll_processes_authorized_operator_command(self):
        send_messages = []

        def fake_post(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            if request.full_url.endswith("/getUpdates"):
                return _Response(
                    200,
                    {
                        "ok": True,
                        "result": [
                            {
                                "update_id": 501,
                                "message": {
                                    "from": {"id": 42},
                                    "chat": {"id": 123456},
                                    "text": "/status",
                                },
                            }
                        ],
                    },
                )
            send_messages.append(payload["text"])
            return _Response(200, {"ok": True, "result": {"message_id": 1}})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            config = root / "harness.toml"
            config.write_text(
                """
[telegram]
enabled = true
bot_token_env = "HOH_TELEGRAM_BOT_TOKEN"
chat_id_env = "HOH_TELEGRAM_CHAT_ID"
user_id_env = "HOH_TELEGRAM_USER_ID"
""",
                encoding="utf-8",
            )
            notifier = TelegramBotNotifier(
                bot_token="secret",
                chat_id="123456",
                http_post=fake_post,
            )
            stdout = io.StringIO()
            with (
                patch("llm_harness.cli.build_notifier", return_value=notifier),
                patch.dict(
                    "os.environ",
                    {
                        "HOH_TELEGRAM_BOT_TOKEN": "secret",
                        "HOH_TELEGRAM_CHAT_ID": "123456",
                        "HOH_TELEGRAM_USER_ID": "42",
                    },
                ),
            ):
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "telegram-poll",
                            "--config",
                            str(config),
                            "--project-root",
                            str(root),
                            "--state-root",
                            str(state),
                        ]
                    )
            from llm_harness.state import HohStateStore

            store = HohStateStore(state)
            self.assertEqual(store.telegram_update_offset(), 502)
            self.assertEqual(store.operator_events()[0].command, "status")

        self.assertEqual(exit_code, 0)
        self.assertIn("processed=1", stdout.getvalue())
        self.assertIn("HoH operator command: OK", send_messages[0])

    def test_telegram_watch_runs_limited_iterations(self):
        send_messages = []
        requested_offsets = []

        def fake_post(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            if request.full_url.endswith("/getUpdates"):
                requested_offsets.append(payload.get("offset"))
                result = []
                if payload.get("offset") is None:
                    result = [
                        {
                            "update_id": 701,
                            "message": {
                                "from": {"id": 42},
                                "chat": {"id": 123456},
                                "text": "/status",
                            },
                        }
                    ]
                return _Response(200, {"ok": True, "result": result})
            send_messages.append(payload["text"])
            return _Response(200, {"ok": True, "result": {"message_id": 1}})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            config = root / "harness.toml"
            config.write_text(
                """
[telegram]
enabled = true
bot_token_env = "HOH_TELEGRAM_BOT_TOKEN"
chat_id_env = "HOH_TELEGRAM_CHAT_ID"
user_id_env = "HOH_TELEGRAM_USER_ID"
""",
                encoding="utf-8",
            )
            notifier = TelegramBotNotifier(
                bot_token="secret",
                chat_id="123456",
                http_post=fake_post,
            )
            stdout = io.StringIO()
            with (
                patch("llm_harness.cli.build_notifier", return_value=notifier),
                patch.dict(
                    "os.environ",
                    {
                        "HOH_TELEGRAM_BOT_TOKEN": "secret",
                        "HOH_TELEGRAM_CHAT_ID": "123456",
                        "HOH_TELEGRAM_USER_ID": "42",
                    },
                ),
            ):
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "telegram-watch",
                            "--config",
                            str(config),
                            "--project-root",
                            str(root),
                            "--state-root",
                            str(state),
                            "--iterations",
                            "2",
                            "--interval-seconds",
                            "0",
                            "--timeout",
                            "0",
                        ]
                    )
            from llm_harness.state import HohStateStore

            store = HohStateStore(state)
            self.assertEqual(store.telegram_update_offset(), 702)

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertEqual(requested_offsets, [None, 702])
        self.assertEqual(len(send_messages), 1)
        self.assertIn("iteration=1 processed=1 ignored=0 next_offset=702", stdout.getvalue())
        self.assertIn("iteration=2 processed=0 ignored=0 next_offset=702", stdout.getvalue())
        self.assertIn("iterations=2", stdout.getvalue())
        self.assertIn("processed=1", stdout.getvalue())
        self.assertIn("status=completed", stdout.getvalue())

    def test_telegram_watch_rejects_negative_iterations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "harness.toml"
            config.write_text("[telegram]\nenabled = false\n", encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "telegram-watch",
                        "--config",
                        str(config),
                        "--project-root",
                        str(root),
                        "--iterations",
                        "-1",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("--iterations must be 0 or greater", stdout.getvalue())

    def test_doctor_command_prints_environment_report(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["doctor"])

        self.assertIn(exit_code, (0, 1))
        self.assertIn("check=git", stdout.getvalue())
        self.assertIn("check=runtime", stdout.getvalue())

    def test_hermes_check_command_reports_status(self):
        status = HermesAcpStatus(
            ok=True,
            command=("hermes", "acp", "--check"),
            stdout="Hermes ACP check OK\n",
            stderr="",
            return_code=0,
        )
        stdout = io.StringIO()
        with patch("llm_harness.cli.check_hermes_acp", return_value=status):
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["hermes-check"])

        self.assertEqual(exit_code, 0)
        self.assertIn("ok=True", stdout.getvalue())

    def test_hermes_dry_run_prints_worker_prompt(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "hermes-dry-run",
                    "--objective",
                    "Create adapter",
                    "--acceptance",
                    "Patch is returned.",
                    "--check",
                    "python -m unittest",
                    "--allowed-path",
                    "src/llm_harness",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("isolated repository path", stdout.getvalue())
        self.assertIn("Patch is returned.", stdout.getvalue())

    def test_hermes_smoke_reports_result(self):
        result = HarnessRunResult(
            repository=Path("C:/tmp/repo"),
            work_item_id="hermes-smoke",
            commit="abc123",
            pre_apply=VerificationReport(ok=True),
            post_apply=VerificationReport(ok=True),
            command_results=(),
        )
        stdout = io.StringIO()
        with patch("llm_harness.cli._run_hermes_smoke", return_value=result):
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["hermes-smoke", "--timeout", "1"])

        self.assertEqual(exit_code, 0)
        self.assertIn("ok=True", stdout.getvalue())
        self.assertIn("commit=abc123", stdout.getvalue())

    def test_hermes_run_reports_task_result(self):
        result = HarnessRunResult(
            repository=Path("C:/tmp/repo"),
            work_item_id="task-1",
            commit="def456",
            pre_apply=VerificationReport(ok=True),
            post_apply=VerificationReport(ok=True),
            command_results=(),
        )
        stdout = io.StringIO()
        with patch("llm_harness.cli._run_hermes_task", return_value=result) as run_task:
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["hermes-run", "--project-root", "C:/repo", "--task", "task.json"])

        self.assertEqual(exit_code, 0)
        self.assertIn("ok=True", stdout.getvalue())
        self.assertIn("commit=def456", stdout.getvalue())
        run_task.assert_called_once_with(Path("C:/repo"), Path("task.json"), 300.0)

    def test_hermes_run_reports_task_load_error(self):
        stdout = io.StringIO()
        with patch("llm_harness.cli._run_hermes_task", side_effect=TaskLoadError("boom")):
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["hermes-run", "--task", "task.json"])

        self.assertEqual(exit_code, 1)
        self.assertIn("ok=False", stdout.getvalue())
        self.assertIn("error=boom", stdout.getvalue())

    def test_worker_run_uses_configured_stub_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            (root / ".gitignore").write_text(".tmp\n", encoding="utf-8")
            self._git(root, "add", ".gitignore")
            self._git(root, "commit", "-m", "Initial commit")
            task = self._stub_worker_task_file(root)
            config = root / "harness.toml"
            config.write_text(
                """
[worker]
type = "stub"
command = "stub"
args = []
timeout_seconds = 1
""",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "worker-run",
                        "--config",
                        str(config),
                        "--project-root",
                        str(root),
                        "--task",
                        str(task),
                    ]
                )

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertIn("ok=True", stdout.getvalue())
        self.assertIn("commit=", stdout.getvalue())

    def test_worker_run_uses_command_adapter_in_isolated_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            root.mkdir()
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            self._git(root, "add", "README.md")
            self._git(root, "commit", "-m", "Initial commit")

            worker_script = base / "worker.py"
            worker_script.write_text(
                "import os\n"
                "import sys\n"
                "from pathlib import Path\n"
                "prompt = sys.stdin.read()\n"
                "assert 'Task id: command-worker-task' in prompt\n"
                "assert os.environ['HOH_WORK_ITEM_ID'] == 'command-worker-task'\n"
                "Path(os.environ['HOH_REPOSITORY'], 'COMMAND_WORKER.md').write_text('command ok\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            task = base / "task.json"
            task.write_text(
                json.dumps(
                    {
                        "id": "command-worker-task",
                        "title": "Command worker task",
                        "objective": "Create COMMAND_WORKER.md through a generic command worker.",
                        "acceptance_criteria": ["COMMAND_WORKER.md exists."],
                        "verification_commands": [
                            f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'COMMAND_WORKER.md\').read_text(encoding=\'utf-8\') == \'command ok\\n\'"'
                        ],
                        "allowed_paths": ["COMMAND_WORKER.md"],
                        "non_goals": [],
                    }
                ),
                encoding="utf-8",
            )
            config = base / "harness.toml"
            config.write_text(
                "\n".join(
                    (
                        "[worker]",
                        'type = "command"',
                        f"command = {json.dumps(sys.executable)}",
                        f"args = [{json.dumps(str(worker_script))}]",
                        "timeout_seconds = 10",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "worker-run",
                        "--config",
                        str(config),
                        "--project-root",
                        str(root),
                        "--task",
                        str(task),
                    ]
                )

            content = (root / "COMMAND_WORKER.md").read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertIn("ok=True", stdout.getvalue())
        self.assertIn("commit=", stdout.getvalue())
        self.assertEqual(content, "command ok\n")

    def test_queue_add_and_list_use_persistent_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self._task_file(root)
            state = root / "state"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                add_code = main(["queue-add", "--project-root", str(root), "--state-root", str(state), "--task", str(task)])
            list_stdout = io.StringIO()
            with contextlib.redirect_stdout(list_stdout):
                list_code = main(["queue-list", "--project-root", str(root), "--state-root", str(state)])

        self.assertEqual(add_code, 0)
        self.assertEqual(list_code, 0)
        self.assertIn("task=cli-task", stdout.getvalue())
        self.assertIn("tasks=1", list_stdout.getvalue())
        self.assertIn("status=queued", list_stdout.getvalue())

    def test_queue_run_next_records_result(self):
        result = HarnessRunResult(
            repository=Path("C:/tmp/repo"),
            work_item_id="cli-task",
            commit="fed789",
            pre_apply=VerificationReport(ok=True),
            post_apply=VerificationReport(ok=True),
            command_results=(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self._task_file(root)
            state = root / "state"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["queue-add", "--project-root", str(root), "--state-root", str(state), "--task", str(task)])
            stdout = io.StringIO()
            with patch("llm_harness.cli._run_worker_work_item", return_value=result) as run_worker:
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(["queue-run-next", "--project-root", str(root), "--state-root", str(state)])
            list_stdout = io.StringIO()
            with contextlib.redirect_stdout(list_stdout):
                main(["queue-list", "--project-root", str(root), "--state-root", str(state)])
            history_stdout = io.StringIO()
            with contextlib.redirect_stdout(history_stdout):
                main(["queue-history", "--project-root", str(root), "--state-root", str(state)])

        self.assertEqual(exit_code, 0)
        self.assertIn("ok=True", stdout.getvalue())
        self.assertIn("commit=fed789", stdout.getvalue())
        self.assertIn("status=done", list_stdout.getvalue())
        self.assertIn("runs=1", history_stdout.getvalue())
        self.assertIn("commit=fed789", history_stdout.getvalue())
        self.assertEqual(run_worker.call_args.args[2].worker.type, "hermes_acp")

    @patch("llm_harness.critic_runtime.create_critic_adapter")
    def test_queue_run_next_automatically_runs_configured_critic(self, create_critic):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            (root / ".gitignore").write_text(".tmp\n", encoding="utf-8")
            self._git(root, "add", ".gitignore")
            self._git(root, "commit", "-m", "Initial commit")
            task = self._stub_worker_task_file(root)
            state = root / "state"
            config = root / "harness.toml"
            config.write_text(
                """
[worker]
type = "stub"
command = "stub"
args = []

[critic]
type = "claude_code"
command = "claude"
timeout_seconds = 30

[three_head]
mode = "required"
max_attempts = 3

[three_head.roles.logic]
identity = "logic"
provider = "codex"
model = "supervisor"

[three_head.roles.worker]
identity = "worker"
provider = "hermes"
model = "worker"

[three_head.roles.critic]
identity = "critic"
provider = "claude"
model = "sonnet"
""",
                encoding="utf-8",
            )
            create_critic.return_value = _ApproveCritic()
            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "queue-add",
                        "--project-root",
                        str(root),
                        "--state-root",
                        str(state),
                        "--task",
                        str(task),
                    ]
                )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "queue-run-next",
                        "--project-root",
                        str(root),
                        "--state-root",
                        str(state),
                        "--config",
                        str(config),
                    ]
                )
            queue = json.loads((state / "queue.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertIn("critic_decision=approve", stdout.getvalue())
        self.assertIn("status=done", stdout.getvalue())
        self.assertEqual(queue[0]["status"], "done")

    def test_audit_history_lists_persisted_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            from llm_harness.state import HohStateStore

            record = HohStateStore(state).record_audit_report(
                "# Project audit report\n",
                ok=True,
                findings_count=0,
                command_results_count=2,
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["audit-history", "--project-root", str(root), "--state-root", str(state)])

        self.assertEqual(exit_code, 0)
        self.assertIn("audits=1", stdout.getvalue())
        self.assertIn(f"latest={record.report_path}", stdout.getvalue())
        self.assertIn(f"audit={record.audit_id} ok=True findings=0 checks=2 path={record.report_path}", stdout.getvalue())

    def test_audit_history_reports_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["audit-history", "--project-root", str(root), "--state-root", str(state)])

        self.assertEqual(exit_code, 0)
        self.assertIn("audits=0", stdout.getvalue())

    def test_queue_retry_requeues_failed_task(self):
        failed = HarnessRunResult(
            repository=Path("C:/tmp/repo"),
            work_item_id="cli-task",
            commit=None,
            pre_apply=VerificationReport(ok=False, findings=("worker failed",)),
            post_apply=VerificationReport(ok=False, findings=("worker failed",)),
            command_results=(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self._task_file(root)
            state = root / "state"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["queue-add", "--project-root", str(root), "--state-root", str(state), "--task", str(task)])
            with patch("llm_harness.cli._run_worker_work_item", return_value=failed):
                with contextlib.redirect_stdout(io.StringIO()):
                    main(["queue-run-next", "--project-root", str(root), "--state-root", str(state)])
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["queue-retry", "--project-root", str(root), "--state-root", str(state), "--task-id", "cli-task"])
            list_stdout = io.StringIO()
            with contextlib.redirect_stdout(list_stdout):
                main(["queue-list", "--project-root", str(root), "--state-root", str(state)])

        self.assertEqual(exit_code, 0)
        self.assertIn("status=queued", stdout.getvalue())
        self.assertIn("attempts=1", stdout.getvalue())
        self.assertIn("status=queued", list_stdout.getvalue())

    def test_queue_recover_running_requeues_stuck_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self._task_file(root)
            state = root / "state"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["queue-add", "--project-root", str(root), "--state-root", str(state), "--task", str(task)])
            from llm_harness.state import HohStateStore

            HohStateStore(state).mark_running("cli-task")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "queue-recover-running",
                        "--project-root",
                        str(root),
                        "--state-root",
                        str(state),
                        "--task-id",
                        "cli-task",
                        "--reason",
                        "process crashed",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("status=queued", stdout.getvalue())
        self.assertIn("last_error=process crashed", stdout.getvalue())

    def test_queue_stale_reports_stale_running_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self._task_file(root)
            state = root / "state"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["queue-add", "--project-root", str(root), "--state-root", str(state), "--task", str(task)])
            from llm_harness.state import HohStateStore

            store = HohStateStore(state)
            store.mark_running("cli-task")
            self._set_task_updated_at(store.queue_path, "cli-task", "2026-01-01T00:00:00+00:00")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "queue-stale",
                        "--project-root",
                        str(root),
                        "--state-root",
                        str(state),
                        "--max-age-minutes",
                        "60",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("ok=False", stdout.getvalue())
        self.assertIn("stale=1", stdout.getvalue())
        self.assertIn("task=cli-task", stdout.getvalue())

    def test_queue_stale_returns_success_without_stale_running_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["queue-stale", "--project-root", str(root), "--state-root", str(state)])

        self.assertEqual(exit_code, 0)
        self.assertIn("ok=True", stdout.getvalue())
        self.assertIn("stale=0", stdout.getvalue())

    def test_queue_retry_rejects_non_failed_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self._task_file(root)
            state = root / "state"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["queue-add", "--project-root", str(root), "--state-root", str(state), "--task", str(task)])
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["queue-retry", "--project-root", str(root), "--state-root", str(state), "--task-id", "cli-task"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Only failed tasks can be retried", stdout.getvalue())

    def test_queue_run_loop_runs_until_empty(self):
        result = HarnessRunResult(
            repository=Path("C:/tmp/repo"),
            work_item_id="cli-task",
            commit="fed789",
            pre_apply=VerificationReport(ok=True),
            post_apply=VerificationReport(ok=True),
            command_results=(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self._task_file(root)
            state = root / "state"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["queue-add", "--project-root", str(root), "--state-root", str(state), "--task", str(task)])
            stdout = io.StringIO()
            with patch("llm_harness.cli._run_worker_work_item", return_value=result):
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(["queue-run-loop", "--project-root", str(root), "--state-root", str(state)])

        self.assertEqual(exit_code, 0)
        self.assertIn("completed=1", stdout.getvalue())
        self.assertIn("status=empty", stdout.getvalue())

    def test_queue_run_loop_final_audit_notifies_ready(self):
        notifier = StubTelegramNotifier()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            self._write_ready_git_project(root)
            state = base / "state"
            stdout = io.StringIO()
            with patch("llm_harness.cli.build_notifier", return_value=notifier):
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "queue-run-loop",
                            "--project-root",
                            str(root),
                            "--state-root",
                            str(state),
                            "--final-audit",
                            "--final-check",
                            f'"{sys.executable}" -c "print(\'ok\')"',
                        ]
                    )
            ready_report_path = self._value_from_output(stdout.getvalue(), "audit_report")
            ready_report_exists = ready_report_path.exists()
            ready_report_text = ready_report_path.read_text(encoding="utf-8") if ready_report_exists else ""

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertIn("final_audit_ready=True", stdout.getvalue())
        self.assertIn("status=ready", stdout.getvalue())
        self.assertTrue(ready_report_exists)
        self.assertIn("# Project audit report", ready_report_text)
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("project ready", notifier.messages[0])
        self.assertIn(str(ready_report_path), notifier.messages[0])
        self.assertIn("Final audit: passed.", notifier.messages[0])

    def test_queue_run_loop_final_audit_blocks_failed_task_state(self):
        notifier = StubTelegramNotifier()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            self._write_ready_git_project(root)
            state = base / "state"
            from llm_harness.state import HohStateStore

            store = HohStateStore(state)
            store.enqueue(
                WorkItem(
                    id="failed-task",
                    title="Failed task",
                    objective="Exercise the final handoff gate.",
                    acceptance_criteria=("The gate blocks failed state.",),
                    verification_commands=("python -m unittest",),
                )
            )
            store.mark_running("failed-task")
            store.record_failure("failed-task", "2026-01-01T00:00:00+00:00", "simulated failure")
            stdout = io.StringIO()
            with patch("llm_harness.cli.build_notifier", return_value=notifier):
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "queue-run-loop",
                            "--project-root",
                            str(root),
                            "--state-root",
                            str(state),
                            "--final-audit",
                            "--final-check",
                            f'"{sys.executable}" -c "print(\'must not run\')"',
                        ]
                    )
            audit_records = store.audit_reports()

        self.assertEqual(exit_code, 1, stdout.getvalue())
        self.assertIn("blocking_state=failed count=1", stdout.getvalue())
        self.assertIn("status=task_state_blocked", stdout.getvalue())
        self.assertEqual(audit_records, ())
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("failed=1", notifier.messages[0])

    def test_queue_run_loop_final_audit_failure_notifies_customer(self):
        notifier = StubTelegramNotifier()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            root.mkdir()
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Initial incomplete project")
            state = base / "state"
            stdout = io.StringIO()
            with patch("llm_harness.cli.build_notifier", return_value=notifier):
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "queue-run-loop",
                            "--project-root",
                            str(root),
                            "--state-root",
                            str(state),
                            "--final-audit",
                            "--final-check",
                            f'"{sys.executable}" -c "print(\'ok\')"',
                        ]
                    )
            failed_report_path = self._value_from_output(stdout.getvalue(), "audit_report")
            failed_report_exists = failed_report_path.exists()
            failed_report_text = failed_report_path.read_text(encoding="utf-8") if failed_report_exists else ""

        self.assertEqual(exit_code, 1)
        self.assertIn("final_audit_ready=False", stdout.getvalue())
        self.assertIn("status=audit_failed", stdout.getvalue())
        self.assertIn("finding=DOCS_DIR_MISSING", stdout.getvalue())
        self.assertTrue(failed_report_exists)
        self.assertIn("DOCS_DIR_MISSING", failed_report_text)
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("final audit failed", notifier.messages[0])
        self.assertIn(str(failed_report_path), notifier.messages[0])
        self.assertIn("DOCS_DIR_MISSING", notifier.messages[0])

    def test_queue_run_loop_stops_and_notifies_on_stale_running_task(self):
        notifier = StubTelegramNotifier()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self._task_file(root)
            state = root / "state"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["queue-add", "--project-root", str(root), "--state-root", str(state), "--task", str(task)])
            from llm_harness.state import HohStateStore

            store = HohStateStore(state)
            store.mark_running("cli-task")
            self._set_task_updated_at(store.queue_path, "cli-task", "2026-01-01T00:00:00+00:00")
            stdout = io.StringIO()
            with (
                patch("llm_harness.cli.build_notifier", return_value=notifier),
                patch("llm_harness.cli._run_worker_work_item") as run_hermes,
            ):
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "queue-run-loop",
                            "--project-root",
                            str(root),
                            "--state-root",
                            str(state),
                            "--stale-minutes",
                            "60",
                        ]
                    )

        self.assertEqual(exit_code, 1)
        self.assertIn("stale_running=1", stdout.getvalue())
        self.assertIn("status=blocked", stdout.getvalue())
        run_hermes.assert_not_called()
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("stale running task", notifier.messages[0])
        self.assertIn("/recover", notifier.messages[0])
        self.assertIn("/stop", notifier.messages[0])
        self.assertNotIn("реши сам", notifier.messages[0])

    def test_queue_run_loop_stops_and_notifies_on_blocker(self):
        failed = HarnessRunResult(
            repository=Path("C:/tmp/repo"),
            work_item_id="cli-task",
            commit=None,
            pre_apply=VerificationReport(ok=False, findings=("worker failed",)),
            post_apply=VerificationReport(ok=False, findings=("worker failed",)),
            command_results=(),
        )
        notifier = StubTelegramNotifier()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self._task_file(root)
            state = root / "state"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["queue-add", "--project-root", str(root), "--state-root", str(state), "--task", str(task)])
            stdout = io.StringIO()
            with (
                patch("llm_harness.cli.build_notifier", return_value=notifier),
                patch("llm_harness.cli._run_worker_work_item", return_value=failed),
            ):
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(["queue-run-loop", "--project-root", str(root), "--state-root", str(state)])

        self.assertEqual(exit_code, 1)
        self.assertIn("status=blocked", stdout.getvalue())
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("How should HoH proceed?", notifier.messages[0])
        self.assertIn("/retry", notifier.messages[0])
        self.assertIn("/stop", notifier.messages[0])
        self.assertNotIn("реши сам", notifier.messages[0])

    def test_queue_run_loop_reports_notification_error(self):
        failed = HarnessRunResult(
            repository=Path("C:/tmp/repo"),
            work_item_id="cli-task",
            commit=None,
            pre_apply=VerificationReport(ok=False, findings=("worker failed",)),
            post_apply=VerificationReport(ok=False, findings=("worker failed",)),
            command_results=(),
        )

        class FailingNotifier:
            def notify_user_action_required(self, text: str) -> None:
                raise RuntimeError("telegram down")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self._task_file(root)
            state = root / "state"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["queue-add", "--project-root", str(root), "--state-root", str(state), "--task", str(task)])
            stdout = io.StringIO()
            with (
                patch("llm_harness.cli.build_notifier", return_value=FailingNotifier()),
                patch("llm_harness.cli._run_worker_work_item", return_value=failed),
            ):
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(["queue-run-loop", "--project-root", str(root), "--state-root", str(state)])

        self.assertEqual(exit_code, 1)
        self.assertIn("notification_error=telegram down", stdout.getvalue())
        self.assertIn("status=blocked", stdout.getvalue())

    def test_queue_run_next_reports_empty_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["queue-run-next", "--project-root", tmp, "--state-root", str(Path(tmp) / "state")])

        self.assertEqual(exit_code, 1)
        self.assertIn("error=No queued tasks.", stdout.getvalue())

    def test_queue_run_next_reports_dependency_blocked_with_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            from llm_harness.state import HohStateStore

            store = HohStateStore(state)
            base = WorkItem(
                id="scheduler-base",
                title="Base",
                objective="Create the base.",
                acceptance_criteria=("Base exists.",),
                verification_commands=("python -m unittest",),
            )
            dependent = WorkItem(
                id="scheduler-dependent",
                title="Dependent",
                objective="Create the dependent.",
                acceptance_criteria=("Dependent exists.",),
                verification_commands=("python -m unittest",),
                depends_on=("scheduler-base",),
                priority=10,
            )
            store.enqueue_many(((base, ""), (dependent, "")))
            store.mark_running(base.id)
            store.record_failure(base.id, "2026-01-01T00:00:00+00:00", "base failed")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "queue-run-next",
                        "--project-root",
                        str(root),
                        "--state-root",
                        str(state),
                        "--json",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 1)
            self.assertEqual(payload["data"]["status"], "dependency_blocked")
            self.assertEqual(
                payload["data"]["schedule"]["waiting"][0]["blockers"],
                [{"status": "failed", "task_id": "scheduler-base"}],
            )

    def _git(self, repository: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)
        return completed.stdout

    def _write_ready_git_project(self, root: Path) -> None:
        root.mkdir()
        self._git(root, "init")
        self._git(root, "config", "user.email", "test@example.local")
        self._git(root, "config", "user.name", "Harness Test")
        (root / "README.md").write_text("# Demo\n\n## Setup\n\nRun checks.\n", encoding="utf-8")
        write_architecture_docs(root / "docs")
        self._git(root, "add", ".")
        self._git(root, "commit", "-m", "Initial ready project")

    def _value_from_output(self, output: str, key: str) -> Path:
        prefix = f"{key}="
        for line in output.splitlines():
            if line.startswith(prefix):
                return Path(line[len(prefix) :])
        raise AssertionError(f"Missing output key: {key}")

    def _task_file(self, root: Path) -> Path:
        task = root / "task.json"
        task.write_text(
            """{
  "id": "cli-task",
  "title": "CLI task",
  "objective": "Exercise queue CLI.",
  "acceptance_criteria": ["Queue CLI reports state."],
  "verification_commands": ["python -m unittest"],
  "allowed_paths": ["HARNESS_CLI.md"],
  "non_goals": []
}
""",
            encoding="utf-8",
        )
        return task

    def _stub_worker_task_file(self, root: Path) -> Path:
        task = root / "stub-task.json"
        payload = {
            "id": "stub-task",
            "title": "Stub worker task",
            "objective": "Exercise generic worker adapter execution.",
            "acceptance_criteria": ["HARNESS_DEMO.md exists."],
            "verification_commands": [
                f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'HARNESS_DEMO.md\').exists()"'
            ],
            "allowed_paths": ["HARNESS_DEMO.md"],
            "non_goals": [],
        }
        task.write_text(json.dumps(payload), encoding="utf-8")
        return task

    def _project_spec_payload(self) -> dict:
        return {
            "id": "cli-plan",
            "title": "CLI plan",
            "goal": "Create deterministic project lifecycle artifacts.",
            "customer": "Customer",
            "business_requirements": ["The roadmap is explicit."],
            "definition_of_done": ["The generated task is queued."],
            "tasks": [
                {
                    "id": "cli-plan-task",
                    "title": "Create CLI plan artifact",
                    "objective": "Create PLAN.md.",
                    "acceptance_criteria": ["PLAN.md exists."],
                    "verification_commands": [
                        "python -c \"from pathlib import Path; assert Path('PLAN.md').exists()\""
                    ],
                    "allowed_paths": ["PLAN.md"],
                    "non_goals": [],
                }
            ],
        }

    def _set_task_updated_at(self, queue_path: Path, task_id: str, updated_at: str) -> None:
        payload = json.loads(queue_path.read_text(encoding="utf-8"))
        for task in payload:
            if task["work_item"]["id"] == task_id:
                task["updated_at_utc"] = updated_at
        queue_path.write_text(json.dumps(payload), encoding="utf-8")


class _ApproveCritic:
    name = "approve-critic"

    def review(self, repository, bundle):
        critic = bundle["three_head"]["roles"]["critic"]
        from llm_harness.config import RoleIdentityConfig

        return build_critic_decision(
            bundle,
            RoleIdentityConfig(
                identity=critic["identity"],
                provider=critic["provider"],
                model=critic["model"],
            ),
            {
                "decision": "approve",
                "summary": "All recorded evidence passes.",
                "findings": [],
                "correction_brief": None,
                "escalation": None,
            },
        )


class _Response:
    def __init__(self, status: int, body: dict) -> None:
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
