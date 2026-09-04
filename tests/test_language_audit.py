from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

from llm_harness.language_audit import (
    ANALYZER_RUNNERS,
    run_language_analyzers,
)


class LanguageAuditTests(unittest.TestCase):
    def test_python_reports_unreachable_code_unused_private_declaration_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "src" / "app.py"
            unused = root / "src" / "_unused.py"
            app.parent.mkdir()
            app.write_text(
                "def _unused_helper():\n"
                "    return 1\n\n"
                "def main():\n"
                "    return 0\n"
                "    print('unreachable')\n",
                encoding="utf-8",
            )
            unused.write_text("VALUE = 1\n", encoding="utf-8")

            evidence = run_language_analyzers(
                root,
                (app, unused),
                analyzers=("python",),
                entry_points=("src/app.py",),
            )[0]

        codes = {finding.code for finding in evidence.findings}
        self.assertEqual(evidence.status, "findings")
        self.assertIn("PYTHON_UNREACHABLE_CODE", codes)
        self.assertIn("PYTHON_UNUSED_PRIVATE_DECLARATION", codes)
        self.assertIn("PYTHON_UNUSED_FILE", codes)

    def test_python_import_graph_keeps_reachable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "src" / "app.py"
            used = root / "src" / "pkg" / "used.py"
            used.parent.mkdir(parents=True)
            app.write_text("from pkg import used\nprint(used.VALUE)\n", encoding="utf-8")
            used.write_text("VALUE = 1\n", encoding="utf-8")

            evidence = run_language_analyzers(
                root,
                (app, used),
                analyzers=("python",),
                entry_points=("src/app.py",),
            )[0]

        self.assertEqual(evidence.status, "passed")
        self.assertEqual(evidence.findings, ())

    def test_python_private_declaration_used_from_another_module_is_not_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "src" / "app.py"
            helper = root / "src" / "helper.py"
            app.parent.mkdir()
            app.write_text("from helper import _shared\nprint(_shared())\n", encoding="utf-8")
            helper.write_text("def _shared():\n    return 1\n", encoding="utf-8")

            evidence = run_language_analyzers(
                root,
                (app, helper),
                analyzers=("python",),
                entry_points=("src/app.py",),
            )[0]

        self.assertEqual(evidence.status, "passed")

    def test_typescript_reports_unused_declaration_and_unreachable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "src" / "index.ts"
            used = root / "src" / "used.ts"
            unused = root / "src" / "unused.ts"
            index.parent.mkdir()
            index.write_text(
                "import { value } from './used';\n"
                "const abandoned = 1;\n"
                "console.log(value);\n",
                encoding="utf-8",
            )
            used.write_text("export const value = 1;\n", encoding="utf-8")
            unused.write_text("export const unused = 2;\n", encoding="utf-8")

            evidence = run_language_analyzers(
                root,
                (index, used, unused),
                analyzers=("typescript",),
                entry_points=("src/index.ts",),
            )[0]

        codes = {finding.code for finding in evidence.findings}
        self.assertEqual(evidence.status, "findings")
        self.assertIn("TYPESCRIPT_UNUSED_DECLARATION", codes)
        self.assertIn("TYPESCRIPT_UNUSED_FILE", codes)
        self.assertNotIn("src/used.ts", {finding.path for finding in evidence.findings})

    def test_javascript_import_graph_and_dead_declaration_are_analyzed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "src" / "index.js"
            used = root / "src" / "used.js"
            unused = root / "src" / "unused.js"
            index.parent.mkdir()
            index.write_text(
                "const { value } = require('./used');\n"
                "function abandoned() { return 1; }\n"
                "console.log(value);\n",
                encoding="utf-8",
            )
            used.write_text("export const value = 1;\n", encoding="utf-8")
            unused.write_text("export const unused = 2;\n", encoding="utf-8")

            evidence = run_language_analyzers(
                root,
                (index, used, unused),
                analyzers=("javascript",),
                entry_points=("src/index.js",),
            )[0]

        codes = {finding.code for finding in evidence.findings}
        self.assertEqual(evidence.status, "findings")
        self.assertIn("JAVASCRIPT_UNUSED_DECLARATION", codes)
        self.assertIn("JAVASCRIPT_UNUSED_FILE", codes)
        self.assertNotIn("src/used.js", {finding.path for finding in evidence.findings})

    def test_missing_language_and_runner_failure_have_explicit_evidence_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.py"
            source.write_text("print('ok')\n", encoding="utf-8")
            unsupported = run_language_analyzers(
                root, (source,), analyzers=("typescript",)
            )[0]
            with patch.dict(
                ANALYZER_RUNNERS,
                {"python": lambda *_: (_ for _ in ()).throw(RuntimeError("engine failed"))},
            ):
                unavailable = run_language_analyzers(
                    root, (source,), analyzers=("python",)
                )[0]

        self.assertEqual(unsupported.status, "unsupported")
        self.assertEqual(unavailable.status, "unavailable")
        self.assertIn("engine failed", unavailable.detail)

    def test_exclude_paths_are_removed_before_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated = root / "generated" / "_dead.py"
            generated.parent.mkdir()
            generated.write_text("def _unused():\n    return 1\n", encoding="utf-8")

            evidence = run_language_analyzers(
                root,
                (generated,),
                analyzers=("python",),
                exclude_paths=("generated/**",),
            )[0]

        self.assertEqual(evidence.status, "unsupported")
        self.assertEqual(evidence.files_analyzed, 0)


if __name__ == "__main__":
    unittest.main()
