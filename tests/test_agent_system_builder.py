from __future__ import annotations

import json

from fastapi.testclient import TestClient

from apps.api import dependencies
from apps.api.main import app
from packages.workflow_core.agent_system import AgentSystemBlueprintMapper, AgentTopologyClassifier, SubAgentPlanner, SubAgentValidator
from packages.workflow_core.models import (
    AgentProductionReadinessReport,
    AgentReadinessDimension,
    AgentSystemBlueprint,
    AgentTopologyClassifierInput,
    AgentTopologyType,
    MotherAgentDefinition,
)
from packages.workflow_core.models.enums import NodeExecutionStatus, NodeType, RiskLevel, WorkflowRunStatus
from packages.workflow_core.runtime import WorkflowRunner
from packages.workflow_core.adapters.mock_llm import MockLLMClient
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


def test_readiness_report_does_not_allow_low_score_candidate_gate() -> None:
    report = AgentProductionReadinessReport(
        dimensions=[AgentReadinessDimension(name="release_readiness", score=8)],
        overall_score=8,
        ready_for_candidate=True,
        blocking_gaps=[],
    )

    assert report.ready_for_candidate is False


def test_blueprint_mapper_includes_mother_agent_allowed_tools() -> None:
    blueprint = AgentSystemBlueprint(
        system_id="customer-followup-agent-system",
        name="客户跟进 Agent System",
        description="客户跟进",
        primary_goal="整理客户沟通记录并输出周报",
        expected_outputs=["周报"],
        topology_type=AgentTopologyType.WORKFLOW_AGENT,
        mother_agent=MotherAgentDefinition(
            agent_id="mother-agent",
            name="客户跟进调度 Agent",
            role="manager",
            responsibility="调度客户跟进流程",
            system_prompt="整理客户跟进需求并输出结构化草案。",
            planning_policy="plan_then_execute",
            routing_policy="single_agent",
            allowed_tools=["text_parser"],
        ),
    )

    workflow_package = AgentSystemBlueprintMapper().to_workflow_package(blueprint)

    assert "tool-text-parser" in {tool.tool_id for tool in workflow_package.tool_policies}
    mother_agent = next(agent for agent in workflow_package.agent_specs if agent.agent_id == "mother-agent")
    assert mother_agent.tools == ["tool-text-parser"]


def test_blueprint_mapper_normalizes_llm_schema_shorthand() -> None:
    blueprint = AgentSystemBlueprint(
        system_id="customer-followup-agent-system",
        name="客户跟进 Agent System",
        description="客户跟进",
        primary_goal="整理客户沟通记录并输出周报",
        expected_outputs=["周报"],
        topology_type=AgentTopologyType.WORKFLOW_AGENT,
        workflow_nodes=[
            {
                "node_id": "report-generation",
                "name": "生成周报",
                "node_type": "llm_reasoning",
                "input_schema": {
                    "type": "object",
                    "properties": {"conversation": "string"},
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"summary": "string", "next_actions": {"type": "list", "items": "string"}},
                },
            }
        ],
    )

    workflow_package = AgentSystemBlueprintMapper().to_workflow_package(blueprint)
    contract = workflow_package.data_contracts[0]

    assert contract.input_schema["properties"]["conversation"] == {"type": "string"}
    assert contract.output_schema["properties"]["summary"] == {"type": "string"}
    assert contract.output_schema["properties"]["next_actions"]["type"] == "array"


def test_blueprint_mapper_deduplicates_tools_by_normalized_id() -> None:
    blueprint = AgentSystemBlueprint(
        system_id="tool-dedupe-agent-system",
        name="工具去重 Agent System",
        description="工具去重",
        primary_goal="避免重复工具 id",
        expected_outputs=["结果"],
        topology_type=AgentTopologyType.SINGLE_AGENT,
        tool_requirements=["agent system", "agent-system", "agent_system"],
    )

    workflow_package = AgentSystemBlueprintMapper().to_workflow_package(blueprint)

    assert [tool.tool_id for tool in workflow_package.tool_policies] == ["tool-agent-system"]


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
    app.dependency_overrides[dependencies.get_llm_client] = lambda: MockLLMClient()
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
        assert payload["skill_packages"]
        assert payload["readiness_report"]["ready_for_candidate"] is True
        assert repository.get_workflow(workflow_id) is None
        assert repository.get_workflow_version(workflow_id, "0.2.0-candidate") is not None
        saved_session = repository.get_agent_build_session(session_id)
        assert saved_session is not None
        assert saved_session.candidate_workflow_id == workflow_id
        audit_events = repository.list_audit_events(event_type="agent_system_candidate")
        assert audit_events
        assert audit_events[0].details["skill_package_count"] >= 1
    finally:
        app.dependency_overrides.clear()


def test_agent_system_message_updates_persisted_session_and_skill_drafts() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    app.dependency_overrides[dependencies.get_llm_client] = lambda: MockLLMClient()
    try:
        client = TestClient(app)
        headers = admin_headers(client)
        created = client.post(
            "/api/agent-systems/sessions",
            headers=headers,
            json={"user_request": "帮我做一个客户跟进 Agent，自动整理沟通记录并输出周报"},
        )
        assert created.status_code == 200
        session_id = created.json()["session_id"]

        updated = client.post(
            f"/api/agent-systems/sessions/{session_id}/messages",
            headers=headers,
            json={"message": "需要记录客户阶段，提醒下次跟进，并且周报发布前要人工确认"},
        )

        assert updated.status_code == 200
        payload = updated.json()
        assert len(payload["messages"]) >= 3
        assert payload["readiness_report"]["overall_score"] >= 70
        assert payload["skill_packages"]
        persisted = repository.get_agent_build_session(session_id)
        assert persisted is not None
        assert persisted.messages[-1].role == "assistant"
        assert persisted.skill_packages[0].skill_id.startswith("skill-")
    finally:
        app.dependency_overrides.clear()


def test_agent_system_invalid_real_llm_output_returns_low_sensitive_error() -> None:
    class BadLLM:
        provider = "agnes"
        model = "bad-json-model"

        def complete(self, _prompt: str) -> str:
            return "not json"

    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    app.dependency_overrides[dependencies.get_llm_client] = lambda: BadLLM()
    try:
        client = TestClient(app)
        headers = admin_headers(client)

        response = client.post(
            "/api/agent-systems/sessions",
            headers=headers,
            json={"user_request": "帮我做一个客户跟进 Agent"},
        )

        assert response.status_code == 502
        detail = response.json()["detail"]
        assert detail["message"] == "LLM agent system generation failed"
        assert detail["provider"] == "agnes"
        assert "帮我做一个客户跟进 Agent" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_agent_system_real_llm_can_keep_session_below_candidate_gate() -> None:
    class ClarifyingLLM:
        provider = "agnes"
        model = "Agnes-2.0-Flash"

        def complete(self, _prompt: str) -> str:
            return json.dumps(
                {
                    "assistant_message": "我需要先确认客户来源、可用工具和周报发送审批边界，再保存候选版本。",
                    "clarifying_questions": ["客户来源在哪里？", "是否允许写入提醒？", "周报是否需要人工审批？"],
                    "requirement_state": {
                        "summary": "客户跟进 Agent",
                        "confirmed_facts": ["目标是整理客户沟通并输出周报"],
                        "missing_information": ["tool_permissions", "approval_boundaries"],
                        "assumptions": ["第一版面向个人和小团队"],
                        "constraints": ["不自动安装 Skill"],
                    },
                    "topology_recommendation": {
                        "topology_type": "workflow_agent",
                        "confidence": 0.82,
                        "reason": "客户跟进包含整理、提醒、周报三个连续步骤",
                        "suggested_agents": ["customer-followup-agent"],
                        "suggested_questions": ["客户来源在哪里？"],
                    },
                    "current_blueprint": {
                        "system_id": "customer-followup-agent-system",
                        "name": "客户跟进 Agent System",
                        "description": "整理客户沟通并输出周报",
                        "target_user_groups": ["个人用户", "小团队"],
                        "user_skill_level": "beginner",
                        "primary_goal": "整理客户沟通记录，提醒下一步，并输出周报",
                        "expected_outputs": ["客户阶段摘要", "下一步提醒", "周报草稿"],
                        "interaction_mode": "chat",
                        "topology_type": "workflow_agent",
                        "mother_agent": None,
                        "subagents": [],
                        "workflow_nodes": [],
                        "tool_requirements": ["document_writer", "reminder_writer"],
                        "memory_requirements": ["conversation requirement state"],
                        "evaluation_requirements": ["schema validation"],
                        "approval_requirements": ["weekly report publish review"],
                        "observability_requirements": ["trace"],
                        "risk_level": "medium",
                        "release_policy": "save_candidate_then_promote_after_review",
                    },
                    "readiness_report": {
                        "dimensions": [
                            {"name": "goal_clarity", "score": 82, "blocker": False, "notes": "目标已明确"},
                            {"name": "io_contract", "score": 76, "blocker": False, "notes": "输出已初步定义"},
                            {"name": "tool_permissions", "score": 72, "blocker": False, "notes": "仍需确认写入范围"},
                            {"name": "memory_strategy", "score": 74, "blocker": False, "notes": "会话记忆已定义"},
                            {"name": "failure_handling", "score": 70, "blocker": False, "notes": "需要结构化失败策略"},
                            {"name": "evaluation_cases", "score": 70, "blocker": False, "notes": "需要补测试样例"},
                            {"name": "approval_boundaries", "score": 72, "blocker": False, "notes": "需要确认周报审批"},
                            {"name": "release_readiness", "score": 70, "blocker": False, "notes": "等待确认后保存"},
                        ],
                        "overall_score": 73,
                        "ready_for_candidate": False,
                        "blocking_gaps": [],
                        "next_questions": ["是否允许写入提醒？"],
                    },
                    "skill_packages": [
                        {
                            "skill_id": "skill-customer-followup",
                            "name": "客户跟进 Skill",
                            "agent_id": "customer-followup-agent",
                            "trigger_scenarios": ["用户需要整理客户沟通记录并生成周报"],
                            "system_prompt": "整理客户跟进状态，提出下一步行动，并生成周报草稿。",
                            "input_schema": {"type": "object"},
                            "output_schema": {"type": "object"},
                            "tool_permissions": ["draft_only"],
                            "memory_scope": "session",
                            "failure_policy": "return_structured_error",
                            "evaluation_cases": [{"name": "weekly_report", "input": {}, "expected": {}}],
                            "usage_notes": "候选 Skill 草案，不自动安装。",
                        }
                    ],
                    "change_summary": "created clarifying customer follow-up draft",
                }
            )

    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    app.dependency_overrides[dependencies.get_llm_client] = lambda: ClarifyingLLM()
    try:
        client = TestClient(app)
        headers = admin_headers(client)
        created = client.post(
            "/api/agent-systems/sessions",
            headers=headers,
            json={"user_request": "帮我做一个客户跟进 Agent"},
        )
        assert created.status_code == 200
        session_payload = created.json()
        assert session_payload["generation_mode"] == "llm"
        assert session_payload["readiness_report"]["overall_score"] == 73
        assert session_payload["readiness_report"]["ready_for_candidate"] is False

        candidate = client.post(
            f"/api/agent-systems/sessions/{session_payload['session_id']}/candidate",
            headers=headers,
            json={"version": "0.1.0-clarifying"},
        )

        assert candidate.status_code == 409
        assert repository.list_audit_events(event_type="agent_system_candidate") == []
    finally:
        app.dependency_overrides.clear()


def test_agent_system_real_llm_retries_invalid_structured_output_once() -> None:
    class FlakyLLM:
        provider = "agnes"
        model = "Agnes-2.0-Flash"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, _prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                return "not json"
            return json.dumps(_valid_customer_followup_llm_payload(ready_for_candidate=True))

    llm = FlakyLLM()
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    app.dependency_overrides[dependencies.get_llm_client] = lambda: llm
    try:
        client = TestClient(app)
        headers = admin_headers(client)
        response = client.post(
            "/api/agent-systems/sessions",
            headers=headers,
            json={"user_request": "帮我做一个客户跟进 Agent"},
        )

        assert response.status_code == 200
        assert llm.calls == 2
        assert response.json()["generation_mode"] == "llm"
        assert response.json()["readiness_report"]["ready_for_candidate"] is True
    finally:
        app.dependency_overrides.clear()


def _valid_customer_followup_llm_payload(ready_for_candidate: bool) -> dict:
    return {
        "assistant_message": "我已生成客户跟进 Agent 的结构化方案。",
        "clarifying_questions": [] if ready_for_candidate else ["是否允许写入提醒？"],
        "requirement_state": {
            "summary": "客户跟进 Agent",
            "confirmed_facts": ["目标是整理客户沟通并输出周报"],
            "missing_information": [] if ready_for_candidate else ["tool_permissions"],
            "assumptions": ["第一版面向个人和小团队"],
            "constraints": ["不自动安装 Skill"],
        },
        "topology_recommendation": {
            "topology_type": "workflow_agent",
            "confidence": 0.82,
            "reason": "客户跟进包含整理、提醒、周报三个连续步骤",
            "suggested_agents": ["customer-followup-agent"],
            "suggested_questions": [],
        },
        "current_blueprint": {
            "system_id": "customer-followup-agent-system",
            "name": "客户跟进 Agent System",
            "description": "整理客户沟通并输出周报",
            "target_user_groups": ["个人用户", "小团队"],
            "user_skill_level": "beginner",
            "primary_goal": "整理客户沟通记录，提醒下一步，并输出周报",
            "expected_outputs": ["客户阶段摘要", "下一步提醒", "周报草稿"],
            "interaction_mode": "chat",
            "topology_type": "workflow_agent",
            "mother_agent": None,
            "subagents": [],
            "workflow_nodes": [],
            "tool_requirements": ["document_writer"],
            "memory_requirements": ["conversation requirement state"],
            "evaluation_requirements": ["schema validation"],
            "approval_requirements": ["weekly report publish review"],
            "observability_requirements": ["trace"],
            "risk_level": "medium",
            "release_policy": "save_candidate_then_promote_after_review",
        },
        "readiness_report": {
            "dimensions": [
                {"name": "goal_clarity", "score": 82, "blocker": False, "notes": "目标已明确"},
                {"name": "io_contract", "score": 76, "blocker": False, "notes": "输出已初步定义"},
                {"name": "tool_permissions", "score": 72, "blocker": False, "notes": "工具为草稿权限"},
                {"name": "memory_strategy", "score": 74, "blocker": False, "notes": "会话记忆已定义"},
                {"name": "failure_handling", "score": 70, "blocker": False, "notes": "结构化失败策略"},
                {"name": "evaluation_cases", "score": 70, "blocker": False, "notes": "基础用例"},
                {"name": "approval_boundaries", "score": 72, "blocker": False, "notes": "发布前审批"},
                {"name": "release_readiness", "score": 70, "blocker": False, "notes": "候选保存"},
            ],
            "overall_score": 73,
            "ready_for_candidate": ready_for_candidate,
            "blocking_gaps": [],
            "next_questions": [] if ready_for_candidate else ["是否允许写入提醒？"],
        },
        "skill_packages": [
            {
                "skill_id": "skill-customer-followup",
                "name": "客户跟进 Skill",
                "agent_id": "customer-followup-agent",
                "trigger_scenarios": ["用户需要整理客户沟通记录并生成周报"],
                "system_prompt": "整理客户跟进状态，提出下一步行动，并生成周报草稿。",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "tool_permissions": ["draft_only"],
                "memory_scope": "session",
                "failure_policy": "return_structured_error",
                "evaluation_cases": [{"name": "weekly_report", "input": {}, "expected": {}}],
                "usage_notes": "候选 Skill 草案，不自动安装。",
            }
        ],
        "change_summary": "created customer follow-up draft",
    }


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
