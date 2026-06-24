from __future__ import annotations

from collections import Counter
import json
from math import ceil
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from apps.api.audit import build_audit_event
from apps.api.dependencies import get_metric_collector, get_optimization_loop, get_repository
from apps.api.pagination import DEFAULT_QUERY_LIMIT, LimitQuery, OffsetQuery
from apps.api.routes.workflows import _build_release_readiness, dump_model
from apps.api.security import require_scope
from apps.api.settings import settings
from packages.workflow_core.governance.metrics import MetricCollector
from packages.workflow_core.governance.optimization_loop import OptimizationLoop
from packages.workflow_core.governance.trace_exporter import OTLPTraceExporter, OTLPTraceExporterConfig, TraceExportError
from packages.workflow_core.models import ActorContext, WorkflowPackage
from packages.workflow_core.models.enums import NodeExecutionStatus, PermissionLevel, RiskLevel, WorkflowRunStatus
from packages.workflow_core.ops import (
    RepositorySnapshot,
    RetentionPolicy,
    apply_retention_policy,
    build_retention_report,
    export_repository_snapshot,
    import_repository_snapshot,
    preview_repository_snapshot_import,
)
from packages.workflow_core.storage import WorkflowRepository

router = APIRouter(prefix="/api/governance", tags=["governance"])

_RUN_REPORT_SAMPLE_LIMIT = 20
_ACTIVE_RUN_STATUSES = {
    WorkflowRunStatus.CREATED,
    WorkflowRunStatus.RUNNING,
    WorkflowRunStatus.PAUSED,
}
_TERMINAL_RUN_STATUSES = {
    WorkflowRunStatus.COMPLETED,
    WorkflowRunStatus.FAILED,
    WorkflowRunStatus.REJECTED,
    WorkflowRunStatus.CANCELED,
}
_RECOVERY_RUN_STATUSES = {
    WorkflowRunStatus.FAILED,
    WorkflowRunStatus.REJECTED,
    WorkflowRunStatus.CANCELED,
}


class TraceExportRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=160)
    endpoint_url: str | None = Field(default=None, max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    service_name: str = Field(default="agent-workflow-builder", min_length=1, max_length=200)
    service_version: str = Field(default="0.1.0", min_length=1, max_length=80)
    deployment_environment: str | None = Field(default=None, max_length=120)
    include_payload: bool = True


class RetentionApplyRequest(BaseModel):
    workflow_id: str | None = Field(default=None, min_length=1, max_length=160)
    run_retention_days: int | None = Field(default=None, ge=1)
    eval_retention_days: int | None = Field(default=None, ge=1)
    audit_retention_days: int | None = Field(default=None, ge=1)
    sample_limit: int = Field(default=20, ge=1, le=200)
    categories: list[str] | None = Field(default=None, min_length=1, max_length=2)
    dry_run: bool = True
    confirm_apply: bool = False
    snapshot_acknowledged: bool = False
    reason: str | None = Field(default=None, min_length=1, max_length=500)


class SnapshotImportRequest(BaseModel):
    snapshot: RepositorySnapshot
    dry_run: bool = True
    confirm_import: bool = False
    skip_existing_eval_results: bool = True
    reason: str | None = Field(default=None, min_length=1, max_length=500)


@router.get("/metrics")
def get_metrics(
    workflow_id: str | None = None,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
    metric_collector: MetricCollector = Depends(get_metric_collector),
) -> dict[str, Any]:
    traces = repository.list_traces(workflow_id=workflow_id)
    metrics = metric_collector.collect(traces)
    failure_distribution = metrics.get("failure_reason_distribution", {})
    metrics["failure_reason_distribution"] = dict(failure_distribution)
    return metrics


@router.get("/run-report")
def get_run_report(
    workflow_id: str | None = None,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    workflows = _governance_workflows(repository, workflow_id)
    workflow_ids = {workflow.workflow_id for workflow in workflows}
    runs = [
        run
        for workflow in workflows
        for run in repository.list_runs(workflow_id=workflow.workflow_id)
    ]
    status_counts = Counter(str(run.status) for run in runs)
    pending_approvals = [run for run in runs if run.status == WorkflowRunStatus.PAUSED]
    recovery_runs = [run for run in runs if run.status in _RECOVERY_RUN_STATUSES]
    shadow_validation_runs = _shadow_validation_runs(workflows, runs, repository)
    run_durations = [_run_duration_ms(run) for run in runs]
    pending_node_counts = Counter(run.current_node_id or "workflow_run" for run in pending_approvals)
    recovery_reason_counts = Counter(_run_recovery_code(run) for run in recovery_runs)

    return {
        "workflow_id": workflow_id,
        "workflow_count": len(workflows),
        "workflow_ids": sorted(workflow_ids),
        "run_count": len(runs),
        "trace_count": sum(len(run.traces) for run in runs),
        "status_counts": {status.value: status_counts.get(status.value, 0) for status in WorkflowRunStatus},
        "active_run_count": sum(1 for run in runs if run.status in _ACTIVE_RUN_STATUSES),
        "terminal_run_count": sum(1 for run in runs if run.status in _TERMINAL_RUN_STATUSES),
        "live_run_count": sum(1 for run in runs if not run.shadow_mode),
        "shadow_run_count": sum(1 for run in runs if run.shadow_mode),
        "pending_approval_count": len(pending_approvals),
        "pending_node_counts": dict(pending_node_counts),
        "recovery_queue_count": len(recovery_runs),
        "recovery_reason_counts": dict(recovery_reason_counts),
        "shadow_validation_pending_count": len(shadow_validation_runs),
        "average_run_duration_ms": (sum(run_durations) / len(run_durations)) if run_durations else 0,
        "sample_limit": _RUN_REPORT_SAMPLE_LIMIT,
        "pending_approvals": [_run_queue_item(run) for run in _recent_runs(pending_approvals)],
        "recovery_queue": [_run_recovery_item(run) for run in _recent_runs(recovery_runs)],
        "shadow_validation_queue": [_run_queue_item(run) for run in _recent_runs(shadow_validation_runs)],
        "run_items": _run_items(
            pending_approval_count=len(pending_approvals),
            failed_run_count=status_counts.get(WorkflowRunStatus.FAILED.value, 0),
            rejected_run_count=status_counts.get(WorkflowRunStatus.REJECTED.value, 0),
            canceled_run_count=status_counts.get(WorkflowRunStatus.CANCELED.value, 0),
            shadow_validation_pending_count=len(shadow_validation_runs),
            active_run_count=sum(1 for run in runs if run.status in _ACTIVE_RUN_STATUSES),
        ),
    }


@router.get("/cost-report")
def get_cost_report(
    workflow_id: str | None = None,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    workflows = _governance_workflows(repository, workflow_id)
    workflow_ids = {workflow.workflow_id for workflow in workflows}
    runs = [
        run
        for workflow in workflows
        for run in repository.list_runs(workflow_id=workflow.workflow_id)
    ]
    traces = [trace for run in runs for trace in run.traces]
    node_costs = _node_costs(traces)
    estimated_input_tokens = sum(_estimated_tokens(trace.input_snapshot) for trace in traces)
    estimated_output_tokens = sum(_estimated_tokens(trace.output_snapshot) for trace in traces)
    estimated_total_tokens = estimated_input_tokens + estimated_output_tokens
    total_duration_ms = sum(trace.duration_ms or 0 for trace in traces)
    human_touch_count = sum(1 for trace in traces if trace.status == NodeExecutionStatus.APPROVAL_REQUIRED)
    retry_trace_count = sum(1 for trace in traces if trace.attempt > 1 or trace.retryable)

    return {
        "workflow_id": workflow_id,
        "workflow_count": len(workflows),
        "workflow_ids": sorted(workflow_ids),
        "run_count": len(runs),
        "trace_count": len(traces),
        "live_run_count": sum(1 for run in runs if not run.shadow_mode),
        "shadow_run_count": sum(1 for run in runs if run.shadow_mode),
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "estimated_total_tokens": estimated_total_tokens,
        "total_duration_ms": total_duration_ms,
        "average_trace_duration_ms": (total_duration_ms / len(traces)) if traces else 0,
        "human_touch_count": human_touch_count,
        "retry_trace_count": retry_trace_count,
        "node_costs": node_costs,
        "cost_items": _cost_items(node_costs, human_touch_count, retry_trace_count),
    }


@router.get("/quality-report")
def get_quality_report(
    workflow_id: str | None = None,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
    metric_collector: MetricCollector = Depends(get_metric_collector),
    optimization_loop: OptimizationLoop = Depends(get_optimization_loop),
) -> dict[str, Any]:
    workflows = _governance_workflows(repository, workflow_id)
    workflow_ids = {workflow.workflow_id for workflow in workflows}
    runs = [
        run
        for workflow in workflows
        for run in repository.list_runs(workflow_id=workflow.workflow_id)
    ]
    traces = [trace for run in runs for trace in run.traces]
    metrics = metric_collector.collect(traces)
    eval_results = [
        result
        for workflow in workflows
        for result in repository.list_eval_results(workflow.workflow_id)
    ]
    readiness_reports = [_build_release_readiness(workflow, repository) for workflow in workflows]
    suggestions = [
        suggestion
        for workflow in workflows
        for suggestion in optimization_loop.run(
            workflow.workflow_id,
            repository.list_traces(workflow_id=workflow.workflow_id),
            repository.list_eval_results(workflow.workflow_id),
        )
    ]
    eval_pass_count = sum(1 for result in eval_results if result.passed)
    eval_fail_count = len(eval_results) - eval_pass_count
    eval_pass_rate = (eval_pass_count / len(eval_results)) if eval_results else 0
    average_eval_score = (
        sum(result.score for result in eval_results) / len(eval_results)
        if eval_results
        else 0
    )
    shadow_comparisons = [
        result
        for result in eval_results
        if result.details.get("eval_type") == "shadow_comparison"
    ]
    passing_shadow_comparisons = [result for result in shadow_comparisons if result.passed]
    release_ready_count = sum(1 for report in readiness_reports if report["live_ready"])
    unready_count = len(readiness_reports) - release_ready_count
    readiness_rate = (release_ready_count / len(readiness_reports)) if readiness_reports else 0
    node_success_rate = float(metrics.get("node_success_rate", 0) or 0)
    quality_score = _quality_score(node_success_rate, eval_pass_rate, readiness_rate)
    failed_node_counts = _failed_node_counts(traces)
    suggestion_type_counts = Counter(str(suggestion.suggestion_type) for suggestion in suggestions)
    blocking_reason_counts = Counter(
        reason
        for report in readiness_reports
        for reason in report["blocking_reasons"]
    )

    return {
        "workflow_id": workflow_id,
        "workflow_count": len(workflows),
        "workflow_ids": sorted(workflow_ids),
        "run_count": len(runs),
        "trace_count": len(traces),
        "quality_score": quality_score,
        "quality_level": _quality_level(quality_score),
        "node_success_rate": node_success_rate,
        "tool_success_rate": float(metrics.get("tool_success_rate", 0) or 0),
        "average_trace_duration_ms": metrics.get("average_duration_ms", 0),
        "failed_node_counts": failed_node_counts,
        "eval_result_count": len(eval_results),
        "eval_pass_count": eval_pass_count,
        "eval_fail_count": eval_fail_count,
        "eval_pass_rate": eval_pass_rate,
        "average_eval_score": average_eval_score,
        "shadow_comparison_count": len(shadow_comparisons),
        "passing_shadow_comparison_count": len(passing_shadow_comparisons),
        "failed_shadow_comparison_count": len(shadow_comparisons) - len(passing_shadow_comparisons),
        "release_ready_version_count": release_ready_count,
        "unready_version_count": unready_count,
        "blocking_reason_counts": dict(blocking_reason_counts),
        "optimization_suggestion_count": len(suggestions),
        "suggestion_type_counts": dict(suggestion_type_counts),
        "quality_items": _quality_items(
            failed_node_counts=failed_node_counts,
            eval_fail_count=eval_fail_count,
            failed_shadow_comparison_count=len(shadow_comparisons) - len(passing_shadow_comparisons),
            unready_version_count=unready_count,
            node_success_rate=node_success_rate,
            eval_result_count=len(eval_results),
            suggestion_count=len(suggestions),
        ),
    }


@router.get("/risk-report")
def get_risk_report(
    workflow_id: str | None = None,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    workflows = _governance_workflows(repository, workflow_id)
    workflow_ids = {workflow.workflow_id for workflow in workflows}
    runs = [
        run
        for workflow in workflows
        for run in repository.list_runs(workflow_id=workflow.workflow_id)
    ]
    eval_results = [
        result
        for workflow in workflows
        for result in repository.list_eval_results(workflow.workflow_id)
    ]
    audit_events = [
        event
        for workflow in workflows
        for event in repository.list_audit_events(workflow_id=workflow.workflow_id)
    ]
    readiness_reports = [_build_release_readiness(workflow, repository) for workflow in workflows]
    readiness_by_version = {
        (report["workflow_id"], report["workflow_version"]): report
        for report in readiness_reports
    }

    tool_risk = _tool_risk(workflows)
    run_risk = _run_risk(runs, readiness_by_version)
    quality_risk = _quality_risk(eval_results, readiness_reports)
    audit_risk = _audit_risk(audit_events)
    risk_items = _risk_items(tool_risk, run_risk, quality_risk, audit_risk)
    risk_score = min(100, sum(_risk_item_weight(item["severity"]) for item in risk_items))

    return {
        "workflow_id": workflow_id,
        "workflow_count": len(workflows),
        "workflow_ids": sorted(workflow_ids),
        "risk_score": risk_score,
        "risk_level": _risk_level(risk_score),
        "tool_risk": tool_risk,
        "run_risk": run_risk,
        "quality_risk": quality_risk,
        "audit_risk": audit_risk,
        "risk_items": risk_items,
    }


@router.get("/retention-report")
def get_retention_report(
    workflow_id: str | None = None,
    run_retention_days: int | None = Query(default=None, ge=1),
    eval_retention_days: int | None = Query(default=None, ge=1),
    audit_retention_days: int | None = Query(default=None, ge=1),
    sample_limit: int = Query(default=20, ge=1, le=200),
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    policy = RetentionPolicy(
        run_retention_days=run_retention_days or settings.run_retention_days,
        eval_retention_days=eval_retention_days or settings.eval_retention_days,
        audit_retention_days=audit_retention_days or settings.audit_retention_days,
        sample_limit=sample_limit,
    )
    try:
        report = build_retention_report(repository, policy, workflow_id=workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return dump_model(report)


@router.get("/snapshot")
def export_snapshot(
    workflow_id: str | None = None,
    include_runs: bool = True,
    include_eval_results: bool = True,
    include_audit_events: bool = True,
    actor: ActorContext = Depends(require_scope("workflow:write")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    if actor.role != "workflow-admin":
        _save_snapshot_audit_event(
            repository=repository,
            actor=actor,
            action="export",
            status="failed",
            workflow_id=workflow_id,
            reason=None,
            details={
                "gate": "role",
                "message": "only workflow-admin can export repository snapshots",
                "actor_role": actor.role,
            },
        )
        raise HTTPException(status_code=403, detail="only workflow-admin can export repository snapshots")
    snapshot = export_repository_snapshot(
        repository,
        workflow_id=workflow_id,
        include_runs=include_runs,
        include_eval_results=include_eval_results,
        include_audit_events=include_audit_events,
    )
    _save_snapshot_audit_event(
        repository=repository,
        actor=actor,
        action="export",
        status="succeeded",
        workflow_id=workflow_id,
        reason=None,
        details={
            "summary": snapshot.summary(),
            "include_runs": include_runs,
            "include_eval_results": include_eval_results,
            "include_audit_events": include_audit_events,
        },
    )
    return {
        "snapshot": dump_model(snapshot),
        "summary": snapshot.summary(),
    }


@router.post("/snapshot/import")
def import_snapshot(
    request: SnapshotImportRequest,
    actor: ActorContext = Depends(require_scope("workflow:write")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    summary = request.snapshot.summary()
    if not request.dry_run and not request.confirm_import:
        _save_snapshot_audit_event(
            repository=repository,
            actor=actor,
            action="import",
            status="failed",
            workflow_id=request.snapshot.workflow_id,
            reason=request.reason,
            details={"gate": "confirm_import", "message": "confirm_import is required when dry_run is false", "summary": summary},
        )
        raise HTTPException(status_code=422, detail="confirm_import is required when dry_run is false")
    if not request.dry_run and actor.role != "workflow-admin":
        _save_snapshot_audit_event(
            repository=repository,
            actor=actor,
            action="import",
            status="failed",
            workflow_id=request.snapshot.workflow_id,
            reason=request.reason,
            details={
                "gate": "role",
                "message": "only workflow-admin can import repository snapshots",
                "actor_role": actor.role,
                "summary": summary,
            },
        )
        raise HTTPException(status_code=403, detail="only workflow-admin can import repository snapshots")
    if not request.dry_run and not request.reason:
        _save_snapshot_audit_event(
            repository=repository,
            actor=actor,
            action="import",
            status="failed",
            workflow_id=request.snapshot.workflow_id,
            reason=request.reason,
            details={"gate": "reason", "message": "reason is required when dry_run is false", "summary": summary},
        )
        raise HTTPException(status_code=422, detail="reason is required when dry_run is false")
    if request.dry_run:
        try:
            report = preview_repository_snapshot_import(
                repository,
                request.snapshot,
                skip_existing_eval_results=request.skip_existing_eval_results,
            )
        except ValueError as exc:
            _save_snapshot_audit_event(
                repository=repository,
                actor=actor,
                action="import_preview",
                status="failed",
                workflow_id=request.snapshot.workflow_id,
                reason=request.reason,
                details={"gate": "snapshot_schema", "message": str(exc), "summary": summary},
            )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _save_snapshot_audit_event(
            repository=repository,
            actor=actor,
            action="import_preview",
            status="succeeded",
            workflow_id=request.snapshot.workflow_id,
            reason=request.reason,
            details={
                "dry_run": True,
                "skip_existing_eval_results": request.skip_existing_eval_results,
                "summary": summary,
                "report": dump_model(report),
            },
        )
        return {
            "dry_run": True,
            "summary": summary,
            "report": dump_model(report),
        }

    try:
        report = import_repository_snapshot(
            repository,
            request.snapshot,
            skip_existing_eval_results=request.skip_existing_eval_results,
        )
    except ValueError as exc:
        _save_snapshot_audit_event(
            repository=repository,
            actor=actor,
            action="import",
            status="failed",
            workflow_id=request.snapshot.workflow_id,
            reason=request.reason,
            details={"gate": "snapshot_schema", "message": str(exc), "summary": summary},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _save_snapshot_audit_event(
        repository=repository,
        actor=actor,
        action="import",
        status="succeeded",
        workflow_id=request.snapshot.workflow_id,
        reason=request.reason,
        details={
            "dry_run": False,
            "confirm_import": request.confirm_import,
            "skip_existing_eval_results": request.skip_existing_eval_results,
            "summary": summary,
            "report": dump_model(report),
        },
    )
    return {
        "dry_run": False,
        "summary": summary,
        "report": dump_model(report),
    }


@router.post("/retention-apply")
def apply_retention(
    request: RetentionApplyRequest,
    actor: ActorContext = Depends(require_scope("workflow:write")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    if not request.dry_run and not request.confirm_apply:
        _save_retention_apply_audit_event(
            repository=repository,
            actor=actor,
            request=request,
            status="failed",
            details={"gate": "confirm_apply", "message": "confirm_apply is required when dry_run is false"},
        )
        raise HTTPException(status_code=422, detail="confirm_apply is required when dry_run is false")
    if not request.dry_run and actor.role != "workflow-admin":
        _save_retention_apply_audit_event(
            repository=repository,
            actor=actor,
            request=request,
            status="failed",
            details={
                "gate": "role",
                "message": "only workflow-admin can apply retention deletion",
                "actor_role": actor.role,
            },
        )
        raise HTTPException(status_code=403, detail="only workflow-admin can apply retention deletion")
    if not request.dry_run and not request.snapshot_acknowledged:
        _save_retention_apply_audit_event(
            repository=repository,
            actor=actor,
            request=request,
            status="failed",
            details={"gate": "snapshot_acknowledged", "message": "snapshot_acknowledged is required when dry_run is false"},
        )
        raise HTTPException(status_code=422, detail="snapshot_acknowledged is required when dry_run is false")
    if not request.dry_run and not request.reason:
        _save_retention_apply_audit_event(
            repository=repository,
            actor=actor,
            request=request,
            status="failed",
            details={"gate": "reason", "message": "reason is required when dry_run is false"},
        )
        raise HTTPException(status_code=422, detail="reason is required when dry_run is false")
    policy = RetentionPolicy(
        run_retention_days=request.run_retention_days or settings.run_retention_days,
        eval_retention_days=request.eval_retention_days or settings.eval_retention_days,
        audit_retention_days=request.audit_retention_days or settings.audit_retention_days,
        sample_limit=request.sample_limit,
    )
    try:
        report = apply_retention_policy(
            repository,
            policy,
            workflow_id=request.workflow_id,
            categories=request.categories,
            dry_run=request.dry_run,
        )
    except ValueError as exc:
        _save_retention_apply_audit_event(
            repository=repository,
            actor=actor,
            request=request,
            status="failed",
            details={"gate": "retention_policy", "message": str(exc)},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _save_retention_apply_audit_event(
        repository=repository,
        actor=actor,
        request=request,
        status="succeeded",
        details={
            "dry_run": report.dry_run,
            "snapshot_acknowledged": request.snapshot_acknowledged,
            "requested_categories": report.requested_categories,
            "eligible_counts": report.eligible_counts,
            "deleted_counts": report.deleted_counts,
            "skipped_counts": report.skipped_counts,
            "retention_items": [
                {
                    "category": item["category"],
                    "count": item["count"],
                    "sample_truncated": item["sample_truncated"],
                    "recommendation": item["recommendation"],
                }
                for item in report.retention_report.retention_items
            ],
        },
    )
    return dump_model(report)


def _save_snapshot_audit_event(
    *,
    repository: WorkflowRepository,
    actor: ActorContext,
    action: str,
    status: str,
    workflow_id: str | None,
    reason: str | None,
    details: dict[str, Any],
) -> None:
    repository.save_audit_event(
        build_audit_event(
            event_type="repository_snapshot",
            action=action,
            status=status,
            actor=actor,
            workflow_id=workflow_id,
            resource_type="repository_snapshot",
            resource_id=workflow_id or "all-workflows",
            reason=reason,
            details=details,
        )
    )


def _save_retention_apply_audit_event(
    *,
    repository: WorkflowRepository,
    actor: ActorContext,
    request: RetentionApplyRequest,
    status: str,
    details: dict[str, Any],
) -> None:
    repository.save_audit_event(
        build_audit_event(
            event_type="workflow_retention_apply",
            action="apply",
            status=status,
            actor=actor,
            workflow_id=request.workflow_id,
            resource_type="repository_retention",
            resource_id=request.workflow_id or "all-workflows",
            reason=request.reason,
            details={
                "dry_run": request.dry_run,
                "confirm_apply": request.confirm_apply,
                "snapshot_acknowledged": request.snapshot_acknowledged,
                "categories": request.categories or [],
                **details,
            },
        )
    )


@router.get("/eval-results")
def list_eval_results(
    workflow_id: str | None = None,
    limit: LimitQuery = DEFAULT_QUERY_LIMIT,
    offset: OffsetQuery = 0,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> list[dict[str, Any]]:
    return [dump_model(result) for result in repository.list_eval_results(workflow_id, limit=limit, offset=offset)]


@router.get("/audit-events")
def list_audit_events(
    workflow_id: str | None = None,
    run_id: str | None = None,
    event_type: str | None = None,
    limit: LimitQuery = DEFAULT_QUERY_LIMIT,
    offset: OffsetQuery = 0,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> list[dict[str, Any]]:
    return [
        dump_model(event)
        for event in repository.list_audit_events(
            workflow_id=workflow_id,
            run_id=run_id,
            event_type=event_type,
            limit=limit,
            offset=offset,
        )
    ]


@router.post("/trace-export")
def export_run_trace(
    request: TraceExportRequest,
    actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    run = repository.get_run(request.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    exporter = OTLPTraceExporter(
        OTLPTraceExporterConfig(
            endpoint_url=request.endpoint_url,
            headers=request.headers,
            service_name=request.service_name,
            service_version=request.service_version,
            deployment_environment=request.deployment_environment,
        )
    )
    payload = exporter.build_run_payload(run)
    span_count = _otlp_span_count(payload)
    response: dict[str, Any] = {
        "run_id": run.run_id,
        "workflow_id": run.workflow_id,
        "workflow_version": run.workflow_version,
        "span_count": span_count,
        "exported": False,
        "endpoint_configured": request.endpoint_url is not None,
    }

    if request.endpoint_url:
        try:
            export_result = exporter.export_run(run)
        except TraceExportError as exc:
            repository.save_audit_event(
                build_audit_event(
                    event_type="workflow_trace_export",
                    action="export",
                    status="failed",
                    actor=actor,
                    workflow_id=run.workflow_id,
                    workflow_version=run.workflow_version,
                    run_id=run.run_id,
                    resource_type="workflow_run",
                    resource_id=run.run_id,
                    details={
                        "endpoint_configured": True,
                        "span_count": span_count,
                        "error_type": type(exc).__name__,
                        "payload_returned": False,
                    },
                )
            )
            raise HTTPException(status_code=502, detail="trace export failed") from exc
        response.update(
            {
                "exported": True,
                "status_code": export_result["status_code"],
            }
        )

    if request.include_payload or not request.endpoint_url:
        response["payload"] = payload

    repository.save_audit_event(
        build_audit_event(
            event_type="workflow_trace_export",
            action="export",
            status="succeeded",
            actor=actor,
            workflow_id=run.workflow_id,
            workflow_version=run.workflow_version,
            run_id=run.run_id,
            resource_type="workflow_run",
            resource_id=run.run_id,
            details={
                "endpoint_configured": request.endpoint_url is not None,
                "span_count": span_count,
                "payload_returned": "payload" in response,
                "service_name": request.service_name,
                "deployment_environment_present": request.deployment_environment is not None,
            },
        )
    )
    return response


def _governance_workflows(repository: WorkflowRepository, workflow_id: str | None) -> list[WorkflowPackage]:
    if workflow_id:
        workflow = repository.get_workflow(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        return [workflow]
    return repository.list_workflows()


def _node_costs(traces: list[Any]) -> list[dict[str, Any]]:
    cost_by_node: dict[str, dict[str, Any]] = {}
    for trace in traces:
        item = cost_by_node.setdefault(
            trace.node_id,
            {
                "node_id": trace.node_id,
                "trace_count": 0,
                "estimated_input_tokens": 0,
                "estimated_output_tokens": 0,
                "estimated_total_tokens": 0,
                "total_duration_ms": 0,
                "average_duration_ms": 0,
                "human_touch_count": 0,
                "retry_trace_count": 0,
            },
        )
        input_tokens = _estimated_tokens(trace.input_snapshot)
        output_tokens = _estimated_tokens(trace.output_snapshot)
        item["trace_count"] += 1
        item["estimated_input_tokens"] += input_tokens
        item["estimated_output_tokens"] += output_tokens
        item["estimated_total_tokens"] += input_tokens + output_tokens
        item["total_duration_ms"] += trace.duration_ms or 0
        item["human_touch_count"] += 1 if trace.status == NodeExecutionStatus.APPROVAL_REQUIRED else 0
        item["retry_trace_count"] += 1 if trace.attempt > 1 or trace.retryable else 0

    for item in cost_by_node.values():
        item["average_duration_ms"] = item["total_duration_ms"] / item["trace_count"] if item["trace_count"] else 0

    return sorted(
        cost_by_node.values(),
        key=lambda item: (item["estimated_total_tokens"], item["total_duration_ms"], item["node_id"]),
        reverse=True,
    )


def _estimated_tokens(payload: Any) -> int:
    if payload in ({}, [], None):
        return 0
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return max(1, ceil(len(encoded) / 4))


def _otlp_span_count(payload: dict[str, Any]) -> int:
    return sum(
        len(scope_spans.get("spans", []))
        for resource_spans in payload.get("resourceSpans", [])
        for scope_spans in resource_spans.get("scopeSpans", [])
    )


def _cost_items(
    node_costs: list[dict[str, Any]],
    human_touch_count: int,
    retry_trace_count: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if human_touch_count:
        items.append({
            "code": "human_touch_cost",
            "message": "Approval pauses introduce manual handling cost.",
            "count": human_touch_count,
        })
    if retry_trace_count:
        items.append({
            "code": "retry_cost",
            "message": "Retryable traces consumed extra execution budget.",
            "count": retry_trace_count,
        })
    if node_costs:
        highest = node_costs[0]
        items.append({
            "code": "highest_token_node",
            "message": f"{highest['node_id']} has the highest estimated token footprint.",
            "count": highest["estimated_total_tokens"],
        })
    return items


def _shadow_validation_runs(
    workflows: list[WorkflowPackage],
    runs: list[Any],
    repository: WorkflowRepository,
) -> list[Any]:
    compared_run_ids = {
        result.details.get("run_id")
        for workflow in workflows
        for result in repository.list_eval_results(workflow.workflow_id)
        if result.details.get("eval_type") == "shadow_comparison"
    }
    return [
        run
        for run in runs
        if run.shadow_mode
        and run.status == WorkflowRunStatus.COMPLETED
        and run.run_id not in compared_run_ids
    ]


def _recent_runs(runs: list[Any]) -> list[Any]:
    return sorted(runs, key=lambda run: run.updated_at, reverse=True)[:_RUN_REPORT_SAMPLE_LIMIT]


def _run_queue_item(run: Any) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "workflow_id": run.workflow_id,
        "workflow_version": run.workflow_version,
        "status": run.status,
        "current_node_id": run.current_node_id,
        "shadow_mode": run.shadow_mode,
        "trace_count": len(run.traces),
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def _run_recovery_item(run: Any) -> dict[str, Any]:
    return {
        **_run_queue_item(run),
        "failure_node_id": _run_failure_node_id(run),
        "failure_reason_code": _run_recovery_code(run),
        "recommended_action_code": _run_recovery_action_code(run),
    }


def _run_duration_ms(run: Any) -> int:
    return max(0, round((run.updated_at - run.created_at).total_seconds() * 1000))


def _string_payload_value(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _run_failure_node_id(run: Any) -> str | None:
    return (
        _string_payload_value(run.output_payload, "failed_node_id")
        or _string_payload_value(run.output_payload, "pending_node_id")
        or (run.traces[-1].node_id if run.traces else run.current_node_id)
    )


def _run_failure_error(run: Any) -> str | None:
    failed_traces = [trace for trace in run.traces if trace.status == NodeExecutionStatus.FAILED]
    latest_failed_trace = failed_traces[-1] if failed_traces else None
    return _string_payload_value(run.output_payload, "error") or (latest_failed_trace.error if latest_failed_trace else None)


def _run_recovery_code(run: Any) -> str:
    if run.status == WorkflowRunStatus.CANCELED:
        return "workflow_canceled"
    if run.status == WorkflowRunStatus.REJECTED:
        return "approval_rejected"
    if run.status == WorkflowRunStatus.FAILED:
        return _failure_reason_code(_run_failure_error(run))
    return str(run.status)


def _run_recovery_action_code(run: Any) -> str:
    reason_code = _run_recovery_code(run)
    if reason_code == "workflow_canceled":
        return "review_cancel_audit_and_rerun_if_needed"
    if reason_code == "approval_rejected":
        return "correct_business_input_before_rerun"
    if reason_code == "max_steps_exceeded":
        return "inspect_graph_and_rerun_with_reviewed_step_budget"
    if reason_code in {"schema_validation_failure", "contract_validation_failure"}:
        return "fix_contract_or_upstream_payload_then_rerun"
    if reason_code in {"permission_failure", "approval_failure"}:
        return "review_policy_and_role_configuration_then_rerun"
    return "inspect_failed_trace_and_rerun_after_fix"


def _run_items(
    *,
    pending_approval_count: int,
    failed_run_count: int,
    rejected_run_count: int,
    canceled_run_count: int,
    shadow_validation_pending_count: int,
    active_run_count: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if failed_run_count:
        items.append({
            "severity": "high",
            "code": "failed_runs_in_recovery_queue",
            "message": "Failed terminal runs need diagnosis before rerun.",
            "count": failed_run_count,
        })
    if pending_approval_count:
        items.append({
            "severity": "medium",
            "code": "pending_approvals",
            "message": "Paused runs are waiting for human approval decisions.",
            "count": pending_approval_count,
        })
    if rejected_run_count:
        items.append({
            "severity": "medium",
            "code": "rejected_runs_need_review",
            "message": "Rejected runs should be reviewed before rerun.",
            "count": rejected_run_count,
        })
    if shadow_validation_pending_count:
        items.append({
            "severity": "medium",
            "code": "shadow_validation_pending",
            "message": "Completed shadow runs need human expected-output comparison.",
            "count": shadow_validation_pending_count,
        })
    if canceled_run_count:
        items.append({
            "severity": "low",
            "code": "canceled_runs_present",
            "message": "Canceled runs remain available for audit and optional rerun.",
            "count": canceled_run_count,
        })
    if active_run_count and not pending_approval_count:
        items.append({
            "severity": "low",
            "code": "active_runs_present",
            "message": "Active runs should be monitored until terminal or approval state.",
            "count": active_run_count,
        })
    return items


def _failed_node_counts(traces: list[Any]) -> list[dict[str, Any]]:
    failed_by_node: dict[str, dict[str, Any]] = {}
    for trace in traces:
        if trace.status != NodeExecutionStatus.FAILED:
            continue
        item = failed_by_node.setdefault(
            trace.node_id,
            {
                "node_id": trace.node_id,
                "failed_trace_count": 0,
                "failure_reason_codes": Counter(),
            },
        )
        item["failed_trace_count"] += 1
        item["failure_reason_codes"][_failure_reason_code(trace.error)] += 1

    result: list[dict[str, Any]] = []
    for item in failed_by_node.values():
        result.append({
            "node_id": item["node_id"],
            "failed_trace_count": item["failed_trace_count"],
            "failure_reason_codes": dict(item["failure_reason_codes"]),
        })
    return sorted(
        result,
        key=lambda item: (item["failed_trace_count"], item["node_id"]),
        reverse=True,
    )


def _failure_reason_code(error: str | None) -> str:
    if not error:
        return "unknown_failure"
    normalized = error.lower()
    if "max_steps_exceeded" in normalized:
        return "max_steps_exceeded"
    if "schema" in normalized:
        return "schema_validation_failure"
    if "contract" in normalized or "validation" in normalized:
        return "contract_validation_failure"
    if "permission" in normalized or "forbidden" in normalized:
        return "permission_failure"
    if "approval" in normalized:
        return "approval_failure"
    return "runtime_failure"


def _quality_score(node_success_rate: float, eval_pass_rate: float, readiness_rate: float) -> int:
    return round(((node_success_rate * 0.45) + (eval_pass_rate * 0.35) + (readiness_rate * 0.20)) * 100)


def _quality_level(score: int) -> str:
    if score >= 80:
        return "healthy"
    if score >= 50:
        return "watch"
    return "weak"


def _quality_items(
    *,
    failed_node_counts: list[dict[str, Any]],
    eval_fail_count: int,
    failed_shadow_comparison_count: int,
    unready_version_count: int,
    node_success_rate: float,
    eval_result_count: int,
    suggestion_count: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if failed_node_counts:
        items.append({
            "severity": "high",
            "code": "failed_nodes_present",
            "message": "Failed node traces need contract, tool, or runtime triage.",
            "count": sum(item["failed_trace_count"] for item in failed_node_counts),
        })
    if eval_fail_count:
        items.append({
            "severity": "high",
            "code": "failed_evals_present",
            "message": "Failed evals should be converted into regression cases before rollout.",
            "count": eval_fail_count,
        })
    if failed_shadow_comparison_count:
        items.append({
            "severity": "medium",
            "code": "failed_shadow_comparisons_present",
            "message": "Shadow outputs diverged from expected human handling.",
            "count": failed_shadow_comparison_count,
        })
    if unready_version_count:
        items.append({
            "severity": "medium",
            "code": "release_readiness_missing",
            "message": "Workflow versions are missing live-readiness evidence.",
            "count": unready_version_count,
        })
    if node_success_rate < 0.9 and node_success_rate > 0:
        items.append({
            "severity": "medium",
            "code": "node_success_rate_below_target",
            "message": "Node success rate is below the production quality target.",
            "count": round(node_success_rate * 100),
        })
    if eval_result_count == 0:
        items.append({
            "severity": "medium",
            "code": "no_eval_evidence",
            "message": "No eval evidence exists for this workflow selection.",
            "count": 0,
        })
    if suggestion_count:
        items.append({
            "severity": "low",
            "code": "optimization_suggestions_available",
            "message": "Optimizer produced improvement suggestions for review.",
            "count": suggestion_count,
        })
    return items


def _tool_risk(workflows: list[WorkflowPackage]) -> dict[str, Any]:
    tool_policies = [policy for workflow in workflows for policy in workflow.tool_policies]
    risk_counts = Counter(str(policy.risk_level) for policy in tool_policies)
    write_tools = [
        policy
        for policy in tool_policies
        if policy.permission_level == PermissionLevel.WRITE_REQUIRES_APPROVAL or policy.requires_approval
    ]
    approval_gap_tool_ids = [
        policy.tool_id
        for policy in write_tools
        if not policy.requires_approval or not policy.allowed_roles
    ]
    return {
        "tool_count": len(tool_policies),
        "risk_level_counts": {level.value: risk_counts.get(level.value, 0) for level in RiskLevel},
        "high_risk_tool_count": risk_counts.get(RiskLevel.HIGH.value, 0),
        "high_risk_tool_ids": [
            policy.tool_id
            for policy in tool_policies
            if policy.risk_level == RiskLevel.HIGH
        ],
        "write_tool_count": len(write_tools),
        "write_tool_ids": [policy.tool_id for policy in write_tools],
        "approval_gap_tool_ids": approval_gap_tool_ids,
    }


def _run_risk(runs: list[Any], readiness_by_version: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(run.status) for run in runs)
    live_runs = [run for run in runs if not run.shadow_mode]
    shadow_runs = [run for run in runs if run.shadow_mode]
    live_unready_runs = [
        run
        for run in live_runs
        if not readiness_by_version.get((run.workflow_id, run.workflow_version or ""), {}).get("live_ready", False)
    ]
    return {
        "total_run_count": len(runs),
        "status_counts": {status.value: status_counts.get(status.value, 0) for status in WorkflowRunStatus},
        "live_run_count": len(live_runs),
        "shadow_run_count": len(shadow_runs),
        "failed_run_count": status_counts.get(WorkflowRunStatus.FAILED.value, 0),
        "canceled_run_count": status_counts.get(WorkflowRunStatus.CANCELED.value, 0),
        "live_runs_on_unready_version_count": len(live_unready_runs),
        "live_runs_on_unready_version_ids": [run.run_id for run in live_unready_runs],
    }


def _quality_risk(eval_results: list[Any], readiness_reports: list[dict[str, Any]]) -> dict[str, Any]:
    failed_evals = [result for result in eval_results if not result.passed]
    shadow_comparisons = [
        result for result in eval_results
        if result.details.get("eval_type") == "shadow_comparison"
    ]
    failed_shadow_comparisons = [result for result in shadow_comparisons if not result.passed]
    unready_versions = [
        f"{report['workflow_id']}@{report['workflow_version']}"
        for report in readiness_reports
        if not report["live_ready"]
    ]
    return {
        "eval_result_count": len(eval_results),
        "failed_eval_count": len(failed_evals),
        "shadow_comparison_count": len(shadow_comparisons),
        "failed_shadow_comparison_count": len(failed_shadow_comparisons),
        "unready_version_count": len(unready_versions),
        "unready_versions": unready_versions,
    }


def _audit_risk(audit_events: list[Any]) -> dict[str, Any]:
    release_gate_blocks = [
        event
        for event in audit_events
        if event.event_type == "workflow_run_start"
        and event.status == "failed"
        and event.details.get("gate") == "release_readiness"
    ]
    idempotency_conflicts = [
        event
        for event in audit_events
        if event.status == "failed"
        and event.details.get("gate") == "idempotency_conflict"
    ]
    return {
        "audit_event_count": len(audit_events),
        "release_gate_block_count": len(release_gate_blocks),
        "idempotency_conflict_count": len(idempotency_conflicts),
    }


def _risk_items(
    tool_risk: dict[str, Any],
    run_risk: dict[str, Any],
    quality_risk: dict[str, Any],
    audit_risk: dict[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if tool_risk["approval_gap_tool_ids"]:
        items.append({
            "severity": "high",
            "code": "write_tool_approval_gap",
            "message": "Approval-gated write tools are missing approval settings or allowed roles.",
            "count": len(tool_risk["approval_gap_tool_ids"]),
        })
    if run_risk["live_runs_on_unready_version_count"]:
        items.append({
            "severity": "high",
            "code": "live_runs_without_release_readiness",
            "message": "Live runs exist for workflow versions that are not currently release-ready.",
            "count": run_risk["live_runs_on_unready_version_count"],
        })
    if quality_risk["failed_shadow_comparison_count"]:
        items.append({
            "severity": "medium",
            "code": "failed_shadow_comparison",
            "message": "Shadow comparison failures need review before live rollout.",
            "count": quality_risk["failed_shadow_comparison_count"],
        })
    if quality_risk["unready_version_count"]:
        items.append({
            "severity": "medium",
            "code": "unready_workflow_version",
            "message": "Workflow versions are missing release-readiness evidence.",
            "count": quality_risk["unready_version_count"],
        })
    if audit_risk["release_gate_block_count"]:
        items.append({
            "severity": "medium",
            "code": "release_gate_block",
            "message": "Release-readiness gate blocked live execution attempts.",
            "count": audit_risk["release_gate_block_count"],
        })
    if run_risk["failed_run_count"]:
        items.append({
            "severity": "medium",
            "code": "failed_runs",
            "message": "Failed runs require operational triage.",
            "count": run_risk["failed_run_count"],
        })
    if tool_risk["high_risk_tool_count"]:
        items.append({
            "severity": "low",
            "code": "high_risk_tools_present",
            "message": "High-risk tools are present and should remain covered by approval and audit controls.",
            "count": tool_risk["high_risk_tool_count"],
        })
    return items


def _risk_item_weight(severity: str) -> int:
    return {"high": 30, "medium": 15, "low": 5}.get(severity, 0)


def _risk_level(score: int) -> str:
    if score >= 60:
        return "high"
    if score >= 25:
        return "medium"
    return "low"
