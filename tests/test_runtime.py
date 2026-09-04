from pathlib import Path
import sys
import tempfile
import unittest

import os
from unittest.mock import patch

from llm_harness.runtime import (
    DISTRIBUTION_ROOT_ENV,
    RuntimeConfig,
    distribution_root,
    inspect_runtime,
)


class RuntimeTests(unittest.TestCase):
    def test_runtime_reports_missing_embedded_python_and_wheels(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = inspect_runtime(Path(tmp), RuntimeConfig(require_embedded_python=True), root=Path(tmp))

        self.assertFalse(status.ok)
        self.assertEqual(len(status.findings), 2)
        self.assertIn("Embedded Python is required but missing", status.findings[0])
        self.assertIn("Vendored wheels directory is missing", status.findings[1])

    def test_runtime_reports_empty_wheelhouse_and_non_executable_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python_path = root / "runtime/python/python.exe"
            wheels_path = root / "vendor/wheels"
            python_path.parent.mkdir(parents=True)
            wheels_path.mkdir(parents=True)
            python_path.write_text("", encoding="utf-8")

            status = inspect_runtime(root, RuntimeConfig(require_embedded_python=True), root=root)

        self.assertFalse(status.ok)
        self.assertEqual(len(status.findings), 2)
        self.assertIn("Embedded Python failed to start", status.findings[0])
        self.assertIn("contains no .whl files", status.findings[1])

    def test_runtime_passes_when_python_runs_and_wheelhouse_has_wheel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheels_path = root / "vendor/wheels"
            wheels_path.mkdir(parents=True)
            (wheels_path / "llm_harness-0.1.0-py3-none-any.whl").write_text("", encoding="utf-8")

            status = inspect_runtime(
                root,
                RuntimeConfig(require_embedded_python=True, embedded_python_path=sys.executable),
                root=root,
            )

        self.assertTrue(status.ok)
        self.assertEqual(status.findings, ())
        self.assertIsNotNone(status.python_version)
        self.assertEqual(status.wheel_count, 1)


class DistributionRootTests(unittest.TestCase):
    def test_the_installation_is_read_from_the_environment_the_launchers_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {DISTRIBUTION_ROOT_ENV: tmp}):
                self.assertEqual(distribution_root(), Path(tmp).resolve())

    def test_the_users_project_is_never_where_the_interpreter_is_looked_for(self):
        """An installed Windows user was told the interpreter they ran on was missing.

        doctor resolved runtime/python against the project being worked on, so it
        reported a failure naming a path inside the user's own repository and sent
        them to scripts/bootstrap-runtime.ps1, which ships for maintainers.
        """
        with tempfile.TemporaryDirectory() as tmp:
            installation = Path(tmp) / "installation"
            project = Path(tmp) / "project"
            wheels = installation / "vendor" / "wheels"
            wheels.mkdir(parents=True)
            (wheels / "llm_harness-0.1.0-py3-none-any.whl").write_text("", encoding="utf-8")
            project.mkdir()

            status = inspect_runtime(
                project,
                RuntimeConfig(require_embedded_python=True, embedded_python_path=sys.executable),
                root=installation,
            )

        self.assertTrue(status.ok, status.findings)
        self.assertNotIn(str(project), str(status.wheels_path))


if __name__ == "__main__":
    unittest.main()
