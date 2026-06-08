from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from packages.workflow_core.models.agent import AgentSpec
from packages.workflow_core.models.common import StrictBaseModel, utc_now
from packages.workflow_core.models.contract import DataContract
from packages.workflow_core.models.eval import EvalSpec
from packages.workflow_core.models.problem import ProblemSpec
from packages.workflow_core.models.process import ProcessSpec
from packages.workflow_core.models.tool import ToolPolicy


class WorkflowPackage(StrictBaseModel):
    workflow_id: str
    name: str
    version: str
    problem_spec: ProblemSpec
    process_spec: ProcessSpec
    data_contracts: list[DataContract]
    tool_policies: list[ToolPolicy]
    agent_specs: list[AgentSpec]
    eval_specs: list[EvalSpec]
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_package_references(self) -> "WorkflowPackage":
        if self.process_spec.workflow_id != self.workflow_id:
            raise ValueError("process_spec.workflow_id must match workflow_id")

        contract_ids = {contract.contract_id for contract in self.data_contracts}
        tool_ids = {tool.tool_id for tool in self.tool_policies}
        node_ids = {node.node_id for node in self.process_spec.nodes}

        for node in self.process_spec.nodes:
            if node.input_contract_id not in contract_ids:
                raise ValueError(f"node {node.node_id} references unknown input contract")
            if node.output_contract_id not in contract_ids:
                raise ValueError(f"node {node.node_id} references unknown output contract")
            missing_tools = set(node.tool_ids) - tool_ids
            if missing_tools:
                raise ValueError(f"node {node.node_id} references unknown tools: {sorted(missing_tools)}")

        for agent in self.agent_specs:
            if agent.input_contract_id not in contract_ids or agent.output_contract_id not in contract_ids:
                raise ValueError(f"agent {agent.agent_id} references unknown contracts")
            missing_tools = set(agent.tools) - tool_ids
            if missing_tools:
                raise ValueError(f"agent {agent.agent_id} references unknown tools: {sorted(missing_tools)}")

        for eval_spec in self.eval_specs:
            if eval_spec.workflow_id != self.workflow_id:
                raise ValueError("eval_spec.workflow_id must match workflow_id")
            if eval_spec.target_node_id and eval_spec.target_node_id not in node_ids:
                raise ValueError(f"eval {eval_spec.eval_id} references unknown target node")

        return self
