from __future__ import annotations

import json
from typing import Any

from .config import CloudModelEndpoint
from .domain import CommandResult, SemanticVerificationReport, WorkItem, WorkerPatch
from .lifecycle import ProjectSpec, project_spec_from_mapping
from .model_providers import ModelRequest, create_model_provider


def _string_array(min_items: int = 0) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
    }
    if min_items:
        schema["minItems"] = min_items
    return schema


PROJECT_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "spec_version",
        "id",
        "title",
        "goal",
        "customer",
        "business_requirements",
        "definition_of_done",
        "non_functional_requirements",
        "documentation_requirements",
        "constraints",
        "open_questions",
        "tasks",
    ],
    "properties": {
        "spec_version": {"type": "string", "enum": ["1.0"]},
        "id": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 1},
        "goal": {"type": "string", "minLength": 1},
        "customer": {"type": "string", "minLength": 1},
        "business_requirements": _string_array(1),
        "definition_of_done": _string_array(1),
        "non_functional_requirements": _string_array(),
        "documentation_requirements": _string_array(),
        "constraints": _string_array(),
        "open_questions": _string_array(),
        "tasks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "title",
                    "objective",
                    "acceptance_criteria",
                    "verification_commands",
                    "allowed_paths",
                    "non_goals",
                    "depends_on",
                    "priority",
                ],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "title": {"type": "string", "minLength": 1},
                    "objective": {"type": "string", "minLength": 1},
                    "acceptance_criteria": _string_array(1),
                    "verification_commands": _string_array(1),
                    "allowed_paths": _string_array(1),
                    "non_goals": _string_array(),
                    "depends_on": _string_array(),
                    "priority": {"type": "integer"},
                },
            },
        },
    },
}


SEMANTIC_VERIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "summary", "findings"],
    "properties": {
        "decision": {"type": "string", "enum": ["pass", "fail", "escalate"]},
        "summary": {"type": "string", "minLength": 1},
        "findings": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
}


def generate_project_spec(
    requirements_text: str,
    endpoint: CloudModelEndpoint,
    *,
    provider=None,
) -> tuple[ProjectSpec, object]:
    if not requirements_text.strip():
        raise ValueError("Requirements input must be non-empty.")
    selected = provider or create_model_provider(endpoint)
    response = selected.invoke(
        ModelRequest(
            instructions=(
                "You are the planning model. Convert customer requirements into an atomic HoH project plan. "
                "Do not select agents, models, credentials, Git authority, or orchestration roles. "
                "Use spec_version 1.0. Tasks must be dependency-safe, bounded, and independently verifiable. "
                "Every task must declare allowed_paths: the repository paths it may change. "
                "Use the single entry \"*\" only when a task genuinely needs the whole repository."
            ),
            input_text=requirements_text,
            output_schema_name="hoh_project_spec_v1",
            output_schema=PROJECT_SPEC_SCHEMA,
        )
    )
    _validate_project_spec_shape(response.output)
    return project_spec_from_mapping(response.output), response.evidence


class ModelSemanticVerifier:
    """Advisory model gate that can block/escalate, but has no repository authority."""

    def __init__(self, endpoint: CloudModelEndpoint, *, provider=None) -> None:
        self.endpoint = endpoint
        self.provider = provider or create_model_provider(endpoint)

    def verify(
        self,
        work_item: WorkItem,
        worker_patch: WorkerPatch,
        command_results: tuple[CommandResult, ...],
    ) -> SemanticVerificationReport:
        evidence_payload = {
            "task": {
                "id": work_item.id,
                "title": work_item.title,
                "objective": work_item.objective,
                "acceptance_criteria": list(work_item.acceptance_criteria),
                "non_goals": list(work_item.non_goals),
            },
            "patch": worker_patch.patch,
            "verification_commands": [
                {
                    "command": item.command,
                    "return_code": item.return_code,
                    "stdout": _truncate(item.stdout),
                    "stderr": _truncate(item.stderr),
                }
                for item in command_results
            ],
        }
        response = self.provider.invoke(
            ModelRequest(
                instructions=(
                    "You are a semantic verifier, separate from the worker and supervisor. "
                    "Evaluate only whether the supplied patch evidence satisfies the task. "
                    "Return pass, fail, or escalate. Escalate only when customer judgment is required. "
                    "You have no authority to modify files, run tools, commit, merge, close tasks, or approve "
                    "despite failed deterministic checks."
                ),
                input_text=json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True),
                output_schema_name="hoh_semantic_verification",
                output_schema=SEMANTIC_VERIFICATION_SCHEMA,
            )
        )
        output = response.output
        if set(output) != {"decision", "summary", "findings"}:
            raise ValueError("Semantic verifier output must contain only decision, summary, and findings.")
        decision = output.get("decision")
        summary = output.get("summary")
        findings = output.get("findings")
        if decision not in {"pass", "fail", "escalate"}:
            raise ValueError("Semantic verifier returned an invalid decision.")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("Semantic verifier returned an empty summary.")
        if not isinstance(findings, list) or any(not isinstance(item, str) or not item.strip() for item in findings):
            raise ValueError("Semantic verifier findings must be a list of non-empty strings.")
        return SemanticVerificationReport(
            decision=decision,
            summary=summary.strip(),
            findings=tuple(item.strip() for item in findings),
            evidence=response.evidence,
        )


def _truncate(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def _validate_project_spec_shape(output: dict[str, Any]) -> None:
    allowed = set(PROJECT_SPEC_SCHEMA["properties"])
    required = set(PROJECT_SPEC_SCHEMA["required"])
    actual = set(output)
    missing = required - actual
    extra = actual - allowed
    if missing or extra:
        details = []
        if missing:
            details.append("missing=" + ",".join(sorted(missing)))
        if extra:
            details.append("unexpected=" + ",".join(sorted(extra)))
        raise ValueError("Generated project spec violates the closed contract: " + " ".join(details))
    tasks = output.get("tasks")
    if not isinstance(tasks, list):
        return
    task_schema = PROJECT_SPEC_SCHEMA["properties"]["tasks"]["items"]
    task_allowed = set(task_schema["properties"])
    task_required = set(task_schema["required"])
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        task_actual = set(task)
        task_missing = task_required - task_actual
        task_extra = task_actual - task_allowed
        if task_missing or task_extra:
            details = []
            if task_missing:
                details.append("missing=" + ",".join(sorted(task_missing)))
            if task_extra:
                details.append("unexpected=" + ",".join(sorted(task_extra)))
            raise ValueError(
                f"Generated project task {index + 1} violates the closed contract: "
                + " ".join(details)
            )
