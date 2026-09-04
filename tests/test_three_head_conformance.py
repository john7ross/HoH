from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import json
import tempfile
import unittest

from llm_harness.three_head_conformance import run_three_head_conformance
from llm_harness.cli import main


class ThreeHeadConformanceTests(unittest.TestCase):
    def test_approve_reject_rework_and_escalate_scenarios(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_three_head_conformance(Path(tmp) / "scenarios")

        self.assertTrue(report.ok)
        self.assertTrue(report.approve)
        self.assertTrue(report.reject)
        self.assertTrue(report.rework)
        self.assertTrue(report.escalate)
        self.assertTrue(report.critic_disabled)
        self.assertTrue(report.git_clean)

    def test_cli_returns_machine_readable_scenario_report(self):
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["three-head-conformance", "--json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["approve"])
        self.assertTrue(payload["data"]["rework"])
        self.assertTrue(payload["data"]["escalate"])
        self.assertTrue(payload["data"]["critic_disabled"])


if __name__ == "__main__":
    unittest.main()
