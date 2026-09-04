from pathlib import Path
import sys
import tempfile
import unittest

from llm_harness.git_ops import run_verification_commands


class GitOpsTests(unittest.TestCase):
    def test_run_verification_commands_returns_command_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = run_verification_commands(
                Path(tmp),
                (f'"{sys.executable}" -c "print(\'ok\')"',),
            )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ok)
        self.assertIn("ok", results[0].stdout)

    def test_run_verification_commands_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = run_verification_commands(
                Path(tmp),
                (f'"{sys.executable}" -c "import time; time.sleep(2)"',),
                timeout_seconds=0.1,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].return_code, 124)
        self.assertIn("timed out", results[0].stderr)


if __name__ == "__main__":
    unittest.main()
