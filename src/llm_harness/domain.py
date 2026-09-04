from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class WorkItem:
    id: str
    title: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    verification_commands: tuple[str, ...]
    allowed_paths: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    priority: int = 0
    correction_instructions: tuple[str, ...] = ()
    correction_decision_id: str | None = None


@dataclass(frozen=True)
class WorkerPatch:
    worker_name: str
    work_item_id: str
    patch: str
    notes: str = ""


@dataclass(frozen=True)
class VerificationReport:
    ok: bool
    findings: tuple[str, ...] = ()
    metrics: dict[str, int | str | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelInvocationEvidence:
    provider: str
    model: str
    endpoint: str
    request_id: str | None
    attempts: int
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    request_sha256: str = ""
    response_sha256: str = ""


@dataclass(frozen=True)
class SemanticVerificationReport:
    decision: str
    summary: str
    findings: tuple[str, ...] = ()
    evidence: ModelInvocationEvidence | None = None

    def __post_init__(self) -> None:
        if self.decision not in {"pass", "fail", "escalate"}:
            raise ValueError("Semantic verifier decision must be pass, fail, or escalate.")

    @property
    def ok(self) -> bool:
        return self.decision == "pass"


@dataclass(frozen=True)
class CommandResult:
    command: str
    return_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.return_code == 0


@dataclass(frozen=True)
class HarnessRunResult:
    repository: Path
    work_item_id: str
    commit: str | None
    pre_apply: VerificationReport
    post_apply: VerificationReport
    command_results: tuple[CommandResult, ...]
    semantic_verification: SemanticVerificationReport | None = None

    @property
    def ok(self) -> bool:
        return (
            self.commit is not None
            and self.pre_apply.ok
            and self.post_apply.ok
            and all(result.ok for result in self.command_results)
            and (self.semantic_verification is None or self.semantic_verification.ok)
        )
