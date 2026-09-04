"""Fixtures shared by the tests that need a project the readiness audit accepts."""

from __future__ import annotations

from pathlib import Path

from llm_harness.audit import diagram_source_digest

DIAGRAMS = (
    ("architecture-c4-component.puml", "hoh-c4-component.png", "C4 component view"),
    ("architecture-sequence.puml", "hoh-sequence.png", "Main sequence"),
)


def write_architecture_docs(docs_dir: Path, title: str = "Architecture") -> Path:
    """Write an architecture document with the diagrams the audit requires.

    The audit wants a PlantUML source, its committed render, and a digest file that
    holds the two together, because GitHub cannot draw PlantUML inline and a
    committed render goes stale the moment its source moves. Six fixtures needed the
    same three files, so they share this one.
    """
    docs_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", ""]
    digests: list[str] = []
    for source_name, image_name, caption in DIAGRAMS:
        source = docs_dir / source_name
        source.write_text("@startuml\n@enduml\n", encoding="utf-8")
        (docs_dir / image_name).write_bytes(b"\x89PNG\r\n\x1a\n")
        digests.append(f"{diagram_source_digest(source)}  {source_name}\n")
        lines.extend([f"![{caption}]({image_name})", ""])
    (docs_dir / "diagrams.sha256").write_text("".join(digests), encoding="utf-8")

    architecture = docs_dir / "architecture.md"
    architecture.write_text("\n".join(lines), encoding="utf-8")
    return architecture
