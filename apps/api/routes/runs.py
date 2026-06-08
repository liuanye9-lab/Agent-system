from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apps.api.audit import build_audit_event
from apps.api.dependencies import get_repository, get_workflow_runner
from apps.api.pagination import DEFAULT_QUERY_LIMIT, LimitQuery, OffsetQuery
from apps.api.redaction import dump_redacted_model
from apps.api.run_requests import fingerprint_run_request
from apps.api.security import require_scope
from packages.workflow_core.models import ActorContext, EvalResult, TraceRecord, WorkflowPackage, WorkflowRun
from packages.workflow_core.models.enums import NodeExecutionStatus, WorkflowRunStatus
from packages.workflow_core.runtime import ToolExecutionContext, WorkflowRunner
from packages.workflow_core.storage import WorkflowRepository

router = APIRouter(prefix="/api/runs", tags=["runs"])


class ApprovalRequest(BaseModel):
    approved: bool = True
    approval_payload: dict[str, Any] = Field(default_factory=dict)
    max_steps: int = Field(default=50, ge=1, le=200)
    max_retries: int = Field(default=1, ge=0, le=5)


class CancelRunRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class RerunRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    max_steps: int = Field(default=50, ge=1, le=200)
    max_retries: int = Field(default=1, ge=0, le=5)
    shadow_mode: bool | None = None
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )


class ShadowComparisonRequest(BaseModel):
    expected_output: dict[str, Any] = Field(default_factory=dict)
    pass_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    notes: str | None = Field(default=None, max_length=1000)


@router.get("")
def list_runs(
    workflow_id: str | None = None,
    status: WorkflowRunStatus | None = None,
    limit: LimitQuery = DEFAULT_QUERY_LIMIT,
    offset: OffsetQuery = 0,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> list[dict[str, Any]]:
    return [
        {
            "run_id": run.run_id,
            "workflow_id": run.workflow_id,
            "workflow_version": run.workflow_version,
            "rerun_of_run_id": run.rerun_of_run_id,
            "shadow_mode": run.shadow_mode,
            "status": run.status,
            "current_node_id": run.current_node_id,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
        }
        for run in repository.list_runs(workflow_id=workflow_id, status=status, limit=limit, offset=offset)
    ]


@router.get("/{run_id}")
def get_run(
    run_id: str,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return dump_redacted_model(run)


@router.get("/{run_id}/diagnostics")
def get_run_diagnostics(
    run_id: str,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _build_run_diagnostics(run)


@router.get("/{run_id}/shadow-comparisons")
def list_shadow_comparisons(
    run_id: str,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> list[dict[str, Any]]:
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return [dump_redacted_model(result) for result in _shadow_comparison_results(repository, run)]


@router.post("/{run_id}/shadow-comparisons")
def compare_shadow_run(
    run_id: str,
    request: ShadowComparisonRequest,
    actor: ActorContext = Depends(require_scope("workflow:evaluate")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if not run.shadow_mode:
        raise HTTPException(status_code=409, detail="only shadow runs can be compared")
    if run.status not in _TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="only terminal shadow runs can be compared")

    comparison = _compare_expected_output(run.output_payload, request.expected_output, request.pass_threshold)
    result = EvalResult(
        eval_id=f"shadow-comparison-{run.run_id}-{uuid4().hex[:8]}",
        workflow_id=run.workflow_id,
        score=comparison["score"],
        passed=comparison["passed"],
        reason=comparison["reason"],
        details={
            "eval_type": "shadow_comparison",
            "run_id": run.run_id,
            "workflow_version": run.workflow_version,
            "shadow_mode": run.shadow_mode,
            "run_status": run.status,
            "pass_threshold": request.pass_threshold,
            "notes_present": request.notes is not None,
            **comparison["details"],
        },
    )
    repository.save_eval_results(run.workflow_id, [result])
    repository.save_audit_event(
        build_audit_event(
            event_type="workflow_run_shadow_comparison",
            action="compare",
            status="succeeded",
            actor=actor,
            workflow_id=run.workflow_id,
            workflow_version=run.workflow_version,
            run_id=run.run_id,
            resource_type="workflow_run",
            resource_id=run.run_id,
            reason=request.notes,
            details={
                "eval_id": result.eval_id,
                "passed": result.passed,
                "score": result.score,
                "pass_threshold": request.pass_threshold,
                "compared_path_count": result.details["compared_path_count"],
                "matched_path_count": result.details["matched_path_count"],
                "missing_path_count": len(result.details["missing_paths"]),
                "mismatched_path_count": len(result.details["mismatched_paths"]),
                "notes_present": request.notes is not None,
            },
        )
    )
    return dump_redacted_model(result)


@router.post("/{run_id}/rerun")
def rerun_run(
    run_id: str,
    request: RerunRequest,
    actor: ActorContext = Depends(require_scope("workflow:run")),
    repository: WorkflowRepository = Depends(get_repository),
    runner: WorkflowRunner = Depends(get_workflow_runner),
) -> dict[str, Any]:
    source_run = repository.get_run(run_id)
    if source_run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if source_run.status not in _TERMINAL_RUN_STATUSES:
        _save_rerun_audit_event(
            repository=repository,
            actor=actor,
            source_run=source_run,
            rerun=None,
            action="rerun",
            status="failed",
            reason=request.reason,
            details={
                "gate": "source_status",
                "message": "only terminal runs can be rerun",
                "source_status": source_run.status,
            },
        )
        raise HTTPException(
            status_code=409,
            detail={
                "message": "only terminal runs can be rerun",
                "status": source_run.status,
            },
        )
    workflow = _workflow_for_run(repository, source_run)
    if workflow is None:
        _save_rerun_audit_event(
            repository=repository,
            actor=actor,
            source_run=source_run,
            rerun=None,
            action="rerun",
            status="failed",
            reason=request.reason,
            details={
                "gate": "workflow_version_lookup",
                "message": "workflow version for source run not found",
            },
        )
        raise HTTPException(status_code=409, detail="workflow version for source run not found")

    shadow_mode = source_run.shadow_mode if request.shadow_mode is None else request.shadow_mode
    request_fingerprint = fingerprint_run_request(
        request_kind="run_rerun",
        workflow_id=workflow.workflow_id,
        workflow_version=workflow.version,
        input_payload=source_run.input_payload,
        max_steps=request.max_steps,
        max_retries=request.max_retries,
        shadow_mode=shadow_mode,
        source_run_id=source_run.run_id,
    )
    if request.idempotency_key:
        existing_run = repository.get_run_by_idempotency_key(workflow.workflow_id, request.idempotency_key)
        if existing_run is not None:
            if existing_run.request_fingerprint != request_fingerprint:
                _save_rerun_audit_event(
                    repository=repository,
                    actor=actor,
                    source_run=source_run,
                    rerun=existing_run,
                    action="replay",
                    status="failed",
                    reason=request.reason,
                    details={
                        "gate": "idempotency_conflict",
                        "message": "idempotency key was already used with a different rerun request",
                    },
                )
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "idempotency key was already used with a different rerun request",
                        "workflow_id": workflow.workflow_id,
                        "existing_run_id": existing_run.run_id,
                    },
                )
            _save_rerun_audit_event(
                repository=repository,
                actor=actor,
                source_run=source_run,
                rerun=existing_run,
                action="replay",
                status="succeeded",
                reason=request.reason,
                details={
                    "source_status": source_run.status,
                    "result_status": existing_run.status,
                    "shadow_mode": existing_run.shadow_mode,
                    "trace_count": len(existing_run.traces),
                },
            )
            return dump_redacted_model(existing_run)

    rerun = runner.run(
        workflow,
        input_payload=source_run.input_payload,
        rerun_of_run_id=source_run.run_id,
        idempotency_key=request.idempotency_key,
        request_fingerprint=request_fingerprint if request.idempotency_key else None,
        max_steps=request.max_steps,
        max_retries=request.max_retries,
        shadow_mode=shadow_mode,
        actor_context=_tool_execution_context(actor),
        checkpoint=repository.save_run,
    )
    repository.save_run(rerun)
    _save_rerun_audit_event(
        repository=repository,
        actor=actor,
        source_run=source_run,
        rerun=rerun,
        action="rerun",
        status="succeeded",
        reason=request.reason,
        details={
            "source_status": source_run.status,
            "result_status": rerun.status,
            "input_keys": sorted(source_run.input_payload.keys()),
            "max_steps": request.max_steps,
            "max_retries": request.max_retries,
            "shadow_mode": shadow_mode,
            "idempotency_key_present": request.idempotency_key is not None,
            "trace_count": len(rerun.traces),
        },
    )
    return dump_redacted_model(rerun)


@router.get("/{run_id}/traces")
def get_run_traces(
    run_id: str,
    limit: LimitQuery = DEFAULT_QUERY_LIMIT,
    offset: OffsetQuery = 0,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> list[dict[str, Any]]:
    if repository.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return [dump_redacted_model(trace) for trace in repository.list_traces(run_id=run_id, limit=limit, offset=offset)]


@router.post("/{run_id}/cancel")
def cancel_run(
    run_id: str,
    request: CancelRunRequest,
    actor: ActorContext = Depends(require_scope("workflow:cancel")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status not in {WorkflowRunStatus.CREATED, WorkflowRunStatus.RUNNING, WorkflowRunStatus.PAUSED}:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "run cannot be canceled from its current status",
                "status": run.status,
            },
        )

    previous_status = run.status
    canceled_at = datetime.now(timezone.utc)
    run.status = WorkflowRunStatus.CANCELED
    run.output_payload = {
        **run.output_payload,
        "canceled": True,
        "cancel_reason": request.reason,
        "canceled_by": {
            "actor_id": actor.actor_id,
            "actor_role": actor.role,
            "actor_display_name": actor.display_name,
        },
    }
    run.traces.append(
        TraceRecord(
            run_id=run.run_id,
            workflow_id=run.workflow_id,
            workflow_version=run.workflow_version,
            node_id=run.current_node_id or "workflow_run",
            input_snapshot={"cancel_reason_present": request.reason is not None},
            output_snapshot={"status": WorkflowRunStatus.CANCELED, "previous_status": previous_status},
            status=NodeExecutionStatus.SKIPPED,
            error="workflow run canceled",
            started_at=canceled_at,
            ended_at=canceled_at,
            duration_ms=0,
        )
    )
    run.updated_at = canceled_at
    repository.save_run(run)
    repository.save_audit_event(
        build_audit_event(
            event_type="workflow_run_cancel",
            action="cancel",
            status="succeeded",
            actor=actor,
            workflow_id=run.workflow_id,
            workflow_version=run.workflow_version,
            run_id=run.run_id,
            resource_type="workflow_run",
            resource_id=run.run_id,
            reason=request.reason,
            details={
                "previous_status": previous_status,
                "result_status": run.status,
                "current_node_id": run.current_node_id,
            },
        )
    )
    return dump_redacted_model(run)


@router.post("/{run_id}/approval")
def submit_approval(
    run_id: str,
    request: ApprovalRequest,
    actor: ActorContext = Depends(require_scope("workflow:approve")),
    repository: WorkflowRepository = Depends(get_repository),
    runner: WorkflowRunner = Depends(get_workflow_runner),
) -> dict[str, Any]:
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status != WorkflowRunStatus.PAUSED:
        raise HTTPException(status_code=409, detail="run is not paused for approval")
    workflow = _workflow_for_run(repository, run)
    if workflow is None:
        raise HTTPException(status_code=409, detail="workflow version for run not found")
    _ensure_actor_can_approve(workflow, run, actor)
    approval_payload = {
        **request.approval_payload,
        "actor_id": actor.actor_id,
        "actor_role": actor.role,
        "actor_display_name": actor.display_name,
    }
    resumed = runner.resume(
        workflow_package=workflow,
        run=run,
        approved=request.approved,
        approval_payload=approval_payload,
        max_steps=request.max_steps,
        max_retries=request.max_retries,
        actor_context=_tool_execution_context(actor),
        checkpoint=repository.save_run,
    )
    repository.save_run(resumed)
    repository.save_audit_event(
        build_audit_event(
            event_type="run_approval",
            action="approve" if request.approved else "reject",
            status="succeeded",
            actor=actor,
            workflow_id=resumed.workflow_id,
            workflow_version=resumed.workflow_version,
            run_id=resumed.run_id,
            resource_type="workflow_run",
            resource_id=resumed.run_id,
            reason=str(request.approval_payload.get("reason")) if request.approval_payload.get("reason") else None,
            details={
                "approved": request.approved,
                "current_node_id": run.current_node_id,
                "result_status": resumed.status,
                "max_steps": request.max_steps,
                "max_retries": request.max_retries,
            },
        )
    )
    return dump_redacted_model(resumed)


_TERMINAL_RUN_STATUSES = {
    WorkflowRunStatus.COMPLETED,
    WorkflowRunStatus.FAILED,
    WorkflowRunStatus.REJECTED,
    WorkflowRunStatus.CANCELED,
}


def _tool_execution_context(actor: ActorContext) -> ToolExecutionContext:
    return ToolExecutionContext(
        actor_id=actor.actor_id,
        actor_role=actor.role,
        actor_scopes=tuple(actor.scopes),
    )


def _save_rerun_audit_event(
    *,
    repository: WorkflowRepository,
    actor: ActorContext,
    source_run: WorkflowRun,
    rerun: WorkflowRun | None,
    action: str,
    status: str,
    reason: str | None,
    details: dict[str, Any],
) -> None:
    resource_id = rerun.run_id if rerun is not None else source_run.run_id
    repository.save_audit_event(
        build_audit_event(
            event_type="workflow_run_rerun",
            action=action,
            status=status,
            actor=actor,
            workflow_id=source_run.workflow_id,
            workflow_version=source_run.workflow_version,
            run_id=rerun.run_id if rerun is not None else source_run.run_id,
            resource_type="workflow_run",
            resource_id=resource_id,
            reason=reason,
            details={
                "source_run_id": source_run.run_id,
                "rerun_run_id": rerun.run_id if rerun is not None else None,
                **details,
            },
        )
    )


def _ensure_actor_can_approve(workflow: WorkflowPackage, run: WorkflowRun, actor: ActorContext) -> None:
    node = next((candidate for candidate in workflow.process_spec.nodes if candidate.node_id == run.current_node_id), None)
    if node is None:
        raise HTTPException(status_code=409, detail="paused node not found in workflow")

    allowed_roles = {
        role
        for policy in workflow.tool_policies
        if policy.tool_id in node.tool_ids
        for role in policy.allowed_roles
    }
    if actor.role == "workflow-admin" or actor.role in allowed_roles:
        return
    raise HTTPException(
        status_code=403,
        detail={
            "message": "actor role is not allowed to approve this node",
            "actor_role": actor.role,
            "allowed_roles": sorted(allowed_roles),
        },
    )


def _workflow_for_run(repository: WorkflowRepository, run: WorkflowRun) -> WorkflowPackage | None:
    if run.workflow_version:
        return repository.get_workflow_version(run.workflow_id, run.workflow_version)
    return repository.get_workflow(run.workflow_id)


def _shadow_comparison_results(repository: WorkflowRepository, run: WorkflowRun) -> list[EvalResult]:
    return [
        result
        for result in repository.list_eval_results(run.workflow_id)
        if result.details.get("eval_type") == "shadow_comparison"
        and result.details.get("run_id") == run.run_id
    ]


def _compare_expected_output(
    actual_output: dict[str, Any],
    expected_output: dict[str, Any],
    pass_threshold: float,
) -> dict[str, Any]:
    expected_paths = _flatten_leaf_paths(expected_output)
    if not expected_paths:
        raise HTTPException(status_code=422, detail="expected_output must contain at least one comparable field")

    matched_paths: list[str] = []
    missing_paths: list[str] = []
    mismatched_paths: list[str] = []
    for path, expected_value in expected_paths.items():
        found, actual_value = _get_path_value(actual_output, path)
        path_label = _format_path(path)
        if not found:
            missing_paths.append(path_label)
        elif actual_value == expected_value:
            matched_paths.append(path_label)
        else:
            mismatched_paths.append(path_label)

    score = len(matched_paths) / len(expected_paths)
    passed = score >= pass_threshold
    return {
        "score": score,
        "passed": passed,
        "reason": (
            f"shadow comparison {'passed' if passed else 'failed'}: "
            f"{len(matched_paths)}/{len(expected_paths)} expected paths matched"
        ),
        "details": {
            "compared_path_count": len(expected_paths),
            "matched_path_count": len(matched_paths),
            "matched_paths": matched_paths,
            "missing_paths": missing_paths,
            "mismatched_paths": mismatched_paths,
        },
    }


def _flatten_leaf_paths(payload: Any, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    if isinstance(payload, dict):
        flattened: dict[tuple[str, ...], Any] = {}
        for key, value in payload.items():
            flattened.update(_flatten_leaf_paths(value, (*prefix, str(key))))
        return flattened
    return {prefix: payload} if prefix else {}


def _get_path_value(payload: Any, path: tuple[str, ...]) -> tuple[bool, Any]:
    current = payload
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _format_path(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _build_run_diagnostics(run: WorkflowRun) -> dict[str, Any]:
    trace_counts: dict[str, int] = {}
    for trace in run.traces:
        status = str(trace.status)
        trace_counts[status] = trace_counts.get(status, 0) + 1

    failed_traces = [trace for trace in run.traces if trace.status == NodeExecutionStatus.FAILED]
    latest_failed_trace = failed_traces[-1] if failed_traces else None
    error = _string_payload_value(run.output_payload, "error") or (latest_failed_trace.error if latest_failed_trace else None)
    node_id = (
        _string_payload_value(run.output_payload, "failed_node_id")
        or _string_payload_value(run.output_payload, "pending_node_id")
        or (latest_failed_trace.node_id if latest_failed_trace else run.current_node_id)
    )
    retry_budget_exhausted = bool(
        latest_failed_trace
        and latest_failed_trace.retryable
        and latest_failed_trace.attempt >= latest_failed_trace.max_attempts
    )

    failure = None
    if run.status == WorkflowRunStatus.FAILED or latest_failed_trace is not None:
        failure = {
            "node_id": node_id,
            "error": error,
            "attempt": _int_payload_value(run.output_payload, "attempt")
            or (latest_failed_trace.attempt if latest_failed_trace else None),
            "max_attempts": _int_payload_value(run.output_payload, "max_attempts")
            or (latest_failed_trace.max_attempts if latest_failed_trace else None),
            "retryable": _bool_payload_value(run.output_payload, "retryable")
            if "retryable" in run.output_payload
            else (latest_failed_trace.retryable if latest_failed_trace else None),
            "retry_budget_exhausted": retry_budget_exhausted,
            "pending_node_id": _string_payload_value(run.output_payload, "pending_node_id"),
            "executed_steps": _int_payload_value(run.output_payload, "executed_steps"),
            "max_steps": _int_payload_value(run.output_payload, "max_steps"),
        }

    return {
        "run_id": run.run_id,
        "workflow_id": run.workflow_id,
        "workflow_version": run.workflow_version,
        "status": run.status,
        "current_node_id": run.current_node_id,
        "shadow_mode": run.shadow_mode,
        "is_terminal": run.status in _TERMINAL_RUN_STATUSES,
        "trace_count": len(run.traces),
        "trace_counts": trace_counts,
        "failure": failure,
        "approval": {
            "required": run.status == WorkflowRunStatus.PAUSED,
            "node_id": run.current_node_id if run.status == WorkflowRunStatus.PAUSED else None,
        },
        "recommended_actions": _recommended_run_actions(run, error, retry_budget_exhausted),
        "updated_at": run.updated_at.isoformat(),
    }


def _recommended_run_actions(
    run: WorkflowRun,
    error: str | None,
    retry_budget_exhausted: bool,
) -> list[str]:
    if run.status == WorkflowRunStatus.PAUSED:
        return ["Review the paused node context, then approve or reject the run."]
    if run.status == WorkflowRunStatus.CANCELED:
        return ["Review the cancel audit event; rerun the terminal run if cancellation was accidental."]
    if run.status == WorkflowRunStatus.REJECTED:
        return ["Review the rejection payload; rerun only after the business input is corrected."]
    if run.status == WorkflowRunStatus.COMPLETED:
        if run.shadow_mode:
            return ["Compare the shadow output with human handling before enabling live write execution."]
        return ["No recovery action is required."]
    if run.status in {WorkflowRunStatus.CREATED, WorkflowRunStatus.RUNNING}:
        return ["Monitor the run until it reaches a terminal status or pauses for approval."]

    if error == "max_steps_exceeded":
        return [
            "Inspect the pending node and graph transitions.",
            "Rerun with a larger max_steps budget after confirming the workflow is not looping.",
        ]
    if error and ("input_contract_validation_failed" in error or "output_contract_validation_failed" in error):
        return [
            "Inspect the failed node data contract and its adjacent node payloads.",
            "Fix the package contract or upstream output, then rerun the terminal run.",
        ]
    if retry_budget_exhausted:
        return [
            "Inspect the transient tool failure and retry metadata.",
            "Increase max_retries only after confirming the tool failure is recoverable.",
        ]
    return ["Inspect the failed trace, fix the workflow or tool behavior, then rerun the terminal run."]


def _string_payload_value(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _int_payload_value(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _bool_payload_value(payload: dict[str, Any], key: str) -> bool | None:
    value = payload.get(key)
    return value if isinstance(value, bool) else None
