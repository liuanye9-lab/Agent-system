from __future__ import annotations

import re
from typing import Any

from packages.workflow_core.models import (
    AgentSpec,
    AgentSystemBlueprint,
    AgentWorkflowNode,
    AgentWorkflowNodeType,
    DataContract,
    EvalSpec,
    ProcessEdge,
    ProcessNode,
    ProcessSpec,
    ProblemSpec,
    ToolPolicy,
    WorkflowPackage,
)
from packages.workflow_core.models.enums import EvalType, NodeType, PermissionLevel, RiskLevel


class AgentSystemBlueprintMapper:
    def to_workflow_package(
        self,
        blueprint: AgentSystemBlueprint,
        version: str = "0.1.0",
    ) -> WorkflowPackage:
        workflow_id = _slug(blueprint.system_id)[:64]
        nodes = blueprint.workflow_nodes or self._fallback_nodes(blueprint)
        process_nodes = [self._process_node(node, blueprint) for node in nodes]
        edges = self._edges(nodes)
        data_contracts = [self._contract_for(node) for node in nodes]
        tool_policies = self._tool_policies(blueprint)
        agent_specs = self._agent_specs(blueprint, process_nodes, workflow_id)
        eval_specs = [
            EvalSpec(
                eval_id=f"{workflow_id}-end-to-end",
                workflow_id=workflow_id,
                name=f"{blueprint.name} end-to-end eval",
                eval_type=EvalType.END_TO_END,
                target_node_id=process_nodes[-1].node_id,
                input_case={"context": {"user_request": blueprint.primary_goal}},
                expected_output={},
                scoring_rules=["completed_or_paused", "trace_present", "decision_present"],
            )
        ]
        return WorkflowPackage(
            workflow_id=workflow_id,
            name=blueprint.name,
            version=version,
            problem_spec=ProblemSpec(
                id=f"{workflow_id}-problem",
                title=blueprint.name,
                description=blueprint.description,
                target_users=blueprint.target_user_groups,
                business_goal=blueprint.primary_goal,
                start_event="user describes an agent system need in the default chat builder",
                end_state="operator reviews a generated candidate workflow package",
                success_metrics=[
                    "topology recommendation is explainable",
                    "candidate is saved without promoting current version",
                    "approval and shadow mode remain available",
                ],
                constraints=["low friction chat-first entry", "no direct promotion", "schema-validated agent outputs"],
                risks=[blueprint.risk_level.value],
                human_roles=["operator"],
            ),
            process_spec=ProcessSpec(
                id=f"{workflow_id}-process",
                workflow_id=workflow_id,
                nodes=process_nodes,
                edges=edges,
                entry_node_id=process_nodes[0].node_id,
                terminal_node_ids=[process_nodes[-1].node_id],
                version=version,
            ),
            data_contracts=data_contracts,
            tool_policies=tool_policies,
            agent_specs=agent_specs,
            eval_specs=eval_specs,
        )

    def _fallback_nodes(self, blueprint: AgentSystemBlueprint) -> list[AgentWorkflowNode]:
        return [
            AgentWorkflowNode(
                node_id="agent-system-response",
                name="生成 Agent 系统响应",
                node_type=AgentWorkflowNodeType.LLM_REASONING,
                assigned_agent_id=blueprint.mother_agent.agent_id if blueprint.mother_agent else None,
            )
        ]

    def _process_node(self, node: AgentWorkflowNode, blueprint: AgentSystemBlueprint) -> ProcessNode:
        return ProcessNode(
            node_id=node.node_id,
            name=node.name,
            node_type=self._node_type(node.node_type),
            owner_role=node.assigned_agent_id or "agent-system",
            description=f"Agent System node mapped from {node.node_type.value}",
            done_condition="node output validates against its DataContract and can be traced",
            requires_approval=node.approval_required,
            input_contract_id=f"contract-{node.node_id}",
            output_contract_id=f"contract-{node.node_id}",
            tool_ids=self._tool_ids_for(node, blueprint),
            assigned_agent_id=node.assigned_agent_id,
            context_policy=self._context_policy_for(node, blueprint),
        )

    def _node_type(self, node_type: AgentWorkflowNodeType) -> NodeType:
        return {
            AgentWorkflowNodeType.LLM_REASONING: NodeType.REASONING,
            AgentWorkflowNodeType.TOOL_CALL: NodeType.REASONING,
            AgentWorkflowNodeType.SUBAGENT_CALL: NodeType.SUBAGENT_CALL,
            AgentWorkflowNodeType.HUMAN_REVIEW: NodeType.REVIEW,
            AgentWorkflowNodeType.MEMORY_READ: NodeType.READ,
            AgentWorkflowNodeType.MEMORY_WRITE: NodeType.WRITE,
            AgentWorkflowNodeType.EVALUATION: NodeType.REVIEW,
            AgentWorkflowNodeType.FINAL_RESPONSE: NodeType.REASONING,
        }[node_type]

    def _tool_ids_for(self, node: AgentWorkflowNode, blueprint: AgentSystemBlueprint) -> list[str]:
        if node.node_type == AgentWorkflowNodeType.TOOL_CALL:
            return [_tool_id(tool) for tool in blueprint.tool_requirements]
        subagent = next((item for item in blueprint.subagents if item.subagent_id == node.assigned_agent_id), None)
        if subagent:
            return [_tool_id(tool) for tool in subagent.allowed_tools]
        return []

    def _context_policy_for(self, node: AgentWorkflowNode, blueprint: AgentSystemBlueprint) -> str | None:
        subagent = next((item for item in blueprint.subagents if item.subagent_id == node.assigned_agent_id), None)
        return subagent.context_policy.value if subagent else None

    def _edges(self, nodes: list[AgentWorkflowNode]) -> list[ProcessEdge]:
        node_ids = {node.node_id for node in nodes}
        edges: list[ProcessEdge] = []
        for index, node in enumerate(nodes):
            if node.dependencies:
                for dependency in node.dependencies:
                    if dependency in node_ids:
                        edges.append(ProcessEdge(source_node_id=dependency, target_node_id=node.node_id))
            elif index > 0:
                edges.append(ProcessEdge(source_node_id=nodes[index - 1].node_id, target_node_id=node.node_id))
        return edges

    def _contract_for(self, node: AgentWorkflowNode) -> DataContract:
        return DataContract(
            contract_id=f"contract-{node.node_id}",
            name=f"{node.name} contract",
            description="Schema boundary for an Agent System Builder node",
            input_schema=node.input_schema or {"type": "object", "additionalProperties": True},
            output_schema=node.output_schema or {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
                "additionalProperties": True,
            },
            required_fields=[],
            validation_rules=["json_schema"],
            error_policy="fail_node_with_structured_error",
            example_input={"context": {"user_request": "Build an agent"}},
            example_output={"summary": "Node completed"},
        )

    def _tool_policies(self, blueprint: AgentSystemBlueprint) -> list[ToolPolicy]:
        tool_names = list(dict.fromkeys(blueprint.tool_requirements + [tool for subagent in blueprint.subagents for tool in subagent.allowed_tools]))
        policies: list[ToolPolicy] = []
        for tool in tool_names:
            tool_id = _tool_id(tool)
            write_like = any(keyword in tool.lower() for keyword in ["publish", "send", "database"])
            policies.append(
                ToolPolicy(
                    tool_id=tool_id,
                    name=tool,
                    description=f"Mock policy for {tool}",
                    adapter="mock",
                    permission_level=PermissionLevel.WRITE_REQUIRES_APPROVAL if write_like else PermissionLevel.DRAFT_ONLY,
                    risk_level=RiskLevel.HIGH if write_like else blueprint.risk_level,
                    requires_approval=write_like,
                    allowed_roles=["workflow-admin"] if write_like else [],
                    required_scopes=["workflow:approve"] if write_like else [],
                    input_schema={"type": "object", "additionalProperties": True},
                    output_schema={"type": "object", "additionalProperties": True},
                )
            )
        return policies

    def _agent_specs(
        self,
        blueprint: AgentSystemBlueprint,
        process_nodes: list[ProcessNode],
        workflow_id: str,
    ) -> list[AgentSpec]:
        agents: list[AgentSpec] = []
        for definition in [blueprint.mother_agent, *blueprint.subagents]:
            if definition is None:
                continue
            node = next(
                (item for item in process_nodes if item.assigned_agent_id == getattr(definition, "agent_id", None) or item.assigned_agent_id == getattr(definition, "subagent_id", None)),
                process_nodes[0],
            )
            agent_id = getattr(definition, "agent_id", None) or getattr(definition, "subagent_id")
            agents.append(
                AgentSpec(
                    agent_id=agent_id,
                    name=definition.name,
                    role=getattr(definition, "role", getattr(definition, "specialty", "subagent")),
                    goal=getattr(definition, "responsibility", getattr(definition, "description", blueprint.primary_goal)),
                    instructions=getattr(definition, "system_prompt", getattr(definition, "task_contract", "Follow the assigned contract.")),
                    input_contract_id=node.input_contract_id,
                    output_contract_id=node.output_contract_id,
                    tools=[_tool_id(tool) for tool in getattr(definition, "allowed_tools", [])],
                    guardrails=["schema_validation_required", "least_privilege_tools", "low_sensitive_trace"],
                    model_config={"provider": "mock", "model": "agent-system-builder"},
                )
            )
        if not agents:
            node = process_nodes[0]
            agents.append(
                AgentSpec(
                    agent_id=f"{workflow_id}-agent",
                    name=f"{blueprint.name} Agent",
                    role="agent-system",
                    goal=blueprint.primary_goal,
                    instructions="Complete the agent system workflow with schema-validated outputs.",
                    input_contract_id=node.input_contract_id,
                    output_contract_id=node.output_contract_id,
                    tools=[],
                    guardrails=["schema_validation_required"],
                    model_config={"provider": "mock", "model": "agent-system-builder"},
                )
            )
        return agents


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "agent-system"


def _tool_id(value: str) -> str:
    return f"tool-{_slug(value)}"
