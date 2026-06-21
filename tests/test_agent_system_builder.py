from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api import dependencies
from apps.api.main import app
from packages.workflow_core.agent_system import AgentSystemBlueprintMapper, AgentTopologyClassifier, SubAgentPlanner, SubAgentValidator
from packages.workflow_core.models import AgentSystemBlueprint, AgentTopologyClassifierInput, AgentTopologyType
from packages.workflow_core.models.enums import NodeExecutionStatus, NodeType, RiskLevel, WorkflowRunStatus
from packages.workflow_core.runtime import WorkflowRunner
from packages.workflow_core.storage import MemoryWorkflowRepository


def admin_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/token", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_agent_topology_classifier_covers_expected_topologies() -> None:
    classifier = AgentTopologyClassifier()

    simple = classifier.classify(AgentTopologyClassifierInput(user_request="帮我总结一段文字"))
    workflow = classifier.classify(
        AgentTopologyClassifierInput(
            user_request="先读取资料，然后分析，最后输出报告",
            task_complexity="medium",
        )
    )
    manager = classifier.classify(
        AgentTopologyClassifierInput(
            user_request="我想做投资研究 Agent，需要新闻研究、财报分析、风险审查和报告生成",
            number_of_distinct_capabilities=3,
        )
    )
    multi = classifier.classify(
        AgentTopologyClassifierInput(
            user_request="长期协作、多分支、多审批的团队 Agent 系统",
            risk_level=RiskLevel.HIGH,
            task_complexity="high",
            number_of_distinct_capabilities=5,
            need_for_context_isolation=True,
            need_for_permission_isolation=True,
        )
    )

    assert simple.topology_type == AgentTopologyType.SINGLE_AGENT
    assert workflow.topology_type == AgentTopologyType.WORKFLOW_AGENT
    assert manager.topology_type == AgentTopologyType.MANAGER_SUBAGENTS
    assert multi.topology_type == AgentTopologyType.MULTI_AGENT_WORKFLOW


def test_subagent_planner_generates_boundaries_and_validator_accepts_them() -> None:
    blueprint = _blueprint()

    plan = SubAgentPlanner().plan(
        blueprint,
        "投资研究需要新闻研究、财报分析、风险审查和报告生成",
    )
    completed = blueprint.model_copy(
        update={
            "mother_agent": plan.mother_agent,
            "subagents": plan.subagents,
            "workflow_nodes": plan.workflow_nodes,
        }
    )
    report = SubAgentValidator().validate(completed)

    assert plan.mother_agent is not None
    assert len(plan.subagents) >= 3
    assert all(subagent.when_to_use for subagent in plan.subagents)
    assert all(subagent.when_not_to_use for subagent in plan.subagents)
    assert all(subagent.input_schema and subagent.output_schema for subagent in plan.subagents)
    assert len({subagent.specialty for subagent in plan.subagents}) == len(plan.subagents)
    assert report.valid


def test_blueprint_maps_to_workflow_package_with_subagent_call_nodes() -> None:
    workflow_package = _mapped_workflow()

    assert any(node.node_type == NodeType.SUBAGENT_CALL for node in workflow_package.process_spec.nodes)
    assert workflow_package.eval_specs
    assert workflow_package.workflow_id
    assert workflow_package.version == "0.2.0"


def test_subagent_call_node_executes_and_records_trace() -> None:
    workflow_package = _mapped_workflow()

    run = WorkflowRunner().run(workflow_package, input_payload={"context": {"ticker": "MOCK"}}, shadow_mode=True)

    assert run.status == WorkflowRunStatus.COMPLETED
    subagent_traces = [trace for trace in run.traces if trace.node_id.startswith("call-")]
    assert subagent_traces
    assert all(trace.status == NodeExecutionStatus.SUCCESS for trace in subagent_traces)
    assert "subagent_result" in subagent_traces[0].output_snapshot


def test_agent_system_candidate_api_saves_version_without_current_workflow() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        headers = admin_headers(client)
        session_response = client.post(
            "/api/agent-systems/sessions",
            headers=headers,
            json={"user_request": "我想做投资研究 Agent，需要新闻研究、财报分析、风险审查和报告生成"},
        )
        assert session_response.status_code == 200
        session_id = session_response.json()["session_id"]

        candidate_response = client.post(
            f"/api/agent-systems/sessions/{session_id}/candidate",
            headers=headers,
            json={"version": "0.2.0-candidate"},
        )

        assert candidate_response.status_code == 200
        payload = candidate_response.json()
        workflow_id = payload["workflow_package"]["workflow_id"]
        assert payload["saved_as_current"] is False
        assert repository.get_workflow(workflow_id) is None
        assert repository.get_workflow_version(workflow_id, "0.2.0-candidate") is not None
    finally:
        app.dependency_overrides.clear()


def _mapped_workflow():
    blueprint = _blueprint()
    plan = SubAgentPlanner().plan(
        blueprint,
        "投资研究需要新闻研究、财报分析、风险审查和报告生成",
    )
    completed = blueprint.model_copy(
        update={
            "mother_agent": plan.mother_agent,
            "subagents": plan.subagents,
            "workflow_nodes": plan.workflow_nodes,
        }
    )
    return AgentSystemBlueprintMapper().to_workflow_package(completed, version="0.2.0")


def _blueprint() -> AgentSystemBlueprint:
    return AgentSystemBlueprint(
        system_id="investment-research-agent-system",
        name="投资研究 Agent System",
        description="低门槛投资研究 Agent",
        target_user_groups=["个人用户"],
        primary_goal="帮助用户研究新闻、财报和风险，输出投资观察报告",
        expected_outputs=["风险提示", "观察报告"],
        topology_type=AgentTopologyType.MANAGER_SUBAGENTS,
        tool_requirements=["web_search", "file_reader", "document_writer"],
        evaluation_requirements=["schema validation"],
        approval_requirements=["high risk output review"],
        risk_level=RiskLevel.HIGH,
    )
