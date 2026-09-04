from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import re
import subprocess

from .command_policy import CommandPolicyError, OperatorCommandPolicy
from .git_ops import decode_process_output
from .domain import CommandResult
from .config import AuditConfig
from .language_audit import AnalyzerEvidence, run_language_analyzers
from .tasks import TaskLoadError, load_work_item


AUDIT_IGNORE_LINE_MARKER = "hoh-audit: ignore-line"
BLOCKING_MARKER_PATTERN = re.compile(
    r"\b(TODO|FIXME|HACK|XXX|PLACEHOLDER)\b|not implemented|NotImplementedError",  # hoh-audit: ignore-line
    re.IGNORECASE,
)
LIFECYCLE_POLICY_BEFORE_PATTERN = re.compile(
    r"\b(?:no|without|must\s+not|do\s+not|forbid(?:s|den)?|prohibit(?:s|ed)?)\b"
    r"(?:(?!\b(?:but|however|except)\b)[^.!?;]){0,160}$",
    re.IGNORECASE,
)
LIFECYCLE_POLICY_AFTER_PATTERN = re.compile(
    r"^[^.!?;]{0,80}\b(?:not\s+in|is\s+absent|are\s+absent|must\s+be\s+absent)\b",
    re.IGNORECASE,
)

TEXT_FILE_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

GENERATED_OR_CACHE_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
}


@dataclass(frozen=True)
class AuditFinding:
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class ProjectAuditReport:
    project_root: Path
    findings: tuple[AuditFinding, ...] = ()
    command_results: tuple[CommandResult, ...] = ()
    analyzer_evidence: tuple[AnalyzerEvidence, ...] = ()
    metrics: dict[str, int | bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.findings and all(result.ok for result in self.command_results)

    def to_markdown(self) -> str:
        lines = [
            "# Project audit report",
            "",
            f"- Project root: `{self.project_root}`",
            f"- Ready: `{str(self.ok).lower()}`",
            f"- Findings: `{len(self.findings)}`",
            f"- Verification commands: `{len(self.command_results)}`",
            f"- Language analyzers: `{len(self.analyzer_evidence)}`",
            "",
            "## Metrics",
            "",
        ]
        if self.metrics:
            for key in sorted(self.metrics):
                lines.append(f"- {key}: `{self.metrics[key]}`")
        else:
            lines.append("- none")

        lines.extend(["", "## Verification commands", ""])
        if self.command_results:
            for result in self.command_results:
                lines.append(f"- `{result.command}` -> `{result.return_code}`")
                # A release gate that only prints the exit code makes the reader
                # re-run the command by hand to learn anything. Show the end of the
                # output, which is where a test runner puts the failure.
                if not result.ok:
                    tail = _output_tail(result)
                    if tail:
                        lines.extend(["", "  ```", *(f"  {line}" for line in tail), "  ```", ""])
        else:
            lines.append("- none")

        lines.extend(["", "## Language analyzers", ""])
        if self.analyzer_evidence:
            for evidence in self.analyzer_evidence:
                detail = f" — {evidence.detail}" if evidence.detail else ""
                lines.append(
                    f"- `{evidence.analyzer}` -> `{evidence.status}` "
                    f"(files: `{evidence.files_analyzed}`, findings: `{len(evidence.findings)}`){detail}"
                )
        else:
            lines.append("- none")

        lines.extend(["", "## Findings", ""])
        if self.findings:
            for finding in self.findings:
                location = f" `{finding.path}`" if finding.path else ""
                lines.append(f"- `{finding.code}`{location}: {finding.message}")
        else:
            lines.append("- none")

        return "\n".join(lines) + "\n"


def run_project_audit(
    project_root: Path,
    check_commands: tuple[str, ...] = (),
    # A --check is routinely a whole test suite; 120s used to time one out at 124
    # and report a green project as failed.
    command_timeout_seconds: float = 1800.0,
    audit_config: AuditConfig | None = None,
) -> ProjectAuditReport:
    root = project_root.resolve()
    findings: list[AuditFinding] = []
    metrics: dict[str, int | bool] = {}

    if not root.exists() or not root.is_dir():
        return ProjectAuditReport(
            project_root=root,
            findings=(AuditFinding("PROJECT_ROOT_MISSING", "Project root does not exist or is not a directory."),),
        )

    is_git_repo = _is_git_repository(root)
    metrics["is_git_repository"] = is_git_repo
    if not is_git_repo:
        findings.append(AuditFinding("GIT_REPOSITORY_MISSING", "Project root is not a git repository."))

    if is_git_repo:
        dirty_entries = _git_lines(root, "status", "--porcelain")
        metrics["git_dirty_entries"] = len(dirty_entries)
        if dirty_entries:
            findings.append(
                AuditFinding(
                    "GIT_WORKTREE_DIRTY",
                    "Final audit requires a clean git worktree before readiness handoff.",
                )
            )
    else:
        dirty_entries = []
        metrics["git_dirty_entries"] = 0

    readme = root / "README.md"
    metrics["readme_exists"] = readme.exists()
    if not readme.exists():
        findings.append(AuditFinding("README_MISSING", "README.md is required.", "README.md"))

    docs_dir = root / "docs"
    metrics["docs_dir_exists"] = docs_dir.exists()
    if not docs_dir.exists() or not docs_dir.is_dir():
        findings.append(AuditFinding("DOCS_DIR_MISSING", "docs/ directory is required.", "docs"))

    architecture = docs_dir / "architecture.md"
    metrics["architecture_doc_exists"] = architecture.exists()
    if architecture.exists():
        architecture_text = architecture.read_text(encoding="utf-8", errors="replace")
        findings.extend(_diagram_findings(root, docs_dir, architecture, architecture_text, metrics))
    else:
        metrics["c4_component_diagram_exists"] = False
        metrics["sequence_diagram_exists"] = False
        metrics["diagram_renders_current"] = False
        findings.append(
            AuditFinding("ARCHITECTURE_DOC_MISSING", "docs/architecture.md is required.", "docs/architecture.md")
        )

    non_markdown_docs = _non_markdown_docs(docs_dir)
    metrics["non_markdown_docs_count"] = len(non_markdown_docs)
    for path in non_markdown_docs:
        findings.append(
            AuditFinding(
                "NON_MARKDOWN_DOCUMENTATION",
                "Documentation files must be Markdown.",
                _relative(root, path),
            )
        )

    lifecycle_findings, lifecycle_metrics = _lifecycle_artifact_findings(root)
    metrics.update(lifecycle_metrics)
    findings.extend(lifecycle_findings)

    tracked_files = _tracked_files(root) if is_git_repo else _walk_project_files(root)
    metrics["audited_files_count"] = len(tracked_files)

    generated_files = _tracked_generated_files(root, tracked_files)
    metrics["tracked_generated_files_count"] = len(generated_files)
    for path in generated_files:
        findings.append(
            AuditFinding(
                "TRACKED_GENERATED_ARTIFACT",
                "Generated/cache/build artifacts must not be part of final project state unless explicitly justified.",
                _relative(root, path),
            )
        )

    marker_findings = _blocking_marker_findings(root, tracked_files)
    metrics["blocking_markers_count"] = len(marker_findings)
    findings.extend(marker_findings)

    analyzer_config = audit_config or AuditConfig()
    analyzer_evidence = run_language_analyzers(
        root,
        tracked_files,
        analyzers=analyzer_config.language_analyzers,
        entry_points=analyzer_config.entry_points,
        exclude_paths=analyzer_config.exclude_paths,
    )
    metrics["language_analyzers_count"] = len(analyzer_evidence)
    metrics["language_analyzers_findings_count"] = sum(
        len(evidence.findings) for evidence in analyzer_evidence
    )
    metrics["language_analyzers_unavailable_count"] = sum(
        evidence.status == "unavailable" for evidence in analyzer_evidence
    )
    metrics["language_analyzers_unsupported_count"] = sum(
        evidence.status == "unsupported" for evidence in analyzer_evidence
    )
    for evidence in analyzer_evidence:
        findings.extend(
            AuditFinding(item.code, item.message, item.path)
            for item in evidence.findings
        )
        if evidence.status == "unavailable" and analyzer_config.fail_on_unavailable:
            findings.append(
                AuditFinding(
                    "LANGUAGE_ANALYZER_UNAVAILABLE",
                    f"Required analyzer '{evidence.analyzer}' is unavailable: {evidence.detail}",
                )
            )

    command_results = _run_check_commands(root, check_commands, command_timeout_seconds)
    metrics["verification_commands_count"] = len(command_results)
    if not command_results:
        findings.append(
            AuditFinding(
                "VERIFICATION_COMMANDS_MISSING",
                "Final audit requires at least one explicit verification command through --check.",
            )
        )
    for result in command_results:
        if not result.ok:
            findings.append(
                AuditFinding(
                    "VERIFICATION_COMMAND_FAILED",
                    f"Verification command exited with {result.return_code}: {result.command}",
                )
            )

    return ProjectAuditReport(
        project_root=root,
        findings=tuple(findings),
        command_results=command_results,
        analyzer_evidence=analyzer_evidence,
        metrics=metrics,
    )


def _is_git_repository(root: Path) -> bool:
    completed = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def _git_lines(root: Path, *args: str) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return ()
    return tuple(line for line in completed.stdout.splitlines() if line.strip())


def _tracked_files(root: Path) -> tuple[Path, ...]:
    return tuple(root / line for line in _git_lines(root, "ls-files"))


def _walk_project_files(root: Path) -> tuple[Path, ...]:
    ignored = {".git"}
    files: list[Path] = []
    for path in root.rglob("*"):
        if any(part in ignored for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            files.append(path)
    return tuple(files)


DIAGRAM_SOURCES = (
    ("c4_component", "architecture-c4-component.puml", "hoh-c4-component.png", "C4_COMPONENT_DIAGRAM"),
    ("sequence", "architecture-sequence.puml", "hoh-sequence.png", "SEQUENCE_DIAGRAM"),
)
DIAGRAM_DIGESTS = "diagrams.sha256"


def diagram_source_digest(source: Path) -> str:
    """Digest a diagram source independently of how git checked it out.

    .gitattributes normalises these files, so the same commit is CRLF in a Windows
    working tree and LF everywhere else. Hashing the bytes on disk would therefore
    report a perfectly current render as stale on every machine that did not
    produce it, CI included.
    """
    import hashlib

    return hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _diagram_findings(
    root: Path,
    docs_dir: Path,
    architecture: Path,
    architecture_text: str,
    metrics: dict[str, Any],
) -> list[AuditFinding]:
    """Require both diagrams, their renders, and proof the renders are not stale.

    GitHub does not draw PlantUML inline, so the PNGs are committed. A committed
    render is a copy of the source, and a copy is exactly the kind of documentation
    that rots quietly: the .puml changes, nobody re-renders, and the picture in the
    README goes on describing a system that no longer exists. Recording each
    source's digest at render time turns that into a build failure.
    """
    import hashlib

    findings: list[AuditFinding] = []
    recorded: dict[str, str] = {}
    digest_file = docs_dir / DIAGRAM_DIGESTS
    if digest_file.exists():
        for line in digest_file.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) == 2:
                recorded[parts[1]] = parts[0]

    current = True
    for metric, source_name, image_name, code in DIAGRAM_SOURCES:
        source = docs_dir / source_name
        image = docs_dir / image_name
        present = source.exists() and image.exists() and image_name in architecture_text
        metrics[f"{metric}_diagram_exists"] = present
        if not present:
            findings.append(
                AuditFinding(
                    f"{code}_MISSING",
                    f"Architecture documentation must ship {source_name}, its rendered "
                    f"{image_name}, and reference the image.",
                    _relative(root, architecture),
                )
            )
            current = False
            continue
        digest = diagram_source_digest(source)
        if recorded.get(source_name) != digest:
            current = False
            findings.append(
                AuditFinding(
                    f"{code}_RENDER_STALE",
                    f"{source_name} changed since it was last rendered. "
                    "Run scripts/render-diagrams.sh and commit the result.",
                    _relative(root, source),
                )
            )
    metrics["diagram_renders_current"] = current
    return findings


# The rule below exists so prose does not arrive as a PDF or a .docx nobody can
# diff. Diagram sources and their committed renders are neither: the .puml is text
# under review, the .png is what GitHub can actually display, and the digest file
# is what proves the two agree.
DIAGRAM_SUFFIXES = frozenset({".puml", ".png"})


def _non_markdown_docs(docs_dir: Path) -> tuple[Path, ...]:
    if not docs_dir.exists() or not docs_dir.is_dir():
        return ()
    return tuple(
        path
        for path in docs_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() != ".md"
        and path.suffix.lower() not in DIAGRAM_SUFFIXES
        and path.name != DIAGRAM_DIGESTS
    )


def _tracked_generated_files(root: Path, files: tuple[Path, ...]) -> tuple[Path, ...]:
    generated: list[Path] = []
    for path in files:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part in GENERATED_OR_CACHE_PARTS for part in parts):
            generated.append(path)
    return tuple(generated)


def _lifecycle_artifact_findings(root: Path) -> tuple[tuple[AuditFinding, ...], dict[str, int | bool]]:
    findings: list[AuditFinding] = []
    metrics: dict[str, int | bool] = {}

    hoh_docs = root / "docs" / "hoh"
    hoh_tasks = root / "tasks" / "hoh"
    lifecycle_present = hoh_docs.exists() or hoh_tasks.exists()
    metrics["lifecycle_artifacts_present"] = lifecycle_present

    project_brief = hoh_docs / "project-brief.md"
    roadmap = hoh_docs / "roadmap.md"
    metrics["lifecycle_project_brief_exists"] = project_brief.exists()
    metrics["lifecycle_roadmap_exists"] = roadmap.exists()

    task_files = _lifecycle_task_files(hoh_tasks)
    metrics["lifecycle_task_files_count"] = len(task_files)

    if not lifecycle_present:
        metrics["lifecycle_task_ids_count"] = 0
        metrics["lifecycle_invalid_task_files_count"] = 0
        return tuple(findings), metrics

    if not project_brief.exists():
        findings.append(
            AuditFinding(
                "LIFECYCLE_PROJECT_BRIEF_MISSING",
                "Lifecycle artifacts require docs/hoh/project-brief.md.",
                "docs/hoh/project-brief.md",
            )
        )
    if not roadmap.exists():
        findings.append(
            AuditFinding(
                "LIFECYCLE_ROADMAP_MISSING",
                "Lifecycle artifacts require docs/hoh/roadmap.md.",
                "docs/hoh/roadmap.md",
            )
        )
    if not task_files:
        findings.append(
            AuditFinding(
                "LIFECYCLE_TASKS_MISSING",
                "Lifecycle artifacts require at least one task file under tasks/hoh/.",
                "tasks/hoh",
            )
        )

    seen_ids: dict[str, Path] = {}
    invalid_count = 0
    for path in task_files:
        try:
            work_item = load_work_item(path)
        except TaskLoadError as exc:
            invalid_count += 1
            findings.append(
                AuditFinding(
                    "LIFECYCLE_TASK_INVALID",
                    f"Lifecycle task file is invalid: {exc}",
                    _relative(root, path),
                )
            )
            continue

        existing = seen_ids.get(work_item.id)
        if existing is not None:
            findings.append(
                AuditFinding(
                    "LIFECYCLE_TASK_DUPLICATE_ID",
                    f"Lifecycle task id '{work_item.id}' is duplicated by {_relative(root, existing)}.",
                    _relative(root, path),
                )
            )
        else:
            seen_ids[work_item.id] = path

    metrics["lifecycle_task_ids_count"] = len(seen_ids)
    metrics["lifecycle_invalid_task_files_count"] = invalid_count
    return tuple(findings), metrics


def _lifecycle_task_files(tasks_dir: Path) -> tuple[Path, ...]:
    if not tasks_dir.exists() or not tasks_dir.is_dir():
        return ()
    suffixes = {".json", ".md", ".markdown"}
    return tuple(sorted(path for path in tasks_dir.iterdir() if path.is_file() and path.suffix.lower() in suffixes))


def _blocking_marker_findings(root: Path, files: tuple[Path, ...]) -> tuple[AuditFinding, ...]:
    findings: list[AuditFinding] = []
    for path in files:
        if path.suffix.lower() not in TEXT_FILE_SUFFIXES:
            continue
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if AUDIT_IGNORE_LINE_MARKER in line:
                continue
            if BLOCKING_MARKER_PATTERN.search(line) and not _is_negated_lifecycle_policy_line(
                root, path, line
            ):
                findings.append(
                    AuditFinding(
                        "BLOCKING_MARKER_FOUND",
                        "Final audit found blocking marker.",  # hoh-audit: ignore-line
                        f"{_relative(root, path)}:{line_number}",
                    )
                )
    return tuple(findings)


def _is_negated_lifecycle_policy_line(root: Path, path: Path, line: str) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix().casefold()
    except ValueError:
        return False
    is_lifecycle_artifact = (
        relative == "project-spec.json"
        or relative in {"docs/hoh/project-brief.md", "docs/hoh/roadmap.md"}
        or relative.startswith("tasks/hoh/")
    )
    if not is_lifecycle_artifact:
        return False
    matches = tuple(BLOCKING_MARKER_PATTERN.finditer(line))
    return bool(matches) and all(
        LIFECYCLE_POLICY_BEFORE_PATTERN.search(line[: match.start()])
        or LIFECYCLE_POLICY_AFTER_PATTERN.search(line[match.end() :])
        for match in matches
    )


OUTPUT_TAIL_LINES = 20


def _output_tail(result: CommandResult) -> tuple[str, ...]:
    """The last lines a failing command printed, stderr first.

    Test runners put the failure at the end of stderr, and a truncated tail is
    enough to name it. The whole output can be megabytes, which is not something
    to paste into a report.
    """
    for stream in (result.stderr, result.stdout):
        lines = [line.rstrip() for line in stream.splitlines() if line.strip()]
        if lines:
            return tuple(lines[-OUTPUT_TAIL_LINES:])
    return ()


def _run_check_commands(
    root: Path,
    commands: tuple[str, ...],
    timeout_seconds: float,
) -> tuple[CommandResult, ...]:
    policy = OperatorCommandPolicy()
    results: list[CommandResult] = []
    for command in commands:
        try:
            argv = policy.resolve(command)
        except CommandPolicyError as exc:
            results.append(
                CommandResult(command=command, return_code=126, stdout="", stderr=str(exc))
            )
            continue
        try:
            completed = subprocess.run(
                argv,
                cwd=root,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
            results.append(
                CommandResult(
                    command=command,
                    return_code=completed.returncode,
                    stdout=decode_process_output(completed.stdout),
                    stderr=decode_process_output(completed.stderr),
                )
            )
        except OSError as exc:
            # A command naming a program that is not there used to raise out of the
            # audit entirely, so one bad check reported a traceback instead of a
            # finding and the other checks never ran.
            results.append(
                CommandResult(
                    command=command,
                    return_code=127,
                    stdout="",
                    stderr=f"Verification command could not be started: {exc}",
                )
            )
        except subprocess.TimeoutExpired as exc:
            results.append(
                CommandResult(
                    command=command,
                    return_code=124,
                    stdout=decode_process_output(exc.stdout),
                    stderr=f"Verification command timed out after {timeout_seconds} seconds.",
                )
            )
    return tuple(results)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
