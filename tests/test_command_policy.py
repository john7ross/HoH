import os
from pathlib import Path
import sys
import tempfile
import unittest

from llm_harness.command_policy import (
    CommandPolicy,
    CommandPolicyError,
    OperatorCommandPolicy,
)
from llm_harness.git_ops import run_verification_commands


def _python(script: str) -> str:
    return f'"{sys.executable}" -c "{script}"'


class CommandPolicyTests(unittest.TestCase):
    def test_allowed_runner_is_parsed_into_argv(self):
        policy = CommandPolicy()

        self.assertEqual(
            policy.resolve('python -c "print(1)"'),
            ("python", "-c", "print(1)"),
        )

    def test_shell_interpreters_are_always_refused(self):
        policy = CommandPolicy(allow_unlisted_commands=True)

        for command in ("sh -c 'rm -rf /'", "bash -lc whoami", "cmd /c dir", "powershell -c ls"):
            with self.assertRaises(CommandPolicyError):
                policy.resolve(command)

    def test_shell_operators_are_refused_instead_of_becoming_arguments(self):
        policy = CommandPolicy(allow_unlisted_commands=True)

        for command in (
            "python -c pass && curl https://example.invalid",
            "python -c pass | tee out.txt",
            "python -c pass ; rm -rf .",
            "python -c pass > out.txt",
        ):
            with self.assertRaisesRegex(CommandPolicyError, "shell operators"):
                policy.resolve(command)

    def test_shell_operators_are_refused_even_without_spaces_around_them(self):
        """Only spaced operators were caught, so a glued one ran as one dead argument.

        This is how the Windows payload build wrote its test check for months:
        "set PYTHONPATH=src&& python -m unittest" was accepted, handed to a program
        called "set", and the tests it named never ran.
        """
        policy = OperatorCommandPolicy()

        for command in (
            r"set PYTHONPATH=src&& python -m unittest discover -s tests",
            "python -c pass&&curl https://example.invalid",
            "python -c pass|tee out.txt",
            "python -m unittest discover -s tests 2>&1",
        ):
            with self.assertRaisesRegex(CommandPolicyError, "shell operators"):
                policy.resolve(command)

    def test_an_ampersand_inside_a_quoted_argument_is_not_a_shell_operator(self):
        policy = OperatorCommandPolicy()

        argv = policy.resolve('python -c pass --url "https://host/x?a=1&b=2"')

        self.assertIn("https://host/x?a=1&b=2", argv)

    def test_unlisted_program_is_refused_by_default(self):
        with self.assertRaisesRegex(CommandPolicyError, "not in the allowed command list"):
            CommandPolicy().resolve("curl https://example.invalid/payload")

    def test_operator_policy_allows_unlisted_programs_but_still_no_shell(self):
        policy = OperatorCommandPolicy()

        self.assertEqual(policy.resolve("git status --short"), ("git", "status", "--short"))
        with self.assertRaises(CommandPolicyError):
            policy.resolve("sh -c 'echo hi'")

    def test_unparsable_and_empty_commands_are_refused(self):
        policy = CommandPolicy(allow_unlisted_commands=True)

        with self.assertRaises(CommandPolicyError):
            policy.resolve("   ")
        with self.assertRaises(CommandPolicyError):
            policy.resolve('python -c "unterminated')

    def test_secret_environment_is_not_handed_to_verification_commands(self):
        policy = CommandPolicy()
        source = {
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-secret",
            "HOH_TELEGRAM_BOT_TOKEN": "123:abc",
            "DEEPSEEK_SECRET": "s",
            "DB_PASSWORD": "p",
            "PROJECT_NAME": "hoh",
        }

        child = policy.child_environment(source)

        self.assertEqual(set(child), {"PATH", "PROJECT_NAME"})
        self.assertEqual(
            set(CommandPolicy(inherit_secret_environment=True).child_environment(source)),
            set(source),
        )


class VerificationExecutionTests(unittest.TestCase):
    def test_shell_metacharacters_do_not_reach_a_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "pwned.txt"
            command = (
                f'{_python("print(1)")} && '
                f'"{sys.executable}" -c "open(r\'{marker}\', \'w\').close()"'
            )

            results = run_verification_commands(root, (command,))

            self.assertEqual(len(results), 1)
            self.assertFalse(results[0].ok)
            self.assertIn("shell operators", results[0].stderr)
            self.assertFalse(marker.exists())

    def test_refused_command_reports_a_failure_instead_of_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = run_verification_commands(Path(tmp), ("curl https://example.invalid",))

            self.assertEqual(results[0].return_code, 126)
            self.assertIn("not in the allowed command list", results[0].stderr)

    def test_allow_listed_command_still_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = run_verification_commands(Path(tmp), (_python("print('ok')"),))

            self.assertTrue(results[0].ok, results[0].stderr)
            self.assertIn("ok", results[0].stdout)

    def test_provider_credentials_are_not_visible_to_the_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HOH_TEST_OPENAI_API_KEY"] = "sk-should-not-leak"
            try:
                results = run_verification_commands(
                    Path(tmp),
                    (_python("import os; print(os.environ.get('HOH_TEST_OPENAI_API_KEY', 'absent'))"),),
                )
            finally:
                del os.environ["HOH_TEST_OPENAI_API_KEY"]

            self.assertTrue(results[0].ok, results[0].stderr)
            self.assertIn("absent", results[0].stdout)

    def test_non_ascii_command_output_survives_decoding(self):
        """Write the bytes, do not print them.

        print() encodes through the child's stdout encoding, which on Windows is
        the ANSI code page: cp1251 on a Russian machine, where this passed, and
        cp1252 on the CI runner, where the child died of UnicodeEncodeError before
        HoH decoded anything. What is under test here is the decoding, so the child
        emits UTF-8 bytes and the machine's code page stops mattering.
        """
        script = "import sys; sys.stdout.buffer.write('проверка пройдена'.encode('utf-8'))"
        with tempfile.TemporaryDirectory() as tmp:
            results = run_verification_commands(Path(tmp), (_python(script),))

            self.assertTrue(results[0].ok, results[0].stderr)
            self.assertIn("проверка пройдена", results[0].stdout)

    def test_the_running_interpreter_is_allowed_under_any_name(self):
        """A frozen build is its own interpreter, and it is not planner-provided.

        In HoH.app sys.executable is .../MacOS/HoH, so the fixtures that verify
        their work by running the interpreter were refused by the allow-list and
        protocol-conformance failed from an installed application while passing
        from a source checkout.
        """
        policy = CommandPolicy(allowed_commands=())

        argv = policy.resolve(_python("pass"))

        self.assertEqual(argv[0], sys.executable)

    def test_another_program_sharing_the_name_is_still_refused(self):
        interpreter_name = Path(sys.executable).name
        policy = CommandPolicy(allowed_commands=())

        with self.assertRaises(CommandPolicyError):
            policy.resolve(f'"{Path(tempfile.gettempdir()) / interpreter_name}" --version')

    def test_missing_executable_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = run_verification_commands(
                Path(tmp),
                ("pytest --version",),
                policy=CommandPolicy(allowed_commands=("definitely-not-installed-runner",)),
            )

            self.assertEqual(results[0].return_code, 126)


if __name__ == "__main__":
    unittest.main()
