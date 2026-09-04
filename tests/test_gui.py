import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from llm_harness.config import A2AAgentConfig, CloudModelEndpoint, HarnessConfig, load_config, project_config_path, write_config
from llm_harness.gui import TEXT, _mix_color, driver_display_name, validate_translations
from llm_harness.gui_services import (
    EditableModelPrice,
    EditableMcpServer,
    EditableProcessProfile,
    EditableA2AAgent,
    EditableProjectSettings,
    editable_process_profiles,
    editable_model_prices,
    editable_role_mcp_policy,
    editable_a2a_agents,
    environment_variable_status,
    GuiSettings,
    load_gui_settings,
    project_config_text,
    remove_process_profile,
    remove_model_price,
    remove_mcp_server,
    remove_a2a_agent,
    save_gui_settings,
    save_process_profile,
    save_model_price,
    save_mcp_server,
    save_role_mcp_policy,
    save_a2a_agent,
    save_editable_project_settings,
    save_project_config_text,
    save_project_roles,
    run_gui_command,
    set_session_environment_variables,
)
from llm_harness.role_profiles import load_effective_config, load_role_profile
from llm_harness.role_catalog import build_role_catalog



class RegistryFetchTests(unittest.TestCase):
    """Preparing a team on a machine that has never cached the Registry."""

    def test_a_never_cached_registry_is_fetched_instead_of_refused(self):
        from types import SimpleNamespace

        from llm_harness import gui_services

        registry = SimpleNamespace(agents=[SimpleNamespace(id="codex-acp")])
        fetches = []

        def refresh():
            fetches.append(True)
            return registry

        with (
            patch.object(gui_services, "load_acp_registry", return_value=None),
            patch.object(gui_services, "refresh_acp_registry", side_effect=refresh),
            patch.object(gui_services, "ManagedAgentInstaller") as installer,
        ):
            installer.return_value.status.return_value = "status"
            statuses = gui_services.registry_agent_statuses(fetch_if_missing=True)

        self.assertEqual(len(fetches), 1)
        self.assertEqual(statuses[0][0].id, "codex-acp")

    def test_without_that_flag_a_missing_registry_is_still_an_error(self):
        from llm_harness import gui_services

        with (
            patch.object(gui_services, "load_acp_registry", return_value=None),
            patch.object(gui_services, "refresh_acp_registry") as refresh,
        ):
            with self.assertRaisesRegex(ValueError, "not cached"):
                gui_services.registry_agent_statuses()

        refresh.assert_not_called()


class ElevatedCardLayoutTests(unittest.TestCase):
    """Width must flow one way: manager -> card -> body, never back.

    A card that asks its body how wide it wants to be, and then requests that width
    from the geometry manager, closes a loop. Inside a grid column with `uniform`
    the two disagree by one pixel and relayout forever, which hangs the GUI rather
    than failing it. This pins the rule instead of trying to detect the hang.
    """

    def _card(self):
        import tkinter as tk
        from tkinter import ttk

        from llm_harness.gui_widgets import ElevatedCard, SurfaceStyle

        try:
            root = tk.Tk()
        except Exception as exc:  # noqa: BLE001 - headless CI has no display
            self.skipTest(f"No display available: {exc}")
        style = SurfaceStyle(
            background="#0b1020", surface="#151d31", surface_hover="#1a2340", shadow="#050814"
        )
        card = ElevatedCard(root, style, lambda host: ttk.Frame(host))
        card.pack(fill="both", expand=True)
        root.geometry("600x400")
        root.update()
        return root, card

    def test_a_stretched_card_never_requests_its_own_width(self):
        import tkinter as tk

        root, card = self._card()
        try:
            self.assertGreater(card.winfo_width(), 1)
            before = int(card.cget("width"))

            event = tk.Event()
            event.width = card.winfo_width() + 120  # type: ignore[attr-defined]
            event.height = 80  # type: ignore[attr-defined]
            card._body_resized(event)  # type: ignore[arg-type]

            self.assertEqual(int(card.cget("width")), before)
        finally:
            root.destroy()

    def test_a_card_still_asks_for_a_width_before_it_has_one(self):
        import tkinter as tk
        from tkinter import ttk

        from llm_harness.gui_widgets import ElevatedCard, SurfaceStyle

        try:
            root = tk.Tk()
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"No display available: {exc}")
        try:
            style = SurfaceStyle(
                background="#0b1020", surface="#151d31", surface_hover="#1a2340", shadow="#050814"
            )
            card = ElevatedCard(root, style, lambda host: ttk.Frame(host))
            event = tk.Event()
            event.width = 240  # type: ignore[attr-defined]
            event.height = 90  # type: ignore[attr-defined]

            card._body_resized(event)  # type: ignore[arg-type]

            self.assertGreaterEqual(int(card.cget("width")), 240)
            self.assertGreaterEqual(int(card.cget("height")), 90)
        finally:
            root.destroy()


class GuiErrorMessageTests(unittest.TestCase):
    def _app(self):
        import tkinter as tk

        from llm_harness.gui import HohDesktopApp

        root = tk.Tk()
        return HohDesktopApp(root, GuiSettings(locale="en"), auto_load=False), root

    def test_errors_are_explained_before_the_technical_detail(self):
        try:
            app, root = self._app()
        except Exception as exc:  # noqa: BLE001 - headless CI has no display
            self.skipTest(f"No display available: {exc}")
        try:
            permission = app._error_message(PermissionError("Access is denied: C:/locked"))
            missing = app._error_message(FileNotFoundError("no such file: harness.toml"))
            invalid = app._error_message(ValueError("Unsupported GUI locale: xx"))
            unexpected = app._error_message(RuntimeError("boom"))
        finally:
            app.close()

        self.assertIn("not allowed to read or write", permission)
        self.assertIn("Access is denied", permission)
        self.assertIn("is not there", missing)
        self.assertIn("not valid", invalid)
        self.assertIn("something unexpected happened", unexpected)
        for message in (permission, missing, invalid, unexpected):
            self.assertIn("Technical detail:", message)

    def test_error_message_keeps_a_detail_even_for_a_silent_exception(self):
        try:
            app, root = self._app()
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"No display available: {exc}")
        try:
            message = app._error_message(RuntimeError(""))
        finally:
            app.close()

        self.assertIn("RuntimeError", message)


class GuiServiceTests(unittest.TestCase):
    def test_guided_mcp_policy_and_server_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            save_role_mcp_policy(
                root,
                "worker",
                permission_mode="allow_once",
                allowed_tool_kinds=("read", "search", "fetch"),
            )
            save_mcp_server(
                root,
                "worker",
                EditableMcpServer(
                    name="docs",
                    transport="http",
                    url="https://mcp.example/api",
                    headers=(("Authorization", "MCP_DOCS_TOKEN"),),
                ),
            )

            policy = editable_role_mcp_policy(load_config(project_config_path(root)), "worker")
            self.assertEqual(policy.allowed_tool_kinds, ("read", "search", "fetch"))
            self.assertEqual(policy.servers[0].headers, (("Authorization", "MCP_DOCS_TOKEN"),))
            remove_mcp_server(root, "worker", "docs")
            policy = editable_role_mcp_policy(load_config(project_config_path(root)), "worker")

        self.assertEqual(policy.servers, ())

    def test_guided_model_price_create_update_and_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            save_model_price(root, EditableModelPrice("OpenAI", "gpt-test", 1.5, 6.0, "usd"))

            prices = editable_model_prices(load_config(project_config_path(root)))
            self.assertEqual(prices, (EditableModelPrice("openai", "gpt-test", 1.5, 6.0, "USD"),))

            save_model_price(root, EditableModelPrice("openai", "gpt-test", 2.0, 8.0, "EUR"))
            prices = editable_model_prices(load_config(project_config_path(root)))
            self.assertEqual(len(prices), 1)
            self.assertEqual(prices[0].output_per_million, 8.0)
            self.assertEqual(prices[0].currency, "EUR")

            remove_model_price(root, "OPENAI", "GPT-TEST")
            self.assertEqual(editable_model_prices(load_config(project_config_path(root))), ())

    @patch("llm_harness.gui_services.subprocess.run")
    def test_gui_daily_operations_map_to_machine_cli(self, run):
        run.return_value = subprocess.CompletedProcess(args=(), returncode=0, stdout="{}", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            run_gui_command(root, "history")
            self.assertIn("queue-history", run.call_args.args[0])
            run_gui_command(root, "retry", task_id="task-1")
            self.assertIn("queue-retry", run.call_args.args[0])
            self.assertIn("task-1", run.call_args.args[0])
            run_gui_command(root, "recover", task_id="task-2", reason="process stopped")
            self.assertIn("queue-recover-running", run.call_args.args[0])
            self.assertIn("process stopped", run.call_args.args[0])

    def test_gui_retry_and_recover_require_operator_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            with self.assertRaisesRegex(ValueError, "Select a task"):
                run_gui_command(root, "retry")
            with self.assertRaisesRegex(ValueError, "reason is required"):
                run_gui_command(root, "recover", task_id="task-2")

    @patch("llm_harness.gui_services.subprocess.run")
    def test_gui_role_check_runs_the_role_conformance_diagnostic(self, run):
        run.return_value = subprocess.CompletedProcess(args=(), returncode=0, stdout="{}", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            result = run_gui_command(root, "check_role_worker")

        self.assertTrue(result.ok)
        command = run.call_args.args[0]
        self.assertIn("role-conformance", command)
        self.assertEqual(command[command.index("--role") + 1], "worker")

    @patch("llm_harness.gui_services.subprocess.run")
    def test_run_next_rejects_unavailable_saved_agent_before_subprocess(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            profile = SimpleNamespace(
                supervisor=SimpleNamespace(agent="missing", driver="acp"),
                worker=SimpleNamespace(agent="codex", driver="acp"),
                critic=None,
            )
            snapshot = SimpleNamespace(
                profile=profile,
                catalog={
                    "agents": [
                        {
                            "name": "missing",
                            "available": False,
                            "detail": "binary is not on PATH",
                            "supervisor_driver": "acp",
                        },
                        {
                            "name": "codex",
                            "available": True,
                            "detail": "npx available",
                            "worker_driver": "acp",
                        },
                    ]
                },
            )
            with patch("llm_harness.gui_services.load_project_snapshot", return_value=snapshot):
                with self.assertRaisesRegex(ValueError, "unavailable.*binary is not on PATH"):
                    run_gui_command(root, "run_next")

        run.assert_not_called()

    def test_direct_model_role_targets_require_configured_credentials(self):
        config = HarnessConfig(
            supervisor_model=CloudModelEndpoint("openai", "gpt-supervisor"),
            verifier_model=CloudModelEndpoint("deepseek", "deepseek-chat"),
            agents=(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(
                "os.environ",
                {"OPENAI_API_KEY": "openai-secret", "DEEPSEEK_API_KEY": "deepseek-secret"},
                clear=False,
            ):
                catalog = build_role_catalog(root, config)

        direct = {item["name"]: item for item in catalog["agents"] if item["source"] == "direct_model"}
        self.assertTrue(direct["direct-supervisor-model"]["available"])
        self.assertEqual(direct["direct-supervisor-model"]["supervisor_driver"], "model_json")
        self.assertTrue(direct["direct-critic-model"]["available"])
        self.assertEqual(direct["direct-critic-model"]["critic_driver"], "model_json")
        self.assertNotIn("secret", repr(direct))

    def test_theme_color_interpolation_supports_card_animation(self):
        self.assertEqual(_mix_color("#000000", "#ffffff", 0.5), "#808080")

    def test_default_catalog_exposes_declarative_aider_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = build_role_catalog(Path(tmp), HarnessConfig())

        aider = next(item for item in catalog["agents"] if item["name"] == "aider")
        self.assertEqual(aider["worker_driver"], "process")
        self.assertEqual(aider["process_profile"], "aider")
        self.assertEqual(aider["version_args"], ["--version"])

    def test_guided_process_profile_create_update_and_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            save_process_profile(
                root,
                EditableProcessProfile(
                    name="my-cli",
                    command="my-cli",
                    args=("--quiet",),
                    prompt_transport="file",
                    prompt_argument="--prompt-file",
                    required_args=("--no-commit",),
                    forbidden_args=("--commit",),
                    model_argument="--model",
                ),
            )
            loaded = load_config(project_config_path(root))
            profile = next(item for item in editable_process_profiles(loaded) if item.name == "my-cli")
            self.assertEqual(profile.prompt_argument, "--prompt-file")
            self.assertEqual(profile.required_args, ("--no-commit",))

            save_process_profile(
                root,
                EditableProcessProfile(name="my-cli", command="renamed-cli", prompt_transport="stdin"),
            )
            loaded = load_config(project_config_path(root))
            profile = next(item for item in editable_process_profiles(loaded) if item.name == "my-cli")
            self.assertEqual(profile.command, "renamed-cli")

            remove_process_profile(root, "my-cli")
            loaded = load_config(project_config_path(root))
            self.assertNotIn("my-cli", {item.name for item in loaded.agents})

    def test_guided_a2a_endpoint_round_trip_and_role_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            save_a2a_agent(
                root,
                EditableA2AAgent(
                    name="remote-team",
                    card_url="https://agent.example/card.json",
                    roles=("supervisor", "worker", "critic"),
                    auth_kind="bearer",
                    credential_env="REMOTE_TOKEN",
                ),
            )
            loaded = load_config(project_config_path(root))
            editable = editable_a2a_agents(loaded)[0]
            self.assertEqual(editable.name, "remote-team")
            self.assertEqual(editable.credential_env, "REMOTE_TOKEN")
            with patch("llm_harness.role_catalog.probe_a2a_agent", return_value=(True, "A2A 1.0", "2.3.4")):
                catalog = build_role_catalog(root, loaded)
            remote = next(item for item in catalog["agents"] if item["name"] == "remote-team")
            self.assertEqual(remote["source"], "a2a")
            self.assertEqual(remote["worker_driver"], "a2a")

            with patch("llm_harness.gui_services.load_role_profile", return_value=None):
                remove_a2a_agent(root, "remote-team")
            self.assertEqual(load_config(project_config_path(root)).a2a_agents, ())

    def test_process_profile_rejects_secret_bearing_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            with self.assertRaisesRegex(ValueError, "secret-bearing"):
                save_process_profile(
                    root,
                    EditableProcessProfile(
                        name="unsafe",
                        command="unsafe",
                        args=("--api-key=plaintext",),
                    ),
                )

    def test_translation_catalogs_have_identical_keys(self):
        validate_translations()
        self.assertEqual(set(TEXT["ru"]), set(TEXT["en"]))
        self.assertIn("Supervisor", TEXT["ru"]["supervisor"])

    def test_driver_id_has_plain_language_role_transport_label(self):
        self.assertEqual(driver_display_name("ru", "acp"), "ACP · протокол агентов [acp]")
        self.assertEqual(
            driver_display_name("en", "model_json"),
            "Model API · strict JSON [model_json]",
        )
        self.assertEqual(
            driver_display_name("en", "process"),
            "CLI profile · safe one-shot process [process]",
        )
        self.assertEqual(driver_display_name("ru", "future_transport"), "future_transport")

    def test_gui_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gui-settings.json"
            remembered_project = Path(tmp) / "work"
            remembered_project.mkdir()
            expected = GuiSettings(
                locale="en",
                theme="light",
                last_project_root=str(remembered_project),
                onboarding_completed=True,
                update_catalog_url="https://updates.example/HoH-releases.signed.json",
                automatic_updates=True,
            )
            save_gui_settings(expected, path)
            actual = load_gui_settings(path)
        self.assertEqual(actual, expected)

    def test_gui_settings_clear_deleted_last_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gui-settings.json"
            expected = GuiSettings(
                locale="ru",
                theme="dark",
                last_project_root=str(Path(tmp) / "deleted-workspace"),
            )
            save_gui_settings(expected, path)

            actual = load_gui_settings(path)

        self.assertEqual(actual.last_project_root, "")
        self.assertFalse(actual.onboarding_completed)

    def test_three_roles_are_saved_and_resolve_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            catalog = {
                "agents": [{
                    "name": "codex", "available": True, "detail": "npx available",
                    "supervisor_driver": "acp", "worker_driver": "acp", "critic_driver": "acp",
                }]
            }
            snapshot = SimpleNamespace(config=HarnessConfig(), catalog=catalog)
            with patch("llm_harness.gui_services.load_project_snapshot", return_value=snapshot):
                save_project_roles(
                    root,
                    supervisor_agent="codex",
                    supervisor_model="gpt-supervisor",
                    supervisor_driver="acp",
                    worker_agent="codex",
                    worker_model="gpt-worker",
                    worker_driver="acp",
                    critic_enabled=True,
                    critic_agent="codex",
                    critic_model="gpt-critic",
                    critic_driver="acp",
                    max_attempts=4,
                )
            profile = load_role_profile(root)
            effective = load_effective_config(root)

        self.assertEqual(profile.supervisor.model, "gpt-supervisor")  # type: ignore[union-attr]
        self.assertEqual(profile.worker.model, "gpt-worker")  # type: ignore[union-attr]
        self.assertEqual(profile.critic.model, "gpt-critic")  # type: ignore[union-attr]
        self.assertEqual(profile.max_attempts, 4)  # type: ignore[union-attr]
        self.assertEqual(effective.supervisor.driver, "acp")
        self.assertEqual(effective.worker.driver, "acp")
        self.assertEqual(effective.critic.driver, "acp")

    def test_unavailable_agent_is_rejected_before_profile_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            catalog = {
                "agents": [
                    {
                        "name": "missing-agent",
                        "available": False,
                        "detail": "binary is not on PATH",
                        "supervisor_driver": "acp",
                        "worker_driver": "acp",
                        "critic_driver": "acp",
                    }
                ]
            }
            snapshot = SimpleNamespace(config=HarnessConfig(), catalog=catalog)
            with patch("llm_harness.gui_services.load_project_snapshot", return_value=snapshot):
                with self.assertRaisesRegex(ValueError, "unavailable.*binary is not on PATH"):
                    save_project_roles(
                        root,
                        supervisor_agent="missing-agent",
                        supervisor_model=None,
                        supervisor_driver="acp",
                        worker_agent="missing-agent",
                        worker_model=None,
                        worker_driver="acp",
                        critic_enabled=False,
                        critic_agent=None,
                        critic_model=None,
                        critic_driver=None,
                        max_attempts=3,
                    )
            self.assertIsNone(load_role_profile(root))

    def test_guided_settings_preserve_advanced_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            write_config(
                project_config_path(root),
                HarnessConfig(forbidden_paths=(".git/", "private/")),
            )
            save_editable_project_settings(
                root,
                EditableProjectSettings(
                    supervisor_provider="deepseek",
                    supervisor_model="deepseek-chat",
                    supervisor_api_key_env="MY_DEEPSEEK_KEY",
                    verifier_provider="stub",
                    verifier_model="verifier-stub",
                    require_embedded_python=True,
                    embedded_python_path="runtime/python/python.exe",
                    wheels_path="vendor/wheels",
                    require_tests=True,
                    worker_trust_level="patch_only",
                    max_parallel_tasks=2,
                ),
            )
            loaded = load_config(project_config_path(root))

        self.assertEqual(loaded.supervisor_model.provider, "deepseek")
        self.assertEqual(loaded.supervisor_model.api_key_env, "MY_DEEPSEEK_KEY")
        self.assertEqual(loaded.scheduler.max_parallel_tasks, 2)
        self.assertEqual(loaded.forbidden_paths, (".git/", "private/"))

    def test_environment_status_exposes_names_but_never_secret_values(self):
        config = HarnessConfig(
            supervisor_model=CloudModelEndpoint(
                provider="deepseek",
                model="deepseek-chat",
                api_key_env="MY_DEEPSEEK_KEY",
            )
        )
        with patch.dict("os.environ", {"MY_DEEPSEEK_KEY": "super-secret-value"}, clear=False):
            records = environment_variable_status(config)

        self.assertEqual(records, (("MY_DEEPSEEK_KEY", True, "Supervisor"),))
        self.assertNotIn("super-secret-value", repr(records))

    def test_environment_status_includes_a2a_credential_name_without_value(self):
        config = HarnessConfig(
            a2a_agents=(
                A2AAgentConfig(
                    "remote", "https://agent.example/card.json", auth_kind="bearer", credential_env="REMOTE_TOKEN"
                ),
            )
        )
        with patch.dict("os.environ", {"REMOTE_TOKEN": "remote-secret"}, clear=False):
            records = environment_variable_status(config)
        self.assertIn(("REMOTE_TOKEN", True, "A2A remote"), records)
        self.assertNotIn("remote-secret", repr(records))

    def test_session_credentials_exist_only_in_process_environment(self):
        config = HarnessConfig(
            supervisor_model=CloudModelEndpoint(
                provider="deepseek", model="deepseek-chat", api_key_env="TEMP_HOH_KEY"
            )
        )
        with patch.dict("os.environ", {}, clear=False):
            loaded = set_session_environment_variables(config, {"TEMP_HOH_KEY": "secret-value"})
            self.assertEqual(loaded, ("TEMP_HOH_KEY",))
            self.assertEqual(__import__("os").environ["TEMP_HOH_KEY"], "secret-value")
            self.assertNotIn("secret-value", repr(loaded))

    def test_project_json_editor_validates_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            payload = json.loads(project_config_text(root))
            payload["require_tests"] = False
            save_project_config_text(root, json.dumps(payload))

            loaded = load_config(project_config_path(root))

        self.assertFalse(loaded.require_tests)

    def test_project_json_editor_rejects_invalid_json_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            with self.assertRaisesRegex(ValueError, "Invalid project configuration JSON"):
                save_project_config_text(root, "{")
            self.assertFalse(project_config_path(root).exists())


if __name__ == "__main__":
    unittest.main()
