from pathlib import Path
import unittest

from llm_harness.frozen_entry import plan_frozen_invocation


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FrozenEntryTests(unittest.TestCase):
    def test_double_click_opens_the_gui(self):
        invocation = plan_frozen_invocation([])

        self.assertIsNone(invocation.python_code)
        self.assertIsNone(invocation.module)
        self.assertEqual(invocation.cli_args, ("gui",))

    def test_module_form_reaches_the_cli(self):
        """Every GUI operation and the scheduler run "-m llm_harness <subcommand>".

        The bundle used to hand those words straight to argparse, which rejected
        "llm_harness" as a subcommand, so on macOS the buttons did nothing.
        """
        invocation = plan_frozen_invocation(
            ["-m", "llm_harness", "doctor", "--project-root", "/tmp"]
        )

        self.assertIsNone(invocation.python_code)
        self.assertEqual(invocation.cli_args, ("doctor", "--project-root", "/tmp"))

    def test_code_form_is_carried_out_of_the_argument_list(self):
        invocation = plan_frozen_invocation(["-c", "print(1)"])

        self.assertEqual(invocation.python_code, "print(1)")
        self.assertEqual(invocation.cli_args, ())

    def test_subcommands_pass_through_unchanged(self):
        invocation = plan_frozen_invocation(["doctor", "--config", "d.toml"])

        self.assertIsNone(invocation.python_code)
        self.assertEqual(invocation.cli_args, ("doctor", "--config", "d.toml"))

    def test_incomplete_interpreter_options_are_reported(self):
        for argv in (["-c"], ["-m"]):
            with self.subTest(argv=argv):
                with self.assertRaises(ValueError):
                    plan_frozen_invocation(argv)

    def test_other_modules_are_run_as_modules(self):
        """package-portable runs "-m unittest" and "-m compileall" this way.

        Reporting them as unsupported would be a second wrong answer; running them
        reports honestly when the bundle does not carry the module.
        """
        invocation = plan_frozen_invocation(["-m", "unittest", "discover", "-s", "tests"])

        self.assertIsNone(invocation.python_code)
        self.assertEqual(invocation.module, "unittest")
        self.assertEqual(invocation.cli_args, ("discover", "-s", "tests"))

    def test_the_bundle_entry_point_uses_this_plan(self):
        """The entry point is not imported by the suite, so read it instead."""
        entry = (PROJECT_ROOT / "packaging" / "macos" / "hoh_gui.py").read_text(encoding="utf-8")

        self.assertIn("plan_frozen_invocation", entry)
        self.assertIn("invocation.python_code", entry)
        self.assertIn("invocation.module", entry)
        self.assertIn("invocation.cli_args", entry)


if __name__ == "__main__":
    unittest.main()
