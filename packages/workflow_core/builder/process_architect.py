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
                node_id="define-launch-goal",
                name="定义上市目标",
                node_type=NodeType.REASONING,
                owner_role="产品经理",
                description="明确新品上市目标、目标市场、核心成功指标和约束。",
                done_condition="上市目标、目标用户、成功指标和关键约束完整。",
                input_contract_id="contract-define-launch-goal",
                output_contract_id="contract-define-launch-goal",
            ),
            ProcessNode(
                node_id="collect-market-insights",
                name="收集市场洞察",
                node_type=NodeType.READ,
                owner_role="市场负责人",
                description="汇总市场、竞品、用户需求和渠道信号。",
                done_condition="洞察摘要覆盖目标市场、竞品和用户痛点。",
                input_contract_id="contract-collect-market-insights",
                output_contract_id="contract-collect-market-insights",
            ),
            ProcessNode(
                node_id="product-positioning",
                name="产品定位",
                node_type=NodeType.REASONING,
                owner_role="产品营销",
                description="生成目标人群、差异化卖点、价值主张和叙事。",
                done_condition="定位陈述、主卖点和风险假设通过校验。",
                input_contract_id="contract-product-positioning",
                output_contract_id="contract-product-positioning",
            ),
            ProcessNode(
                node_id="channel-pricing",
                name="渠道定价",
                node_type=NodeType.REASONING,
                owner_role="渠道负责人",
                description="生成渠道策略、价格区间、促销限制和毛利假设。",
                done_condition="渠道策略与价格建议包含依据和风险。",
                input_contract_id="contract-channel-pricing",
                output_contract_id="contract-channel-pricing",
            ),
            ProcessNode(
                node_id="business-case",
                name="商业论证",
                node_type=NodeType.REVIEW,
                owner_role="财务与业务负责人",
                description="审查目标、洞察、定位、定价是否支撑商业论证。",
                done_condition="商业论证给出通过、打回或补充材料结论。",
                requires_approval=True,
                input_contract_id="contract-business-case",
                output_contract_id="contract-business-case",
            ),
            ProcessNode(
                node_id="global-launch-check",
                name="全球操盘检查",
                node_type=NodeType.READ,
                owner_role="全球运营",
                description="检查区域、本地化、合规、库存、支持与上市节奏。",
                done_condition="全球上市检查清单无阻断项或已标注风险。",
                input_contract_id="contract-global-launch-check",
                output_contract_id="contract-global-launch-check",
            ),
            ProcessNode(
                node_id="go-no-go-decision",
                name="Go / No-Go 决策",
                node_type=NodeType.WRITE,
                owner_role="业务审批人",
                description="生成决策草稿并等待人工审批后才允许写入外部系统。",
                done_condition="Go / No-Go 决策完成审批并产出审计记录。",
                requires_approval=True,
                input_contract_id="contract-go-no-go-decision",
                output_contract_id="contract-go-no-go-decision",
            ),
        ]
        edges = [
            ProcessEdge(source_node_id="define-launch-goal", target_node_id="collect-market-insights"),
            ProcessEdge(source_node_id="collect-market-insights", target_node_id="product-positioning"),
            ProcessEdge(source_node_id="product-positioning", target_node_id="channel-pricing"),
            ProcessEdge(source_node_id="channel-pricing", target_node_id="business-case"),
            ProcessEdge(
                source_node_id="business-case",
                target_node_id="product-positioning",
                condition="商业论证不通过，打回定位修正",
                edge_type=EdgeType.REJECT,
            ),
            ProcessEdge(
                source_node_id="business-case",
                target_node_id="global-launch-check",
                condition="商业论证通过",
                edge_type=EdgeType.CONDITIONAL,
            ),
            ProcessEdge(source_node_id="global-launch-check", target_node_id="go-no-go-decision"),
        ]
        return ProcessSpec(
            id=f"{workflow_id}-process",
            workflow_id=workflow_id,
            nodes=nodes,
            edges=edges,
            entry_node_id="define-launch-goal",
            terminal_node_ids=["go-no-go-decision"],
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
