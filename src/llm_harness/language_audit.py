from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Callable, Iterable


SUPPORTED_ANALYZERS = ("python", "javascript", "typescript")
PYTHON_SUFFIXES = {".py"}
JAVASCRIPT_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs"}
TYPESCRIPT_SUFFIXES = {".ts", ".tsx", ".mts", ".cts"}
JS_IMPORT_PATTERN = re.compile(
    r"""(?:\bimport\s*(?:\([^)]*\)|[^;'"]*?\sfrom\s*)|\brequire\s*\(|\bexport\s+[^;'"]*?\sfrom\s*)"""
    r"""["'](?P<target>\.[^"']+)["']"""
)
JS_DECLARATION_PATTERN = re.compile(
    r"^\s*(?!export\b)(?:(?:async\s+)?function|class|const|let|var|interface|type|enum)"
    r"\s+(?P<name>[$A-Za-z_][$\w]*)\b"
)
JS_IDENTIFIER_PATTERN = re.compile(r"[$A-Za-z_][$\w]*")


@dataclass(frozen=True)
class AnalyzerFinding:
    code: str
    message: str
    path: str


@dataclass(frozen=True)
class AnalyzerEvidence:
    analyzer: str
    language: str
    status: str
    files_analyzed: int
    findings: tuple[AnalyzerFinding, ...] = ()
    detail: str = ""


def run_language_analyzers(
    root: Path,
    files: tuple[Path, ...],
    *,
    analyzers: tuple[str, ...] = SUPPORTED_ANALYZERS,
    entry_points: tuple[str, ...] = (),
    exclude_paths: tuple[str, ...] = (),
) -> tuple[AnalyzerEvidence, ...]:
    # Keep all graph keys on one canonical path representation.  macOS exposes
    # /tmp through the /private/tmp symlink; without normalizing both source
    # files and import targets, a resolved import could not match its source
    # file and was incorrectly reported as unreachable.
    root = root.expanduser().resolve()
    normalized = tuple(dict.fromkeys(item.strip().casefold() for item in analyzers))
    eligible = tuple(
        path.expanduser().resolve()
        for path in files
        if path.exists()
        and path.is_file()
        and not _matches_any(_relative(root, path), exclude_paths)
    )
    results: list[AnalyzerEvidence] = []
    for analyzer in normalized:
        try:
            runner = ANALYZER_RUNNERS.get(analyzer)
            if runner is None:
                results.append(
                    AnalyzerEvidence(
                        analyzer=analyzer,
                        language=analyzer,
                        status="unsupported",
                        files_analyzed=0,
                        detail="No built-in analyzer is registered for this language.",
                    )
                )
            else:
                results.append(runner(root, eligible, entry_points))
        except Exception as exc:
            results.append(
                AnalyzerEvidence(
                    analyzer=analyzer,
                    language=analyzer,
                    status="unavailable",
                    files_analyzed=0,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
    return tuple(results)


AnalyzerRunner = Callable[[Path, tuple[Path, ...], tuple[str, ...]], AnalyzerEvidence]


def _analyze_python(
    root: Path,
    files: tuple[Path, ...],
    configured_entry_points: tuple[str, ...],
) -> AnalyzerEvidence:
    python_files = tuple(path for path in files if path.suffix.casefold() in PYTHON_SUFFIXES)
    if not python_files:
        return AnalyzerEvidence("python", "python", "unsupported", 0, detail="No tracked Python files.")

    trees: dict[Path, ast.AST] = {}
    findings: list[AnalyzerFinding] = []
    for path in python_files:
        relative = _relative(root, path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=relative)
        except SyntaxError as exc:
            findings.append(
                AnalyzerFinding(
                    "PYTHON_ANALYSIS_PARSE_ERROR",
                    f"Python analyzer could not parse the file: {exc.msg}.",
                    f"{relative}:{exc.lineno or 1}",
                )
            )
            continue
        trees[path] = tree

    referenced_names = {
        name
        for tree in trees.values()
        for name in _python_referenced_names(tree)
    }
    for path, tree in trees.items():
        findings.extend(_python_dead_code_findings(root, path, tree, referenced_names))

    findings.extend(
        _unused_file_findings(
            root,
            python_files,
            _python_import_graph(root, python_files, trees),
            configured_entry_points,
            language="PYTHON",
        )
    )
    status = "findings" if findings else "passed"
    return AnalyzerEvidence("python", "python", status, len(python_files), tuple(findings))


def _python_dead_code_findings(
    root: Path,
    path: Path,
    tree: ast.AST,
    referenced_names: set[str],
) -> tuple[AnalyzerFinding, ...]:
    findings: list[AnalyzerFinding] = []
    relative = _relative(root, path)
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            statements = getattr(node, field, None)
            if not isinstance(statements, list):
                continue
            terminal_seen = False
            for statement in statements:
                if terminal_seen:
                    findings.append(
                        AnalyzerFinding(
                            "PYTHON_UNREACHABLE_CODE",
                            "Statement is unreachable after an unconditional control-flow exit.",
                            f"{relative}:{getattr(statement, 'lineno', 1)}",
                        )
                    )
                    break
                terminal_seen = isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue))

    for statement in getattr(tree, "body", ()):
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if (
            statement.name.startswith("_")
            and not statement.name.startswith("__")
            and statement.name not in referenced_names
        ):
            findings.append(
                AnalyzerFinding(
                    "PYTHON_UNUSED_PRIVATE_DECLARATION",
                    f"Private top-level declaration '{statement.name}' is never referenced in this module.",
                    f"{relative}:{statement.lineno}",
                )
            )
    return tuple(findings)


def _python_referenced_names(tree: ast.AST) -> set[str]:
    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    names.update(node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
            names.update(
                item.value
                for item in node.value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
    return names


def _python_import_graph(
    root: Path,
    files: tuple[Path, ...],
    trees: dict[Path, ast.AST],
) -> dict[Path, set[Path]]:
    modules: dict[str, Path] = {}
    module_names: dict[Path, str] = {}
    for path in files:
        module = _python_module_name(root, path)
        module_names[path] = module
        modules[module] = path

    graph = {path: set() for path in files}
    for path, tree in trees.items():
        current = module_names[path]
        package_parts = current.split(".")[:-1]
        if path.name == "__init__.py":
            package_parts = current.split(".")
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base_parts = package_parts
                if node.level:
                    keep = max(0, len(package_parts) - node.level + 1)
                    base_parts = package_parts[:keep]
                prefix = ".".join((*base_parts, node.module) if node.module else base_parts)
                if prefix:
                    targets.append(prefix)
                targets.extend(
                    f"{prefix}.{alias.name}" if prefix else alias.name
                    for alias in node.names
                    if alias.name != "*"
                )
            for target in targets:
                resolved = _longest_module_match(target, modules)
                if resolved is not None and resolved != path:
                    graph[path].add(resolved)
    return graph


def _python_module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    parts = list(relative.with_suffix("").parts)
    if parts and parts[0] in {"src", "lib"}:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _longest_module_match(target: str, modules: dict[str, Path]) -> Path | None:
    candidate = target
    while candidate:
        if candidate in modules:
            return modules[candidate]
        candidate = candidate.rpartition(".")[0]
    return None


def _analyze_javascript_family(
    root: Path,
    files: tuple[Path, ...],
    configured_entry_points: tuple[str, ...],
    language: str,
    suffixes: set[str],
) -> AnalyzerEvidence:
    source_files = tuple(path for path in files if path.suffix.casefold() in suffixes)
    if not source_files:
        return AnalyzerEvidence(language, language, "unsupported", 0, detail=f"No tracked {language} files.")

    graph = {path: set() for path in source_files}
    findings: list[AnalyzerFinding] = []
    file_set = set(source_files)
    for path in source_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = _relative(root, path)
        for target in JS_IMPORT_PATTERN.finditer(text):
            resolved = _resolve_js_import(path, target.group("target"), file_set)
            if resolved is not None and resolved != path:
                graph[path].add(resolved)
        identifiers = JS_IDENTIFIER_PATTERN.findall(_strip_js_comments_and_strings(text))
        counts = {name: identifiers.count(name) for name in set(identifiers)}
        depth = 0
        for line_number, line in enumerate(text.splitlines(), start=1):
            if depth == 0:
                declaration = JS_DECLARATION_PATTERN.match(line)
                if declaration and counts.get(declaration.group("name"), 0) == 1:
                    name = declaration.group("name")
                    findings.append(
                        AnalyzerFinding(
                            f"{language.upper()}_UNUSED_DECLARATION",
                            f"Non-exported top-level declaration '{name}' is never referenced in this module.",
                            f"{relative}:{line_number}",
                        )
                    )
            depth += line.count("{") - line.count("}")
            depth = max(depth, 0)

    findings.extend(
        _unused_file_findings(
            root,
            source_files,
            graph,
            configured_entry_points,
            language=language.upper(),
        )
    )
    status = "findings" if findings else "passed"
    return AnalyzerEvidence(language, language, status, len(source_files), tuple(findings))


def _analyze_javascript(
    root: Path,
    files: tuple[Path, ...],
    entry_points: tuple[str, ...],
) -> AnalyzerEvidence:
    return _analyze_javascript_family(
        root, files, entry_points, "javascript", JAVASCRIPT_SUFFIXES
    )


def _analyze_typescript(
    root: Path,
    files: tuple[Path, ...],
    entry_points: tuple[str, ...],
) -> AnalyzerEvidence:
    return _analyze_javascript_family(
        root, files, entry_points, "typescript", TYPESCRIPT_SUFFIXES
    )


def _strip_js_comments_and_strings(text: str) -> str:
    without_blocks = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    without_lines = re.sub(r"//.*", " ", without_blocks)
    return re.sub(r"""(["'`])(?:\\.|(?!\1).)*\1""", " ", without_lines, flags=re.DOTALL)


def _resolve_js_import(source: Path, target: str, files: set[Path]) -> Path | None:
    base = (source.parent / target).resolve()
    candidates = [base]
    candidates.extend(base.with_suffix(suffix) for suffix in (*JAVASCRIPT_SUFFIXES, *TYPESCRIPT_SUFFIXES))
    candidates.extend(base / f"index{suffix}" for suffix in (*JAVASCRIPT_SUFFIXES, *TYPESCRIPT_SUFFIXES))
    return next((candidate for candidate in candidates if candidate in files), None)


def _unused_file_findings(
    root: Path,
    files: tuple[Path, ...],
    graph: dict[Path, set[Path]],
    configured_entry_points: tuple[str, ...],
    *,
    language: str,
) -> tuple[AnalyzerFinding, ...]:
    explicit_roots = {
        path
        for path in files
        if _matches_any(_relative(root, path), configured_entry_points)
    }
    inferred_roots = _inferred_entry_points(root, files, language)
    roots = explicit_roots or inferred_roots
    reachable = _reachable(graph, roots)
    incoming = {path: 0 for path in files}
    for targets in graph.values():
        for target in targets:
            incoming[target] = incoming.get(target, 0) + 1

    findings: list[AnalyzerFinding] = []
    strict = bool(explicit_roots)
    for path in files:
        relative = _relative(root, path)
        private_orphan = path.stem.startswith("_") and path.name not in {"__init__.py", "__main__.py"}
        if path not in reachable and (strict or (private_orphan and incoming.get(path, 0) == 0)):
            findings.append(
                AnalyzerFinding(
                    f"{language}_UNUSED_FILE",
                    (
                        "Tracked source file is unreachable from configured audit entry points."
                        if strict
                        else "Private tracked source file has no inbound static imports."
                    ),
                    relative,
                )
            )
    return tuple(findings)


def _inferred_entry_points(root: Path, files: tuple[Path, ...], language: str) -> set[Path]:
    roots: set[Path] = set()
    for path in files:
        relative = PurePosixPath(_relative(root, path))
        if path.name == "__main__.py" or path.stem.startswith("test_") or "tests" in relative.parts:
            roots.add(path)
        elif not path.stem.startswith("_"):
            roots.add(path)
    if language in {"JAVASCRIPT", "TYPESCRIPT"}:
        roots.update(_package_json_entry_points(root, files))
    return roots


def _package_json_entry_points(root: Path, files: tuple[Path, ...]) -> set[Path]:
    package_json = root / "package.json"
    if not package_json.exists():
        return set()
    try:
        import json

        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    values: list[str] = []
    for key in ("main", "module", "browser"):
        if isinstance(payload.get(key), str):
            values.append(payload[key])
    if isinstance(payload.get("bin"), str):
        values.append(payload["bin"])
    elif isinstance(payload.get("bin"), dict):
        values.extend(value for value in payload["bin"].values() if isinstance(value, str))
    file_map = {_relative(root, path).lstrip("./"): path for path in files}
    return {file_map[value.lstrip("./")] for value in values if value.lstrip("./") in file_map}


def _reachable(graph: dict[Path, set[Path]], roots: Iterable[Path]) -> set[Path]:
    visited: set[Path] = set()
    pending = list(roots)
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, ()))
    return visited


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    candidate = PurePosixPath(path)
    return any(candidate.match(pattern.replace("\\", "/")) for pattern in patterns)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


ANALYZER_RUNNERS: dict[str, AnalyzerRunner] = {
    "python": _analyze_python,
    "javascript": _analyze_javascript,
    "typescript": _analyze_typescript,
}
