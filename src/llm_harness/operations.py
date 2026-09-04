from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .coordination import repository_coordination_root
from .domain import (
    CommandResult,
    HarnessRunResult,
    ModelInvocationEvidence,
    SemanticVerificationReport,
    VerificationReport,
)
from .durable_io import DurableIOError, atomic_write_json, read_json
from .git_ops import (
    GitError,
    commit_changed_files,
    commit_parents,
    current_head,
    find_commit_by_trailer,
    repository_is_clean,
    resolve_commit,
)
from .journal import redact
from .state import HohStateStore, RollbackRecord, StateStoreError


OperationAction = Literal["task_commit", "rollback"]
TERMINAL_OUTCOMES = {"complete", "cancelled", "reverted", "failed"}


class OperationLedgerError(RuntimeError):
    pass


class ReconciliationBlockedError(OperationLedgerError):
    pass


@dataclass(frozen=True)
class OperationContext:
    operation_id: str
    run_id: str
    state_root: Path
    started_at_utc: str
    review_required: bool = False

    @classmethod
    def create(
        cls,
        state_root: Path,
        started_at_utc: str,
        *,
        review_required: bool = False,
    ) -> "OperationContext":
        return cls(
            operation_id=str(uuid4()),
            run_id=str(uuid4()),
            state_root=state_root.resolve(),
            started_at_utc=started_at_utc,
            review_required=review_required,
        )


@dataclass(frozen=True)
class ReconciliationItem:
    operation_id: str
    action: str
    classification: str
    safe_action: str | None
    detail: str
    terminal: bool = False


@dataclass(frozen=True)
class ReconciliationReport:
    repository: Path
    items: tuple[ReconciliationItem, ...]

    @property
    def blocking(self) -> tuple[ReconciliationItem, ...]:
        return tuple(item for item in self.items if not item.terminal)

    @property
    def ok(self) -> bool:
        return not self.blocking


class OperationLedger:
    def __init__(self, repository: Path) -> None:
        self.repository = repository.resolve()
        self.root = repository_coordination_root(self.repository) / "operations"

    def begin_task(
        self,
        context: OperationContext,
        *,
        task_id: str,
        patch: str,
        expected_changed_files: tuple[str, ...],
    ) -> dict[str, Any]:
        baseline_head = current_head(self.repository)
        baseline_files, expected_post_files = _patch_file_evidence(
            self.repository,
            patch,
            expected_changed_files,
        )
        return self._create(
            {
                "version": 1,
                "operation_id": context.operation_id,
                "action": "task_commit",
                "repository": str(self.repository),
                "state_root": str(context.state_root),
                "task_id": task_id,
                "run_id": context.run_id,
                "started_at_utc": context.started_at_utc,
                "review_required": context.review_required,
                "baseline_head": baseline_head,
                "patch_sha256": _sha256(patch),
                "expected_changed_files": list(expected_changed_files),
                "baseline_files": baseline_files,
                "expected_post_files": expected_post_files,
                "created_at_utc": _utc_now(),
                "phases": {"intent_written": _utc_now()},
                "result": None,
                "commit": None,
                "terminal_outcome": None,
                "terminal_detail": None,
            }
        )

    def begin_rollback(
        self,
        operation_id: str,
        *,
        state_root: Path,
        task_id: str,
        run_id: str,
        target_commit: str,
        patch: str,
        expected_changed_files: tuple[str, ...],
        reason: str,
    ) -> dict[str, Any]:
        baseline_head = current_head(self.repository)
        baseline_files, expected_post_files = _patch_file_evidence(
            self.repository,
            patch,
            expected_changed_files,
        )
        return self._create(
            {
                "version": 1,
                "operation_id": operation_id,
                "action": "rollback",
                "repository": str(self.repository),
                "state_root": str(state_root.resolve()),
                "task_id": task_id,
                "run_id": run_id,
                "target_commit": target_commit,
                "reason": redact(reason),
                "baseline_head": baseline_head,
                "patch_sha256": _sha256(patch),
                "expected_changed_files": list(expected_changed_files),
                "baseline_files": baseline_files,
                "expected_post_files": expected_post_files,
                "created_at_utc": _utc_now(),
                "phases": {"intent_written": _utc_now()},
                "rollback_record": None,
                "commit": None,
                "terminal_outcome": None,
                "terminal_detail": None,
            }
        )

    def phase(self, operation_id: str, name: str, **changes: Any) -> dict[str, Any]:
        record = self.load(operation_id)
        if record.get("terminal_outcome") is not None:
            raise OperationLedgerError(f"Operation is already terminal: {operation_id}")
        phases = dict(record["phases"])
        phases.setdefault(name, _utc_now())
        record["phases"] = phases
        record.update(changes)
        self._write(record)
        return record

    def terminal(self, operation_id: str, outcome: str, detail: str | None = None) -> dict[str, Any]:
        if outcome not in TERMINAL_OUTCOMES:
            raise OperationLedgerError(f"Unsupported terminal outcome: {outcome}")
        record = self.load(operation_id)
        existing = record.get("terminal_outcome")
        if existing is not None:
            if existing == outcome:
                return record
            raise OperationLedgerError(f"Operation already ended as {existing}: {operation_id}")
        record["terminal_outcome"] = outcome
        record["terminal_detail"] = redact(detail)
        record["finished_at_utc"] = _utc_now()
        self._write(record)
        return record

    def load(self, operation_id: str) -> dict[str, Any]:
        if not _safe_id(operation_id):
            raise OperationLedgerError("Operation id contains unsupported characters.")
        try:
            payload = read_json(self.root / f"{operation_id}.json")
        except DurableIOError as exc:
            raise OperationLedgerError(str(exc)) from exc
        return self._validate(payload, operation_id)

    def exists(self, operation_id: str) -> bool:
        if not _safe_id(operation_id):
            return False
        return (self.root / f"{operation_id}.json").is_file()

    def records(self) -> tuple[dict[str, Any], ...]:
        if not self.root.exists():
            return ()
        records: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                payload = read_json(path)
                records.append(self._validate(payload, path.stem))
            except (DurableIOError, OperationLedgerError) as exc:
                records.append(
                    {
                        "operation_id": path.stem,
                        "action": "unknown",
                        "_corruption": str(exc),
                        "terminal_outcome": None,
                    }
                )
        return tuple(records)

    def reconcile(self, *, auto_complete_state: bool = False) -> ReconciliationReport:
        items: list[ReconciliationItem] = []
        for record in self.records():
            item = self._classify(record)
            if auto_complete_state and item.safe_action == "complete_state":
                self._complete_state(record)
                item = self._classify(self.load(item.operation_id))
            elif auto_complete_state and item.safe_action == "finish_ledger":
                self.terminal(item.operation_id, "complete", "State evidence already complete.")
                item = self._classify(self.load(item.operation_id))
            items.append(item)
        return ReconciliationReport(self.repository, tuple(items))

    def assert_no_blocking(self) -> None:
        report = self.reconcile(auto_complete_state=True)
        if report.blocking:
            summary = "; ".join(
                f"{item.operation_id}={item.classification}" for item in report.blocking
            )
            raise ReconciliationBlockedError(
                "Canonical mutation is blocked by unresolved operation evidence: " + summary
            )

    def guarded_resolve(self, operation_id: str, action: str) -> ReconciliationItem:
        record = self.load(operation_id)
        item = self._classify(record)
        if action == "complete-state" and item.safe_action == "complete_state":
            self._complete_state(record)
        elif action == "finish-ledger" and item.safe_action == "finish_ledger":
            self.terminal(operation_id, "complete", "State evidence already complete.")
        elif action == "cancel-intent" and item.classification == "not_started":
            self.terminal(operation_id, "cancelled", "Operator cancelled clean, unapplied intent.")
        elif action == "restore-patch" and item.classification == "patch_applied":
            _restore_recorded_baseline(self.repository, record)
            if not repository_is_clean(self.repository) or current_head(self.repository) != record["baseline_head"]:
                raise ReconciliationBlockedError("Guarded patch restore did not return to the exact baseline.")
            self.terminal(operation_id, "reverted", "Operator restored exact expected paths to baseline.")
        else:
            raise ReconciliationBlockedError(
                f"Action {action} is unsafe for {operation_id} in state {item.classification}."
            )
        return self._classify(self.load(operation_id))

    def _complete_state(self, record: dict[str, Any]) -> None:
        state_root = Path(record["state_root"])
        store = HohStateStore(state_root)
        if record["action"] == "task_commit":
            result = _result_from_json(record["result"], self.repository, str(record["commit"]))
            store.record_result(
                str(record["task_id"]),
                str(record["started_at_utc"]),
                result,
                review_required=bool(record.get("review_required")),
                run_id=str(record["run_id"]),
            )
        elif record["action"] == "rollback":
            rollback = _rollback_from_json(record["rollback_record"])
            store.complete_rollback(rollback)
        else:
            raise OperationLedgerError(f"Cannot complete unknown operation action: {record['action']}")
        self.phase(str(record["operation_id"]), "state_recorded")
        self.terminal(str(record["operation_id"]), "complete", "StateStore completed from durable evidence.")

    def _classify(self, record: dict[str, Any]) -> ReconciliationItem:
        operation_id = str(record.get("operation_id", "unknown"))
        action = str(record.get("action", "unknown"))
        if record.get("_corruption"):
            return ReconciliationItem(
                operation_id, action, "corrupted", None, str(record["_corruption"])
            )
        if record.get("terminal_outcome") is not None:
            return ReconciliationItem(
                operation_id,
                action,
                str(record["terminal_outcome"]),
                None,
                str(record.get("terminal_detail") or ""),
                terminal=True,
            )
        try:
            state_complete = self._state_complete(record)
            commit = record.get("commit") or find_commit_by_trailer(
                self.repository, "HoH-Operation", operation_id
            )
            head = current_head(self.repository)
            clean = repository_is_clean(self.repository)
        except (GitError, StateStoreError, OperationLedgerError) as exc:
            return ReconciliationItem(operation_id, action, "contradictory", None, str(exc))
        if state_complete:
            return ReconciliationItem(
                operation_id, action, "state_complete", "finish_ledger",
                "Expected StateStore evidence already exists."
            )
        if commit is not None:
            try:
                full = resolve_commit(self.repository, str(commit))
                parents = commit_parents(self.repository, full)
                changed = commit_changed_files(self.repository, full)
            except GitError as exc:
                return ReconciliationItem(operation_id, action, "contradictory", None, str(exc))
            expected = tuple(str(path) for path in record["expected_changed_files"])
            if (
                len(parents) == 1
                and parents[0] == record["baseline_head"]
                and set(changed) == set(expected)
                and record.get("result" if action == "task_commit" else "rollback_record") is not None
                and clean
            ):
                if record.get("commit") != full:
                    record["commit"] = full
                    if action == "rollback" and isinstance(record.get("rollback_record"), dict):
                        record["rollback_record"]["rollback_commit"] = full
                    self._write(record)
                return ReconciliationItem(
                    operation_id, action, "commit_exists_state_missing", "complete_state",
                    "Exact trailer, parent, scope, clean worktree, and durable state payload match."
                )
            return ReconciliationItem(
                operation_id, action, "contradictory", None,
                "Commit evidence does not match baseline, scope, payload, or clean-worktree guards."
            )
        if head == record["baseline_head"] and clean:
            return ReconciliationItem(
                operation_id, action, "not_started", "cancel_intent",
                "HEAD and worktree still match the recorded baseline."
            )
        if head == record["baseline_head"] and not clean and "patch_applied" in record["phases"]:
            actual = _working_changed_files(self.repository)
            expected = tuple(str(path) for path in record["expected_changed_files"])
            if set(actual) == set(expected) and _working_tree_matches_patch(
                self.repository, record
            ):
                return ReconciliationItem(
                    operation_id, action, "patch_applied", "restore_patch",
                    "HEAD, dirty paths, and exact patch digest match recorded evidence."
                )
        return ReconciliationItem(
            operation_id, action, "contradictory", None,
            "Git HEAD/worktree does not match any safe reconciliation state."
        )

    def _state_complete(self, record: dict[str, Any]) -> bool:
        store = HohStateStore(Path(record["state_root"]))
        if record["action"] == "task_commit":
            matches = [run for run in store.history() if run.run_id == record["run_id"]]
            if not matches:
                return False
            if len(matches) != 1 or matches[0].work_item_id != record["task_id"]:
                raise OperationLedgerError("Run id exists with contradictory task evidence.")
            commit = record.get("commit")
            return commit is None or resolve_commit(self.repository, matches[0].commit or "") == resolve_commit(
                self.repository, str(commit)
            )
        matches = [
            rollback for rollback in store.rollback_records()
            if rollback.rollback_id == record["operation_id"]
        ]
        if not matches:
            return False
        if len(matches) != 1 or matches[0].work_item_id != record["task_id"]:
            raise OperationLedgerError("Rollback id exists with contradictory evidence.")
        return True

    def _create(self, record: dict[str, Any]) -> dict[str, Any]:
        path = self.root / f"{record['operation_id']}.json"
        if path.exists():
            existing = self.load(str(record["operation_id"]))
            comparable = dict(existing)
            comparable.pop("record_sha256", None)
            if comparable == record:
                return existing
            raise OperationLedgerError(f"Operation id already exists: {record['operation_id']}")
        self._write(record)
        return record

    def _write(self, record: dict[str, Any]) -> None:
        integrity_payload = dict(record)
        integrity_payload.pop("record_sha256", None)
        record["record_sha256"] = hashlib.sha256(
            json.dumps(
                integrity_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        atomic_write_json(self.root / f"{record['operation_id']}.json", record)

    def _validate(self, payload: Any, expected_id: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise OperationLedgerError(f"Operation record must be an object: {expected_id}")
        required = {
            "version", "operation_id", "action", "repository", "state_root", "task_id",
            "run_id", "baseline_head", "patch_sha256", "expected_changed_files",
            "baseline_files", "expected_post_files", "phases", "terminal_outcome",
            "record_sha256",
        }
        missing = required - payload.keys()
        if missing:
            raise OperationLedgerError(
                f"Operation record {expected_id} misses fields: {','.join(sorted(missing))}"
            )
        if payload["operation_id"] != expected_id or payload["version"] != 1:
            raise OperationLedgerError(f"Operation identity/version mismatch: {expected_id}")
        if payload["action"] not in {"task_commit", "rollback"}:
            raise OperationLedgerError(f"Unsupported operation action: {payload['action']}")
        if Path(str(payload["repository"])).resolve() != self.repository:
            raise OperationLedgerError(f"Operation repository mismatch: {expected_id}")
        if (
            not isinstance(payload["patch_sha256"], str)
            or len(payload["patch_sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in payload["patch_sha256"])
        ):
            raise OperationLedgerError(f"Operation patch digest is invalid: {expected_id}")
        integrity_payload = dict(payload)
        recorded_digest = str(integrity_payload.pop("record_sha256"))
        actual_digest = hashlib.sha256(
            json.dumps(
                integrity_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if recorded_digest != actual_digest:
            raise OperationLedgerError(f"Operation record digest mismatch: {expected_id}")
        if (
            not isinstance(payload["phases"], dict)
            or not isinstance(payload["expected_changed_files"], list)
            or not isinstance(payload["baseline_files"], dict)
            or not isinstance(payload["expected_post_files"], dict)
        ):
            raise OperationLedgerError(f"Operation phase/scope shape is invalid: {expected_id}")
        expected_keys = {str(path) for path in payload["expected_changed_files"]}
        if set(payload["baseline_files"]) != expected_keys or set(payload["expected_post_files"]) != expected_keys:
            raise OperationLedgerError(f"Operation file evidence does not match scope: {expected_id}")
        for evidence in (*payload["baseline_files"].values(), *payload["expected_post_files"].values()):
            _validate_file_evidence(evidence, expected_id)
        for relative in payload["expected_changed_files"]:
            candidate = Path(str(relative))
            if candidate.is_absolute() or ".." in candidate.parts:
                raise OperationLedgerError(f"Operation scope contains unsafe path: {expected_id}")
        return payload


def result_to_json(result: HarnessRunResult) -> dict[str, Any]:
    semantic = result.semantic_verification
    evidence = semantic.evidence if semantic else None
    return {
        "pre_apply": {
            "ok": result.pre_apply.ok,
            "findings": redact(list(result.pre_apply.findings)),
            "metrics": dict(result.pre_apply.metrics),
        },
        "post_apply": {
            "ok": result.post_apply.ok,
            "findings": redact(list(result.post_apply.findings)),
            "metrics": dict(result.post_apply.metrics),
        },
        "command_results": [
            {
                "command": item.command,
                "return_code": item.return_code,
                "stdout": redact(item.stdout),
                "stderr": redact(item.stderr),
            }
            for item in result.command_results
        ],
        "semantic_verification": (
            {
                "decision": semantic.decision,
                "summary": redact(semantic.summary),
                "findings": redact(list(semantic.findings)),
                "evidence": (
                    {
                        "provider": evidence.provider,
                        "model": evidence.model,
                        "endpoint": evidence.endpoint,
                        "request_id": evidence.request_id,
                        "attempts": evidence.attempts,
                        "latency_ms": evidence.latency_ms,
                        "input_tokens": evidence.input_tokens,
                        "output_tokens": evidence.output_tokens,
                        "total_tokens": evidence.total_tokens,
                        "request_sha256": evidence.request_sha256,
                        "response_sha256": evidence.response_sha256,
                    }
                    if evidence else None
                ),
            }
            if semantic else None
        ),
    }


def rollback_to_json(record: RollbackRecord) -> dict[str, Any]:
    return {
        "rollback_id": record.rollback_id,
        "created_at_utc": record.created_at_utc,
        "work_item_id": record.work_item_id,
        "run_id": record.run_id,
        "target_commit": record.target_commit,
        "rollback_commit": record.rollback_commit,
        "reason": redact(record.reason),
        "ok": record.ok,
        "changed_files": list(record.changed_files),
        "command_results": [
            {
                "command": item.command,
                "return_code": item.return_code,
                "stdout": redact(item.stdout),
                "stderr": redact(item.stderr),
            }
            for item in record.command_results
        ],
        "error": redact(record.error),
    }


def crash_point(name: str) -> None:
    if (
        os.environ.get("HOH_ENABLE_TEST_CRASH_INJECTION") == "1"
        and os.environ.get("HOH_TEST_CRASH_POINT") == name
    ):
        os._exit(91)


def _result_from_json(payload: Any, repository: Path, commit: str) -> HarnessRunResult:
    if not isinstance(payload, dict):
        raise OperationLedgerError("Missing durable task result payload.")
    semantic_payload = payload.get("semantic_verification")
    semantic = None
    if semantic_payload is not None:
        raw_evidence = semantic_payload.get("evidence")
        evidence = ModelInvocationEvidence(**raw_evidence) if raw_evidence else None
        semantic = SemanticVerificationReport(
            decision=str(semantic_payload["decision"]),
            summary=str(semantic_payload["summary"]),
            findings=tuple(semantic_payload.get("findings", [])),
            evidence=evidence,
        )
    return HarnessRunResult(
        repository=repository,
        work_item_id="",
        commit=commit,
        pre_apply=VerificationReport(
            ok=bool(payload["pre_apply"]["ok"]),
            findings=tuple(payload["pre_apply"].get("findings", [])),
            metrics=dict(payload["pre_apply"].get("metrics", {})),
        ),
        post_apply=VerificationReport(
            ok=bool(payload["post_apply"]["ok"]),
            findings=tuple(payload["post_apply"].get("findings", [])),
            metrics=dict(payload["post_apply"].get("metrics", {})),
        ),
        command_results=tuple(CommandResult(**item) for item in payload.get("command_results", [])),
        semantic_verification=semantic,
    )


def _rollback_from_json(payload: Any) -> RollbackRecord:
    if not isinstance(payload, dict):
        raise OperationLedgerError("Missing durable rollback record payload.")
    return RollbackRecord(
        rollback_id=str(payload["rollback_id"]),
        created_at_utc=str(payload["created_at_utc"]),
        work_item_id=str(payload["work_item_id"]),
        run_id=str(payload["run_id"]),
        target_commit=str(payload["target_commit"]),
        rollback_commit=str(payload["rollback_commit"]) if payload.get("rollback_commit") else None,
        reason=str(payload["reason"]),
        ok=bool(payload["ok"]),
        changed_files=tuple(payload["changed_files"]),
        command_results=tuple(CommandResult(**item) for item in payload["command_results"]),
        error=str(payload["error"]) if payload.get("error") else None,
    )


def _working_changed_files(repository: Path) -> tuple[str, ...]:
    import subprocess

    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise GitError(completed.stderr.strip() or "git status failed")
    return tuple(
        line[3:].replace("\\", "/").strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    )


def _patch_file_evidence(
    repository: Path,
    patch: str,
    expected_paths: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, Any]]:
    import subprocess
    import tempfile

    baseline_files: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as temporary:
        candidate = Path(temporary)
        for relative in expected_paths:
            source_path = repository / relative
            evidence = _path_evidence(source_path)
            baseline_files[relative] = evidence
            if evidence is not None:
                if evidence["kind"] != "file":
                    raise OperationLedgerError(
                        f"Patch evidence supports regular files only: {relative}"
                    )
                destination = candidate / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source_path.read_bytes())
        # Feed the patch as bytes. Text mode translates its "\n" to "\r\n" on Windows,
        # and the CRLF context then matches no line in the LF files, so every task
        # failed here with "patch does not apply".
        applied = subprocess.run(
            ["git", "apply", "--unsafe-paths"],
            cwd=candidate,
            input=patch.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        if applied.returncode != 0:
            raise OperationLedgerError(
                "Could not derive expected post-image from worker patch: "
                + (applied.stderr.decode("utf-8", "replace").strip() or "git apply failed")
            )
        expected_post_files = {
            relative: _path_evidence(candidate / relative)
            for relative in expected_paths
        }
    return baseline_files, expected_post_files


def _working_tree_matches_patch(repository: Path, record: dict[str, Any]) -> bool:
    return all(
        _path_evidence(repository / relative) == record["expected_post_files"][relative]
        for relative in record["expected_changed_files"]
    )


def _restore_recorded_baseline(repository: Path, record: dict[str, Any]) -> None:
    import subprocess

    tracked = tuple(
        relative
        for relative, evidence in record["baseline_files"].items()
        if evidence is not None
    )
    if tracked:
        restored = subprocess.run(
            [
                "git",
                "restore",
                "--source",
                str(record["baseline_head"]),
                "--staged",
                "--worktree",
                "--",
                *tracked,
            ],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        if restored.returncode != 0:
            raise GitError(restored.stderr.strip() or "git restore baseline paths failed")
    for relative, evidence in record["baseline_files"].items():
        if evidence is not None:
            continue
        path = repository / relative
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            raise GitError(f"Expected a file path while restoring baseline: {relative}")


def _path_evidence(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        target = os.readlink(path).encode("utf-8")
        return {
            "kind": "symlink",
            "sha256": hashlib.sha256(target).hexdigest(),
            "size": len(target),
        }
    if not path.exists():
        return None
    if not path.is_file():
        return {"kind": "other", "sha256": "", "size": 0}
    return _bytes_evidence(path.read_bytes())


def _bytes_evidence(content: bytes) -> dict[str, Any]:
    return {
        "kind": "file",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }


def _validate_file_evidence(evidence: Any, operation_id: str) -> None:
    if evidence is None:
        return
    if not isinstance(evidence, dict) or set(evidence) != {"kind", "sha256", "size"}:
        raise OperationLedgerError(f"Invalid operation file evidence: {operation_id}")
    if evidence["kind"] not in {"file", "symlink"}:
        raise OperationLedgerError(f"Unsupported operation file kind: {operation_id}")
    digest = evidence["sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or not isinstance(evidence["size"], int)
        or evidence["size"] < 0
    ):
        raise OperationLedgerError(f"Invalid operation file digest/size: {operation_id}")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_id(value: str) -> bool:
    return bool(value) and all(character.isalnum() or character in "-_" for character in value)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
