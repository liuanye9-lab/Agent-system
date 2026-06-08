from __future__ import annotations

import re

from pydantic import BaseModel, Field, ValidationError

from packages.workflow_core.adapters import LLMClient, MockLLMClient
from packages.workflow_core.builder.llm_json import compact_json, extract_json_object, is_mock_llm
from packages.workflow_core.models import ProblemSpec, ProcessEdge, ProcessNode, ProcessSpec
from packages.workflow_core.models.enums import EdgeType, NodeType


class ProcessNodeLLMOutput(BaseModel):
    node_id: str | None = None
    name: str = Field(min_length=1, max_length=160)
    node_type: NodeType
    owner_role: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    done_condition: str = Field(min_length=1, max_length=1000)
    requires_approval: bool = False


class ProcessEdgeLLMOutput(BaseModel):
    source_node_id: str
    target_node_id: str
    condition: str | None = None
    edge_type: EdgeType = EdgeType.DEFAULT


class ProcessArchitectureLLMOutput(BaseModel):
    nodes: list[ProcessNodeLLMOutput] = Field(min_length=2, max_length=20)
    edges: list[ProcessEdgeLLMOutput] = Field(default_factory=list, max_length=60)
    entry_node_id: str | None = None
    terminal_node_ids: list[str] | None = Field(default=None, max_length=5)


class ProcessArchitectAgent:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or MockLLMClient()

    def design(self, problem_spec: ProblemSpec, workflow_id: str, version: str = "0.1.0") -> ProcessSpec:
        fallback = self._deterministic_design(problem_spec, workflow_id, version)
        if is_mock_llm(self.llm):
            return fallback
        try:
            llm_output = ProcessArchitectureLLMOutput.model_validate(
                extract_json_object(self.llm.complete(self._build_prompt(problem_spec, workflow_id, version)))
            )
            return self._process_from_llm_output(llm_output, workflow_id, version)
        except (ValidationError, ValueError):
            return fallback

    def _deterministic_design(self, problem_spec: ProblemSpec, workflow_id: str, version: str) -> ProcessSpec:
        nodes = [
            ProcessNode(
                node_id="intake-business-request",
                name="接收业务请求",
                node_type=NodeType.REASONING,
                owner_role="业务 Owner",
                description="明确流程目标、触发事件、参与角色、关键约束和成功指标。",
                done_condition="业务目标、起点、终点、角色和成功指标完整。",
                input_contract_id="contract-intake-business-request",
                output_contract_id="contract-intake-business-request",
            ),
            ProcessNode(
                node_id="gather-operational-context",
                name="汇总运营上下文",
                node_type=NodeType.READ,
                owner_role="流程运营",
                description="汇总相关系统记录、历史处理材料、约束条件和待确认事项。",
                done_condition="上下文、材料、假设和缺口被结构化记录。",
                input_contract_id="contract-gather-operational-context",
                output_contract_id="contract-gather-operational-context",
            ),
            ProcessNode(
                node_id="design-execution-plan",
                name="设计执行方案",
                node_type=NodeType.REASONING,
                owner_role="流程架构师",
                description="基于业务目标和上下文生成节点化执行方案、风险假设和交付物草案。",
                done_condition="执行方案包含节点、责任人、数据契约、风险和下一步动作。",
                input_contract_id="contract-design-execution-plan",
                output_contract_id="contract-design-execution-plan",
            ),
            ProcessNode(
                node_id="governance-review",
                name="治理审查",
                node_type=NodeType.REVIEW,
                owner_role="治理负责人",
                description="审查执行方案的数据契约、工具权限、审批点、评估覆盖和发布风险。",
                done_condition="审查给出通过、打回或补充材料结论，并记录阻塞原因。",
                requires_approval=True,
                input_contract_id="contract-governance-review",
                output_contract_id="contract-governance-review",
            ),
            ProcessNode(
                node_id="publish-control-decision",
                name="发布控制决策",
                node_type=NodeType.WRITE,
                owner_role="业务审批人",
                description="生成发布或执行决策草稿，等待人工审批后才允许写入外部系统或生效。",
                done_condition="发布控制决策完成审批，并产出低敏审计记录。",
                requires_approval=True,
                input_contract_id="contract-publish-control-decision",
                output_contract_id="contract-publish-control-decision",
            ),
        ]
        edges = [
            ProcessEdge(source_node_id="intake-business-request", target_node_id="gather-operational-context"),
            ProcessEdge(source_node_id="gather-operational-context", target_node_id="design-execution-plan"),
            ProcessEdge(source_node_id="design-execution-plan", target_node_id="governance-review"),
            ProcessEdge(
                source_node_id="governance-review",
                target_node_id="design-execution-plan",
                condition="治理审查不通过，打回方案修正",
                edge_type=EdgeType.REJECT,
            ),
            ProcessEdge(
                source_node_id="governance-review",
                target_node_id="publish-control-decision",
                condition="治理审查通过",
                edge_type=EdgeType.CONDITIONAL,
            ),
        ]
        return ProcessSpec(
            id=f"{workflow_id}-process",
            workflow_id=workflow_id,
            nodes=nodes,
            edges=edges,
            entry_node_id="intake-business-request",
            terminal_node_ids=["publish-control-decision"],
            version=version,
        )

    def _process_from_llm_output(
        self,
        llm_output: ProcessArchitectureLLMOutput,
        workflow_id: str,
        version: str,
    ) -> ProcessSpec:
        seen_ids: set[str] = set()
        id_mapping: dict[str, str] = {}
        nodes: list[ProcessNode] = []
        for index, item in enumerate(llm_output.nodes, start=1):
            original_id = item.node_id or item.name
            node_id = self._unique_node_id(original_id, seen_ids, index)
            id_mapping[original_id] = node_id
            nodes.append(
                ProcessNode(
                    node_id=node_id,
                    name=item.name,
                    node_type=item.node_type,
                    owner_role=item.owner_role,
                    description=item.description,
                    done_condition=item.done_condition,
                    requires_approval=item.requires_approval or item.node_type in {NodeType.REVIEW, NodeType.WRITE},
                    input_contract_id=f"contract-{node_id}",
                    output_contract_id=f"contract-{node_id}",
                )
            )
        edges = [
            ProcessEdge(
                source_node_id=id_mapping.get(edge.source_node_id, edge.source_node_id),
                target_node_id=id_mapping.get(edge.target_node_id, edge.target_node_id),
                condition=edge.condition,
                edge_type=edge.edge_type,
            )
            for edge in llm_output.edges
        ]
        if not edges:
            edges = [
                ProcessEdge(source_node_id=source.node_id, target_node_id=target.node_id)
                for source, target in zip(nodes, nodes[1:])
            ]
        entry_node_id = id_mapping.get(llm_output.entry_node_id or "", llm_output.entry_node_id) or nodes[0].node_id
        terminal_node_ids = [
            id_mapping.get(node_id, node_id)
            for node_id in (llm_output.terminal_node_ids or [nodes[-1].node_id])
        ]
        return ProcessSpec(
            id=f"{workflow_id}-process",
            workflow_id=workflow_id,
            nodes=nodes,
            edges=edges,
            entry_node_id=entry_node_id,
            terminal_node_ids=terminal_node_ids,
            version=version,
        )

    def _build_prompt(self, problem_spec: ProblemSpec, workflow_id: str, version: str) -> str:
        payload = {
            "workflow_id": workflow_id,
            "version": version,
            "problem_spec": problem_spec.model_dump(mode="json"),
            "allowed_node_types": [item.value for item in NodeType],
            "allowed_edge_types": [item.value for item in EdgeType],
        }
        return (
            "Design a project-grade executable agent workflow process. "
            "Return one JSON object with nodes, edges, entry_node_id, and terminal_node_ids. "
            "Use stable node_id values, 3 to 12 nodes, explicit owners, descriptions, done conditions, "
            "review/write approvals where needed, and edges that form a reachable DAG. "
            f"Input: {compact_json(payload)}"
        )

    def _unique_node_id(self, value: str, seen_ids: set[str], index: int) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or f"node-{index}"
        base = base[:48]
        candidate = base
        suffix = 2
        while candidate in seen_ids:
            candidate = f"{base}-{suffix}"[:64]
            suffix += 1
        seen_ids.add(candidate)
        return candidate
