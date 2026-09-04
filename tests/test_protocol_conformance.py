from pathlib import Path
import tempfile
import unittest

from llm_harness.protocol_conformance import run_protocol_conformance, validate_protocol_assets


class ProtocolConformanceTests(unittest.TestCase):
    def test_distribution_assets_and_external_review_smoke_pass(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            report = run_protocol_conformance(root, Path(tmp) / "repo")

        self.assertTrue(report.ok)
        self.assertEqual(report.assets_checked, 20)
        self.assertTrue(report.canonical_git_clean)
        self.assertEqual(validate_protocol_assets(root), 20)


if __name__ == "__main__":
    unittest.main()
