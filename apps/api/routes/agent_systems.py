from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from apps.api.audit import build_audit_event
from apps.api.dependencies import get_llm_client, get_repository
from apps.api.security import require_scope
from packages.workflow_core.adapters.llm import LLMClient
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
    AgentBuildChange,
    AgentBuildLLMOutput,
    AgentBuildMessage,
    AgentBuildSession,
    AgentProductionReadinessReport,
    AgentReadinessDimension,
    AgentRequirementState,
    AgentSkillPackage,
    AgentSystemBlueprint,
    AgentTopologyClassifierInput,
    AgentTopologyRecommendation,
    AgentTopologyType,
)
from packages.workflow_core.models.common import utc_now
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


def dump_model(model: Any) -> Any:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json", by_alias=True)
    return model


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
    repository: WorkflowRepository = Depends(get_repository),
    llm: LLMClient = Depends(get_llm_client),
) -> dict[str, Any]:
    session_id = f"asb-{uuid4().hex[:12]}"
    session = _build_session_state(session_id=session_id, user_request=request.user_request, llm=llm)
    repository.save_agent_build_session(session)
    return _dump_session(session)


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    return _dump_session(_require_session(repository, session_id))


@router.post("/sessions/{session_id}/messages")
def append_message(
    session_id: str,
    request: AgentSystemMessageRequest,
    _actor: ActorContext = Depends(require_scope("workflow:write")),
    repository: WorkflowRepository = Depends(get_repository),
    llm: LLMClient = Depends(get_llm_client),
) -> dict[str, Any]:
    current = _require_session(repository, session_id)
    session = _build_session_state(
        session_id=session_id,
        user_request=current.user_request,
        llm=llm,
        current_session=current,
        user_message=request.message,
    )
    repository.save_agent_build_session(session)
    return _dump_session(session)


@router.post("/sessions/{session_id}/blueprint")
def generate_blueprint(
    session_id: str,
    _request: GenerateBlueprintRequest,
    _actor: ActorContext = Depends(require_scope("workflow:write")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    session = _require_session(repository, session_id)
    return {"current_blueprint": dump_model(session.current_blueprint)}


@router.get("/sessions/{session_id}/skills")
def get_skill_packages(
    session_id: str,
    _actor: ActorContext = Depends(require_scope("workflow:read")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    session = _require_session(repository, session_id)
    return {"skill_packages": [dump_model(package) for package in session.skill_packages]}


@router.post("/sessions/{session_id}/candidate")
def save_candidate(
    session_id: str,
    request: CandidateRequest,
    actor: ActorContext = Depends(require_scope("workflow:write")),
    repository: WorkflowRepository = Depends(get_repository),
) -> dict[str, Any]:
    session = _require_session(repository, session_id)
    if not session.readiness_report.ready_for_candidate:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "agent build is not ready for candidate save",
                "overall_score": session.readiness_report.overall_score,
                "blocking_gaps": session.readiness_report.blocking_gaps,
                "next_questions": session.readiness_report.next_questions,
            },
        )
    try:
        workflow_package = mapper.to_workflow_package(session.current_blueprint, version=request.version)
    except (ValidationError, ValueError) as exc:
        workflow_id = re.sub(r"[^a-z0-9]+", "-", session.current_blueprint.system_id.lower()).strip("-") or session.current_blueprint.system_id
        repository.save_audit_event(
            build_audit_event(
                event_type="agent_system_candidate",
                action="save_candidate",
                status="failed",
                actor=actor,
                workflow_id=workflow_id,
                workflow_version=request.version,
                resource_type="workflow_package",
                resource_id=f"{workflow_id}@{request.version}",
                details={
                    "reason": exc.__class__.__name__,
                    "validation_error_summary": _llm_validation_error_summary(exc),
                    "readiness_score": session.readiness_report.overall_score,
                    "skill_package_count": len(session.skill_packages),
                },
            )
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": "agent build candidate package validation failed",
                "reason": exc.__class__.__name__,
                "validation_error_summary": _llm_validation_error_summary(exc),
            },
        ) from exc
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
                "topology_type": session.current_blueprint.topology_type,
                "subagent_count": len(session.current_blueprint.subagents),
                "workflow_node_count": len(session.current_blueprint.workflow_nodes),
                "readiness_score": session.readiness_report.overall_score,
                "skill_package_count": len(session.skill_packages),
                "blocking_gap_count": len(session.readiness_report.blocking_gaps),
                "ready_for_candidate": session.readiness_report.ready_for_candidate,
                "saved_as_current": False,
            },
        )
    )
    session = session.model_copy(
        update={
            "candidate_workflow_id": workflow_package.workflow_id,
            "candidate_version": workflow_package.version,
            "updated_at": utc_now(),
        }
    )
    repository.save_agent_build_session(session)
    return {
        "workflow_package": dump_model(workflow_package),
        "validation_report": dump_model(validation_report),
        "skill_packages": [dump_model(package) for package in session.skill_packages],
        "readiness_report": dump_model(session.readiness_report),
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


def _require_session(repository: WorkflowRepository, session_id: str) -> AgentBuildSession:
    session = repository.get_agent_build_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="agent system session not found")
    return session


def _dump_session(state: AgentBuildSession) -> dict[str, Any]:
    return {
        "session_id": state.session_id,
        "assistant_message": state.assistant_message,
        "clarifying_questions": state.clarifying_questions,
        "messages": [dump_model(message) for message in state.messages],
        "requirement_state": dump_model(state.requirement_state),
        "topology_recommendation": dump_model(state.topology_recommendation),
        "current_blueprint": dump_model(state.current_blueprint),
        "readiness_report": dump_model(state.readiness_report),
        "skill_packages": [dump_model(package) for package in state.skill_packages],
        "change_log": [dump_model(change) for change in state.change_log],
        "candidate_workflow_id": state.candidate_workflow_id,
        "candidate_version": state.candidate_version,
        "generation_mode": state.generation_mode,
        "llm_provider": state.llm_provider,
        "llm_model": state.llm_model,
    }


def _build_session_state(
    session_id: str,
    user_request: str,
    llm: LLMClient | None = None,
    current_session: AgentBuildSession | None = None,
    user_message: str | None = None,
) -> AgentBuildSession:
    if llm is not None and not is_mock_llm(llm):
        return _build_llm_session_state(
            session_id=session_id,
            user_request=user_request,
            llm=llm,
            current_session=current_session,
            user_message=user_message,
        )
    return _build_rules_session_state(
        session_id=session_id,
        user_request=user_request,
        current_session=current_session,
        user_message=user_message,
    )


def _build_llm_session_state(
    session_id: str,
    user_request: str,
    llm: LLMClient,
    current_session: AgentBuildSession | None = None,
    user_message: str | None = None,
) -> AgentBuildSession:
    output = _complete_agent_build_llm_output(user_request, current_session, user_message, llm)
    messages = _messages_for(current_session, user_request, user_message)
    messages.append(AgentBuildMessage(role="assistant", content=output.assistant_message))
    return AgentBuildSession(
        session_id=session_id,
        user_request=user_request,
        messages=messages,
        assistant_message=output.assistant_message,
        clarifying_questions=output.clarifying_questions[:3] or output.readiness_report.next_questions[:3],
        requirement_state=output.requirement_state,
        topology_recommendation=output.topology_recommendation,
        current_blueprint=output.current_blueprint,
        readiness_report=output.readiness_report,
        skill_packages=output.skill_packages or _skill_packages_for_blueprint(output.current_blueprint),
        change_log=_change_log_for(current_session, output.change_summary),
        generation_mode="llm",
        llm_provider=getattr(llm, "provider", "unknown"),
        llm_model=getattr(llm, "model", "unknown"),
        created_at=current_session.created_at if current_session else utc_now(),
        updated_at=utc_now(),
    )


def _complete_agent_build_llm_output(
    user_request: str,
    current_session: AgentBuildSession | None,
    user_message: str | None,
    llm: LLMClient,
) -> AgentBuildLLMOutput:
    last_error: Exception | None = None
    last_error_summary = ""
    for attempt in range(3):
        try:
            prompt = _agent_system_prompt(user_request, current_session, user_message)
            if attempt:
                prompt = (
                    f"{prompt}\n\n"
                    "Previous response failed strict JSON schema validation. "
                    "Retry with exactly the required JSON shape, all required fields, valid enum values, "
                    "and no extra markdown.\n"
                    f"Low-sensitive validation error summary: {last_error_summary or last_error.__class__.__name__}."
                )
            payload = extract_json_object(llm.complete(prompt))
            return AgentBuildLLMOutput.model_validate(payload)
        except Exception as exc:
            last_error = exc
            last_error_summary = _llm_validation_error_summary(exc)
    assert last_error is not None
    raise HTTPException(
        status_code=502,
        detail={
            "message": "LLM agent system generation failed",
            "provider": getattr(llm, "provider", "unknown"),
            "model": getattr(llm, "model", "unknown"),
            "reason": last_error.__class__.__name__,
            "validation_error_summary": last_error_summary,
        },
    ) from last_error


def _llm_validation_error_summary(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for error in exc.errors()[:12]:
            location = ".".join(str(part) for part in error.get("loc", [])) or "root"
            parts.append(f"{location}:{error.get('type', 'invalid')}")
        return "; ".join(parts)
    return exc.__class__.__name__


def _build_rules_session_state(
    session_id: str,
    user_request: str,
    current_session: AgentBuildSession | None = None,
    user_message: str | None = None,
) -> AgentBuildSession:
    combined_request = f"{user_request}\n{user_message or ''}".strip()
    extracted = _extract_brief(combined_request)
    classifier_input = AgentTopologyClassifierInput(
        user_request=combined_request,
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
    questions = clarification_engine.questions_for(combined_request)[:3]
    blueprint = _blueprint_for_state(combined_request, extracted, recommendation)
    readiness = _readiness_for(blueprint, questions)
    assistant_message = _assistant_message(recommendation.topology_type, questions, readiness)
    messages = _messages_for(current_session, user_request, user_message)
    messages.append(AgentBuildMessage(role="assistant", content=assistant_message))
    return AgentBuildSession(
        session_id=session_id,
        user_request=user_request,
        messages=messages,
        assistant_message=assistant_message,
        clarifying_questions=questions,
        requirement_state=_requirement_state_for(combined_request, extracted, questions),
        topology_recommendation=recommendation,
        current_blueprint=blueprint,
        readiness_report=readiness,
        skill_packages=_skill_packages_for_blueprint(blueprint),
        change_log=_change_log_for(current_session, "updated agent build state"),
        generation_mode="rules",
        created_at=current_session.created_at if current_session else utc_now(),
        updated_at=utc_now(),
    )


def _agent_system_prompt(user_request: str, current_session: AgentBuildSession | None, user_message: str | None) -> str:
    current_state = dump_model(current_session) if current_session else None
    return f"""
You are a production Agent Builder for personal users and small teams.
Run one conversational iteration that clarifies the requirement, updates the agent blueprint, scores production readiness, and drafts skill packages.

Return one valid JSON object only. Do not include markdown.

Required JSON shape:
{{
  "assistant_message": "short Chinese response; ask questions when readiness is low",
  "clarifying_questions": ["up to 3 concrete follow-up questions"],
  "requirement_state": {{
    "summary": "current concise requirement summary",
    "confirmed_facts": ["facts confirmed by the user"],
    "missing_information": ["unknowns blocking production readiness"],
    "assumptions": ["explicit assumptions"],
    "constraints": ["scope, privacy, timing, budget, tool constraints"]
  }},
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
  }},
  "readiness_report": {{
    "dimensions": [
      {{"name": "goal_clarity", "score": 0, "blocker": false, "notes": "why"}},
      {{"name": "io_contract", "score": 0, "blocker": false, "notes": "why"}},
      {{"name": "tool_permissions", "score": 0, "blocker": false, "notes": "why"}},
      {{"name": "memory_strategy", "score": 0, "blocker": false, "notes": "why"}},
      {{"name": "failure_handling", "score": 0, "blocker": false, "notes": "why"}},
      {{"name": "evaluation_cases", "score": 0, "blocker": false, "notes": "why"}},
      {{"name": "approval_boundaries", "score": 0, "blocker": false, "notes": "why"}},
      {{"name": "release_readiness", "score": 0, "blocker": false, "notes": "why"}}
    ],
    "overall_score": 0,
    "ready_for_candidate": false,
    "blocking_gaps": ["low-sensitive blocker names"],
    "next_questions": ["questions to ask next"]
  }},
  "skill_packages": [
    {{
      "skill_id": "lowercase-ascii-skill-id",
      "name": "skill name",
      "agent_id": "mother-agent or subagent id",
      "trigger_scenarios": ["when to use this skill"],
      "system_prompt": "runtime prompt for this skill",
      "input_schema": {{"type": "object"}},
      "output_schema": {{"type": "object"}},
      "tool_permissions": ["least privilege tool permissions"],
      "memory_scope": "task | session | user",
      "failure_policy": "how failures are handled",
      "evaluation_cases": [{{"name": "case", "input": {{}}, "expected": {{}}}}],
      "usage_notes": "operator-facing notes"
    }}
  ],
  "change_summary": "what changed in this iteration"
}}

Rules:
- This is not a generic chatbot. Build a production-grade Agent workflow candidate.
- Use the user's actual request and the current state to update the blueprint.
- Candidate save is a draft asset gate, not production launch. If the user has confirmed goal, inputs, outputs, tool permissions, memory scope, failure policy, eval cases, approval boundaries, and candidate-only release policy, set ready_for_candidate=true.
- Ask more questions when readiness is below 70 or blockers remain. Do not ask perfection-oriented questions after the required candidate gate facts are already confirmed.
- Score candidate readiness against the available confirmed facts and explicit safe defaults, not against enterprise launch completeness.
- Readiness scores must be integers from 0 to 100, not 0 to 10. Set ready_for_candidate=true only when overall_score >= 70 and blocking_gaps is empty.
- Generate skill package drafts for the mother agent and each useful subagent.
- Every workflow_nodes.assigned_agent_id must reference mother_agent.agent_id or one subagent_id.
- External write, send, publish, database, financial, or destructive actions must be approval-gated.
- Do not invent credentials or claim tools are connected when the user did not provide them.

Initial user request:
{user_request}

Latest user message:
{user_message or ""}

Current persisted state JSON:
{current_state}
""".strip()


def _messages_for(current_session: AgentBuildSession | None, user_request: str, user_message: str | None) -> list[AgentBuildMessage]:
    if current_session:
        messages = list(current_session.messages)
        if user_message:
            messages.append(AgentBuildMessage(role="user", content=user_message))
        return messages
    return [AgentBuildMessage(role="user", content=user_request)]


def _change_log_for(current_session: AgentBuildSession | None, summary: str) -> list[AgentBuildChange]:
    changes = list(current_session.change_log) if current_session else []
    changes.append(AgentBuildChange(summary=summary, changed_sections=["requirements", "blueprint", "readiness", "skills"]))
    return changes[-20:]


def _blueprint_for_state(user_request: str, extracted: dict[str, Any], recommendation: AgentTopologyRecommendation) -> AgentSystemBlueprint:
    draft = AgentSystemBlueprint(
        system_id=_slug(extracted["name"]),
        name=extracted["name"],
        description=user_request,
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
    plan = planner.plan(draft, user_request, {})
    return draft.model_copy(update={"mother_agent": plan.mother_agent, "subagents": plan.subagents, "workflow_nodes": plan.workflow_nodes})


def _readiness_for(blueprint: AgentSystemBlueprint, questions: list[str]) -> AgentProductionReadinessReport:
    dimensions = [
        AgentReadinessDimension(name="goal_clarity", score=80 if blueprint.primary_goal else 30, blocker=not bool(blueprint.primary_goal), notes="primary goal captured"),
        AgentReadinessDimension(name="io_contract", score=75 if blueprint.expected_outputs else 45, blocker=not bool(blueprint.expected_outputs), notes="expected outputs captured"),
        AgentReadinessDimension(name="tool_permissions", score=80 if blueprint.tool_requirements else 68, blocker=False, notes="tool needs are least-privilege drafts"),
        AgentReadinessDimension(name="memory_strategy", score=74 if blueprint.memory_requirements else 64, blocker=False, notes="memory requirements can be refined"),
        AgentReadinessDimension(name="failure_handling", score=72, blocker=False, notes="node failure policy returns structured errors"),
        AgentReadinessDimension(name="evaluation_cases", score=72 if blueprint.evaluation_requirements else 40, blocker=False, notes="baseline eval requirements included"),
        AgentReadinessDimension(name="approval_boundaries", score=82 if blueprint.approval_requirements or blueprint.risk_level != RiskLevel.HIGH else 55, blocker=blueprint.risk_level == RiskLevel.HIGH and not blueprint.approval_requirements, notes="high risk writes require approval"),
        AgentReadinessDimension(name="release_readiness", score=70, blocker=False, notes="candidate save uses promotion gates"),
    ]
    overall = round(sum(item.score for item in dimensions) / len(dimensions))
    blocking_gaps = [item.name for item in dimensions if item.blocker or item.score < 60]
    return AgentProductionReadinessReport(
        dimensions=dimensions,
        overall_score=overall,
        ready_for_candidate=overall >= 70 and not blocking_gaps,
        blocking_gaps=blocking_gaps,
        next_questions=questions,
    )


def _skill_packages_for_blueprint(blueprint: AgentSystemBlueprint) -> list[AgentSkillPackage]:
    definitions = [blueprint.mother_agent, *blueprint.subagents]
    packages: list[AgentSkillPackage] = []
    for definition in definitions:
        if definition is None:
            continue
        agent_id = getattr(definition, "agent_id", None) or getattr(definition, "subagent_id")
        system_prompt = getattr(definition, "system_prompt", getattr(definition, "task_contract", blueprint.primary_goal))
        packages.append(
            AgentSkillPackage(
                skill_id=f"skill-{_slug(agent_id)}",
                name=f"{definition.name} Skill",
                agent_id=agent_id,
                trigger_scenarios=[getattr(definition, "when_to_use", blueprint.primary_goal)],
                system_prompt=system_prompt,
                input_schema=getattr(definition, "input_schema", {"type": "object", "additionalProperties": True}),
                output_schema=getattr(definition, "output_schema", {"type": "object", "additionalProperties": True}),
                tool_permissions=getattr(definition, "allowed_tools", []),
                memory_scope=getattr(definition, "memory_scope", "task"),
                failure_policy=getattr(definition, "retry_policy", "return_structured_error"),
                evaluation_cases=[
                    {
                        "name": "happy_path",
                        "input": {"request": blueprint.primary_goal},
                        "expected": {"status": "completed_or_paused"},
                    }
                ],
                usage_notes="Draft skill package saved with the candidate; it is not auto-installed.",
            )
        )
    return packages


def _requirement_state_for(user_request: str, extracted: dict[str, Any], questions: list[str]) -> AgentRequirementState:
    return AgentRequirementState(
        summary=extracted["goal"],
        confirmed_facts=[f"target users: {', '.join(extracted['target_user_groups'])}", f"risk level: {extracted['risk_level'].value}"],
        missing_information=questions,
        assumptions=["first version targets personal users and small teams", "skill packages are saved as drafts, not installed"],
        constraints=["candidate versions do not replace current workflow", "external write actions require approval"],
    )


def _extract_brief(user_request: str) -> dict[str, Any]:
    lower = user_request.lower()
    risk_level = RiskLevel.HIGH if any(keyword in lower for keyword in ["投资", "交易", "合规", "审批", "发布", "写入"]) else RiskLevel.MEDIUM if "团队" in lower else RiskLevel.LOW
    tool_needs: list[str] = []
    if any(keyword in lower for keyword in ["网页", "新闻", "搜索", "web"]):
        tool_needs.append("web_search")
    if any(keyword in lower for keyword in ["文件", "财报", "pdf", "文档"]):
        tool_needs.append("file_reader")
    if any(keyword in lower for keyword in ["报告", "markdown", "pdf", "草稿", "周报"]):
        tool_needs.append("document_writer")
    if any(keyword in lower for keyword in ["提醒", "日历", "待办"]):
        tool_needs.append("reminder_writer")
    expected_outputs = [label for label in ["摘要", "分析", "报告", "草稿", "风险提示", "执行结果", "周报", "提醒"] if label in user_request]
    if not expected_outputs:
        expected_outputs = ["结构化 Agent 系统蓝图", "可保存 Skill 包草案"]
    capability_count = max(1, sum(1 for keyword in ["研究", "分析", "审查", "报告", "执行", "设计", "开发", "提醒", "整理"] if keyword in user_request))
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
        "target_user_groups": ["小团队"] if "团队" in user_request else ["个人用户", "小团队"],
        "memory_requirements": ["user confirmed preferences", "iteration history"] if any(keyword in user_request for keyword in ["记忆", "长期", "迭代"]) else ["conversation requirement state"],
        "approval_requirements": ["external write and publish review"] if risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH} else [],
    }


def _assistant_message(topology_type: AgentTopologyType, questions: list[str], readiness: AgentProductionReadinessReport) -> str:
    if readiness.ready_for_candidate:
        return f"我已按 {topology_type.value} 生成可保存候选版本的 Agent 方案，生产成熟度 {readiness.overall_score}%。你可以继续补充，也可以保存候选版本。"
    question_text = " ".join(f"{index}. {question}" for index, question in enumerate(questions or readiness.next_questions, start=1))
    return f"我先按 {topology_type.value} 生成当前方案，生产成熟度 {readiness.overall_score}%。还需要确认：{question_text}"


def _name_from_request(user_request: str) -> str:
    compact = re.sub(r"\s+", " ", user_request).strip()
    if "客户" in compact:
        return "客户跟进 Agent System"
    if "投资" in compact:
        return "投资研究 Agent System"
    if "设计" in compact:
        return "设计协作 Agent System"
    if "运营" in compact:
        return "运营助手 Agent System"
    return (compact[:32] or "Agent System Builder Draft").rstrip("，。,. ")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or f"agent-system-{uuid4().hex[:8]}"
