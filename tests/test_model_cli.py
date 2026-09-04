import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm_harness.cli import main
from llm_harness.domain import ModelInvocationEvidence
from llm_harness.lifecycle import ProjectSpec, ProjectTaskSpec


class ModelCliTests(unittest.TestCase):
    def test_requirements_plan_uses_supervisor_model_and_journals_only_evidence(self):
        generated = ProjectSpec(
            id="generated",
            title="Generated",
            goal="Ship",
            customer="Customer",
            business_requirements=("Works",),
            definition_of_done=("Tests pass",),
            tasks=(
                ProjectTaskSpec(
                    id="task-1",
                    title="Build",
                    objective="Build",
                    acceptance_criteria=("Works",),
                    verification_commands=("python -m unittest",),
                ),
            ),
        )
        evidence = ModelInvocationEvidence(
            provider="openai",
            model="test",
            endpoint="/responses",
            request_id="req-1",
            attempts=1,
            latency_ms=3,
            request_sha256="a" * 64,
            response_sha256="b" * 64,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            requirements = root / "requirements.md"
            requirements.write_text("PRIVATE REQUIREMENT BODY", encoding="utf-8")
            state = root / "state"
            stdout = io.StringIO()
            with patch(
                "llm_harness.supervisor_adapters.generate_project_spec",
                return_value=(generated, evidence),
            ) as planner, contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "project-plan",
                        "--project-root",
                        str(root),
                        "--state-root",
                        str(state),
                        "--requirements",
                        str(requirements),
                        "--write",
                        "--json",
                    ]
                )
            journal_text = (state / "interaction-journal.jsonl").read_text(encoding="utf-8")
            payload = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertEqual(planner.call_args.args[0], "PRIVATE REQUIREMENT BODY")
        self.assertEqual(payload["data"]["model_evidence"]["request_id"], "req-1")
        self.assertNotIn("PRIVATE REQUIREMENT BODY", journal_text)
        self.assertIn("request_sha256", journal_text)

    def test_model_smoke_is_offline_by_default_and_live_stub_fails(self):
        offline = io.StringIO()
        with contextlib.redirect_stdout(offline):
            offline_code = main(["model-smoke", "--role", "supervisor", "--json"])
        live = io.StringIO()
        with contextlib.redirect_stdout(live):
            live_code = main(["model-smoke", "--role", "supervisor", "--live", "--json"])

        self.assertEqual(offline_code, 0)
        self.assertTrue(json.loads(offline.getvalue())["ok"])
        self.assertEqual(live_code, 1)
        self.assertEqual(json.loads(live.getvalue())["error"].split(";")[0].startswith("Model role"), True)


if __name__ == "__main__":
    unittest.main()
