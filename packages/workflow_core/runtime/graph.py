from __future__ import annotations

from dataclasses import dataclass

from packages.workflow_core.models import ProcessEdge, ProcessNode, ProcessSpec
from packages.workflow_core.models.enums import EdgeType


@dataclass(frozen=True)
class ExecutableGraph:
    entry_node_id: str
    terminal_node_ids: set[str]
    nodes: dict[str, ProcessNode]
    outgoing_edges: dict[str, list[ProcessEdge]]

    @classmethod
    def from_process_spec(cls, process_spec: ProcessSpec) -> "ExecutableGraph":
        outgoing_edges: dict[str, list[ProcessEdge]] = {node.node_id: [] for node in process_spec.nodes}
        for edge in process_spec.edges:
            outgoing_edges.setdefault(edge.source_node_id, []).append(edge)
        return cls(
            entry_node_id=process_spec.entry_node_id,
            terminal_node_ids=set(process_spec.terminal_node_ids),
            nodes={node.node_id: node for node in process_spec.nodes},
            outgoing_edges=outgoing_edges,
        )

    def next_node_id(self, node_id: str, node_output: dict) -> str | None:
        if node_id in self.terminal_node_ids:
            return None

        edges = self.outgoing_edges.get(node_id, [])
        if not edges:
            return None

        decision = str(node_output.get("decision", "")).lower()
        if decision in {"reject", "rework", "打回"}:
            reject_edges = [edge for edge in edges if edge.edge_type == EdgeType.REJECT]
            if reject_edges:
                return reject_edges[0].target_node_id

        preferred_edges = [edge for edge in edges if edge.edge_type != EdgeType.REJECT]
        return (preferred_edges or edges)[0].target_node_id
