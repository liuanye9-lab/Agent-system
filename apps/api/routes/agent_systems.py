from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apps.api.audit import build_audit_event
from apps.api.dependencies import get_llm_client, get_repository
from apps.api.routes.workflows import dump_model
from apps.api.security import require_scope
from packages.workflow_core.adapters import LLMClient
from packages.workflow_core.agent_system import (
    AgentSystemBlueprintMapper,
    AgentTopologyClassifier,
    ClarificationEngine,
    SubAgentPlanner,
    SubAgentValidator,
)
from packages.workflow_core.builder.llm_json import extract_json_object, is_mock_llm
from packages.workflow_core.models import (
    ActorContext,
    AgentSystemBlueprint,
    AgentTopologyClassifierInput,
    AgentTopologyRecommendation,
    AgentTopologyType,
)
from packages.workflow_core.models.enums import RiskLevel
from packages.workflow_core.storage import WorkflowRepository
from packages.workflow_core.validation import WorkflowPackageLinter

router = APIRouter(prefix="/api/agent-systems", tags=["agent-systems"])

classifier = AgentTopologyClassifier()
clarification_engine = ClarificationEngine()
planner = SubAgentPlanner()
validator = SubAgentValidator()
mapper = AgentSystemBlueprintMapper()
workflow_linter = WorkflowPackageLinter()
_sessions: dict[str, dict[str, Any]] = {}


class CreateAgentSystemSessionRequest(BaseModel):
    user_request: str = Field(min_length=1, max_length=5000)


class AgentSystemMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=5000)


class GenerateBlueprintRequest(BaseModel):
    version: str = Field(default="0.1.0", min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


class CandidateRequest(BaseModel):
    version: str = Field(default="0.1.0", min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


class PlanSubagentsRequest(BaseModel):
    blueprint: AgentSystemBlueprint
    user_request: str = Field(min_length=1, max_length=5000)
    clarified_answers: dict[str, Any] = Field(default_factory=dict)


class ValidateSubagentsRequest(BaseModel):
    blueprint: AgentSystemBlueprint


@router.post("/sessions")
def create_session(
    request: CreateAgentSystemSessionRequest,
    _actor: ActorContext = Depends(require_scope("workflow:write")),
    llm: LLMClient = Depends(get_llm_client),
) -> dict[str, Any]:
    session_id = f"asb-{uuid4().hex[:12]}"
    state = _build_session_state(session_id=session_id, user_request=request.user_request, llm=llm)
    _sessions[session_id] = state
    return _dump_session(state)


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
) -> dict[str, Any]:
    return _dump_session(_require_session(session_id))


@router.post("/sessions/{session_id}/messages")
def append_message(
    session_id: str,
    request: AgentSystemMessageRequest,
    _actor: ActorContext = Depends(require_scope("workflow:write")),
    llm: LLMClient = Depends(get_llm_client),
) -> dict[str, Any]:
    state = _require_session(session_id)
    state["messages"].append({"role": "user", "content": request.message})
    combined_request = f"{state['user_request']}\n{request.message}"
    state.update(_build_session_state(session_id=session_id, user_request=combined_request, llm=llm))
    state["messages"].append({"role": "assistant", "content": state["assistant_message"]})
    _sessions[session_id] = state
    return _dump_session(state)


@router.post("/sessions/{session_id}/blueprint")
def generate_blueprint(
    session_id: str,
    _request: GenerateBlueprintRequest,
    _actor: ActorContext = Depends(require_scope("workflow:write")),
) -> dict[str, Any]:
    state = _require_session(session_id)
    state["current_blueprint"] = _blueprint_for_state(state)
    _sessions[session_id] = state
    return {"current_blueprint": dump_model(state["current_blueprint"])}


@router.post("/sessions/{session_id}/candidate")
def save_candidate(
    session_id: str,
    request: CandidateRequest,
    actor: ActorContext = Depends(require_scope("workflow:write")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    state = _require_session(session_id)
    blueprint = state.get("current_blueprint") or _blueprint_for_state(state)
    workflow_package = mapper.to_workflow_package(blueprint, version=request.version)
    validation_report = workflow_linter.lint(workflow_package)
    if not validation_report.valid:
        repository.save_audit_event(
            build_audit_event(
                event_type="agent_system_candidate",
                action="save_candidate",
                status="failed",
                actor=actor,
                workflow_id=workflow_package.workflow_id,
                workflow_version=workflow_package.version,
                resource_type="workflow_package",
                resource_id=f"{workflow_package.workflow_id}@{workflow_package.version}",
                details={"validation_report": dump_model(validation_report)},
            )
        )
        raise HTTPException(status_code=422, detail=dump_model(validation_report))
    repository.save_workflow_version(workflow_package)
    repository.save_audit_event(
        build_audit_event(
            event_type="agent_system_candidate",
            action="save_candidate",
            status="succeeded",
            actor=actor,
            workflow_id=workflow_package.workflow_id,
            workflow_version=workflow_package.version,
            resource_type="workflow_package",
            resource_id=f"{workflow_package.workflow_id}@{workflow_package.version}",
            details={
                "topology_type": blueprint.topology_type,
                "subagent_count": len(blueprint.subagents),
                "workflow_node_count": len(blueprint.workflow_nodes),
                "saved_as_current": False,
            },
        )
    )
    state["current_blueprint"] = blueprint
    state["candidate_workflow_id"] = workflow_package.workflow_id
    state["candidate_version"] = workflow_package.version
    _sessions[session_id] = state
    return {
        "workflow_package": dump_model(workflow_package),
        "validation_report": dump_model(validation_report),
        "saved_as_current": False,
    }


@router.post("/{system_id}/subagents/plan")
def plan_subagents(
    system_id: str,
    request: PlanSubagentsRequest,
    _actor: ActorContext = Depends(require_scope("workflow:write")),
) -> dict[str, Any]:
    blueprint = request.blueprint.model_copy(update={"system_id": system_id})
    plan = planner.plan(blueprint, request.user_request, request.clarified_answers)
    return {"plan": dump_model(plan)}


@router.post("/{system_id}/subagents/validate")
def validate_subagents(
    system_id: str,
    request: ValidateSubagentsRequest,
    _actor: ActorContext = Depends(require_scope("workflow:write")),
) -> dict[str, Any]:
    blueprint = request.blueprint.model_copy(update={"system_id": system_id})
    return {"validation_report": dump_model(validator.validate(blueprint))}


def _require_session(session_id: str) -> dict[str, Any]:
    state = _sessions.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="agent system session not found")
    return state


def _dump_session(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": state["session_id"],
        "assistant_message": state["assistant_message"],
        "clarifying_questions": state["clarifying_questions"],
        "extracted_brief": dump_model(state["extracted_brief"]),
        "topology_recommendation": dump_model(state["topology_recommendation"]),
        "current_blueprint": dump_model(state["current_blueprint"]) if state.get("current_blueprint") else None,
        "candidate_workflow_id": state.get("candidate_workflow_id"),
        "candidate_version": state.get("candidate_version"),
    }


def _build_session_state(session_id: str, user_request: str, llm: LLMClient | None = None) -> dict[str, Any]:
    if llm is not None and not is_mock_llm(llm):
        return _build_llm_session_state(session_id=session_id, user_request=user_request, llm=llm)
    return _build_rules_session_state(session_id=session_id, user_request=user_request)


def _build_llm_session_state(session_id: str, user_request: str, llm: LLMClient) -> dict[str, Any]:
    try:
        payload = extract_json_object(llm.complete(_agent_system_prompt(user_request)))
        blueprint = AgentSystemBlueprint.model_validate(payload["current_blueprint"])
        recommendation = AgentTopologyRecommendation.model_validate(payload["topology_recommendation"])
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "LLM agent system generation failed",
                "provider": getattr(llm, "provider", "unknown"),
                "model": getattr(llm, "model", "unknown"),
                "reason": exc.__class__.__name__,
            },
        ) from exc
    questions = _string_list(payload.get("clarifying_questions"))[:3] or recommendation.suggested_questions[:3]
    assistant_message = str(payload.get("assistant_message") or _assistant_message(recommendation.topology_type, questions))
    extracted = _extract_brief(user_request)
    return {
        "session_id": session_id,
        "user_request": user_request,
        "messages": [{"role": "user", "content": user_request}],
        "assistant_message": assistant_message,
        "clarifying_questions": questions,
        "extracted_brief": extracted,
        "topology_recommendation": recommendation,
        "current_blueprint": blueprint,
        "generation_mode": "llm",
        "llm_provider": getattr(llm, "provider", "unknown"),
        "llm_model": getattr(llm, "model", "unknown"),
    }


def _build_rules_session_state(session_id: str, user_request: str) -> dict[str, Any]:
    extracted = _extract_brief(user_request)
    classifier_input = AgentTopologyClassifierInput(
        user_request=user_request,
        extracted_goal=extracted["goal"],
        expected_outputs=extracted["expected_outputs"],
        tool_needs=extracted["tool_needs"],
        risk_level=extracted["risk_level"],
        task_complexity=extracted["task_complexity"],
        number_of_distinct_capabilities=extracted["capability_count"],
        need_for_context_isolation=extracted["need_for_context_isolation"],
        need_for_permission_isolation=extracted["need_for_permission_isolation"],
    )
    recommendation = classifier.classify(classifier_input)
    questions = clarification_engine.questions_for(user_request)
    state = {
        "session_id": session_id,
        "user_request": user_request,
        "messages": [{"role": "user", "content": user_request}],
        "assistant_message": _assistant_message(recommendation.topology_type, questions),
        "clarifying_questions": questions,
        "extracted_brief": extracted,
        "topology_recommendation": recommendation,
        "current_blueprint": None,
        "generation_mode": "rules",
    }
    state["current_blueprint"] = _blueprint_for_state(state)
    return state


def _agent_system_prompt(user_request: str) -> str:
    return f"""
You are an Agent System architect. Convert the user's request into a real executable agent-system blueprint.

Return one valid JSON object only. Do not include markdown.

Required JSON shape:
{{
  "assistant_message": "short Chinese response explaining the generated plan",
  "clarifying_questions": ["up to 3 concrete follow-up questions"],
  "topology_recommendation": {{
    "topology_type": "single_agent | workflow_agent | manager_subagents | multi_agent_workflow",
    "confidence": 0.0,
    "reason": "why this topology fits",
    "suggested_agents": ["agent names or ids"],
    "suggested_questions": ["up to 3 questions"]
  }},
  "current_blueprint": {{
    "system_id": "lowercase-ascii-id",
    "name": "Chinese product name",
    "description": "brief description",
    "target_user_groups": ["who uses it"],
    "user_skill_level": "beginner",
    "primary_goal": "main goal",
    "expected_outputs": ["concrete outputs"],
    "interaction_mode": "chat",
    "topology_type": "same as recommendation",
    "mother_agent": {{
      "agent_id": "mother-agent",
      "name": "manager name",
      "role": "manager",
      "responsibility": "what it routes and decides",
      "system_prompt": "runtime prompt",
      "planning_policy": "planning policy",
      "routing_policy": "routing policy",
      "allowed_tools": [],
      "allowed_subagents": ["ids matching subagents"],
      "memory_scope": "session",
      "output_contract": {{"type": "object"}},
      "risk_policy": "risk policy"
    }},
    "subagents": [
      {{
        "subagent_id": "research-subagent",
        "name": "subagent name",
        "specialty": "specialty",
        "description": "description",
        "when_to_use": "routing condition",
        "when_not_to_use": "non-routing condition",
        "task_contract": "input and output contract in words",
        "input_schema": {{"type": "object"}},
        "output_schema": {{"type": "object"}},
        "allowed_tools": [],
        "memory_scope": "task",
        "permission_scope": [],
        "context_policy": "task_only",
        "human_approval_required": false
      }}
    ],
    "workflow_nodes": [
      {{
        "node_id": "manager-plan",
        "name": "node name",
        "node_type": "llm_reasoning | tool_call | subagent_call | human_review | memory_read | memory_write | evaluation | final_response",
        "assigned_agent_id": "mother-agent or subagent id",
        "input_schema": {{"type": "object"}},
        "output_schema": {{"type": "object"}},
        "dependencies": [],
        "approval_required": false
      }}
    ],
    "tool_requirements": [],
    "memory_requirements": [],
    "evaluation_requirements": ["schema validation"],
    "approval_requirements": [],
    "observability_requirements": ["trace"],
    "risk_level": "low | medium | high",
    "release_policy": "save_candidate_then_promote_after_review"
  }}
}}

Rules:
- This must be a real model-generated plan, not a fixed example.
- Use the user's actual request to choose agents, tools, workflow nodes, approvals, and memory.
- If topology is single_agent, mother_agent can be a single generalist and subagents can be [].
- Every workflow_nodes.assigned_agent_id must reference mother_agent.agent_id or one subagent_id.
- Keep external write actions approval-gated.

User request:
{user_request}
""".strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _blueprint_for_state(state: dict[str, Any]) -> AgentSystemBlueprint:
    extracted = state["extracted_brief"]
    recommendation = state["topology_recommendation"]
    draft = AgentSystemBlueprint(
        system_id=_slug(extracted["name"]),
        name=extracted["name"],
        description=state["user_request"],
        target_user_groups=extracted["target_user_groups"],
        primary_goal=extracted["goal"],
        expected_outputs=extracted["expected_outputs"],
        topology_type=recommendation.topology_type,
        tool_requirements=extracted["tool_needs"],
        memory_requirements=extracted["memory_requirements"],
        evaluation_requirements=["schema validation", "end-to-end smoke eval"],
        approval_requirements=extracted["approval_requirements"],
        risk_level=extracted["risk_level"],
    )
    plan = planner.plan(draft, state["user_request"], {})
    return draft.model_copy(
        update={
            "mother_agent": plan.mother_agent,
            "subagents": plan.subagents,
            "workflow_nodes": plan.workflow_nodes,
        }
    )


def _extract_brief(user_request: str) -> dict[str, Any]:
    lower = user_request.lower()
    risk_level = RiskLevel.HIGH if any(keyword in lower for keyword in ["投资", "交易", "合规", "审批", "发布", "写入"]) else RiskLevel.MEDIUM if "团队" in lower else RiskLevel.LOW
    tool_needs = []
    if any(keyword in lower for keyword in ["网页", "新闻", "搜索", "web"]):
        tool_needs.append("web_search")
    if any(keyword in lower for keyword in ["文件", "财报", "pdf", "文档"]):
        tool_needs.append("file_reader")
    if any(keyword in lower for keyword in ["报告", "markdown", "pdf", "草稿"]):
        tool_needs.append("document_writer")
    expected_outputs = []
    for label in ["摘要", "分析", "报告", "草稿", "风险提示", "执行结果"]:
        if label in user_request:
            expected_outputs.append(label)
    if not expected_outputs:
        expected_outputs = ["结构化 Agent 系统蓝图"]
    capability_count = max(1, sum(1 for keyword in ["研究", "分析", "审查", "报告", "执行", "设计", "开发"] if keyword in user_request))
    return {
        "name": _name_from_request(user_request),
        "goal": user_request[:500],
        "expected_outputs": expected_outputs,
        "tool_needs": list(dict.fromkeys(tool_needs)),
        "risk_level": risk_level,
        "task_complexity": "high" if capability_count >= 4 else "medium" if capability_count >= 2 else "low",
        "capability_count": capability_count,
        "need_for_context_isolation": "隐私" in user_request or "隔离" in user_request,
        "need_for_permission_isolation": bool(tool_needs) and risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH},
        "target_user_groups": ["个人用户"] if "团队" not in user_request else ["小团队", "企业团队"],
        "memory_requirements": ["user confirmed preferences"] if "记忆" in user_request or "长期" in user_request else [],
        "approval_requirements": ["high risk output review"] if risk_level == RiskLevel.HIGH else [],
    }


def _assistant_message(topology_type: AgentTopologyType, questions: list[str]) -> str:
    question_text = " ".join(f"{index}. {question}" for index, question in enumerate(questions, start=1))
    return f"我先按 {topology_type.value} 建议生成低门槛蓝图。还需要确认：{question_text}"


def _name_from_request(user_request: str) -> str:
    compact = re.sub(r"\s+", " ", user_request).strip()
    if "投资" in compact:
        return "投资研究 Agent System"
    if "设计" in compact:
        return "设计协作 Agent System"
    if "运营" in compact:
        return "运营助手 Agent System"
    return (compact[:32] or "Agent System Builder Draft").rstrip("，。,. ")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or f"agent-system-{uuid4().hex[:8]}"
