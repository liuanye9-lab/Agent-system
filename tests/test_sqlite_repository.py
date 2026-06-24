from __future__ import annotations

import json
from pathlib import Path

from packages.workflow_core.governance import EvalRunner
from packages.workflow_core.models import (
    AgentBuildMessage,
    AgentBuildSession,
    AgentProductionReadinessReport,
    AgentReadinessDimension,
    AgentRequirementState,
    AgentSkillPackage,
    AgentSystemBlueprint,
    AgentTopologyRecommendation,
    AgentTopologyType,
    AuditEvent,
    WorkflowPackage,
)
from packages.workflow_core.models.enums import RiskLevel
from packages.workflow_core.models.enums import WorkflowRunStatus
from packages.workflow_core.runtime import WorkflowRunner
from packages.workflow_core.storage import SQLiteWorkflowRepository
from packages.workflow_core.storage.sqlite_repository import SQLITE_REPOSITORY_SCHEMA_VERSION


def load_example() -> WorkflowPackage:
    payload = json.loads(Path("examples/new_product_launch.workflow.json").read_text(encoding="utf-8"))
    return WorkflowPackage.model_validate(payload)


def test_sqlite_repository_persists_workflows_runs_traces_and_evals(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.sqlite3'}"
    workflow_package = load_example()
    first_repo = SQLiteWorkflowRepository(database_url)
    first_repo.save_workflow(workflow_package)
    run = WorkflowRunner().run(workflow_package, {"product": "AI workflow platform"})
    first_repo.save_run(run)
    eval_results = EvalRunner().run_all(workflow_package)
    first_repo.save_eval_results(workflow_package.workflow_id, eval_results)

    second_repo = SQLiteWorkflowRepository(database_url)

    assert second_repo.get_workflow(workflow_package.workflow_id) is not None
    persisted_run = second_repo.get_run(run.run_id)
    assert persisted_run is not None
    assert persisted_run.workflow_version == workflow_package.version
    assert persisted_run.traces[-1].workflow_version == workflow_package.version
    assert len(second_repo.list_traces(run_id=run.run_id)) == len(run.traces)
    assert len(second_repo.list_eval_results(workflow_package.workflow_id)) == len(eval_results)


def test_sqlite_repository_records_schema_status(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.sqlite3'}"
    workflow_package = load_example()
    first_repo = SQLiteWorkflowRepository(database_url)
    first_repo.save_workflow(workflow_package)

    second_repo = SQLiteWorkflowRepository(database_url)
    status = second_repo.get_repository_status()

    assert status["backend"] == "sqlite"
    assert status["schema_version"] == SQLITE_REPOSITORY_SCHEMA_VERSION
    assert status["schema_initialized_at"]
    assert status["schema_updated_at"]
    assert status["workflow_count"] == 1
    assert "repository_metadata" in status["tables"]


def test_sqlite_repository_persists_agent_build_sessions(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.sqlite3'}"
    repo = SQLiteWorkflowRepository(database_url)
    blueprint = AgentSystemBlueprint(
        system_id="customer-followup-agent-system",
        name="客户跟进 Agent System",
        description="客户跟进",
        primary_goal="自动整理沟通记录并输出周报",
        expected_outputs=["周报"],
        topology_type=AgentTopologyType.WORKFLOW_AGENT,
        risk_level=RiskLevel.MEDIUM,
    )
    session = AgentBuildSession(
        session_id="asb-test",
        user_request="客户跟进",
        messages=[AgentBuildMessage(role="user", content="客户跟进")],
        assistant_message="已生成客户跟进 Agent 方案",
        requirement_state=AgentRequirementState(summary="客户跟进"),
        topology_recommendation=AgentTopologyRecommendation(
            topology_type=AgentTopologyType.WORKFLOW_AGENT,
            confidence=0.8,
            reason="多步骤客户跟进流程",
        ),
        current_blueprint=blueprint,
        readiness_report=AgentProductionReadinessReport(
            dimensions=[AgentReadinessDimension(name="goal_clarity", score=80)],
            overall_score=80,
            ready_for_candidate=True,
        ),
        skill_packages=[
            AgentSkillPackage(
                skill_id="skill-customer-followup",
                name="客户跟进 Skill",
                agent_id="workflow-agent",
                system_prompt="整理客户跟进记录并输出周报",
            )
        ],
    )

    repo.save_agent_build_session(session)
    reloaded_repo = SQLiteWorkflowRepository(database_url)
    persisted = reloaded_repo.get_agent_build_session("asb-test")

    assert persisted is not None
    assert persisted.session_id == "asb-test"
    assert persisted.skill_packages
    assert persisted.readiness_report.overall_score >= 70
    assert reloaded_repo.get_repository_status()["agent_build_session_count"] == 1


def test_sqlite_repository_finds_run_by_idempotency_key(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.sqlite3'}"
    workflow_package = load_example()
    repo = SQLiteWorkflowRepository(database_url)
    run = WorkflowRunner().run(
        workflow_package,
        {"product": "AI workflow platform"},
        idempotency_key="launch-run-001",
        request_fingerprint="fingerprint-1",
    )

    repo.save_run(run)
    reloaded_repo = SQLiteWorkflowRepository(database_url)
    persisted_run = reloaded_repo.get_run_by_idempotency_key(workflow_package.workflow_id, "launch-run-001")

    assert persisted_run is not None
    assert persisted_run.run_id == run.run_id
    assert persisted_run.request_fingerprint == "fingerprint-1"


def test_sqlite_repository_deletes_runs_and_eval_results(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.sqlite3'}"
    workflow_package = load_example()
    repo = SQLiteWorkflowRepository(database_url)
    run = WorkflowRunner().run(workflow_package, {"product": "AI workflow platform"})
    eval_results = EvalRunner().run_all(workflow_package)
    repo.save_run(run)
    repo.save_eval_results(workflow_package.workflow_id, eval_results)

    assert repo.delete_runs([run.run_id]) == 1
    assert repo.delete_eval_results([result.eval_id for result in eval_results], workflow_id=workflow_package.workflow_id) == len(eval_results)
    reloaded_repo = SQLiteWorkflowRepository(database_url)

    assert reloaded_repo.get_run(run.run_id) is None
    assert reloaded_repo.list_eval_results(workflow_package.workflow_id) == []


def test_sqlite_repository_filters_runs_by_status(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.sqlite3'}"
    workflow_package = load_example()
    repo = SQLiteWorkflowRepository(database_url)
    failed_run = WorkflowRunner().run(workflow_package, {"product": "AI workflow platform"}, max_steps=1)
    paused_run = WorkflowRunner().run(workflow_package, {"product": "AI workflow platform"})

    repo.save_run(failed_run)
    repo.save_run(paused_run)

    failed_runs = repo.list_runs(workflow_id=workflow_package.workflow_id, status=WorkflowRunStatus.FAILED)
    paused_runs = repo.list_runs(workflow_id=workflow_package.workflow_id, status=WorkflowRunStatus.PAUSED)

    assert [run.run_id for run in failed_runs] == [failed_run.run_id]
    assert [run.run_id for run in paused_runs] == [paused_run.run_id]


def test_sqlite_repository_keeps_workflow_version_history(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.sqlite3'}"
    first_version = load_example()
    second_version = first_version.model_copy(
        update={
            "version": "0.2.0",
            "name": "新品上市流程智能体 v2",
            "process_spec": first_version.process_spec.model_copy(update={"version": "0.2.0"}),
        }
    )
    repo = SQLiteWorkflowRepository(database_url)

    repo.save_workflow(first_version)
    repo.save_workflow(second_version)

    assert repo.get_workflow(first_version.workflow_id).version == "0.2.0"
    assert repo.get_workflow_version(first_version.workflow_id, "0.1.0").name == "新品上市流程智能体"
    assert repo.get_workflow_version(first_version.workflow_id, "0.2.0").name == "新品上市流程智能体 v2"
    assert {workflow.version for workflow in repo.list_workflow_versions(first_version.workflow_id)} == {
        "0.1.0",
        "0.2.0",
    }


def test_sqlite_repository_saves_candidate_version_without_changing_current(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.sqlite3'}"
    first_version = load_example()
    candidate_version = first_version.model_copy(
        update={
            "version": "0.2.0",
            "name": "新品上市流程智能体 candidate",
            "process_spec": first_version.process_spec.model_copy(update={"version": "0.2.0"}),
        }
    )
    repo = SQLiteWorkflowRepository(database_url)

    repo.save_workflow(first_version)
    repo.save_workflow_version(candidate_version)

    assert repo.get_workflow(first_version.workflow_id).version == "0.1.0"
    assert repo.get_workflow_version(first_version.workflow_id, "0.2.0").name == "新品上市流程智能体 candidate"
    assert {workflow.version for workflow in repo.list_workflow_versions(first_version.workflow_id)} == {
        "0.1.0",
        "0.2.0",
    }


def test_sqlite_repository_can_promote_historical_workflow_version(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.sqlite3'}"
    first_version = load_example()
    second_version = first_version.model_copy(
        update={
            "version": "0.2.0",
            "name": "新品上市流程智能体 v2",
            "process_spec": first_version.process_spec.model_copy(update={"version": "0.2.0"}),
        }
    )
    repo = SQLiteWorkflowRepository(database_url)
    repo.save_workflow(first_version)
    repo.save_workflow(second_version)

    promoted = repo.promote_workflow_version(first_version.workflow_id, "0.1.0")

    assert promoted is not None
    assert repo.get_workflow(first_version.workflow_id).version == "0.1.0"
    assert repo.get_workflow_version(first_version.workflow_id, "0.2.0") is not None


def test_sqlite_repository_persists_audit_events(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.sqlite3'}"
    repo = SQLiteWorkflowRepository(database_url)
    event = AuditEvent(
        event_id="audit-1",
        event_type="workflow_version_promotion",
        action="promote",
        status="succeeded",
        actor_id="admin-1",
        actor_role="workflow-admin",
        workflow_id="new-product-launch",
        workflow_version="0.1.0",
        resource_type="workflow_package",
        resource_id="new-product-launch@0.1.0",
        reason="rollback",
        details={"promoted_version": "0.1.0"},
    )

    repo.save_audit_event(event)
    reloaded_repo = SQLiteWorkflowRepository(database_url)
    events = reloaded_repo.list_audit_events(workflow_id="new-product-launch")

    assert len(events) == 1
    assert events[0].event_id == "audit-1"
    assert events[0].actor_role == "workflow-admin"
