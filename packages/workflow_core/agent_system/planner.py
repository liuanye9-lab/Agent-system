from __future__ import annotations

import re
from collections import Counter
from typing import Any

from packages.workflow_core.models import (
    AgentSystemBlueprint,
    AgentTopologyType,
    AgentWorkflowNode,
    AgentWorkflowNodeType,
    CollaborationMode,
    ContextPolicy,
    MotherAgentDefinition,
    SubAgentDefinition,
    SubAgentPlan,
    SubAgentValidationIssue,
    SubAgentValidationReport,
)
from packages.workflow_core.models.enums import RiskLevel


class SubAgentPlanner:
    def plan(
        self,
        blueprint: AgentSystemBlueprint,
        user_request: str,
        clarified_answers: dict[str, Any] | None = None,
    ) -> SubAgentPlan:
        clarified_answers = clarified_answers or {}
        topology = blueprint.topology_type

        if topology == AgentTopologyType.SINGLE_AGENT:
            return self._single_agent_plan(blueprint)
        if topology == AgentTopologyType.WORKFLOW_AGENT:
            return self._workflow_agent_plan(blueprint)

        specialties = self._specialties_for(user_request, blueprint)
        subagents = [
            self._subagent_for_specialty(specialty, index, blueprint)
            for index, specialty in enumerate(specialties, start=1)
        ]
        mother_agent = MotherAgentDefinition(
            agent_id="mother-agent",
            name=f"{blueprint.name} 调度员",
            role="manager",
            responsibility="理解需求、拆分任务、调用子 Agent、汇总最终结果并处理风险提示",
            system_prompt=(
                "你是低门槛 Agent System 的母 Agent。默认保持简单，只有在职责、上下文、"
                "工具权限或风险边界清晰不同时才调用子 Agent。"
            ),
            planning_policy="start_simple_then_delegate_to_specialists",
            routing_policy="route_by_specialty_and_risk",
            allowed_tools=[],
            allowed_subagents=[subagent.subagent_id for subagent in subagents],
            memory_scope="session_and_user_confirmed_memory",
            output_contract=_default_output_schema(),
            risk_policy="high risk or write-like actions require human review",
        )
        nodes = self._manager_nodes(mother_agent, subagents, blueprint)
        mode = (
            CollaborationMode.GRAPH_ORCHESTRATED
            if topology == AgentTopologyType.MULTI_AGENT_WORKFLOW
            else CollaborationMode.MANAGER_AS_TOOL_CALLER
        )
        return SubAgentPlan(
            mother_agent=mother_agent,
            subagents=subagents,
            workflow_nodes=nodes,
            routing_policy="manager routes to specialists with narrow context and tool scopes",
            collaboration_mode=mode,
        )

    def _single_agent_plan(self, blueprint: AgentSystemBlueprint) -> SubAgentPlan:
        mother = MotherAgentDefinition(
            agent_id="single-agent",
            name=f"{blueprint.name} Agent",
            role="generalist",
            responsibility=blueprint.primary_goal,
            system_prompt="用一个 Agent 完成任务，保持低操作难度，并在不确定时最多提出三个澄清问题。",
            planning_policy="single_agent_direct_response",
            routing_policy="no_subagent_routing",
            allowed_tools=blueprint.tool_requirements,
            allowed_subagents=[],
            output_contract=_default_output_schema(),
        )
        return SubAgentPlan(
            mother_agent=mother,
            subagents=[],
            workflow_nodes=[
                AgentWorkflowNode(
                    node_id="understand-and-answer",
                    name="理解需求并输出结果",
                    node_type=AgentWorkflowNodeType.LLM_REASONING,
                    assigned_agent_id=mother.agent_id,
                    input_schema=_default_input_schema(),
                    output_schema=_default_output_schema(),
                )
            ],
            collaboration_mode=CollaborationMode.MANAGER_AS_TOOL_CALLER,
        )

    def _workflow_agent_plan(self, blueprint: AgentSystemBlueprint) -> SubAgentPlan:
        mother = MotherAgentDefinition(
            agent_id="workflow-agent",
            name=f"{blueprint.name} Workflow Agent",
            role="workflow_agent",
            responsibility=blueprint.primary_goal,
            system_prompt="按清晰步骤完成任务，不暴露 DAG 等技术概念给低门槛用户。",
            planning_policy="ordered_steps_single_responsibility",
            routing_policy="linear_workflow",
            allowed_tools=blueprint.tool_requirements,
            allowed_subagents=[],
            output_contract=_default_output_schema(),
        )
        return SubAgentPlan(
            mother_agent=mother,
            subagents=[],
            workflow_nodes=[
                AgentWorkflowNode(
                    node_id="clarify-goal",
                    name="澄清目标",
                    node_type=AgentWorkflowNodeType.LLM_REASONING,
                    assigned_agent_id=mother.agent_id,
                    input_schema=_default_input_schema(),
                    output_schema=_default_output_schema(),
                ),
                AgentWorkflowNode(
                    node_id="execute-steps",
                    name="执行步骤",
                    node_type=AgentWorkflowNodeType.TOOL_CALL if blueprint.tool_requirements else AgentWorkflowNodeType.LLM_REASONING,
                    assigned_agent_id=mother.agent_id,
                    input_schema=_default_input_schema(),
                    output_schema=_default_output_schema(),
                    dependencies=["clarify-goal"],
                    approval_required=any(_is_write_like_tool(tool) for tool in blueprint.tool_requirements),
                ),
                AgentWorkflowNode(
                    node_id="final-response",
                    name="生成最终结果",
                    node_type=AgentWorkflowNodeType.FINAL_RESPONSE,
                    assigned_agent_id=mother.agent_id,
                    input_schema=_default_input_schema(),
                    output_schema=_default_output_schema(),
                    dependencies=["execute-steps"],
                ),
            ],
            collaboration_mode=CollaborationMode.GRAPH_ORCHESTRATED,
        )

    def _specialties_for(self, user_request: str, blueprint: AgentSystemBlueprint) -> list[str]:
        lower = user_request.lower()
        specialties: list[str] = []
        if any(keyword in lower for keyword in ["新闻", "web", "search", "研究", "research"]):
            specialties.append("research")
        if any(keyword in lower for keyword in ["财报", "数据", "指标", "analysis", "分析"]):
            specialties.append("analysis")
        if any(keyword in lower for keyword in ["风险", "合规", "review", "审查", "投资"]):
            specialties.append("risk_review")
        if any(keyword in lower for keyword in ["报告", "文档", "总结", "草稿", "输出", "report"]):
            specialties.append("reporting")
        if not specialties:
            specialties = ["planning", "execution", "review"]
        if blueprint.topology_type == AgentTopologyType.MULTI_AGENT_WORKFLOW and "approval" not in specialties:
            specialties.append("approval")
        return list(dict.fromkeys(specialties))[:5]

    def _subagent_for_specialty(
        self,
        specialty: str,
        index: int,
        blueprint: AgentSystemBlueprint,
    ) -> SubAgentDefinition:
        catalog = {
            "research": ("研究 Subagent", "搜索、读取并总结相关资料", ["web_search", "file_reader"]),
            "analysis": ("分析 Subagent", "提取指标、比较证据并形成结构化分析", ["data_reader", "calculator"]),
            "risk_review": ("风险审查 Subagent", "检查结论是否过度武断、是否需要人工确认", []),
            "reporting": ("报告生成 Subagent", "把上游结果整理为用户需要的输出格式", ["document_writer"]),
            "planning": ("规划 Subagent", "把需求拆解为低门槛执行步骤", []),
            "execution": ("执行 Subagent", "在授权工具范围内执行任务步骤", blueprint.tool_requirements[:2]),
            "review": ("复核 Subagent", "检查遗漏、冲突和输出契约", []),
            "approval": ("审批协调 Subagent", "识别需要人工确认的节点和证据", []),
        }
        name, description, default_tools = catalog.get(specialty, (f"专家 Subagent {index}", f"负责 {specialty}", []))
        allowed_tools = [tool for tool in default_tools if not blueprint.tool_requirements or tool in set(blueprint.tool_requirements)]
        if not allowed_tools and specialty in {"research", "analysis", "reporting"}:
            allowed_tools = blueprint.tool_requirements[:1]
        return SubAgentDefinition(
            subagent_id=f"{_slug(specialty)}-subagent",
            name=name,
            specialty=specialty,
            description=description,
            when_to_use=f"当任务需要{description}时使用",
            when_not_to_use="当任务可以由母 Agent 直接完成，或职责边界不清晰时不要调用",
            task_contract=f"输入用户目标和必要上下文，输出 {specialty} 的结构化结果、风险和下一步",
            input_schema=_default_input_schema(),
            output_schema=_subagent_output_schema(),
            allowed_tools=allowed_tools,
            memory_scope="task",
            permission_scope=allowed_tools,
            context_policy=ContextPolicy.FILTERED_CONTEXT if allowed_tools else ContextPolicy.TASK_ONLY,
            human_approval_required=blueprint.risk_level == RiskLevel.HIGH or specialty in {"risk_review", "approval"},
        )

    def _manager_nodes(
        self,
        mother_agent: MotherAgentDefinition,
        subagents: list[SubAgentDefinition],
        blueprint: AgentSystemBlueprint,
    ) -> list[AgentWorkflowNode]:
        nodes = [
            AgentWorkflowNode(
                node_id="manager-plan",
                name="母 Agent 理解与规划",
                node_type=AgentWorkflowNodeType.LLM_REASONING,
                assigned_agent_id=mother_agent.agent_id,
                input_schema=_default_input_schema(),
                output_schema=_default_output_schema(),
            )
        ]
        previous = "manager-plan"
        for subagent in subagents:
            node_id = f"call-{subagent.subagent_id}"
            nodes.append(
                AgentWorkflowNode(
                    node_id=node_id,
                    name=f"调用 {subagent.name}",
                    node_type=AgentWorkflowNodeType.SUBAGENT_CALL,
                    assigned_agent_id=subagent.subagent_id,
                    input_schema=subagent.input_schema,
                    output_schema=_subagent_call_output_schema(),
                    dependencies=[previous],
                    approval_required=subagent.human_approval_required and blueprint.risk_level == RiskLevel.HIGH,
                )
            )
            previous = node_id
        if blueprint.approval_requirements or blueprint.risk_level == RiskLevel.HIGH:
            nodes.append(
                AgentWorkflowNode(
                    node_id="human-risk-review",
                    name="人工风险确认",
                    node_type=AgentWorkflowNodeType.HUMAN_REVIEW,
                    assigned_agent_id=mother_agent.agent_id,
                    input_schema=_default_input_schema(),
                    output_schema=_default_output_schema(),
                    dependencies=[previous],
                    approval_required=True,
                )
            )
            previous = "human-risk-review"
        nodes.append(
            AgentWorkflowNode(
                node_id="manager-final-response",
                name="母 Agent 汇总最终结果",
                node_type=AgentWorkflowNodeType.FINAL_RESPONSE,
                assigned_agent_id=mother_agent.agent_id,
                input_schema=_default_input_schema(),
                output_schema=_default_output_schema(),
                dependencies=[previous],
            )
        )
        return nodes


class SubAgentValidator:
    def validate(self, blueprint: AgentSystemBlueprint) -> SubAgentValidationReport:
        issues: list[SubAgentValidationIssue] = []
        specialties = [subagent.specialty.strip().lower() for subagent in blueprint.subagents]
        for specialty, count in Counter(specialties).items():
            if count > 1:
                issues.append(
                    SubAgentValidationIssue(
                        severity="error",
                        code="duplicate_specialty",
                        message=f"subagent specialty is duplicated: {specialty}",
                        target_id=specialty,
                    )
                )
        for subagent in blueprint.subagents:
            if not subagent.when_to_use or not subagent.when_not_to_use:
                issues.append(SubAgentValidationIssue(severity="error", code="missing_boundary", message="subagent must declare when_to_use and when_not_to_use", target_id=subagent.subagent_id))
            if not subagent.input_schema or not subagent.output_schema:
                issues.append(SubAgentValidationIssue(severity="error", code="missing_contract", message="subagent must declare input and output schemas", target_id=subagent.subagent_id))
            broad_tools = set(subagent.allowed_tools) - set(blueprint.tool_requirements)
            if blueprint.tool_requirements and broad_tools:
                issues.append(SubAgentValidationIssue(severity="warning", code="tool_scope_outside_blueprint", message=f"subagent uses tools outside blueprint requirements: {sorted(broad_tools)}", target_id=subagent.subagent_id))
            if len(subagent.allowed_tools) > 5:
                issues.append(SubAgentValidationIssue(severity="warning", code="tool_scope_too_broad", message="subagent has a broad tool scope", target_id=subagent.subagent_id))
        return SubAgentValidationReport(valid=not any(issue.severity == "error" for issue in issues), issues=issues)


def _default_input_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": True}


def _default_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "decision": {"type": "string"},
            "risks": {"type": "array"},
            "next_actions": {"type": "array"},
        },
        "required": ["summary"],
        "additionalProperties": True,
    }


def _subagent_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "findings": {"type": "array"},
            "risks": {"type": "array"},
            "next_actions": {"type": "array"},
        },
        "required": ["summary"],
        "additionalProperties": True,
    }


def _subagent_call_output_schema() -> dict[str, Any]:
    schema = _default_output_schema()
    schema["properties"]["subagent_result"] = {"type": "object"}
    return schema


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "agent"


def _is_write_like_tool(tool: str) -> bool:
    return any(keyword in tool.lower() for keyword in ["publish", "send", "database", "write", "writer", "reminder", "calendar"])
