import inspect
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from llm_harness.audit import OUTPUT_TAIL_LINES, ProjectAuditReport, diagram_source_digest, run_project_audit
from llm_harness.domain import CommandResult
from llm_harness.config import AuditConfig
from llm_harness.language_audit import ANALYZER_RUNNERS


class AuditReportRenderingTests(unittest.TestCase):
    def _report(self, stdout: str, stderr: str) -> str:
        report = ProjectAuditReport(
            project_root=Path("."),
            command_results=(
                CommandResult(command="run tests", return_code=1, stdout=stdout, stderr=stderr),
                CommandResult(command="compile", return_code=0, stdout="quiet", stderr=""),
            ),
        )
        return report.to_markdown()

    def test_a_failing_command_shows_the_end_of_its_output(self):
        """The report used to print the exit code and nothing else.

        A release build then said "Audit failed. Package was not created." and the
        only way to learn which test broke was to re-run the command by hand.
        """
        markdown = self._report(
            "",
            """AssertionError: nothing matched
FAILED (failures=1)""",
        )

        self.assertIn("FAILED (failures=1)", markdown)
        self.assertIn("AssertionError: nothing matched", markdown)

    def test_a_command_that_passed_stays_a_single_line(self):
        markdown = self._report("", "boom")

        self.assertNotIn("quiet", markdown)

    def test_stdout_is_used_when_the_command_said_nothing_on_stderr(self):
        markdown = self._report("only stdout has it", "")

        self.assertIn("only stdout has it", markdown)

    def test_the_tail_is_bounded(self):
        markdown = self._report("", "\n".join(f"line {index}" for index in range(200)))

        self.assertNotIn("line 0\n", markdown)
        self.assertIn("line 199", markdown)
        self.assertEqual(markdown.count("line "), OUTPUT_TAIL_LINES)


class ProjectAuditTests(unittest.TestCase):
    def test_a_check_is_given_time_for_a_whole_test_suite(self):
        # A --check is routinely "run the test suite". A 120s cap reported a green
        # project as failed with exit 124 once the suite grew past two minutes.
        from llm_harness.audit import run_project_audit

        signature = inspect.signature(run_project_audit)

        self.assertGreaterEqual(signature.parameters["command_timeout_seconds"].default, 900)

    def test_audit_passes_for_clean_repo_with_required_docs_and_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            self._write_ready_docs(root)
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Initial ready project")

            report = run_project_audit(
                root,
                (f'"{sys.executable}" -c "print(\'checks passed\')"',),
            )

        self.assertTrue(report.ok, report.to_markdown())
        self.assertEqual(report.findings, ())
        self.assertEqual(report.metrics["c4_component_diagram_exists"], True)
        self.assertEqual(report.metrics["sequence_diagram_exists"], True)
        self.assertEqual(report.metrics["lifecycle_artifacts_present"], False)

    def test_audit_blocks_missing_diagrams_dirty_tree_and_missing_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs" / "architecture.md").write_text("# Architecture\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Initial incomplete project")
            (root / "README.md").write_text("# Demo\n\nchanged\n", encoding="utf-8")

            report = run_project_audit(root)

        codes = {finding.code for finding in report.findings}
        self.assertFalse(report.ok)
        self.assertIn("GIT_WORKTREE_DIRTY", codes)
        self.assertIn("C4_COMPONENT_DIAGRAM_MISSING", codes)
        self.assertIn("SEQUENCE_DIAGRAM_MISSING", codes)
        self.assertIn("VERIFICATION_COMMANDS_MISSING", codes)

    def test_audit_ignore_line_suppresses_documented_marker_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            self._write_ready_docs(root)
            (root / "docs" / "audit.md").write_text(
                "Search for TODO markers. <!-- hoh-audit: ignore-line -->\n",
                encoding="utf-8",
            )
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Initial ready project")

            report = run_project_audit(
                root,
                (f'"{sys.executable}" -c "print(\'checks passed\')"',),
            )

        self.assertTrue(report.ok, report.to_markdown())

    def test_audit_reports_timed_out_verification_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            self._write_ready_docs(root)
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Initial ready project")

            report = run_project_audit(
                root,
                (f'"{sys.executable}" -c "import time; time.sleep(2)"',),
                command_timeout_seconds=0.1,
            )

        codes = {finding.code for finding in report.findings}
        self.assertFalse(report.ok)
        self.assertEqual(report.command_results[0].return_code, 124)
        self.assertIn("VERIFICATION_COMMAND_FAILED", codes)

    def test_audit_requires_both_diagrams_and_a_render_that_is_not_stale(self):
        """A committed render is a copy, and a copy rots the moment the source moves.

        GitHub does not draw PlantUML inline, so the PNGs have to be committed. That
        makes "somebody edited the .puml and forgot to re-render" a real way for the
        architecture picture to keep describing a system that no longer exists.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            self._write_ready_docs(root)
            source = root / "docs" / "architecture-c4-component.puml"
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Ready project with rendered diagrams")

            accepted = run_project_audit(root)
            codes = {finding.code for finding in accepted.findings}
            # A .puml, a .png and a digest file in docs/ must not trip the
            # "documentation must be Markdown" rule that keeps prose diffable.
            self.assertNotIn("NON_MARKDOWN_DOCUMENTATION", codes)
            self.assertTrue(accepted.metrics["diagram_renders_current"])

            source.write_text("@startuml\ntitle edited\n@enduml\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Edit the diagram source without re-rendering")

            stale = run_project_audit(root)

        self.assertIn("C4_COMPONENT_DIAGRAM_RENDER_STALE", {finding.code for finding in stale.findings})
        self.assertFalse(stale.metrics["diagram_renders_current"])

    def test_a_render_is_not_stale_merely_because_git_changed_the_line_endings(self):
        """The same commit is CRLF on Windows and LF elsewhere.

        Hashing the bytes on disk would call a perfectly current render stale on
        every machine that did not produce it, CI included, and the fix suggested by
        the finding -- re-render -- would make it stale for the other platform
        instead. The digest is taken over normalised content on both sides.
        """
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "architecture-c4-component.puml"
            source.write_bytes(b"@startuml\r\ntitle Demo\r\n@enduml\r\n")
            windows_checkout = diagram_source_digest(source)
            source.write_bytes(b"@startuml\ntitle Demo\n@enduml\n")
            posix_checkout = diagram_source_digest(source)

        self.assertEqual(windows_checkout, posix_checkout)

    def test_audit_reports_a_missing_program_instead_of_crashing(self):
        """One unstartable check must not take the whole audit down with a traceback.

        The Windows payload build hit exactly this: a check naming a program that is
        not there raised FileNotFoundError out of the audit, so the operator saw a
        stack trace and the remaining checks never ran.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            self._write_ready_docs(root)
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Initial ready project")

            report = run_project_audit(
                root,
                ("definitely-not-installed-runner --version", f'"{sys.executable}" -c "pass"'),
            )

        self.assertFalse(report.ok)
        self.assertEqual(len(report.command_results), 2)
        self.assertEqual(report.command_results[0].return_code, 127)
        self.assertEqual(report.command_results[1].return_code, 0)
        self.assertIn("VERIFICATION_COMMAND_FAILED", {finding.code for finding in report.findings})

    def test_audit_blocks_incomplete_lifecycle_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            self._write_ready_docs(root)
            (root / "docs" / "hoh").mkdir()
            (root / "docs" / "hoh" / "project-brief.md").write_text("# Project brief\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Initial project with incomplete lifecycle")

            report = run_project_audit(
                root,
                (f'"{sys.executable}" -c "print(\'checks passed\')"',),
            )

        codes = {finding.code for finding in report.findings}
        self.assertFalse(report.ok)
        self.assertIn("LIFECYCLE_ROADMAP_MISSING", codes)
        self.assertIn("LIFECYCLE_TASKS_MISSING", codes)
        self.assertEqual(report.metrics["lifecycle_artifacts_present"], True)

    def test_audit_blocks_invalid_lifecycle_task_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            self._write_ready_docs(root)
            self._write_lifecycle_docs(root)
            tasks = root / "tasks" / "hoh"
            tasks.mkdir(parents=True)
            (tasks / "001-invalid.json").write_text('{"id": "invalid"}\n', encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Initial project with invalid lifecycle task")

            report = run_project_audit(
                root,
                (f'"{sys.executable}" -c "print(\'checks passed\')"',),
            )

        codes = {finding.code for finding in report.findings}
        self.assertFalse(report.ok)
        self.assertIn("LIFECYCLE_TASK_INVALID", codes)
        self.assertEqual(report.metrics["lifecycle_invalid_task_files_count"], 1)

    def test_audit_passes_valid_lifecycle_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            self._write_ready_docs(root)
            self._write_lifecycle_docs(root)
            self._write_lifecycle_task(root, "001-demo.json", "demo-task")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Initial project with valid lifecycle")

            report = run_project_audit(
                root,
                (f'"{sys.executable}" -c "print(\'checks passed\')"',),
            )

        self.assertTrue(report.ok, report.to_markdown())
        self.assertEqual(report.metrics["lifecycle_artifacts_present"], True)
        self.assertEqual(report.metrics["lifecycle_task_files_count"], 1)
        self.assertEqual(report.metrics["lifecycle_task_ids_count"], 1)

    def test_audit_allows_negated_marker_policy_in_lifecycle_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            self._write_ready_docs(root)
            self._write_lifecycle_docs(root)
            (root / "docs" / "hoh" / "roadmap.md").write_text(
                "# Roadmap\n\nThe artifact contains no TODO or placeholder.\n",  # hoh-audit: ignore-line
                encoding="utf-8",
            )
            (root / "project-spec.json").write_text(
                '{"definition_of_done": ["The result has no TODO or placeholder."]}\n',  # hoh-audit: ignore-line
                encoding="utf-8",
            )
            tasks = root / "tasks" / "hoh"
            tasks.mkdir(parents=True)
            (tasks / "001-demo.json").write_text(
                "{\n"
                '  "id": "demo-task",\n'
                '  "title": "Demo task",\n'
                '  "objective": "Validate lifecycle policy text.",\n'
                '  "acceptance_criteria": ["The file contains no TODO or placeholder."],\n'  # hoh-audit: ignore-line
                '  "verification_commands": ["python -c \\\"assert \'TODO\' not in \'ready\'\\\""],\n'  # hoh-audit: ignore-line
                '  "allowed_paths": [],\n'
                '  "non_goals": ["Do not add a placeholder."]\n'  # hoh-audit: ignore-line
                "}\n",
                encoding="utf-8",
            )
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Lifecycle policy requirements")

            report = run_project_audit(
                root,
                (f'"{sys.executable}" -c "print(\'checks passed\')"',),
            )

        self.assertTrue(report.ok, report.to_markdown())
        self.assertEqual(report.metrics["blocking_markers_count"], 0)

    def test_audit_still_blocks_real_marker_in_lifecycle_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            self._write_ready_docs(root)
            self._write_lifecycle_docs(root)
            self._write_lifecycle_task(root, "001-demo.json", "demo-task")
            task = root / "tasks" / "hoh" / "001-demo.json"
            task.write_text(
                task.read_text(encoding="utf-8").replace(
                    "Validate lifecycle task audit.",
                    "TODO: implement lifecycle task.",  # hoh-audit: ignore-line
                ),
                encoding="utf-8",
            )
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Incomplete lifecycle task")

            report = run_project_audit(
                root,
                (f'"{sys.executable}" -c "print(\'checks passed\')"',),
            )

        self.assertFalse(report.ok)
        self.assertIn("BLOCKING_MARKER_FOUND", {item.code for item in report.findings})

    def test_audit_blocks_marker_after_negated_policy_clause(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            self._write_ready_docs(root)
            self._write_lifecycle_docs(root)
            (root / "docs" / "hoh" / "roadmap.md").write_text(
                "# Roadmap\n\nNo TODO, but FIXME: implement the release gate.\n",  # hoh-audit: ignore-line
                encoding="utf-8",
            )
            self._write_lifecycle_task(root, "001-demo.json", "demo-task")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Partially incomplete lifecycle policy")

            report = run_project_audit(
                root,
                (f'"{sys.executable}" -c "print(\'checks passed\')"',),
            )

        self.assertFalse(report.ok)
        self.assertEqual(report.metrics["blocking_markers_count"], 1)
        self.assertIn("BLOCKING_MARKER_FOUND", {item.code for item in report.findings})

    def test_audit_includes_language_analyzer_evidence_and_blocks_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            self._write_ready_docs(root)
            source = root / "src"
            source.mkdir()
            (source / "main.py").write_text("print('ready')\n", encoding="utf-8")
            (source / "unused.py").write_text("VALUE = 1\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Project with unused source")

            report = run_project_audit(
                root,
                (f'"{sys.executable}" -c "print(\'checks passed\')"',),
                audit_config=AuditConfig(
                    language_analyzers=("python",),
                    entry_points=("src/main.py",),
                ),
            )

        self.assertFalse(report.ok)
        self.assertIn("PYTHON_UNUSED_FILE", {finding.code for finding in report.findings})
        self.assertEqual(report.analyzer_evidence[0].status, "findings")
        self.assertIn("## Language analyzers", report.to_markdown())

    def test_audit_unavailable_analyzer_policy_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.local")
            self._git(root, "config", "user.name", "Harness Test")
            self._write_ready_docs(root)
            (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Ready project")
            runner = lambda *_: (_ for _ in ()).throw(RuntimeError("engine failed"))
            with patch.dict(ANALYZER_RUNNERS, {"python": runner}):
                blocking = run_project_audit(
                    root,
                    (f'"{sys.executable}" -c "print(\'checks passed\')"',),
                    audit_config=AuditConfig(language_analyzers=("python",)),
                )
                advisory = run_project_audit(
                    root,
                    (f'"{sys.executable}" -c "print(\'checks passed\')"',),
                    audit_config=AuditConfig(
                        language_analyzers=("python",),
                        fail_on_unavailable=False,
                    ),
                )

        self.assertEqual(blocking.analyzer_evidence[0].status, "unavailable")
        self.assertIn("LANGUAGE_ANALYZER_UNAVAILABLE", {item.code for item in blocking.findings})
        self.assertTrue(advisory.ok, advisory.to_markdown())

    def _write_ready_docs(self, root: Path) -> None:
        (root / "README.md").write_text(
            "# Demo\n\n## Setup\n\nRun the documented checks.\n",
            encoding="utf-8",
        )
        docs = root / "docs"
        docs.mkdir()
        (docs / "architecture.md").write_text(
            "# Architecture\n\n"
            "![C4 component view](hoh-c4-component.png)\n\n"
            "![Main sequence](hoh-sequence.png)\n",
            encoding="utf-8",
        )
        # The diagrams are PlantUML sources with committed renders, and the audit
        # holds the two together by digest, so a fixture that claims to be ready
        # has to carry all three.
        digests = []
        for source_name, image_name in (
            ("architecture-c4-component.puml", "hoh-c4-component.png"),
            ("architecture-sequence.puml", "hoh-sequence.png"),
        ):
            source = docs / source_name
            source.write_text("@startuml\n@enduml\n", encoding="utf-8")
            (docs / image_name).write_bytes(b"\x89PNG\r\n\x1a\n")
            digests.append(f"{diagram_source_digest(source)}  {source_name}\n")
        (docs / "diagrams.sha256").write_text("".join(digests), encoding="utf-8")

    def _write_lifecycle_docs(self, root: Path) -> None:
        hoh_docs = root / "docs" / "hoh"
        hoh_docs.mkdir(parents=True, exist_ok=True)
        (hoh_docs / "project-brief.md").write_text("# Project brief\n", encoding="utf-8")
        (hoh_docs / "roadmap.md").write_text("# Roadmap\n", encoding="utf-8")

    def _write_lifecycle_task(self, root: Path, filename: str, task_id: str) -> None:
        tasks = root / "tasks" / "hoh"
        tasks.mkdir(parents=True, exist_ok=True)
        (tasks / filename).write_text(
            "{\n"
            f'  "id": "{task_id}",\n'
            '  "title": "Demo task",\n'
            '  "objective": "Validate lifecycle task audit.",\n'
            '  "acceptance_criteria": ["Audit accepts the task."],\n'
            '  "verification_commands": ["python -m unittest"],\n'
            '  "allowed_paths": [],\n'
            '  "non_goals": []\n'
            "}\n",
            encoding="utf-8",
        )

    def _git(self, repository: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)
        return completed.stdout


if __name__ == "__main__":
    unittest.main()
