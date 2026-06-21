from __future__ import annotations

from packages.workflow_core.models import (
    AgentTopologyClassifierInput,
    AgentTopologyRecommendation,
    AgentTopologyType,
)
from packages.workflow_core.models.enums import RiskLevel


class AgentTopologyClassifier:
    """Rules-first topology classifier for low-friction agent creation."""

    def classify(self, request: AgentTopologyClassifierInput) -> AgentTopologyRecommendation:
        text = request.user_request.lower()
        reasons: list[str] = []

        if self._multi_agent_workflow(request, text):
            topology = AgentTopologyType.MULTI_AGENT_WORKFLOW
            confidence = 0.82
            reasons.append("multiple roles, complex state, or approval-heavy collaboration was detected")
        elif self._manager_subagents(request, text):
            topology = AgentTopologyType.MANAGER_SUBAGENTS
            confidence = 0.78
            reasons.append("distinct specialist responsibilities are useful, but final output still needs one manager")
        elif self._workflow_agent(request, text):
            topology = AgentTopologyType.WORKFLOW_AGENT
            confidence = 0.74
            reasons.append("the task has multiple ordered steps while remaining one responsibility")
        else:
            topology = AgentTopologyType.SINGLE_AGENT
            confidence = 0.7
            reasons.append("the request can start as one agent without extra coordination cost")

        return AgentTopologyRecommendation(
            topology_type=topology,
            confidence=confidence,
            reason="; ".join(reasons),
            suggested_agents=self._suggested_agents(topology, request),
            suggested_questions=self._suggested_questions(request),
        )

    def _multi_agent_workflow(self, request: AgentTopologyClassifierInput, text: str) -> bool:
        return (
            request.task_complexity.lower() in {"high", "complex"}
            and request.number_of_distinct_capabilities >= 4
            and (
                request.need_for_context_isolation
                or request.need_for_permission_isolation
                or request.risk_level == RiskLevel.HIGH
            )
        ) or any(keyword in text for keyword in ["长期协作", "多分支", "多审批", "multi team", "long-running"])

    def _manager_subagents(self, request: AgentTopologyClassifierInput, text: str) -> bool:
        specialist_keywords = [
            "研究",
            "分析",
            "审查",
            "生成报告",
            "设计",
            "开发",
            "运营",
            "法务",
            "财报",
            "新闻",
            "risk",
            "research",
            "review",
            "report",
        ]
        return (
            request.number_of_distinct_capabilities >= 3
            or request.need_for_context_isolation
            or request.need_for_permission_isolation
            or sum(1 for keyword in specialist_keywords if keyword in text) >= 3
        )

    def _workflow_agent(self, request: AgentTopologyClassifierInput, text: str) -> bool:
        step_keywords = ["先", "然后", "最后", "步骤", "流程", "从", "到", "多步骤", "workflow", "pipeline"]
        return request.task_complexity.lower() in {"medium", "moderate"} or any(keyword in text for keyword in step_keywords)

    def _suggested_agents(self, topology: AgentTopologyType, request: AgentTopologyClassifierInput) -> list[str]:
        if topology == AgentTopologyType.SINGLE_AGENT:
            return ["general_agent"]
        if topology == AgentTopologyType.WORKFLOW_AGENT:
            return ["workflow_agent"]
        if topology == AgentTopologyType.MANAGER_SUBAGENTS:
            return ["mother_agent", "research_subagent", "execution_subagent", "review_subagent"]
        return ["orchestrator_agent", "planner_subagent", "specialist_subagent", "review_subagent", "approval_subagent"]

    def _suggested_questions(self, request: AgentTopologyClassifierInput) -> list[str]:
        questions = [
            "谁会使用这个 Agent，是个人、团队，还是面向外部用户？",
            "你希望最终输出什么格式或产物？",
            "哪些外部工具、文件、网页、数据库或第三方平台需要接入？",
            "哪些动作必须先经过人工确认？",
            "是否需要长期记忆偏好、规则或历史案例？",
        ]
        if request.expected_outputs:
            questions = [question for question in questions if "最终输出" not in question]
        if request.tool_needs:
            questions = [question for question in questions if "外部工具" not in question]
        return questions[:3]
