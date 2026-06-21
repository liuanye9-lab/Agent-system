from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from packages.workflow_core.models.common import StrictBaseModel
from packages.workflow_core.models.enums import RiskLevel


class AgentTopologyType(StrEnum):
    SINGLE_AGENT = "single_agent"
    WORKFLOW_AGENT = "workflow_agent"
    MANAGER_SUBAGENTS = "manager_subagents"
    MULTI_AGENT_WORKFLOW = "multi_agent_workflow"


class AgentInteractionMode(StrEnum):
    CHAT = "chat"
    FORM = "form"
    API = "api"


class UserSkillLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class ContextPolicy(StrEnum):
    TASK_ONLY = "task_only"
    FILTERED_CONTEXT = "filtered_context"
    FULL_CONTEXT = "full_context"


class AgentWorkflowNodeType(StrEnum):
    LLM_REASONING = "llm_reasoning"
    TOOL_CALL = "tool_call"
    SUBAGENT_CALL = "subagent_call"
    HUMAN_REVIEW = "human_review"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    EVALUATION = "evaluation"
    FINAL_RESPONSE = "final_response"


class CollaborationMode(StrEnum):
    MANAGER_AS_TOOL_CALLER = "manager_as_tool_caller"
    HANDOFF_TO_SPECIALIST = "handoff_to_specialist"
    GRAPH_ORCHESTRATED = "graph_orchestrated"


class MotherAgentDefinition(StrictBaseModel):
    agent_id: str
    name: str
    role: str
    responsibility: str
    system_prompt: str
    planning_policy: str
    routing_policy: str
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_subagents: list[str] = Field(default_factory=list)
    memory_scope: str = "session"
    output_contract: dict[str, Any] = Field(default_factory=dict)
    risk_policy: str = "route high-risk actions to human review"


class SubAgentDefinition(StrictBaseModel):
    subagent_id: str
    name: str
    specialty: str
    description: str
    when_to_use: str
    when_not_to_use: str
    task_contract: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    allowed_tools: list[str] = Field(default_factory=list)
    memory_scope: str = "task"
    permission_scope: list[str] = Field(default_factory=list)
    context_policy: ContextPolicy = ContextPolicy.TASK_ONLY
    return_format: str = "structured_json"
    timeout_policy: str = "bounded"
    retry_policy: str = "no_retry_without_reason"
    evaluation_policy: str = "schema_and_overlap_check"
    human_approval_required: bool = False


class AgentWorkflowNode(StrictBaseModel):
    node_id: str
    name: str
    node_type: AgentWorkflowNodeType
    assigned_agent_id: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    approval_required: bool = False
    trace_required: bool = True
    failure_policy: str = "return_structured_error"


class AgentSystemBlueprint(StrictBaseModel):
    system_id: str
    name: str
    description: str
    target_user_groups: list[str] = Field(default_factory=list)
    user_skill_level: UserSkillLevel = UserSkillLevel.BEGINNER
    primary_goal: str
    expected_outputs: list[str] = Field(default_factory=list)
    interaction_mode: AgentInteractionMode = AgentInteractionMode.CHAT
    topology_type: AgentTopologyType = AgentTopologyType.SINGLE_AGENT
    mother_agent: MotherAgentDefinition | None = None
    subagents: list[SubAgentDefinition] = Field(default_factory=list)
    workflow_nodes: list[AgentWorkflowNode] = Field(default_factory=list)
    tool_requirements: list[str] = Field(default_factory=list)
    memory_requirements: list[str] = Field(default_factory=list)
    evaluation_requirements: list[str] = Field(default_factory=list)
    approval_requirements: list[str] = Field(default_factory=list)
    observability_requirements: list[str] = Field(default_factory=lambda: ["trace"])
    risk_level: RiskLevel = RiskLevel.LOW
    release_policy: str = "save_candidate_then_promote_after_review"

    @model_validator(mode="after")
    def validate_agent_references(self) -> "AgentSystemBlueprint":
        subagent_ids = {subagent.subagent_id for subagent in self.subagents}
        if self.mother_agent:
            missing = set(self.mother_agent.allowed_subagents) - subagent_ids
            if missing:
                raise ValueError(f"mother_agent.allowed_subagents references unknown subagents: {sorted(missing)}")
        agent_ids = subagent_ids | ({self.mother_agent.agent_id} if self.mother_agent else set())
        for node in self.workflow_nodes:
            if node.assigned_agent_id and node.assigned_agent_id not in agent_ids:
                raise ValueError(f"workflow node {node.node_id} references unknown assigned_agent_id")
        return self


class AgentTopologyClassifierInput(StrictBaseModel):
    user_request: str
    extracted_goal: str | None = None
    expected_outputs: list[str] = Field(default_factory=list)
    tool_needs: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    task_complexity: str = "low"
    number_of_distinct_capabilities: int = Field(default=1, ge=1)
    need_for_context_isolation: bool = False
    need_for_permission_isolation: bool = False


class AgentTopologyRecommendation(StrictBaseModel):
    topology_type: AgentTopologyType
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    suggested_agents: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)


class SubAgentPlan(StrictBaseModel):
    mother_agent: MotherAgentDefinition | None = None
    subagents: list[SubAgentDefinition] = Field(default_factory=list)
    workflow_nodes: list[AgentWorkflowNode] = Field(default_factory=list)
    routing_policy: str = "default_to_single_agent_then_delegate_when_boundaries_are_clear"
    collaboration_mode: CollaborationMode = CollaborationMode.MANAGER_AS_TOOL_CALLER


class SubAgentValidationIssue(StrictBaseModel):
    severity: str
    code: str
    message: str
    target_id: str | None = None


class SubAgentValidationReport(StrictBaseModel):
    valid: bool
    issues: list[SubAgentValidationIssue] = Field(default_factory=list)


class SubAgentResult(StrictBaseModel):
    subagent_id: str
    status: str
    summary: str
    structured_output: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    errors: list[str] = Field(default_factory=list)
    tokens_estimate: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    requires_human_review: bool = False
