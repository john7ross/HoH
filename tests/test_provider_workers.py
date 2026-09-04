import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
import contextlib
import io

from llm_harness.config import HarnessConfig, WorkerConfig, load_config
from llm_harness.conformance import run_worker_conformance
from llm_harness.provider_workers import (
    CLAUDE_FORBIDDEN_ARGUMENTS,
    ClaudeCodeWorker,
    OpenClawWorker,
    probe_provider_worker,
)
from llm_harness.targets import LocalAgentTarget
from llm_harness.worker_adapters import WorkerAdapterError, create_worker_adapter


class ProviderWorkerTests(unittest.TestCase):
    def test_claude_named_adapter_passes_fake_executable_conformance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = self._fake_worker(root)
            config = HarnessConfig(
                worker=WorkerConfig(
                    type="claude_code",
                    command=sys.executable,
                    args=(str(fake),),
                    timeout_seconds=15,
                    model="test-claude",
                    max_budget_usd=1.25,
                )
            )
            report = run_worker_conformance(root / "repo", config)

        self.assertTrue(report.ok)
        self.assertEqual(report.adapter_name, "claude-code-worker")
        self.assertIn("provider=claude", report.checks[0].detail)
        self.assertIn("safety_profile=dontAsk", report.checks[0].detail)

    def test_openclaw_named_adapter_passes_fake_executable_conformance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = self._fake_worker(root)
            config = HarnessConfig(
                worker=WorkerConfig(
                    type="openclaw",
                    command=sys.executable,
                    args=(str(fake),),
                    timeout_seconds=15,
                    model="openai/test-worker",
                    thinking="low",
                )
            )
            report = run_worker_conformance(root / "repo", config)

        self.assertTrue(report.ok)
        self.assertEqual(report.adapter_name, "openclaw-worker")
        self.assertIn("provider=openclaw", report.checks[0].detail)
        self.assertIn("ephemeral-config", report.checks[0].detail)

    def test_claude_invocation_uses_safe_noninteractive_file_tools_only(self):
        worker = ClaudeCodeWorker(
            target=LocalAgentTarget("claude", "claude", ()),
            timeout_seconds=30,
            model="sonnet",
        )
        args = worker.command_args()
        joined = " ".join(args)
        self.assertIn("--permission-mode dontAsk", joined)
        self.assertIn("--safe-mode", args)
        self.assertIn("--no-session-persistence", args)
        self.assertIn("Read,Edit,Write,Glob,Grep", args)
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertFalse(any(item.casefold() in CLAUDE_FORBIDDEN_ARGUMENTS for item in ("dontAsk",)))

    def test_openclaw_invocation_is_local_json_without_delivery(self):
        worker = OpenClawWorker(
            target=LocalAgentTarget("openclaw", "openclaw", ()),
            timeout_seconds=30,
            model="openai/test",
        )
        from llm_harness.domain import WorkItem
        from llm_harness.jobs import WorkerJob

        job = WorkerJob(
            id="job-1",
            work_item=WorkItem("task", "Title", "Objective", ("Done",), ("check",)),
            target=worker.target,
            callback_token="token",
        )
        args = worker.command_args(job, Path("prompt.txt"))
        self.assertIn("--local", args)
        self.assertIn("--json", args)
        self.assertIn("--message-file", args)
        self.assertNotIn("--deliver", args)
        self.assertNotIn("--to", args)

    def test_factory_rejects_unsafe_owned_flags_and_missing_openclaw_model(self):
        with self.assertRaisesRegex(WorkerAdapterError, "unsafe flags"):
            create_worker_adapter(
                WorkerConfig(
                    type="claude_code",
                    command="claude",
                    args=("--dangerously-skip-permissions",),
                )
            )
        with self.assertRaisesRegex(WorkerAdapterError, "requires worker.model"):
            create_worker_adapter(WorkerConfig(type="openclaw"))

    def test_named_adapters_reject_direct_canonical_execution(self):
        from llm_harness.domain import WorkItem
        from llm_harness.jobs import WorkerJob

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git_init(root)
            task = WorkItem("task", "Title", "Objective", ("Done",), ("check",))
            claude = ClaudeCodeWorker(
                LocalAgentTarget("claude", "missing-command", ()),
                10,
            )
            job = WorkerJob("job", task, claude.target, "token")
            completion = claude.run_job(root, job)
        self.assertEqual(completion.error_kind, "isolation")
        self.assertIn("hoh/attempt", completion.error or "")

    def test_probe_reports_version_and_unavailable_without_running_agent_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = self._fake_worker(Path(tmp))
            config = WorkerConfig(
                type="claude_code",
                command=sys.executable,
                args=(str(fake),),
            )
            probe = probe_provider_worker(config, resolver=lambda _: sys.executable)
            missing = probe_provider_worker(
                WorkerConfig(type="openclaw", model="openai/test"),
                resolver=lambda _: None,
            )
        self.assertTrue(probe.ok)
        self.assertEqual(probe.version, "fake-worker 1.0")
        self.assertFalse(missing.available)
        self.assertIn("missing command=openclaw", missing.detail)

    def test_claude_error_is_classified_and_telemetry_contains_no_prompt(self):
        from llm_harness.domain import WorkItem
        from llm_harness.jobs import WorkerJob
        import subprocess

        events = []
        worker = ClaudeCodeWorker(
            LocalAgentTarget("claude", "claude", ()),
            10,
            event_sink=lambda direction, payload: events.append((direction, payload)),
        )
        task = WorkItem(
            "task",
            "Title",
            "TOP-SECRET-OBJECTIVE",
            ("Done",),
            ("check",),
        )
        job = WorkerJob("job", task, worker.target, "token")
        completed = subprocess.CompletedProcess(
            args=["claude"],
            returncode=1,
            stdout=json.dumps({"is_error": True, "result": "HTTP 429 rate limit"}),
            stderr="",
        )
        with patch("llm_harness.provider_workers._attempt_isolation_error", return_value=None), patch(
            "llm_harness.provider_workers.subprocess.run",
            return_value=completed,
        ):
            completion = worker.run_job(Path.cwd(), job)

        self.assertEqual(completion.error_kind, "rate_limit")
        self.assertTrue(completion.retryable)
        telemetry = json.dumps(events)
        self.assertNotIn("TOP-SECRET-OBJECTIVE", telemetry)
        self.assertIn("prompt_sha256", telemetry)
        self.assertIn("stdout_sha256", telemetry)

    def test_openclaw_uses_ephemeral_safe_config_and_redacted_telemetry(self):
        from llm_harness.domain import WorkItem
        from llm_harness.jobs import WorkerJob
        import subprocess

        events = []
        observed = {}
        worker = OpenClawWorker(
            LocalAgentTarget("openclaw", "openclaw", ()),
            10,
            model="openai/test",
            event_sink=lambda direction, payload: events.append((direction, payload)),
        )
        task = WorkItem(
            "task",
            "Title",
            "PRIVATE-OPENCLAW-PROMPT",
            ("Done",),
            ("check",),
        )
        job = WorkerJob("job", task, worker.target, "token")

        def fake_run(args, **kwargs):
            observed["message_path"] = Path(args[args.index("--message-file") + 1])
            observed["config_path"] = Path(kwargs["env"]["OPENCLAW_CONFIG_PATH"])
            observed["config"] = json.loads(
                observed["config_path"].read_text(encoding="utf-8")
            )
            observed["workspace"] = kwargs["env"]["OPENCLAW_WORKSPACE_DIR"]
            observed["offline_env"] = kwargs["env"].get("OPENCLAW_OFFLINE")
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps({"status": "ok", "meta": {"transport": "embedded"}}),
                stderr="",
            )

        with tempfile.TemporaryDirectory() as tmp, patch(
            "llm_harness.provider_workers._attempt_isolation_error",
            return_value=None,
        ), patch("llm_harness.provider_workers.subprocess.run", side_effect=fake_run):
            completion = worker.run_job(Path(tmp), job)

        self.assertIsNone(completion.error)
        self.assertFalse(observed["message_path"].exists())
        self.assertFalse(observed["config_path"].exists())
        self.assertEqual(
            observed["config"]["tools"]["allow"],
            ["read", "write", "edit", "apply_patch"],
        )
        self.assertEqual(observed["config"]["tools"]["exec"]["mode"], "deny")
        self.assertTrue(
            observed["config"]["tools"]["exec"]["applyPatch"]["workspaceOnly"]
        )
        self.assertEqual(observed["workspace"], str(Path(tmp).resolve()))
        self.assertIsNone(observed["offline_env"])
        telemetry = json.dumps(events)
        self.assertNotIn("PRIVATE-OPENCLAW-PROMPT", telemetry)
        self.assertNotIn(str(observed["message_path"]), telemetry)
        self.assertIn("<temporary-prompt-file>", telemetry)

    def test_doctor_exposes_named_adapter_safety_profile(self):
        from llm_harness.doctor import run_doctor
        from llm_harness.runtime import RuntimeConfig

        config = HarnessConfig(
            runtime=RuntimeConfig(require_embedded_python=False),
            worker=WorkerConfig(type="claude_code", command="claude"),
        )
        report = run_doctor(
            Path.cwd(),
            config,
            agent_resolver=lambda _: "C:/fake/claude.exe",
        )
        check = next(item for item in report.checks if item.name == "worker")
        self.assertTrue(check.ok)
        self.assertIn("provider=claude", check.detail)
        self.assertIn("dontAsk-file-tools-only", check.detail)

    def test_config_parses_provider_worker_controls_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_path = root / "claude.toml"
            claude_path.write_text(
                """
[worker]
type = "claude_code"
model = "sonnet"
max_budget_usd = 2.5
timeout_seconds = 40
""",
                encoding="utf-8",
            )
            openclaw_path = root / "openclaw.toml"
            openclaw_path.write_text(
                """
[worker]
type = "openclaw"
model = "openai/gpt-test"
thinking = "high"
""",
                encoding="utf-8",
            )
            claude = load_config(claude_path).worker
            openclaw = load_config(openclaw_path).worker
        self.assertEqual(claude.command, "claude")
        self.assertEqual(claude.args, ())
        self.assertEqual(claude.model, "sonnet")
        self.assertEqual(claude.max_budget_usd, 2.5)
        self.assertEqual(openclaw.command, "openclaw")
        self.assertEqual(openclaw.model, "openai/gpt-test")
        self.assertEqual(openclaw.thinking, "high")

    def test_worker_smoke_cli_is_offline_by_default_and_live_is_explicit(self):
        from llm_harness.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = self._fake_worker(root)
            escaped_python = sys.executable.replace("\\", "\\\\")
            escaped_fake = str(fake).replace("\\", "\\\\")
            config_path = root / "claude.toml"
            config_path.write_text(
                f"""
[worker]
type = "claude_code"
command = "{escaped_python}"
args = ["{escaped_fake}"]
model = "test"
timeout_seconds = 15
""",
                encoding="utf-8",
            )
            offline_stdout = io.StringIO()
            with contextlib.redirect_stdout(offline_stdout):
                offline_code = main(
                    ["worker-smoke", "--config", str(config_path), "--json"]
                )
            live_stdout = io.StringIO()
            with contextlib.redirect_stdout(live_stdout):
                live_code = main(
                    [
                        "worker-smoke",
                        "--config",
                        str(config_path),
                        "--live",
                        "--json",
                    ]
                )
            offline = json.loads(offline_stdout.getvalue())
            live = json.loads(live_stdout.getvalue())

        self.assertEqual(offline_code, 0)
        self.assertTrue(offline["ok"])
        self.assertFalse(offline["data"]["live"])
        self.assertNotIn("conformance", offline["data"])
        self.assertEqual(live_code, 0)
        self.assertTrue(live["ok"])
        self.assertTrue(live["data"]["conformance"]["ok"])

    def _fake_worker(self, root: Path) -> Path:
        path = root / "fake_provider_worker.py"
        path.write_text(
            textwrap.dedent(
                """
                import json
                import os
                from pathlib import Path
                import sys

                args = sys.argv[1:]
                if "--version" in args:
                    print("fake-worker 1.0")
                    raise SystemExit(0)

                cwd = Path.cwd()
                branch = os.popen("git branch --show-current").read().strip()
                if not branch.startswith("hoh/attempt/"):
                    print("not isolated", file=sys.stderr)
                    raise SystemExit(20)

                if "agent" in args:
                    required = {"--local", "--json", "--message-file", "--model", "--timeout"}
                    if not required.issubset(args) or "--deliver" in args or "--to" in args:
                        print("unsafe openclaw args", file=sys.stderr)
                        raise SystemExit(21)
                    message_path = Path(args[args.index("--message-file") + 1])
                    prompt = message_path.read_text(encoding="utf-8")
                    config_path = Path(os.environ["OPENCLAW_CONFIG_PATH"])
                    config = json.loads(config_path.read_text(encoding="utf-8"))
                    if os.environ.get("OPENCLAW_WORKSPACE_DIR") != str(cwd.resolve()):
                        print("workspace mismatch", file=sys.stderr)
                        raise SystemExit(22)
                    if config["tools"]["allow"] != ["read", "write", "edit", "apply_patch"]:
                        print("unsafe tool allowlist", file=sys.stderr)
                        raise SystemExit(23)
                    if config["tools"]["exec"]["mode"] != "deny":
                        print("exec enabled", file=sys.stderr)
                        raise SystemExit(24)
                    Path("HARNESS_DEMO.md").write_text("openclaw candidate\\n", encoding="utf-8")
                    print(json.dumps({"status": "ok", "meta": {"transport": "embedded", "durationMs": 5}}))
                else:
                    prompt = sys.stdin.read()
                    required = {
                        "-p", "--output-format", "--safe-mode", "--permission-mode",
                        "--tools", "--allowedTools", "--no-session-persistence",
                    }
                    if not required.issubset(args):
                        print("missing safe claude args", file=sys.stderr)
                        raise SystemExit(25)
                    if "--dangerously-skip-permissions" in args:
                        print("unsafe claude args", file=sys.stderr)
                        raise SystemExit(26)
                    if args[args.index("--permission-mode") + 1] != "dontAsk":
                        print("unsafe permission mode", file=sys.stderr)
                        raise SystemExit(27)
                    Path("HARNESS_DEMO.md").write_text("claude candidate\\n", encoding="utf-8")
                    print(json.dumps({
                        "is_error": False,
                        "result": "candidate ready",
                        "session_id": "fake-session",
                        "duration_ms": 5,
                        "num_turns": 1,
                    }))

                if "low-trust local worker" not in prompt:
                    print("missing HoH prompt", file=sys.stderr)
                    raise SystemExit(28)
                """
            ),
            encoding="utf-8",
        )
        return path

    def _git_init(self, root: Path) -> None:
        import subprocess

        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
