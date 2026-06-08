from __future__ import annotations

import re
from dataclasses import dataclass

from packages.workflow_core.adapters import LLMClient, MockLLMClient
from packages.workflow_core.builder.contract_designer import ContractDesignerAgent
from packages.workflow_core.builder.eval_generator import EvalGeneratorAgent
from packages.workflow_core.builder.problem_framer import ProblemFramerAgent
from packages.workflow_core.builder.process_architect import ProcessArchitectAgent
from packages.workflow_core.builder.tool_mapper import ToolMapperAgent
from packages.workflow_core.models import AgentSpec, ProblemSpec, ProcessEdge, ProcessNode, ProcessSpec, WorkflowPackage
from packages.workflow_core.models.enums import NodeType


@dataclass(frozen=True)
class WorkflowBuildResult:
    workflow_package: WorkflowPackage
    clarifying_questions: list[str]


@dataclass(frozen=True)
class WorkflowBuildNode:
    name: str
    node_type: NodeType
    owner_role: str
    description: str | None = None
    done_condition: str | None = None
    requires_approval: bool = False


@dataclass(frozen=True)
class WorkflowBuildBrief:
    workflow_id: str | None = None
    name: str | None = None
    business_goal: str | None = None
    start_event: str | None = None
    end_state: str | None = None
    target_users: list[str] | None = None
    human_roles: list[str] | None = None
    success_metrics: list[str] | None = None
    constraints: list[str] | None = None
    risks: list[str] | None = None
    process_nodes: list[WorkflowBuildNode] | None = None


class WorkflowBuilder:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or MockLLMClient()
        self.problem_framer = ProblemFramerAgent(self.llm)
        self.process_architect = ProcessArchitectAgent(self.llm)
        self.contract_designer = ContractDesignerAgent(self.llm)
        self.tool_mapper = ToolMapperAgent(self.llm)
        self.eval_generator = EvalGeneratorAgent(self.llm)

    def generate(
        self,
        user_request: str,
        version: str = "0.1.0",
        brief: WorkflowBuildBrief | None = None,
    ) -> WorkflowBuildResult:
        workflow_id = self._workflow_id(user_request, brief.workflow_id if brief else None)
        frame = self.problem_framer.frame(user_request, workflow_id)
        problem_spec = self._apply_brief_to_problem_spec(frame.problem_spec, user_request, brief)
        process_spec = (
            self._process_from_brief(brief, workflow_id, version)
            if brief and brief.process_nodes
            else self.process_architect.design(problem_spec, workflow_id, version)
        )
        data_contracts = self.contract_designer.design(process_spec)
        process_spec, tool_policies = self.tool_mapper.map_tools(process_spec)
        agent_specs = [
            AgentSpec(
                agent_id=f"agent-{node.node_id}",
                name=f"{node.name} Agent",
                role=node.owner_role,
                goal=node.done_condition,
                instructions=(
                    f"执行 {node.name}。必须遵守输入输出数据契约，输出结构化 JSON，"
                    "不得绕过 sandbox 或审批直接调用外部写操作。"
                ),
                input_contract_id=node.input_contract_id,
                output_contract_id=node.output_contract_id,
                tools=node.tool_ids,
                guardrails=["schema_validation_required", "sandboxed_tools_only", "approval_required_for_write"],
                model_config=self._agent_model_config(),
            )
            for node in process_spec.nodes
        ]
        eval_target_node_id = self._eval_target_node_id(process_spec)
        eval_specs = self.eval_generator.generate(
            workflow_id,
            target_node_id=eval_target_node_id,
            process_spec=process_spec,
            problem_spec=problem_spec,
        )
        workflow_package = WorkflowPackage(
            workflow_id=workflow_id,
            name=problem_spec.title,
            version=version,
            problem_spec=problem_spec,
            process_spec=process_spec,
            data_contracts=data_contracts,
            tool_policies=tool_policies,
            agent_specs=agent_specs,
            eval_specs=eval_specs,
        )
        return WorkflowBuildResult(
            workflow_package=workflow_package,
            clarifying_questions=frame.clarifying_questions,
        )

    def _workflow_id(self, user_request: str, explicit_workflow_id: str | None = None) -> str:
        if explicit_workflow_id:
            return self._slug(explicit_workflow_id, fallback="workflow-draft")[:64]
        if "新品" in user_request or "launch" in user_request.lower():
            return "new-product-launch"
        return self._slug(user_request, fallback="workflow-draft")[:48]

    def _apply_brief_to_problem_spec(
        self,
        problem_spec: ProblemSpec,
        user_request: str,
        brief: WorkflowBuildBrief | None,
    ) -> ProblemSpec:
        if brief is None:
            return problem_spec
        updates = {
            "title": brief.name or problem_spec.title,
            "description": user_request,
            "business_goal": brief.business_goal or problem_spec.business_goal,
            "start_event": brief.start_event or problem_spec.start_event,
            "end_state": brief.end_state or problem_spec.end_state,
            "target_users": brief.target_users or problem_spec.target_users,
            "human_roles": brief.human_roles or problem_spec.human_roles,
            "success_metrics": brief.success_metrics or problem_spec.success_metrics,
            "constraints": brief.constraints or problem_spec.constraints,
            "risks": brief.risks or problem_spec.risks,
        }
        return problem_spec.model_copy(update=updates)

    def _process_from_brief(
        self,
        brief: WorkflowBuildBrief,
        workflow_id: str,
        version: str,
    ) -> ProcessSpec:
        assert brief.process_nodes
        seen_ids: set[str] = set()
        nodes: list[ProcessNode] = []
        for index, node in enumerate(brief.process_nodes, start=1):
            node_id = self._unique_node_id(node.name, seen_ids, index)
            nodes.append(
                ProcessNode(
                    node_id=node_id,
                    name=node.name,
                    node_type=node.node_type,
                    owner_role=node.owner_role,
                    description=node.description or f"{node.name} 节点负责承接输入、执行判断并输出结构化结果。",
                    done_condition=node.done_condition or f"{node.name} 输出通过数据契约校验并可交接到下一节点。",
                    requires_approval=node.requires_approval or node.node_type == NodeType.WRITE,
                    input_contract_id=f"contract-{node_id}",
                    output_contract_id=f"contract-{node_id}",
                )
            )
        edges = [
            ProcessEdge(source_node_id=source.node_id, target_node_id=target.node_id)
            for source, target in zip(nodes, nodes[1:])
        ]
        return ProcessSpec(
            id=f"{workflow_id}-process",
            workflow_id=workflow_id,
            nodes=nodes,
            edges=edges,
            entry_node_id=nodes[0].node_id,
            terminal_node_ids=[nodes[-1].node_id],
            version=version,
        )

    def _eval_target_node_id(self, process_spec: ProcessSpec) -> str | None:
        review_node = next((node for node in process_spec.nodes if node.node_type == NodeType.REVIEW), None)
        if review_node:
            return review_node.node_id
        return process_spec.terminal_node_ids[0] if process_spec.terminal_node_ids else None

    def _agent_model_config(self) -> dict[str, str]:
        return {
            "provider": getattr(self.llm, "provider", "custom"),
            "model": getattr(self.llm, "model", "unknown"),
        }

    def _unique_node_id(self, name: str, seen_ids: set[str], index: int) -> str:
        base = self._slug(name, fallback=f"node-{index}")[:48]
        candidate = base
        suffix = 2
        while candidate in seen_ids:
            candidate = f"{base}-{suffix}"[:64]
            suffix += 1
        seen_ids.add(candidate)
        return candidate

    def _slug(self, value: str, fallback: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug or fallback
