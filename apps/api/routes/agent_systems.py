from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apps.api.audit import build_audit_event
from apps.api.dependencies import get_repository
from apps.api.routes.workflows import dump_model
from apps.api.security import require_scope
from packages.workflow_core.agent_system import (
    AgentSystemBlueprintMapper,
    AgentTopologyClassifier,
    ClarificationEngine,
    SubAgentPlanner,
    SubAgentValidator,
)
from packages.workflow_core.models import (
    ActorContext,
    AgentSystemBlueprint,
    AgentTopologyClassifierInput,
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
) -> dict[str, Any]:
    session_id = f"asb-{uuid4().hex[:12]}"
    state = _build_session_state(session_id=session_id, user_request=request.user_request)
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
) -> dict[str, Any]:
    state = _require_session(session_id)
    state["messages"].append({"role": "user", "content": request.message})
    combined_request = f"{state['user_request']}\n{request.message}"
    state.update(_build_session_state(session_id=session_id, user_request=combined_request))
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


def _build_session_state(session_id: str, user_request: str) -> dict[str, Any]:
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
    }
    state["current_blueprint"] = _blueprint_for_state(state)
    return state


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
