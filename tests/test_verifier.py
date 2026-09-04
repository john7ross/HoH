import unittest

from llm_harness.domain import WorkItem, WorkerPatch
from llm_harness.trust import WorkerTrustLevel, WorkerTrustPolicy
from llm_harness.verifier import (
    PolicyVerifier,
    changed_files_from_patch,
    documentation_paths,
    scan_patch,
)


def _scoped_work_item(task_id: str = "task-scope", allowed_paths: tuple[str, ...] = ("src/",)) -> WorkItem:
    return WorkItem(
        id=task_id,
        title="Scoped change",
        objective="Only the declared paths may change.",
        acceptance_criteria=("Scope is enforced.",),
        verification_commands=("python -c \"print('ok')\"",),
        allowed_paths=allowed_paths,
    )


class VerifierTests(unittest.TestCase):
    def test_changed_files_from_patch_extracts_targets(self):
        patch = (
            "diff --git a/old.txt b/new.txt\n"
            "--- a/old.txt\n"
            "+++ b/new.txt\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )

        self.assertEqual(changed_files_from_patch(patch), ("old.txt", "new.txt"))

    def test_policy_verifier_blocks_forbidden_paths(self):
        work_item = WorkItem(
            id="task-1",
            title="Unsafe change",
            objective="Try to change git internals.",
            acceptance_criteria=("Forbidden paths are rejected.",),
            verification_commands=("python -c \"print('ok')\"",),
            allowed_paths=("*",),
        )
        worker_patch = WorkerPatch(
            worker_name="stub",
            work_item_id="task-1",
            patch="diff --git a/.git/config b/.git/config\n--- a/.git/config\n+++ b/.git/config\n",
        )

        report = PolicyVerifier().verify_patch_before_apply(work_item, worker_patch)

        self.assertFalse(report.ok)
        self.assertEqual(report.metrics["forbidden_paths_touched_count"], 1)

    def test_allowed_paths_require_path_boundary_match(self):
        work_item = WorkItem(
            id="task-boundary",
            title="Path boundary",
            objective="Only src/app is in scope.",
            acceptance_criteria=("Outside paths are rejected.",),
            verification_commands=("python -c \"print('ok')\"",),
            allowed_paths=("src/app",),
        )
        worker_patch = WorkerPatch(
            worker_name="worker",
            work_item_id="task-boundary",
            patch=(
                "diff --git a/src/application.py b/src/application.py\n"
                "--- /dev/null\n"
                "+++ b/src/application.py\n"
            ),
        )

        report = PolicyVerifier().verify_patch_before_apply(work_item, worker_patch)

        self.assertFalse(report.ok)
        self.assertIn("outside allowed paths", " ".join(report.findings))

    def test_policy_verifier_blocks_parent_directory_escape_paths(self):
        work_item = _scoped_work_item("task-escape", allowed_paths=("*",))
        worker_patch = WorkerPatch(
            worker_name="worker",
            work_item_id="task-escape",
            patch=(
                "diff --git a/../escape.txt b/../escape.txt\n"
                "--- /dev/null\n"
                "+++ b/../escape.txt\n"
            ),
        )

        report = PolicyVerifier().verify_patch_before_apply(work_item, worker_patch)

        self.assertFalse(report.ok)
        self.assertIn("unsafe repository paths", " ".join(report.findings))

    def test_trust_policy_metrics_are_reported(self):
        work_item = _scoped_work_item("task-2", allowed_paths=("file.txt",))
        worker_patch = WorkerPatch(
            worker_name="stub",
            work_item_id="task-2",
            patch="diff --git a/file.txt b/file.txt\n--- /dev/null\n+++ b/file.txt\n",
        )

        report = PolicyVerifier(
            trust_policy=WorkerTrustPolicy(WorkerTrustLevel.PATCH_ONLY)
        ).verify_patch_before_apply(work_item, worker_patch)

        self.assertTrue(report.ok)
        self.assertEqual(report.metrics["worker_trust_level"], "patch_only")
        self.assertFalse(report.metrics["worker_can_create_branch"])
        self.assertFalse(report.metrics["worker_can_commit"])


class DocumentationPathTests(unittest.TestCase):
    def test_documentation_paths_are_measured_from_the_patch(self):
        changed = (
            "src/app.py",
            "docs/guide.md",
            "README.ru.md",
            "CHANGELOG",
            "src/fixtures/sample.txt",
            "deep/nested/notes.md",
        )

        self.assertEqual(
            documentation_paths(changed),
            ("docs/guide.md", "README.ru.md", "CHANGELOG", "deep/nested/notes.md"),
        )

    def test_code_only_patch_reports_no_documentation(self):
        self.assertEqual(documentation_paths(("src/app.py", "tests/test_app.py")), ())


class PatchScanTests(unittest.TestCase):
    def test_quoted_non_ascii_header_is_decoded(self):
        patch = (
            'diff --git "a/\\321\\201\\320\\265\\320\\272\\321\\200\\320\\265\\321\\202.yml" '
            '"b/\\321\\201\\320\\265\\320\\272\\321\\200\\320\\265\\321\\202.yml"\n'
            "new file mode 100644\n"
            "--- /dev/null\n"
            '+++ "b/\\321\\201\\320\\265\\320\\272\\321\\200\\320\\265\\321\\202.yml"\n'
            "@@ -0,0 +1 @@\n"
            "+secret\n"
        )

        scan = scan_patch(patch)

        self.assertEqual(scan.files, ("секрет.yml",))
        self.assertEqual(scan.problems, ())

    def test_non_ascii_file_outside_allowed_paths_is_rejected(self):
        patch = (
            'diff --git "a/\\321\\201\\320\\265\\320\\272\\321\\200\\320\\265\\321\\202.yml" '
            '"b/\\321\\201\\320\\265\\320\\272\\321\\200\\320\\265\\321\\202.yml"\n'
            "new file mode 100644\n"
            "--- /dev/null\n"
            '+++ "b/\\321\\201\\320\\265\\320\\272\\321\\200\\320\\265\\321\\202.yml"\n'
            "@@ -0,0 +1 @@\n"
            "+secret\n"
        )

        report = PolicyVerifier().verify_patch_before_apply(
            _scoped_work_item(), WorkerPatch("worker", "task-scope", patch)
        )

        self.assertFalse(report.ok)
        self.assertIn("outside allowed paths", " ".join(report.findings))

    def test_unified_diff_without_git_header_is_still_scoped(self):
        patch = "--- a/etc/passwd\n+++ b/etc/passwd\n@@ -1 +1 @@\n-old\n+new\n"

        scan = scan_patch(patch)
        report = PolicyVerifier().verify_patch_before_apply(
            _scoped_work_item(), WorkerPatch("worker", "task-scope", patch)
        )

        self.assertEqual(scan.files, ("etc/passwd",))
        self.assertFalse(report.ok)
        self.assertIn("outside allowed paths", " ".join(report.findings))

    def test_unreadable_patch_fails_closed(self):
        report = PolicyVerifier().verify_patch_before_apply(
            _scoped_work_item(), WorkerPatch("worker", "task-scope", "this is not a patch at all\n")
        )

        self.assertFalse(report.ok)
        self.assertIn("could not be parsed", " ".join(report.findings))
        self.assertEqual(report.metrics["patch_parse_problems_count"], 1)

    def test_rename_reports_both_sides(self):
        patch = (
            "diff --git a/src/old.py b/other/new.py\n"
            "similarity index 100%\n"
            "rename from src/old.py\n"
            "rename to other/new.py\n"
        )

        scan = scan_patch(patch)
        report = PolicyVerifier().verify_patch_before_apply(
            _scoped_work_item(), WorkerPatch("worker", "task-scope", patch)
        )

        self.assertEqual(scan.files, ("src/old.py", "other/new.py"))
        self.assertFalse(report.ok)
        self.assertIn("other/new.py", " ".join(report.findings))

    def test_binary_patch_paths_are_read(self):
        patch = (
            "diff --git a/src/logo.png b/src/logo.png\n"
            "new file mode 100644\n"
            "index 0000000..1234567\n"
            "GIT binary patch\n"
            "literal 6\n"
            "NcmZQzU|?VYU|;|U0RRC1\n"
            "\n"
            "literal 0\n"
            "HcmV?d00001\n"
            "\n"
        )

        scan = scan_patch(patch)

        self.assertEqual(scan.files, ("src/logo.png",))
        self.assertEqual(scan.problems, ())

    def test_file_name_with_space_is_read(self):
        patch = (
            "diff --git a/src/my file.txt b/src/my file.txt\n"
            "--- a/src/my file.txt\n"
            "+++ b/src/my file.txt\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )

        self.assertEqual(scan_patch(patch).files, ("src/my file.txt",))

    def test_hunk_body_lines_are_not_mistaken_for_headers(self):
        patch = (
            "diff --git a/src/a.txt b/src/a.txt\n"
            "--- a/src/a.txt\n"
            "+++ b/src/a.txt\n"
            "@@ -1,1 +1,2 @@\n"
            "-- looks like a header\n"
            "+++ also looks like a header\n"
            "+tail\n"
        )

        scan = scan_patch(patch)

        self.assertEqual(scan.files, ("src/a.txt",))
        self.assertEqual(scan.problems, ())

    def test_empty_patch_reports_no_problems(self):
        self.assertEqual(scan_patch(""), scan_patch("   \n"))
        self.assertEqual(scan_patch("").problems, ())

    def test_missing_allowed_paths_fails_closed(self):
        work_item = WorkItem(
            id="task-unscoped",
            title="Unscoped change",
            objective="No allowed paths were declared.",
            acceptance_criteria=("Scope must be declared.",),
            verification_commands=("python -c \"print('ok')\"",),
        )
        worker_patch = WorkerPatch(
            worker_name="worker",
            work_item_id="task-unscoped",
            patch="diff --git a/src/a.txt b/src/a.txt\n--- /dev/null\n+++ b/src/a.txt\n",
        )

        report = PolicyVerifier().verify_patch_before_apply(work_item, worker_patch)

        self.assertFalse(report.ok)
        self.assertIn("declares no allowed paths", " ".join(report.findings))

    def test_whole_repository_scope_marker_allows_any_path(self):
        work_item = _scoped_work_item("task-any", allowed_paths=("*",))
        worker_patch = WorkerPatch(
            worker_name="worker",
            work_item_id="task-any",
            patch="diff --git a/docs/readme.md b/docs/readme.md\n--- /dev/null\n+++ b/docs/readme.md\n",
        )

        self.assertTrue(PolicyVerifier().verify_patch_before_apply(work_item, worker_patch).ok)


if __name__ == "__main__":
    unittest.main()
