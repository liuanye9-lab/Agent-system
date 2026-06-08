from __future__ import annotations

from packages.workflow_core.builder import OptimizerAgent, WorkflowBuilder
from packages.workflow_core.models import EvalResult, TraceRecord
from packages.workflow_core.models.enums import NodeExecutionStatus, SuggestionType


def test_optimizer_agent_suggests_fix_from_failed_trace() -> None:
    trace = TraceRecord(
        run_id="run-1",
        workflow_id="workflow-1",
        node_id="business-case",
        status=NodeExecutionStatus.FAILED,
        error="missing decision field",
    )

    suggestions = OptimizerAgent().suggest("workflow-1", [trace])

    assert suggestions
    assert suggestions[0].suggestion_type == SuggestionType.DATA_CONTRACT_ISSUE
    assert suggestions[0].related_node_id == "business-case"


def test_optimizer_agent_builds_low_sensitive_package_repair_plan() -> None:
    workflow_package = WorkflowBuilder().generate("我想搭建一个新品上市流程智能体").workflow_package
    trace = TraceRecord(
        run_id="run-1",
        workflow_id=workflow_package.workflow_id,
        workflow_version=workflow_package.version,
        node_id="business-case",
        status=NodeExecutionStatus.FAILED,
        error="missing decision field with token raw-secret-token",
    )
    eval_result = EvalResult(
        eval_id="eval-1",
        workflow_id=workflow_package.workflow_id,
        score=0.2,
        passed=False,
        reason="api_key leaked in model output",
    )

    plan = OptimizerAgent().build_repair_plan(workflow_package, [trace], [eval_result])

    assert plan.workflow_id == workflow_package.workflow_id
    assert plan.workflow_version == workflow_package.version
    assert {operation.target_type for operation in plan.operations} == {"data_contract", "eval_specs"}
    contract_operation = next(operation for operation in plan.operations if operation.target_type == "data_contract")
    assert contract_operation.target_id == "contract-business-case"
    assert contract_operation.proposed_changes["add_regression_eval_for_node"] == "business-case"
    encoded_plan = plan.model_dump_json()
    assert "raw-secret-token" not in encoded_plan
    assert "api_key leaked" not in encoded_plan
    assert "[redacted-sensitive-evidence]" in encoded_plan
