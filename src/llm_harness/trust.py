from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WorkerTrustLevel(StrEnum):
    PATCH_ONLY = "patch_only"
    BRANCH_ONLY = "branch_only"
    BRANCH_AND_COMMIT = "branch_and_commit"


# 1.0.0 enforces exactly one level. The other two are recognised so an existing
# configuration gets a clear refusal instead of silently behaving like patch_only.
IMPLEMENTED_WORKER_TRUST_LEVELS = frozenset({WorkerTrustLevel.PATCH_ONLY})


@dataclass(frozen=True)
class WorkerTrustPolicy:
    level: WorkerTrustLevel = WorkerTrustLevel.PATCH_ONLY

    @property
    def requires_patch_output(self) -> bool:
        return self.level in {WorkerTrustLevel.PATCH_ONLY, WorkerTrustLevel.BRANCH_ONLY}

    @property
    def can_create_branch(self) -> bool:
        return self.level in {WorkerTrustLevel.BRANCH_ONLY, WorkerTrustLevel.BRANCH_AND_COMMIT}

    @property
    def can_commit(self) -> bool:
        return self.level is WorkerTrustLevel.BRANCH_AND_COMMIT

    @property
    def supervisor_must_commit(self) -> bool:
        return not self.can_commit


def parse_worker_trust_level(value: str | WorkerTrustLevel) -> WorkerTrustLevel:
    if isinstance(value, WorkerTrustLevel):
        level = value
    else:
        try:
            level = WorkerTrustLevel(value)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in WorkerTrustLevel)
            raise ValueError(
                f"Unknown worker trust level '{value}'. Allowed values: {allowed}"
            ) from exc
    if level not in IMPLEMENTED_WORKER_TRUST_LEVELS:
        raise ValueError(
            f"Worker trust level '{level.value}' is not supported in this release. "
            "HoH grants the Worker no Git authority at all: it works in a disposable worktree "
            f"and returns a patch. Use '{WorkerTrustLevel.PATCH_ONLY.value}'."
        )
    return level

