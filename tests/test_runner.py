from __future__ import annotations

import json
from pathlib import Path

from packages.workflow_core.models import WorkflowPackage
from packages.workflow_core.models.enums import NodeExecutionStatus, WorkflowRunStatus
from packages.workflow_core.builder import WorkflowBuilder
from packages.workflow_core.runtime import ToolExecutionContext, WorkflowRunner


def load_example() -> WorkflowPackage:
    payload = json.loads(Path("examples/new_product_launch.workflow.json").read_text(encoding="utf-8"))
    return WorkflowPackage.model_validate(payload)


def test_workflow_runner_can_run_example_until_approval_pause() -> None:
    workflow_package = load_example()
    run = WorkflowRunner().run(workflow_package, {"product": "AI workflow platform"})

    assert run.status == WorkflowRunStatus.PAUSED
    assert run.current_node_id == "go-no-go-decision"
    assert len(run.traces) == 7


def test_write_node_triggers_approval_required() -> None:
    workflow_package = load_example()
    run = WorkflowRunner().run(workflow_package, {"product": "AI workflow platform"})

    assert run.traces[-1].node_id == "go-no-go-decision"
    assert run.traces[-1].status == NodeExecutionStatus.APPROVAL_REQUIRED
    assert run.output_payload["approval_required"] is True


def test_shadow_run_completes_write_node_as_draft_without_approval() -> None:
    workflow_package = load_example()
    run = WorkflowRunner().run(workflow_package, {"product": "AI workflow platform"}, shadow_mode=True)

    assert run.status == WorkflowRunStatus.COMPLETED
    assert run.shadow_mode is True
    assert run.current_node_id is None
    assert len(run.traces) == 7
    assert run.traces[-1].node_id == "go-no-go-decision"
    assert run.traces[-1].status == NodeExecutionStatus.SUCCESS
    write_output = run.output_payload["go-no-go-decision"]
    assert write_output["shadow_mode"] is True
    assert write_output["decision"] == "shadow_draft_created"
    assert write_output["next_actions"] == ["review_shadow_output"]
    assert write_output["tool_results"] == []


def test_paused_run_can_resume_after_human_approval() -> None:
    workflow_package = load_example()
    runner = WorkflowRunner()
    paused_run = runner.run(workflow_package, {"product": "AI workflow platform"})

    resumed = runner.resume(
        workflow_package,
        paused_run,
        approved=True,
        approval_payload={"approver": "business-owner", "decision": "go"},
    )

    assert resumed.status == WorkflowRunStatus.COMPLETED
    assert resumed.current_node_id is None
    assert resumed.traces[-1].node_id == "go-no-go-decision"
    assert resumed.traces[-1].status == NodeExecutionStatus.SUCCESS
    assert resumed.output_payload["approval_granted"] is True
    write_output = resumed.output_payload["go-no-go-decision"]
    assert write_output["tool_results"][0]["sandbox"]["approval_granted"] is True


def test_resume_can_complete_when_terminal_node_uses_last_step_budget() -> None:
    workflow_package = load_example()
    runner = WorkflowRunner()
    paused_run = runner.run(workflow_package, {"product": "AI workflow platform"})

    resumed = runner.resume(
        workflow_package,
        paused_run,
        approved=True,
        approval_payload={"approver": "business-owner", "decision": "go"},
        max_steps=1,
    )

    assert resumed.status == WorkflowRunStatus.COMPLETED
    assert resumed.current_node_id is None
    assert resumed.traces[-1].node_id == "go-no-go-decision"


def test_paused_run_records_rejection_decision() -> None:
    workflow_package = load_example()
    runner = WorkflowRunner()
    paused_run = runner.run(workflow_package, {"product": "AI workflow platform"})

    rejected = runner.resume(
        workflow_package,
        paused_run,
        approved=False,
        approval_payload={"approver": "business-owner", "reason": "pricing risk"},
    )

    assert rejected.status == WorkflowRunStatus.REJECTED
    assert rejected.traces[-1].status == NodeExecutionStatus.SKIPPED
    assert rejected.output_payload["approval_decision"] == "rejected"


def test_runner_fails_when_node_output_violates_contract() -> None:
    workflow_package = load_example()
    contract = next(contract for contract in workflow_package.data_contracts if contract.contract_id == "contract-define-launch-goal")
    strict_contract = contract.model_copy(
        update={
            "output_schema": {
                "type": "object",
                "properties": {"must_exist": {"type": "string"}},
                "required": ["must_exist"],
            }
        }
    )
    workflow_package = workflow_package.model_copy(
        update={
            "data_contracts": [
                strict_contract if item.contract_id == strict_contract.contract_id else item
                for item in workflow_package.data_contracts
            ]
        }
    )

    run = WorkflowRunner().run(workflow_package, {"product": "AI workflow platform"})

    assert run.status == WorkflowRunStatus.FAILED
    assert run.traces[0].status == NodeExecutionStatus.FAILED
    assert "output_contract_validation_failed" in (run.traces[0].error or "")


def test_builder_generated_package_is_contract_valid_and_runnable() -> None:
    build_result = WorkflowBuilder().generate("我想搭建一个新品上市流程智能体")

    run = WorkflowRunner().run(build_result.workflow_package, {"product": "AI workflow platform"})

    assert run.status == WorkflowRunStatus.PAUSED
    assert run.workflow_version == build_result.workflow_package.version
    assert run.current_node_id == "go-no-go-decision"
    assert len(run.traces) == 7
    assert run.traces[-1].workflow_version == build_result.workflow_package.version
    assert run.traces[0].input_snapshot["context"]["product"] == "AI workflow platform"


def test_runner_retries_retryable_node_failure_and_continues() -> None:
    workflow_package = load_example()
    run = WorkflowRunner().run(
        workflow_package,
        {
            "product": "AI workflow platform",
            "transient_failures": {"define-launch-goal": 1},
        },
        max_retries=1,
    )

    assert run.status == WorkflowRunStatus.PAUSED
    assert run.traces[0].node_id == "define-launch-goal"
    assert run.traces[0].status == NodeExecutionStatus.FAILED
    assert run.traces[0].attempt == 1
    assert run.traces[0].max_attempts == 2
    assert run.traces[0].retryable is True
    assert run.traces[1].node_id == "define-launch-goal"
    assert run.traces[1].status == NodeExecutionStatus.SUCCESS
    assert run.traces[1].attempt == 2
    assert len(run.traces) == 8


def test_runner_fails_after_retry_budget_exhausted() -> None:
    workflow_package = load_example()
    run = WorkflowRunner().run(
        workflow_package,
        {
            "product": "AI workflow platform",
            "transient_failures": {"define-launch-goal": 1},
        },
        max_retries=0,
    )

    assert run.status == WorkflowRunStatus.FAILED
    assert run.output_payload["failed_node_id"] == "define-launch-goal"
    assert run.output_payload["attempt"] == 1
    assert run.output_payload["max_attempts"] == 1
    assert run.output_payload["retryable"] is True
    assert len(run.traces) == 1


def test_runner_does_not_retry_non_retryable_failure() -> None:
    workflow_package = load_example()
    run = WorkflowRunner().run(
        workflow_package,
        {
            "product": "AI workflow platform",
            "force_fail_node": "define-launch-goal",
        },
        max_retries=3,
    )

    assert run.status == WorkflowRunStatus.FAILED
    assert run.output_payload["retryable"] is False
    assert run.traces[0].max_attempts == 4
    assert len(run.traces) == 1


def test_runner_stops_when_max_steps_budget_is_exceeded() -> None:
    workflow_package = load_example()
    run = WorkflowRunner().run(workflow_package, {"product": "AI workflow platform"}, max_steps=1)

    assert run.status == WorkflowRunStatus.FAILED
    assert run.output_payload["error"] == "max_steps_exceeded"
    assert run.output_payload["pending_node_id"] == "collect-market-insights"
    assert run.output_payload["executed_steps"] == 1
    assert run.output_payload["max_steps"] == 1
    assert run.current_node_id == "collect-market-insights"
    assert len(run.traces) == 1


def test_runner_checkpoints_initial_node_and_terminal_states() -> None:
    workflow_package = load_example()
    checkpoints = []

    run = WorkflowRunner().run(
        workflow_package,
        {"product": "AI workflow platform"},
        max_steps=1,
        checkpoint=checkpoints.append,
    )

    assert run.status == WorkflowRunStatus.FAILED
    assert [checkpoint.status for checkpoint in checkpoints] == [
        WorkflowRunStatus.RUNNING,
        WorkflowRunStatus.RUNNING,
        WorkflowRunStatus.FAILED,
    ]
    assert [len(checkpoint.traces) for checkpoint in checkpoints] == [0, 1, 1]
    assert checkpoints[0].current_node_id == "define-launch-goal"
    assert checkpoints[1].current_node_id == "collect-market-insights"
    assert checkpoints[2].output_payload["error"] == "max_steps_exceeded"


def test_resume_checkpoints_rejection_and_approved_resume_states() -> None:
    workflow_package = load_example()
    runner = WorkflowRunner()
    paused_run = runner.run(workflow_package, {"product": "AI workflow platform"})
    rejection_checkpoints = []
    approved_checkpoints = []

    rejected = runner.resume(
        workflow_package,
        paused_run.model_copy(deep=True),
        approved=False,
        approval_payload={"reason": "risk"},
        checkpoint=rejection_checkpoints.append,
    )
    approved = runner.resume(
        workflow_package,
        paused_run.model_copy(deep=True),
        approved=True,
        approval_payload={"decision": "go"},
        checkpoint=approved_checkpoints.append,
    )

    assert rejected.status == WorkflowRunStatus.REJECTED
    assert [checkpoint.status for checkpoint in rejection_checkpoints] == [WorkflowRunStatus.REJECTED]
    assert approved.status == WorkflowRunStatus.COMPLETED
    assert [checkpoint.status for checkpoint in approved_checkpoints] == [
        WorkflowRunStatus.RUNNING,
        WorkflowRunStatus.RUNNING,
        WorkflowRunStatus.COMPLETED,
    ]
    assert approved_checkpoints[-1].current_node_id is None


def test_runner_passes_actor_context_to_tool_scope_checks() -> None:
    workflow_package = load_example()
    scoped_tool = workflow_package.tool_policies[0].model_copy(
        update={"allowed_roles": ["operator"], "required_scopes": ["market:read"]}
    )
    workflow_package = workflow_package.model_copy(
        update={
            "tool_policies": [
                scoped_tool
                if tool.tool_id == scoped_tool.tool_id
                else tool.model_copy(update={"allowed_roles": ["operator"]})
                for tool in workflow_package.tool_policies
            ]
        }
    )

    missing_scope_run = WorkflowRunner().run(
        workflow_package,
        {"product": "AI workflow platform"},
        actor_context=ToolExecutionContext(actor_role="operator"),
    )
    permitted_run = WorkflowRunner().run(
        workflow_package,
        {"product": "AI workflow platform"},
        actor_context=ToolExecutionContext(
            actor_id="operator-1",
            actor_role="operator",
            actor_scopes=("market:read",),
        ),
    )

    assert missing_scope_run.status == WorkflowRunStatus.FAILED
    assert "actor scopes missing" in (missing_scope_run.traces[0].error or "")
    assert permitted_run.status == WorkflowRunStatus.PAUSED


def test_resume_passes_approval_actor_context_to_write_tool_scope_checks() -> None:
    workflow_package = load_example()
    write_tool = next(tool for tool in workflow_package.tool_policies if tool.tool_id == "mock-go-no-go-decision-tool")
    scoped_write_tool = write_tool.model_copy(update={"required_scopes": ["launch:write"]})
    workflow_package = workflow_package.model_copy(
        update={
            "tool_policies": [
                scoped_write_tool if tool.tool_id == scoped_write_tool.tool_id else tool
                for tool in workflow_package.tool_policies
            ]
        }
    )
    runner = WorkflowRunner()
    paused_run = runner.run(workflow_package, {"product": "AI workflow platform"})

    failed_resume = runner.resume(
        workflow_package,
        paused_run.model_copy(deep=True),
        approved=True,
        approval_payload={"approver": "business-owner", "decision": "go"},
        actor_context=ToolExecutionContext(actor_role="business-approver"),
    )
    permitted_resume = runner.resume(
        workflow_package,
        paused_run.model_copy(deep=True),
        approved=True,
        approval_payload={"approver": "business-owner", "decision": "go"},
        actor_context=ToolExecutionContext(
            actor_id="approver-1",
            actor_role="business-approver",
            actor_scopes=("launch:write",),
        ),
    )

    assert failed_resume.status == WorkflowRunStatus.FAILED
    assert "actor scopes missing" in (failed_resume.traces[-1].error or "")
    assert permitted_resume.status == WorkflowRunStatus.COMPLETED
