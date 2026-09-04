from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

from support import write_architecture_docs


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LanguageAuditEndToEndTests(unittest.TestCase):
    def test_disposable_project_cli_reports_python_and_typescript_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "audit-e2e@example.local")
            self._git(root, "config", "user.name", "Audit E2E")
            (root / "README.md").write_text("# Disposable audit project\n", encoding="utf-8")
            write_architecture_docs(root / "docs")
            source = root / "src"
            source.mkdir()
            (source / "main.py").write_text("print('main')\n", encoding="utf-8")
            (source / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")
            (source / "index.ts").write_text("console.log('main');\n", encoding="utf-8")
            (source / "orphan.ts").write_text("export const value = 1;\n", encoding="utf-8")
            config = root / "harness.toml"
            config.write_text(
                "[audit]\n"
                'language_analyzers = ["python", "typescript"]\n'
                'entry_points = ["src/main.py", "src/index.ts"]\n',
                encoding="utf-8",
            )
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Disposable project")

            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "llm_harness",
                    "audit",
                    "--project-root",
                    str(root),
                    "--config",
                    str(config),
                    "--check",
                    f'"{sys.executable}" -c "raise SystemExit(0)"',
                    "--json",
                ],
                cwd=PROJECT_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 1, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(
            [item["status"] for item in payload["data"]["language_analyzers"]],
            ["findings", "findings"],
        )
        codes = {item["code"] for item in payload["data"]["findings"]}
        self.assertIn("PYTHON_UNUSED_FILE", codes)
        self.assertIn("TYPESCRIPT_UNUSED_FILE", codes)

    def _git(self, repository: Path, *args: str) -> None:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)


if __name__ == "__main__":
    unittest.main()
