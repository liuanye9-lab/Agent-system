from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from apps.api.audit import build_audit_event
from apps.api.dependencies import (
    get_eval_runner,
    get_optimization_loop,
    get_repository,
    get_workflow_builder,
    get_workflow_runner,
)
from apps.api.pagination import DEFAULT_QUERY_LIMIT, LimitQuery, OffsetQuery
from apps.api.redaction import dump_redacted_model
from apps.api.run_requests import fingerprint_run_request
from apps.api.security import require_scope
from packages.workflow_core.builder import WorkflowBuildBrief, WorkflowBuildNode, WorkflowBuilder
from packages.workflow_core.governance import EvalRunner, OptimizationLoop
from packages.workflow_core.models import ActorContext, EvalSpec, WorkflowPackage
from packages.workflow_core.models.common import utc_now
from packages.workflow_core.models.enums import EvalType, NodeType, WorkflowRunStatus
from packages.workflow_core.runtime import ToolExecutionContext, WorkflowRunner
from packages.workflow_core.storage import WorkflowRepository, diff_workflow_packages
from packages.workflow_core.validation import WorkflowPackageLinter

router = APIRouter(prefix="/api/workflows", tags=["workflows"])
workflow_linter = WorkflowPackageLinter()
_REPAIR_IMPACT_CHANGE_LIMIT = 100


class GenerateProcessNodeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    node_type: NodeType
    owner_role: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    done_condition: str | None = Field(default=None, max_length=500)
    requires_approval: bool = False


class GenerateWorkflowRequest(BaseModel):
    user_request: str = Field(min_length=1, max_length=5000)
    version: str = Field(default="0.1.0", min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    save_as_current: bool = True
    workflow_id: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str | None = Field(default=None, min_length=1, max_length=160)
    business_goal: str | None = Field(default=None, max_length=1000)
    start_event: str | None = Field(default=None, max_length=500)
    end_state: str | None = Field(default=None, max_length=500)
    target_users: list[str] | None = Field(default=None, max_length=20)
    human_roles: list[str] | None = Field(default=None, max_length=20)
    success_metrics: list[str] | None = Field(default=None, max_length=20)
    constraints: list[str] | None = Field(default=None, max_length=20)
    risks: list[str] | None = Field(default=None, max_length=20)
    process_nodes: list[GenerateProcessNodeRequest] | None = Field(default=None, min_length=1, max_length=20)

    def to_build_brief(self) -> WorkflowBuildBrief | None:
        if not any(
            (
                self.workflow_id,
                self.name,
                self.business_goal,
                self.start_event,
                self.end_state,
                self.target_users,
                self.human_roles,
                self.success_metrics,
                self.constraints,
                self.risks,
                self.process_nodes,
            )
        ):
            return None
        return WorkflowBuildBrief(
            workflow_id=self.workflow_id,
            name=self.name,
            business_goal=self.business_goal,
            start_event=self.start_event,
            end_state=self.end_state,
            target_users=self.target_users,
            human_roles=self.human_roles,
            success_metrics=self.success_metrics,
            constraints=self.constraints,
            risks=self.risks,
            process_nodes=[
                WorkflowBuildNode(
                    name=node.name,
                    node_type=node.node_type,
                    owner_role=node.owner_role,
                    description=node.description,
                    done_condition=node.done_condition,
                    requires_approval=node.requires_approval,
                )
                for node in self.process_nodes or []
            ] or None,
        )


class RunWorkflowRequest(BaseModel):
    input_payload: dict[str, Any] = Field(default_factory=dict)
    workflow_version: str | None = None
    max_steps: int = Field(default=50, ge=1, le=200)
    max_retries: int = Field(default=1, ge=0, le=5)
    shadow_mode: bool = False
    enforce_release_readiness: bool = False
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )


class PromoteWorkflowVersionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    change_summary: str | None = Field(default=None, max_length=1000)
    risk_acceptance: str | None = Field(default=None, max_length=1000)
    reviewed_diff: bool = False
    readiness_acknowledged: bool = False


class ApplyRepairPlanRequest(BaseModel):
    target_version: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    source_version: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    reason: str = Field(min_length=1, max_length=500)
    selected_operation_ids: list[str] | None = Field(default=None, min_length=1, max_length=50)


def dump_model(model: Any) -> Any:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json", by_alias=True)
    return model


@router.post("/generate")
def generate_workflow(
    request: GenerateWorkflowRequest,
    actor: ActorContext = Depends(require_scope("workflow:write")),
    builder: WorkflowBuilder = Depends(get_workflow_builder),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    brief = request.to_build_brief()
    try:
        result = builder.generate(request.user_request, version=request.version, brief=brief)
    except RuntimeError as exc:
        repository.save_audit_event(
            build_audit_event(
                event_type="workflow_package_generation",
                action="generate",
                status="failed",
                actor=actor,
                resource_type="workflow_package",
                resource_id=request.workflow_id or "unknown",
                details={
                    "request_length": len(request.user_request),
                    "structured_brief_present": brief is not None,
                    "process_node_count": len(request.process_nodes or []),
                    "save_as_current": request.save_as_current,
                    "builder_error_type": exc.__class__.__name__,
                    "llm_provider": getattr(getattr(builder, "llm", None), "provider", "unknown"),
                    "llm_model": getattr(getattr(builder, "llm", None), "model", "unknown"),
                },
            )
        )
        raise HTTPException(
            status_code=502,
            detail={"message": "workflow builder failed", "error_type": exc.__class__.__name__},
        ) from exc
    validation_report = workflow_linter.lint(result.workflow_package)
    if not validation_report.valid:
        repository.save_audit_event(
            build_audit_event(
                event_type="workflow_package_generation",
                action="generate",
                status="failed",
                actor=actor,
                workflow_id=result.workflow_package.workflow_id,
                workflow_version=result.workflow_package.version,
                resource_type="workflow_package",
                resource_id=f"{result.workflow_package.workflow_id}@{result.workflow_package.version}",
                details={
                    "request_length": len(request.user_request),
                    "structured_brief_present": brief is not None,
                    "process_node_count": len(request.process_nodes or []),
                    "save_as_current": request.save_as_current,
                    "validation_report": dump_model(validation_report),
                },
            )
        )
        raise HTTPException(status_code=500, detail=dump_model(validation_report))
    if not request.save_as_current and repository.get_workflow(result.workflow_package.workflow_id) is None:
        repository.save_audit_event(
            build_audit_event(
                event_type="workflow_package_generation",
                action="generate",
                status="failed",
                actor=actor,
                workflow_id=result.workflow_package.workflow_id,
                workflow_version=result.workflow_package.version,
                resource_type="workflow_package",
                resource_id=f"{result.workflow_package.workflow_id}@{result.workflow_package.version}",
                details={
                    "gate": "candidate_base_workflow",
                    "message": "candidate versions require an existing current workflow",
                    "request_length": len(request.user_request),
                    "structured_brief_present": brief is not None,
                    "process_node_count": len(request.process_nodes or []),
                    "save_as_current": request.save_as_current,
                },
            )
        )
        raise HTTPException(status_code=409, detail="candidate versions require an existing current workflow")
    if request.save_as_current:
        repository.save_workflow(result.workflow_package)
    else:
        repository.save_workflow_version(result.workflow_package)
    repository.save_audit_event(
        build_audit_event(
            event_type="workflow_package_generation",
            action="generate",
            status="succeeded",
            actor=actor,
            workflow_id=result.workflow_package.workflow_id,
            workflow_version=result.workflow_package.version,
            resource_type="workflow_package",
            resource_id=f"{result.workflow_package.workflow_id}@{result.workflow_package.version}",
            details={
                "request_length": len(request.user_request),
                "structured_brief_present": brief is not None,
                "process_node_count": len(request.process_nodes or []),
                "save_as_current": request.save_as_current,
                "clarifying_question_count": len(result.clarifying_questions),
                "validation_error_count": len(validation_report.errors),
                "validation_warning_count": len(validation_report.warnings),
            },
        )
    )
    return {
        "workflow_package": dump_model(result.workflow_package),
        "clarifying_questions": result.clarifying_questions,
        "validation_report": dump_model(validation_report),
        "saved_as_current": request.save_as_current,
    }


@router.get("")
def list_workflows(
    limit: LimitQuery = DEFAULT_QUERY_LIMIT,
    offset: OffsetQuery = 0,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> list[dict[str, Any]]:
    return [
        {
            "workflow_id": workflow.workflow_id,
            "name": workflow.name,
            "version": workflow.version,
            "created_at": workflow.created_at.isoformat(),
        }
        for workflow in repository.list_workflows(limit=limit, offset=offset)
    ]


@router.get("/{workflow_id}/versions")
def list_workflow_versions(
    workflow_id: str,
    limit: LimitQuery = DEFAULT_QUERY_LIMIT,
    offset: OffsetQuery = 0,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> list[dict[str, Any]]:
    if repository.get_workflow(workflow_id) is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return [
        {
            "workflow_id": workflow.workflow_id,
            "name": workflow.name,
            "version": workflow.version,
            "created_at": workflow.created_at.isoformat(),
            "updated_at": workflow.updated_at.isoformat(),
        }
        for workflow in repository.list_workflow_versions(workflow_id, limit=limit, offset=offset)
    ]


@router.get("/{workflow_id}/versions/{version}")
def get_workflow_version(
    workflow_id: str,
    version: str,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    workflow = repository.get_workflow_version(workflow_id, version)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow version not found")
    return dump_model(workflow)


@router.post("/{workflow_id}/versions/{version}/evals/run")
def run_workflow_version_evals(
    workflow_id: str,
    version: str,
    actor: ActorContext = Depends(require_scope("workflow:evaluate")),
    repository: WorkflowRepository = Depends(get_repository),
    eval_runner: EvalRunner = Depends(get_eval_runner),
) -> list[dict[str, Any]]:
    workflow = repository.get_workflow_version(workflow_id, version)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow version not found")
    return _run_and_persist_workflow_evals(
        workflow=workflow,
        actor=actor,
        repository=repository,
        eval_runner=eval_runner,
    )


@router.get("/{workflow_id}/release-readiness")
def get_release_readiness(
    workflow_id: str,
    version: str | None = None,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    workflow = (
        repository.get_workflow_version(workflow_id, version)
        if version
        else repository.get_workflow(workflow_id)
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return _build_release_readiness(workflow, repository)


@router.post("/{workflow_id}/versions/{version}/promote")
def promote_workflow_version(
    workflow_id: str,
    version: str,
    request: PromoteWorkflowVersionRequest,
    actor: ActorContext = Depends(require_scope("workflow:promote")),
    repository: WorkflowRepository = Depends(get_repository),
    eval_runner: EvalRunner = Depends(get_eval_runner),
) -> dict[str, Any]:
    if actor.role != "workflow-admin":
        _save_promotion_audit_event(
            repository=repository,
            actor=actor,
            workflow_id=workflow_id,
            version=version,
            status="failed",
            reason=request.reason,
            details={
                "gate": "role",
                "message": "only workflow-admin can promote workflow versions",
                "actor_role": actor.role,
            },
        )
        raise HTTPException(
            status_code=403,
            detail={
                "message": "only workflow-admin can promote workflow versions",
                "actor_role": actor.role,
            },
        )
    candidate = repository.get_workflow_version(workflow_id, version)
    if candidate is None:
        _save_promotion_audit_event(
            repository=repository,
            actor=actor,
            workflow_id=workflow_id,
            version=version,
            status="failed",
            reason=request.reason,
            details={
                "gate": "version_lookup",
                "message": "workflow version not found",
            },
        )
        raise HTTPException(status_code=404, detail="workflow version not found")
    current_workflow = repository.get_workflow(workflow_id)
    release_context = _build_release_context(
        request=request,
        candidate=candidate,
        current_workflow=current_workflow,
        repository=repository,
    )
    validation_report = workflow_linter.lint(candidate)
    if not validation_report.valid:
        _save_promotion_audit_event(
            repository=repository,
            actor=actor,
            workflow_id=workflow_id,
            version=version,
            status="failed",
            reason=request.reason,
            details={
                "gate": "quality",
                "validation_report": dump_model(validation_report),
                "release_context": release_context,
            },
        )
        raise HTTPException(status_code=422, detail=dump_model(validation_report))
    eval_results = _tag_eval_results_with_version(eval_runner.run_all(candidate), candidate.version)
    repository.save_eval_results(workflow_id, eval_results)
    failed_evals = [result for result in eval_results if not result.passed]
    if failed_evals:
        _save_promotion_audit_event(
            repository=repository,
            actor=actor,
            workflow_id=workflow_id,
            version=version,
            status="failed",
            reason=request.reason,
            details={
                "gate": "eval",
                "failed_eval_results": [dump_model(result) for result in failed_evals],
                "eval_count": len(eval_results),
                "failed_count": len(failed_evals),
                "release_context": release_context,
            },
        )
        raise HTTPException(
            status_code=422,
            detail={
                "valid": False,
                "gate": "eval",
                "failed_eval_results": [dump_model(result) for result in failed_evals],
            },
        )
    promoted = repository.promote_workflow_version(workflow_id, version)
    if promoted is None:
        _save_promotion_audit_event(
            repository=repository,
            actor=actor,
            workflow_id=workflow_id,
            version=version,
            status="failed",
            reason=request.reason,
            details={
                "gate": "promote",
                "message": "workflow version not found during promote",
            },
        )
        raise HTTPException(status_code=404, detail="workflow version not found")
    _save_promotion_audit_event(
        repository=repository,
        actor=actor,
        workflow_id=workflow_id,
        version=version,
        status="succeeded",
        reason=request.reason,
        details={
            "promoted_version": version,
            "eval_results": [dump_model(result) for result in eval_results],
            "release_context": release_context,
        },
    )
    return {
        "workflow_package": dump_model(promoted),
        "promotion": {
            "workflow_id": workflow_id,
            "version": version,
            "reason": request.reason,
            "change_summary": request.change_summary,
            "risk_acceptance": request.risk_acceptance,
            "reviewed_diff": request.reviewed_diff,
            "readiness_acknowledged": request.readiness_acknowledged,
            "actor_id": actor.actor_id,
            "actor_role": actor.role,
        },
        "release_context": release_context,
        "validation_report": dump_model(validation_report),
        "eval_results": [dump_model(result) for result in eval_results],
    }


def _build_release_context(
    *,
    request: PromoteWorkflowVersionRequest,
    candidate: WorkflowPackage,
    current_workflow: WorkflowPackage | None,
    repository: WorkflowRepository,
) -> dict[str, Any]:
    changes = (
        diff_workflow_packages(current_workflow, candidate)
        if current_workflow is not None
        else []
    )
    readiness = _build_release_readiness(candidate, repository)
    return {
        "current_version": current_workflow.version if current_workflow else None,
        "target_version": candidate.version,
        "change_count": len(changes),
        "change_summary_present": bool(request.change_summary),
        "risk_acceptance_present": bool(request.risk_acceptance),
        "reviewed_diff": request.reviewed_diff,
        "readiness_acknowledged": request.readiness_acknowledged,
        "live_ready": readiness["live_ready"],
        "blocking_reasons": readiness["blocking_reasons"],
    }


def _save_promotion_audit_event(
    *,
    repository: WorkflowRepository,
    actor: ActorContext,
    workflow_id: str,
    version: str,
    status: str,
    reason: str | None,
    details: dict[str, Any],
) -> None:
    repository.save_audit_event(
        build_audit_event(
            event_type="workflow_version_promotion",
            action="promote",
            status=status,
            actor=actor,
            workflow_id=workflow_id,
            workflow_version=version,
            resource_type="workflow_package",
            resource_id=f"{workflow_id}@{version}",
            reason=reason,
            details=details,
        )
    )


def _version_filtered_traces(repository: WorkflowRepository, workflow_id: str, version: str) -> list[Any]:
    return [
        trace
        for trace in repository.list_traces(workflow_id=workflow_id)
        if trace.workflow_version in {None, version}
    ]


def _version_filtered_eval_results(repository: WorkflowRepository, workflow_id: str, version: str) -> list[Any]:
    return [
        result
        for result in repository.list_eval_results(workflow_id)
        if result.details.get("workflow_version") in {None, version}
    ]


def _apply_repair_plan_to_candidate(
    *,
    source_workflow: WorkflowPackage,
    target_version: str,
    repair_plan: Any,
) -> WorkflowPackage:
    data_contracts = list(source_workflow.data_contracts)
    tool_policies = list(source_workflow.tool_policies)
    eval_specs = list(source_workflow.eval_specs)

    for operation in repair_plan.operations:
        if operation.target_type == "data_contract":
            data_contracts = _apply_contract_repair(data_contracts, operation)
        elif operation.target_type == "tool_policy":
            tool_policies = _apply_tool_policy_repair(tool_policies, operation)
        elif operation.target_type == "eval_specs":
            eval_specs = _apply_eval_repair(eval_specs, operation, source_workflow.workflow_id)

    current_time = utc_now()
    return source_workflow.model_copy(
        update={
            "version": target_version,
            "data_contracts": data_contracts,
            "tool_policies": tool_policies,
            "eval_specs": eval_specs,
            "created_at": current_time,
            "updated_at": current_time,
        }
    )


def _apply_contract_repair(data_contracts: list[Any], operation: Any) -> list[Any]:
    updated_contracts = []
    for contract in data_contracts:
        if contract.contract_id != operation.target_id:
            updated_contracts.append(contract)
            continue
        changes = operation.proposed_changes
        updated_contracts.append(
            contract.model_copy(
                update={
                    "required_fields": _append_unique(
                        contract.required_fields,
                        changes.get("required_fields_append", []),
                    ),
                    "validation_rules": _append_unique(
                        contract.validation_rules,
                        changes.get("validation_rules_append", []),
                    ),
                    "error_policy": changes.get("error_policy") or contract.error_policy,
                }
            )
        )
    return updated_contracts


def _apply_tool_policy_repair(tool_policies: list[Any], operation: Any) -> list[Any]:
    target_ids = {target_id.strip() for target_id in operation.target_id.split(",") if target_id.strip()}
    updated_policies = []
    for policy in tool_policies:
        if policy.tool_id not in target_ids:
            updated_policies.append(policy)
            continue
        changes = operation.proposed_changes
        updated_policies.append(
            policy.model_copy(
                update={
                    "requires_approval": bool(changes.get("requires_approval", policy.requires_approval)),
                    "allowed_roles": _append_unique(
                        policy.allowed_roles,
                        changes.get("allowed_roles_append", []),
                    ),
                    "required_scopes": _append_unique(
                        policy.required_scopes,
                        changes.get("required_scopes_append", []),
                    ),
                }
            )
        )
    return updated_policies


def _apply_eval_repair(eval_specs: list[EvalSpec], operation: Any, workflow_id: str) -> list[EvalSpec]:
    existing_ids = {eval_spec.eval_id for eval_spec in eval_specs}
    eval_id = _unique_eval_id(workflow_id, operation.action, existing_ids)
    if operation.action == "add_regression_eval_specs":
        source_eval_ids = operation.proposed_changes.get("source_eval_ids", [])
        eval_specs.append(
            EvalSpec(
                eval_id=eval_id,
                workflow_id=workflow_id,
                name="Repair regression case",
                eval_type=EvalType.REGRESSION,
                input_case={
                    "context": {
                        "repair_operation_id": operation.operation_id,
                        "source_eval_ids": source_eval_ids,
                    },
                    "artifacts": [],
                    "assumptions": ["repair regression case generated from low-sensitive eval failure evidence"],
                },
                expected_output={"status": "completed_or_paused_for_approval"},
                scoring_rules=[
                    "workflow must produce trace",
                    "workflow must complete or pause for approval",
                    "repair candidate must preserve existing promotion gates",
                ],
            )
        )
        return eval_specs

    eval_specs.append(
        EvalSpec(
            eval_id=eval_id,
            workflow_id=workflow_id,
            name="Repair baseline coverage case",
            eval_type=EvalType.END_TO_END,
            input_case={
                "context": {"repair_operation_id": operation.operation_id},
                "artifacts": [],
                "assumptions": ["repair baseline coverage case"],
            },
            expected_output={"status": "completed_or_paused_for_approval"},
            scoring_rules=[
                "workflow must produce traces",
                "approval-gated writes must pause or run in shadow mode",
            ],
        )
    )
    return eval_specs


def _unique_eval_id(workflow_id: str, action: str, existing_ids: set[str]) -> str:
    normalized_action = action.replace("_", "-")[:48] or "repair"
    base = f"{workflow_id}-repair-{normalized_action}"
    candidate = base
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    existing_ids.add(candidate)
    return candidate


def _append_unique(current: list[str], additions: list[str]) -> list[str]:
    values = list(current)
    seen = set(values)
    for item in additions:
        if item and item not in seen:
            values.append(item)
            seen.add(item)
    return values


def _save_repair_candidate_audit_event(
    *,
    repository: WorkflowRepository,
    actor: ActorContext,
    workflow_id: str,
    source_version: str | None,
    target_version: str,
    status: str,
    reason: str,
    details: dict[str, Any],
) -> None:
    repository.save_audit_event(
        build_audit_event(
            event_type="workflow_package_repair_candidate",
            action="apply_repair_plan",
            status=status,
            actor=actor,
            workflow_id=workflow_id,
            workflow_version=target_version,
            resource_type="workflow_package",
            resource_id=f"{workflow_id}@{target_version}",
            reason=reason,
            details={
                "source_version": source_version,
                "target_version": target_version,
                **details,
            },
        )
    )


@router.get("/{workflow_id}/diff")
def diff_workflow_versions(
    workflow_id: str,
    from_version: str,
    to_version: str,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    from_workflow = repository.get_workflow_version(workflow_id, from_version)
    to_workflow = repository.get_workflow_version(workflow_id, to_version)
    if from_workflow is None or to_workflow is None:
        raise HTTPException(status_code=404, detail="workflow version not found")
    changes = diff_workflow_packages(from_workflow, to_workflow)
    return {
        "workflow_id": workflow_id,
        "from_version": from_version,
        "to_version": to_version,
        "change_count": len(changes),
        "changes": changes,
    }


@router.get("/{workflow_id}")
def get_workflow(
    workflow_id: str,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    workflow = repository.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return dump_model(workflow)


@router.get("/{workflow_id}/export")
def export_workflow(
    workflow_id: str,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    workflow = repository.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return dump_model(workflow)


@router.post("/import")
def import_workflow(
    payload: dict[str, Any],
    save_as_current: bool = True,
    actor: ActorContext = Depends(require_scope("workflow:write")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    try:
        workflow_package = WorkflowPackage.model_validate(payload)
    except ValidationError as exc:
        repository.save_audit_event(
            build_audit_event(
                event_type="workflow_package_import",
                action="import",
                status="failed",
                actor=actor,
                resource_type="workflow_package",
                resource_id=str(payload.get("workflow_id") or "unknown"),
                details={
                    "error_count": len(exc.errors()),
                    "error_types": [str(error.get("type")) for error in exc.errors()],
                },
            )
        )
        return {"valid": False, "errors": exc.errors()}
    validation_report = workflow_linter.lint(workflow_package)
    if not validation_report.valid:
        repository.save_audit_event(
            build_audit_event(
                event_type="workflow_package_import",
                action="import",
                status="failed",
                actor=actor,
                workflow_id=workflow_package.workflow_id,
                workflow_version=workflow_package.version,
                resource_type="workflow_package",
                resource_id=f"{workflow_package.workflow_id}@{workflow_package.version}",
                details={"validation_report": dump_model(validation_report), "save_as_current": save_as_current},
            )
        )
        return dump_model(validation_report)
    if not save_as_current and repository.get_workflow(workflow_package.workflow_id) is None:
        repository.save_audit_event(
            build_audit_event(
                event_type="workflow_package_import",
                action="import",
                status="failed",
                actor=actor,
                workflow_id=workflow_package.workflow_id,
                workflow_version=workflow_package.version,
                resource_type="workflow_package",
                resource_id=f"{workflow_package.workflow_id}@{workflow_package.version}",
                details={
                    "gate": "candidate_base_workflow",
                    "message": "candidate versions require an existing current workflow",
                    "save_as_current": save_as_current,
                },
            )
        )
        raise HTTPException(status_code=409, detail="candidate versions require an existing current workflow")
    if save_as_current:
        repository.save_workflow(workflow_package)
    else:
        repository.save_workflow_version(workflow_package)
    repository.save_audit_event(
        build_audit_event(
            event_type="workflow_package_import",
            action="import",
            status="succeeded",
            actor=actor,
            workflow_id=workflow_package.workflow_id,
            workflow_version=workflow_package.version,
            resource_type="workflow_package",
            resource_id=f"{workflow_package.workflow_id}@{workflow_package.version}",
            details={
                "save_as_current": save_as_current,
                "validation_error_count": len(validation_report.errors),
                "validation_warning_count": len(validation_report.warnings),
            },
        )
    )
    return {
        "valid": True,
        "workflow_package": dump_model(workflow_package),
        "validation_report": dump_model(validation_report),
        "saved_as_current": save_as_current,
    }


@router.post("/validate")
def validate_workflow_package(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        workflow_package = WorkflowPackage.model_validate(payload)
    except ValidationError as exc:
        return {"valid": False, "errors": exc.errors()}
    validation_report = workflow_linter.lint(workflow_package)
    return {
        "valid": validation_report.valid,
        "workflow_id": workflow_package.workflow_id,
        "version": workflow_package.version,
        "errors": [dump_model(issue) for issue in validation_report.errors],
        "warnings": [dump_model(issue) for issue in validation_report.warnings],
    }


@router.post("/{workflow_id}/runs")
def run_workflow(
    workflow_id: str,
    request: RunWorkflowRequest,
    actor: ActorContext = Depends(require_scope("workflow:run")),
    repository: WorkflowRepository = Depends(get_repository),
    runner: WorkflowRunner = Depends(get_workflow_runner),
) -> dict[str, Any]:
    workflow = (
        repository.get_workflow_version(workflow_id, request.workflow_version)
        if request.workflow_version
        else repository.get_workflow(workflow_id)
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    if request.enforce_release_readiness and not request.shadow_mode:
        readiness = _build_release_readiness(workflow, repository)
        if not readiness["live_ready"]:
            repository.save_audit_event(
                build_audit_event(
                    event_type="workflow_run_start",
                    action="start",
                    status="failed",
                    actor=actor,
                    workflow_id=workflow.workflow_id,
                    workflow_version=workflow.version,
                    resource_type="workflow_package",
                    resource_id=f"{workflow.workflow_id}@{workflow.version}",
                    details={
                        "gate": "release_readiness",
                        "blocking_reasons": readiness["blocking_reasons"],
                        "shadow_mode": request.shadow_mode,
                        "enforce_release_readiness": request.enforce_release_readiness,
                    },
                )
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "workflow version is not ready for live execution",
                    "readiness": readiness,
                },
            )
    request_fingerprint = fingerprint_run_request(
        request_kind="run_start",
        workflow_id=workflow.workflow_id,
        workflow_version=workflow.version,
        input_payload=request.input_payload,
        max_steps=request.max_steps,
        max_retries=request.max_retries,
        shadow_mode=request.shadow_mode,
        enforce_release_readiness=request.enforce_release_readiness,
    )
    if request.idempotency_key:
        existing_run = repository.get_run_by_idempotency_key(workflow.workflow_id, request.idempotency_key)
        if existing_run is not None:
            if existing_run.request_fingerprint != request_fingerprint:
                repository.save_audit_event(
                    build_audit_event(
                        event_type="workflow_run_idempotency_replay",
                        action="replay",
                        status="failed",
                        actor=actor,
                        workflow_id=existing_run.workflow_id,
                        workflow_version=existing_run.workflow_version,
                        run_id=existing_run.run_id,
                        resource_type="workflow_run",
                        resource_id=existing_run.run_id,
                        details={
                            "gate": "idempotency_conflict",
                            "message": "idempotency key was already used with a different run request",
                        },
                    )
                )
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "idempotency key was already used with a different run request",
                        "workflow_id": workflow.workflow_id,
                        "existing_run_id": existing_run.run_id,
                    },
                )
            repository.save_audit_event(
                build_audit_event(
                    event_type="workflow_run_idempotency_replay",
                    action="replay",
                    status="succeeded",
                    actor=actor,
                    workflow_id=existing_run.workflow_id,
                    workflow_version=existing_run.workflow_version,
                    run_id=existing_run.run_id,
                    resource_type="workflow_run",
                    resource_id=existing_run.run_id,
                    details={
                        "result_status": existing_run.status,
                        "current_node_id": existing_run.current_node_id,
                        "input_keys": sorted(request.input_payload.keys()),
                        "shadow_mode": request.shadow_mode,
                        "enforce_release_readiness": request.enforce_release_readiness,
                        "trace_count": len(existing_run.traces),
                    },
                )
            )
            return dump_redacted_model(existing_run)
    run = runner.run(
        workflow,
        input_payload=request.input_payload,
        idempotency_key=request.idempotency_key,
        request_fingerprint=request_fingerprint if request.idempotency_key else None,
        max_steps=request.max_steps,
        max_retries=request.max_retries,
        shadow_mode=request.shadow_mode,
        actor_context=_tool_execution_context(actor),
        checkpoint=repository.save_run,
    )
    repository.save_run(run)
    repository.save_audit_event(
        build_audit_event(
            event_type="workflow_run_start",
            action="start",
            status="succeeded",
            actor=actor,
            workflow_id=run.workflow_id,
            workflow_version=run.workflow_version,
            run_id=run.run_id,
            resource_type="workflow_run",
            resource_id=run.run_id,
            details={
                "result_status": run.status,
                "current_node_id": run.current_node_id,
                "input_keys": sorted(request.input_payload.keys()),
                "idempotency_key_present": request.idempotency_key is not None,
                "max_steps": request.max_steps,
                "max_retries": request.max_retries,
                "shadow_mode": request.shadow_mode,
                "enforce_release_readiness": request.enforce_release_readiness,
                "trace_count": len(run.traces),
            },
        )
    )
    return dump_redacted_model(run)


def _tool_execution_context(actor: ActorContext) -> ToolExecutionContext:
    return ToolExecutionContext(
        actor_id=actor.actor_id,
        actor_role=None,
        actor_scopes=tuple(actor.scopes),
    )


@router.post("/{workflow_id}/evals/run")
def run_evals(
    workflow_id: str,
    actor: ActorContext = Depends(require_scope("workflow:evaluate")),
    repository: WorkflowRepository = Depends(get_repository),
    eval_runner: EvalRunner = Depends(get_eval_runner),
) -> list[dict[str, Any]]:
    workflow = repository.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return _run_and_persist_workflow_evals(
        workflow=workflow,
        actor=actor,
        repository=repository,
        eval_runner=eval_runner,
    )


def _run_and_persist_workflow_evals(
    *,
    workflow: WorkflowPackage,
    actor: ActorContext,
    repository: WorkflowRepository,
    eval_runner: EvalRunner,
) -> list[dict[str, Any]]:
    results = _tag_eval_results_with_version(eval_runner.run_all(workflow), workflow.version)
    repository.save_eval_results(workflow.workflow_id, results)
    repository.save_audit_event(
        build_audit_event(
            event_type="workflow_eval_run",
            action="run",
            status="succeeded",
            actor=actor,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.version,
            resource_type="workflow_eval",
            resource_id=f"{workflow.workflow_id}@{workflow.version}",
            details={
                "eval_count": len(results),
                "passed_count": sum(1 for result in results if result.passed),
                "failed_count": sum(1 for result in results if not result.passed),
                "eval_ids": [result.eval_id for result in results],
            },
        )
    )
    return [dump_model(result) for result in results]


def _tag_eval_results_with_version(results: list[Any], workflow_version: str) -> list[Any]:
    return [
        result.model_copy(
            update={
                "details": {
                    **result.details,
                    "workflow_version": workflow_version,
                }
            }
        )
        for result in results
    ]


@router.get("/{workflow_id}/optimization-suggestions")
def get_optimization_suggestions(
    workflow_id: str,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
    optimization_loop: OptimizationLoop = Depends(get_optimization_loop),
) -> list[dict[str, Any]]:
    if repository.get_workflow(workflow_id) is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    traces = repository.list_traces(workflow_id=workflow_id)
    eval_results = repository.list_eval_results(workflow_id)
    suggestions = optimization_loop.run(workflow_id, traces, eval_results)
    return [dump_model(suggestion) for suggestion in suggestions]


@router.get("/{workflow_id}/repair-plan")
def get_workflow_repair_plan(
    workflow_id: str,
    version: str | None = None,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
    optimization_loop: OptimizationLoop = Depends(get_optimization_loop),
) -> dict[str, Any]:
    workflow = (
        repository.get_workflow_version(workflow_id, version)
        if version
        else repository.get_workflow(workflow_id)
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    traces = [
        trace
        for trace in repository.list_traces(workflow_id=workflow_id)
        if trace.workflow_version in {None, workflow.version}
    ]
    eval_results = [
        result
        for result in repository.list_eval_results(workflow_id)
        if result.details.get("workflow_version") in {None, workflow.version}
    ]
    plan = optimization_loop.repair_plan(workflow, traces, eval_results)
    return dump_model(plan)


@router.post("/{workflow_id}/repair-plan/preview")
def preview_workflow_repair_candidate(
    workflow_id: str,
    request: ApplyRepairPlanRequest,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
    optimization_loop: OptimizationLoop = Depends(get_optimization_loop),
) -> dict[str, Any]:
    preview = _build_repair_candidate_preview(
        workflow_id=workflow_id,
        request=request,
        repository=repository,
        optimization_loop=optimization_loop,
        require_unused_target=False,
    )
    return _repair_candidate_preview_response(preview)


@router.post("/{workflow_id}/repair-plan/apply")
def apply_workflow_repair_plan(
    workflow_id: str,
    request: ApplyRepairPlanRequest,
    actor: ActorContext = Depends(require_scope("workflow:write")),
    repository: WorkflowRepository = Depends(get_repository),
    optimization_loop: OptimizationLoop = Depends(get_optimization_loop),
) -> dict[str, Any]:
    source_workflow = (
        repository.get_workflow_version(workflow_id, request.source_version)
        if request.source_version
        else repository.get_workflow(workflow_id)
    )
    if source_workflow is None:
        _save_repair_candidate_audit_event(
            repository=repository,
            actor=actor,
            workflow_id=workflow_id,
            source_version=request.source_version,
            target_version=request.target_version,
            status="failed",
            reason=request.reason,
            details={"gate": "source_lookup", "message": "source workflow version not found"},
        )
        raise HTTPException(status_code=404, detail="source workflow version not found")
    if repository.get_workflow_version(workflow_id, request.target_version) is not None:
        _save_repair_candidate_audit_event(
            repository=repository,
            actor=actor,
            workflow_id=workflow_id,
            source_version=source_workflow.version,
            target_version=request.target_version,
            status="failed",
            reason=request.reason,
            details={"gate": "target_version", "message": "target workflow version already exists"},
        )
        raise HTTPException(status_code=409, detail="target workflow version already exists")

    try:
        preview = _build_repair_candidate_preview(
            workflow_id=workflow_id,
            request=request,
            repository=repository,
            optimization_loop=optimization_loop,
            require_unused_target=True,
            source_workflow=source_workflow,
        )
    except HTTPException as exc:
        _save_repair_candidate_audit_event(
            repository=repository,
            actor=actor,
            workflow_id=workflow_id,
            source_version=source_workflow.version,
            target_version=request.target_version,
            status="failed",
            reason=request.reason,
            details={
                "gate": "operation_selection" if exc.status_code == 422 else "candidate_preview",
                "selected_operation_ids": request.selected_operation_ids or [],
                "detail": exc.detail,
            },
        )
        raise
    repair_plan = preview["repair_plan"]
    candidate = preview["candidate"]
    validation_report = preview["validation_report"]
    source_workflow = preview["source_workflow"]
    selected_operation_ids = [operation.operation_id for operation in repair_plan.operations]
    impact_preview = _build_repair_impact_preview(
        repair_plan=repair_plan,
        changes=preview["changes"],
        validation_report=validation_report,
    )
    if not validation_report.valid:
        _save_repair_candidate_audit_event(
            repository=repository,
            actor=actor,
            workflow_id=workflow_id,
            source_version=source_workflow.version,
            target_version=request.target_version,
            status="failed",
            reason=request.reason,
            details={
                "gate": "quality",
                "operation_count": len(repair_plan.operations),
                "operation_ids": selected_operation_ids,
                "validation_report": dump_model(validation_report),
                "impact_summary": _repair_impact_audit_summary(impact_preview),
            },
        )
        raise HTTPException(status_code=422, detail=dump_model(validation_report))

    repository.save_workflow_version(candidate)
    _save_repair_candidate_audit_event(
        repository=repository,
        actor=actor,
        workflow_id=workflow_id,
        source_version=source_workflow.version,
        target_version=request.target_version,
        status="succeeded",
        reason=request.reason,
        details={
            "operation_count": len(repair_plan.operations),
            "operation_ids": selected_operation_ids,
            "suggestion_count": len(repair_plan.suggestions),
            "validation_error_count": len(validation_report.errors),
            "validation_warning_count": len(validation_report.warnings),
            "impact_summary": _repair_impact_audit_summary(impact_preview),
            "saved_as_current": False,
        },
    )
    return {
        "workflow_package": dump_model(candidate),
        "repair_plan": dump_model(repair_plan),
        "impact_preview": impact_preview,
        "validation_report": dump_model(validation_report),
        "source_version": source_workflow.version,
        "target_version": request.target_version,
        "saved_as_current": False,
    }


def _build_repair_candidate_preview(
    *,
    workflow_id: str,
    request: ApplyRepairPlanRequest,
    repository: WorkflowRepository,
    optimization_loop: OptimizationLoop,
    require_unused_target: bool,
    source_workflow: WorkflowPackage | None = None,
) -> dict[str, Any]:
    source = source_workflow or (
        repository.get_workflow_version(workflow_id, request.source_version)
        if request.source_version
        else repository.get_workflow(workflow_id)
    )
    if source is None:
        raise HTTPException(status_code=404, detail="source workflow version not found")
    target_exists = repository.get_workflow_version(workflow_id, request.target_version) is not None
    if require_unused_target and target_exists:
        raise HTTPException(status_code=409, detail="target workflow version already exists")
    traces = _version_filtered_traces(repository, workflow_id, source.version)
    eval_results = _version_filtered_eval_results(repository, workflow_id, source.version)
    repair_plan = optimization_loop.repair_plan(source, traces, eval_results)
    repair_plan = _select_repair_operations(repair_plan, request.selected_operation_ids)
    candidate = _apply_repair_plan_to_candidate(
        source_workflow=source,
        target_version=request.target_version,
        repair_plan=repair_plan,
    )
    validation_report = workflow_linter.lint(candidate)
    changes = diff_workflow_packages(source, candidate)
    return {
        "source_workflow": source,
        "candidate": candidate,
        "repair_plan": repair_plan,
        "validation_report": validation_report,
        "changes": changes,
        "target_version_available": not target_exists,
    }


def _repair_candidate_preview_response(preview: dict[str, Any]) -> dict[str, Any]:
    source = preview["source_workflow"]
    candidate = preview["candidate"]
    changes = preview["changes"]
    repair_plan = preview["repair_plan"]
    validation_report = preview["validation_report"]
    return {
        "workflow_id": source.workflow_id,
        "source_version": source.version,
        "target_version": candidate.version,
        "target_version_available": preview["target_version_available"],
        "selected_operation_count": len(repair_plan.operations),
        "selected_operation_ids": [operation.operation_id for operation in repair_plan.operations],
        "change_count": len(changes),
        "changes": [dump_model(change) for change in changes],
        "impact_preview": _build_repair_impact_preview(
            repair_plan=repair_plan,
            changes=changes,
            validation_report=validation_report,
        ),
        "validation_report": dump_model(validation_report),
        "candidate_summary": {
            "workflow_id": candidate.workflow_id,
            "version": candidate.version,
            "contract_count": len(candidate.data_contracts),
            "tool_policy_count": len(candidate.tool_policies),
            "eval_spec_count": len(candidate.eval_specs),
        },
    }


def _build_repair_impact_preview(
    *,
    repair_plan: Any,
    changes: list[dict[str, Any]],
    validation_report: Any,
) -> dict[str, Any]:
    field_impacts = [
        _repair_field_impact(change, repair_plan.operations)
        for change in changes[:_REPAIR_IMPACT_CHANGE_LIMIT]
    ]
    impacted_sections = sorted({impact["section"] for impact in field_impacts})
    risk_counts = _count_values(field_impacts, "risk_level")
    release_gate_impacts = sorted(
        {
            gate
            for operation in repair_plan.operations
            for gate in _release_gates_for_repair_operation(operation)
        }
    )
    return {
        "change_count": len(changes),
        "field_impact_count": len(field_impacts),
        "field_impact_limit": _REPAIR_IMPACT_CHANGE_LIMIT,
        "truncated": len(changes) > _REPAIR_IMPACT_CHANGE_LIMIT,
        "impacted_sections": impacted_sections,
        "risk_counts": risk_counts,
        "release_gate_impacts": release_gate_impacts,
        "operation_impacts": [
            _repair_operation_impact(operation)
            for operation in repair_plan.operations
        ],
        "field_impacts": field_impacts,
        "validation_impact": {
            "valid": validation_report.valid,
            "error_count": len(validation_report.errors),
            "warning_count": len(validation_report.warnings),
        },
    }


def _repair_impact_audit_summary(impact_preview: dict[str, Any]) -> dict[str, Any]:
    return {
        "change_count": impact_preview["change_count"],
        "field_impact_count": impact_preview["field_impact_count"],
        "truncated": impact_preview["truncated"],
        "impacted_sections": impact_preview["impacted_sections"],
        "risk_counts": impact_preview["risk_counts"],
        "release_gate_impacts": impact_preview["release_gate_impacts"],
        "validation_impact": impact_preview["validation_impact"],
        "operation_impacts": impact_preview["operation_impacts"],
    }


def _repair_field_impact(change: dict[str, Any], operations: list[Any]) -> dict[str, Any]:
    path = str(change.get("path", "$"))
    section = _repair_change_section(path)
    return {
        "op": change.get("op", "changed"),
        "path": path,
        "section": section,
        "risk_level": _repair_change_risk_level(path, section),
        "reason_code": _repair_change_reason_code(path, section),
        "operation_ids": _operation_ids_for_repair_section(section, operations),
    }


def _repair_operation_impact(operation: Any) -> dict[str, Any]:
    target_type = str(operation.target_type)
    return {
        "operation_id": operation.operation_id,
        "target_type": target_type,
        "target_id": operation.target_id,
        "action": operation.action,
        "risk_level": _repair_operation_risk_level(target_type),
        "release_gate_impacts": _release_gates_for_repair_operation(operation),
        "reason_code": _repair_operation_reason_code(target_type),
    }


def _repair_change_section(path: str) -> str:
    if ".data_contracts" in path:
        return "data_contracts"
    if ".eval_specs" in path:
        return "eval_specs"
    if ".tool_policies" in path:
        return "tool_policies"
    if ".process_spec" in path:
        return "process_spec"
    if ".agent_specs" in path:
        return "agent_specs"
    if ".problem_spec" in path:
        return "problem_spec"
    return "package_metadata"


def _repair_change_risk_level(path: str, section: str) -> str:
    if section == "tool_policies" and any(
        marker in path
        for marker in (
            ".adapter",
            ".permission_level",
            ".requires_approval",
            ".allowed_roles",
            ".required_scopes",
            ".risk_level",
        )
    ):
        return "high"
    if section == "process_spec" and any(
        marker in path
        for marker in (".nodes", ".edges", ".tool_ids", ".output_contract_id", ".input_contract_id")
    ):
        return "high"
    if section in {"data_contracts", "eval_specs", "tool_policies", "process_spec"}:
        return "medium"
    return "low"


def _repair_change_reason_code(path: str, section: str) -> str:
    if section == "data_contracts":
        if ".output_schema" in path:
            return "runtime_output_schema_changed"
        if ".required_fields" in path:
            return "runtime_required_fields_changed"
        if ".validation_rules" in path:
            return "runtime_validation_rules_changed"
        if ".error_policy" in path:
            return "runtime_error_policy_changed"
        return "runtime_contract_changed"
    if section == "eval_specs":
        return "eval_gate_coverage_changed"
    if section == "tool_policies":
        return "tool_sandbox_policy_changed"
    if section == "process_spec":
        return "workflow_graph_changed"
    return "package_metadata_changed"


def _operation_ids_for_repair_section(section: str, operations: list[Any]) -> list[str]:
    target_types = {
        "data_contracts": {"data_contract"},
        "eval_specs": {"eval_specs"},
        "tool_policies": {"tool_policy"},
    }.get(section, set())
    if not target_types:
        return []
    return [
        operation.operation_id
        for operation in operations
        if str(operation.target_type) in target_types
    ]


def _repair_operation_risk_level(target_type: str) -> str:
    if target_type == "tool_policy":
        return "high"
    if target_type in {"data_contract", "eval_specs"}:
        return "medium"
    return "low"


def _release_gates_for_repair_operation(operation: Any) -> list[str]:
    target_type = str(operation.target_type)
    if target_type == "eval_specs":
        return ["version_eval_results"]
    if target_type == "data_contract":
        return ["quality_gate", "version_eval_results"]
    if target_type == "tool_policy":
        return ["quality_gate", "terminal_shadow_run", "passing_shadow_comparison"]
    return ["quality_gate"]


def _repair_operation_reason_code(target_type: str) -> str:
    if target_type == "data_contract":
        return "runtime_contract_repair"
    if target_type == "eval_specs":
        return "regression_eval_repair"
    if target_type == "tool_policy":
        return "approval_or_tool_policy_repair"
    return "package_repair"


def _count_values(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key, "unknown"))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _select_repair_operations(repair_plan: Any, selected_operation_ids: list[str] | None) -> Any:
    if selected_operation_ids is None:
        return repair_plan
    requested_ids = set(selected_operation_ids)
    operations_by_id = {operation.operation_id: operation for operation in repair_plan.operations}
    unknown_ids = sorted(requested_ids - set(operations_by_id))
    if unknown_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "selected repair operations are not in the current repair plan",
                "unknown_operation_ids": unknown_ids,
            },
        )
    selected_operations = [operation for operation in repair_plan.operations if operation.operation_id in requested_ids]
    return repair_plan.model_copy(update={"operations": selected_operations})


def _build_release_readiness(workflow: WorkflowPackage, repository: WorkflowRepository) -> dict[str, Any]:
    validation_report = workflow_linter.lint(workflow)
    eval_results = repository.list_eval_results(workflow.workflow_id)
    version_eval_results = [
        result
        for result in eval_results
        if result.details.get("workflow_version") == workflow.version
        and result.details.get("eval_type") != "shadow_comparison"
    ]
    passing_version_eval_results = [result for result in version_eval_results if result.passed]
    failed_version_eval_results = [result for result in version_eval_results if not result.passed]
    shadow_runs = [
        run
        for run in repository.list_runs(
            workflow_id=workflow.workflow_id,
            status=WorkflowRunStatus.COMPLETED,
        )
        if run.workflow_version == workflow.version and run.shadow_mode
    ]
    shadow_run_ids = {run.run_id for run in shadow_runs}
    shadow_comparisons = [
        result
        for result in eval_results
        if result.details.get("eval_type") == "shadow_comparison"
        and result.details.get("workflow_version") == workflow.version
        and result.details.get("run_id") in shadow_run_ids
    ]
    passing_shadow_comparisons = [result for result in shadow_comparisons if result.passed]
    checks = [
        {
            "name": "quality_gate",
            "status": "passed" if validation_report.valid else "failed",
            "details": {
                "error_count": len(validation_report.errors),
                "warning_count": len(validation_report.warnings),
            },
        },
        {
            "name": "version_eval_results",
            "status": "passed" if version_eval_results and not failed_version_eval_results else "failed",
            "details": {
                "eval_result_count": len(version_eval_results),
                "passing_eval_result_count": len(passing_version_eval_results),
                "failed_eval_result_count": len(failed_version_eval_results),
                "eval_ids": [result.eval_id for result in version_eval_results],
                "failed_eval_ids": [result.eval_id for result in failed_version_eval_results],
            },
        },
        {
            "name": "terminal_shadow_run",
            "status": "passed" if shadow_runs else "failed",
            "details": {
                "shadow_run_count": len(shadow_runs),
                "shadow_run_ids": sorted(shadow_run_ids),
            },
        },
        {
            "name": "passing_shadow_comparison",
            "status": "passed" if passing_shadow_comparisons else "failed",
            "details": {
                "comparison_count": len(shadow_comparisons),
                "passing_comparison_count": len(passing_shadow_comparisons),
                "passing_eval_ids": [result.eval_id for result in passing_shadow_comparisons],
            },
        },
    ]
    blocking_reasons = [
        check["name"]
        for check in checks
        if check["status"] != "passed"
    ]
    return {
        "workflow_id": workflow.workflow_id,
        "workflow_version": workflow.version,
        "live_ready": not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "checks": checks,
    }
