from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from packages.workflow_core.adapters.llm import LLMClient
from packages.workflow_core.adapters.mock_llm import MockLLMClient
from packages.workflow_core.builder.llm_json import compact_json, extract_json_object, is_mock_llm
from packages.workflow_core.models import DataContract, ProcessSpec


class DataContractLLMOutput(BaseModel):
    node_id: str
    name: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    required_fields: list[str] | None = Field(default=None, max_length=30)
    validation_rules: list[str] | None = Field(default=None, max_length=30)
    error_policy: str | None = Field(default=None, max_length=1000)
    example_input: dict[str, Any] | None = None
    example_output: dict[str, Any] | None = None


class ContractDesignLLMOutput(BaseModel):
    contracts: list[DataContractLLMOutput] = Field(default_factory=list, max_length=30)


class ContractDesignerAgent:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or MockLLMClient()

    def design(self, process_spec: ProcessSpec) -> list[DataContract]:
        fallback_by_node_id = {
            node.node_id: self._contract_for_node(node_id=node.node_id, node_name=node.name)
            for node in process_spec.nodes
        }
        if is_mock_llm(self.llm):
            return list(fallback_by_node_id.values())
        try:
            llm_output = ContractDesignLLMOutput.model_validate(
                extract_json_object(self.llm.complete(self._build_prompt(process_spec)))
            )
        except (ValidationError, ValueError):
            return list(fallback_by_node_id.values())

        contracts_by_node_id = dict(fallback_by_node_id)
        for item in llm_output.contracts:
            fallback = fallback_by_node_id.get(item.node_id)
            if fallback is None:
                continue
            contracts_by_node_id[item.node_id] = self._merge_contract(fallback, item)
        return [contracts_by_node_id[node.node_id] for node in process_spec.nodes]

    def _contract_for_node(self, node_id: str, node_name: str) -> DataContract:
        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "context": {"type": "object"},
                "artifacts": {"type": "array", "items": {"type": "object"}},
                "assumptions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["context"],
        }
        output_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "decision": {"type": "string"},
                "risks": {"type": "array", "items": {"type": "string"}},
                "next_actions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "next_actions"],
        }
        return DataContract(
            contract_id=f"contract-{node_id}",
            name=f"{node_name} 数据契约",
            description=f"{node_name} 节点的输入输出结构、校验规则和异常策略。",
            input_schema=input_schema,
            output_schema=output_schema,
            required_fields=["context", "summary", "next_actions"],
            validation_rules=["context 必须存在", "summary 不能为空", "高风险结论必须写入 risks"],
            error_policy="缺少关键字段时暂停节点并记录 trace，等待人工补充或上游重跑。",
            example_input={"context": {"request_type": "workflow_operation"}, "artifacts": [], "assumptions": []},
            example_output={
                "summary": f"{node_name} 输出摘要",
                "decision": "continue",
                "risks": [],
                "next_actions": ["进入下一个节点"],
            },
        )

    def _merge_contract(self, fallback: DataContract, item: DataContractLLMOutput) -> DataContract:
        return fallback.model_copy(
            update={
                "name": item.name or fallback.name,
                "description": item.description or fallback.description,
                "input_schema": self._object_schema(item.input_schema, fallback.input_schema),
                "output_schema": self._object_schema(item.output_schema, fallback.output_schema),
                "required_fields": self._nonempty_list(item.required_fields, fallback.required_fields),
                "validation_rules": self._nonempty_list(item.validation_rules, fallback.validation_rules),
                "error_policy": item.error_policy or fallback.error_policy,
                "example_input": item.example_input or fallback.example_input,
                "example_output": item.example_output or fallback.example_output,
            }
        )

    def _build_prompt(self, process_spec: ProcessSpec) -> str:
        payload = {
            "workflow_id": process_spec.workflow_id,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "name": node.name,
                    "node_type": node.node_type,
                    "owner_role": node.owner_role,
                    "description": node.description,
                    "done_condition": node.done_condition,
                }
                for node in process_spec.nodes
            ],
        }
        return (
            "Design node-level JSON data contracts for this executable agent workflow. "
            "Return one JSON object with contracts. Each contract must include node_id and may include "
            "name, description, input_schema, output_schema, required_fields, validation_rules, "
            "error_policy, example_input, and example_output. Schemas must be JSON Schema objects. "
            f"Input: {compact_json(payload)}"
        )

    def _object_schema(self, value: dict[str, Any] | None, fallback: dict[str, Any]) -> dict[str, Any]:
        if not value or value.get("type") != "object":
            return fallback
        return value

    def _nonempty_list(self, value: list[str] | None, fallback: list[str]) -> list[str]:
        items = [item.strip() for item in value or [] if item.strip()]
        return items or fallback
