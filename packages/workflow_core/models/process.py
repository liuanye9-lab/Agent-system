from __future__ import annotations

from pydantic import Field, model_validator

from packages.workflow_core.models.common import StrictBaseModel
from packages.workflow_core.models.enums import EdgeType, NodeType


class ProcessNode(StrictBaseModel):
    node_id: str
    name: str
    node_type: NodeType
    owner_role: str
    description: str
    done_condition: str
    requires_approval: bool = False
    input_contract_id: str
    output_contract_id: str
    tool_ids: list[str] = Field(default_factory=list)
    assigned_agent_id: str | None = None
    context_policy: str | None = None


class ProcessEdge(StrictBaseModel):
    source_node_id: str
    target_node_id: str
    condition: str | None = None
    edge_type: EdgeType = EdgeType.DEFAULT


class ProcessSpec(StrictBaseModel):
    id: str
    workflow_id: str
    nodes: list[ProcessNode]
    edges: list[ProcessEdge] = Field(default_factory=list)
    entry_node_id: str
    terminal_node_ids: list[str]
    version: str

    @model_validator(mode="after")
    def validate_graph_references(self) -> "ProcessSpec":
        node_ids = {node.node_id for node in self.nodes}
        if self.entry_node_id not in node_ids:
            raise ValueError("entry_node_id must reference an existing node")
        missing_terminal_ids = set(self.terminal_node_ids) - node_ids
        if missing_terminal_ids:
            raise ValueError(f"terminal_node_ids reference unknown nodes: {sorted(missing_terminal_ids)}")
        for edge in self.edges:
            if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
                raise ValueError("edges must reference existing nodes")
        return self
