from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Iterable

from jsonschema import Draft202012Validator, SchemaError

from packages.workflow_core.models import (
    WorkflowPackage,
    WorkflowValidationIssue,
    WorkflowValidationReport,
)
from packages.workflow_core.models.enums import EvalType, NodeType, PermissionLevel


class WorkflowPackageLinter:
    def lint(self, workflow_package: WorkflowPackage) -> WorkflowValidationReport:
        issues: list[WorkflowValidationIssue] = []
        issues.extend(self._duplicate_id_issues(workflow_package))
        issues.extend(self._graph_issues(workflow_package))
        issues.extend(self._tool_policy_issues(workflow_package))
        issues.extend(self._contract_schema_issues(workflow_package))
        issues.extend(self._agent_coverage_issues(workflow_package))
        issues.extend(self._eval_issues(workflow_package))

        errors = [issue for issue in issues if issue.severity == "error"]
        warnings = [issue for issue in issues if issue.severity == "warning"]
        return WorkflowValidationReport(valid=not errors, errors=errors, warnings=warnings)

    def _duplicate_id_issues(self, workflow_package: WorkflowPackage) -> list[WorkflowValidationIssue]:
        specs = [
            ("process_spec.nodes", [node.node_id for node in workflow_package.process_spec.nodes], "duplicate_node_id"),
            ("data_contracts", [contract.contract_id for contract in workflow_package.data_contracts], "duplicate_contract_id"),
            ("tool_policies", [tool.tool_id for tool in workflow_package.tool_policies], "duplicate_tool_id"),
            ("agent_specs", [agent.agent_id for agent in workflow_package.agent_specs], "duplicate_agent_id"),
            ("eval_specs", [eval_spec.eval_id for eval_spec in workflow_package.eval_specs], "duplicate_eval_id"),
        ]
        issues: list[WorkflowValidationIssue] = []
        for path, values, code in specs:
            for duplicate_id in self._duplicates(values):
                issues.append(self._error(code, path, f"duplicate id found: {duplicate_id}"))
        return issues

    def _graph_issues(self, workflow_package: WorkflowPackage) -> list[WorkflowValidationIssue]:
        process = workflow_package.process_spec
        issues: list[WorkflowValidationIssue] = []
        if not process.nodes:
            return [self._error("empty_process", "process_spec.nodes", "process must contain at least one node")]

        node_ids = {node.node_id for node in process.nodes}
        outgoing: dict[str, list[str]] = defaultdict(list)
        reverse: dict[str, list[str]] = defaultdict(list)
        for edge in process.edges:
            outgoing[edge.source_node_id].append(edge.target_node_id)
            reverse[edge.target_node_id].append(edge.source_node_id)

        reachable = self._walk(process.entry_node_id, outgoing)
        for node_id in sorted(node_ids - reachable):
            issues.append(self._error("unreachable_node", f"process_spec.nodes.{node_id}", "node is not reachable from entry"))

        terminal_ids = set(process.terminal_node_ids)
        can_reach_terminal = set(terminal_ids)
        queue: deque[str] = deque(terminal_ids)
        while queue:
            current = queue.popleft()
            for previous in reverse.get(current, []):
                if previous not in can_reach_terminal:
                    can_reach_terminal.add(previous)
                    queue.append(previous)
        for node_id in sorted(node_ids - can_reach_terminal):
            issues.append(self._error("terminal_not_reachable", f"process_spec.nodes.{node_id}", "node cannot reach a terminal node"))

        for node in process.nodes:
            if node.node_id not in terminal_ids and not outgoing.get(node.node_id):
                issues.append(self._error("dead_end_node", f"process_spec.nodes.{node.node_id}", "non-terminal node has no outgoing edge"))
            if node.node_id in terminal_ids and outgoing.get(node.node_id):
                issues.append(self._warning("terminal_has_outgoing_edge", f"process_spec.nodes.{node.node_id}", "terminal node has outgoing edges"))
        return issues

    def _tool_policy_issues(self, workflow_package: WorkflowPackage) -> list[WorkflowValidationIssue]:
        tool_by_id = {tool.tool_id: tool for tool in workflow_package.tool_policies}
        issues: list[WorkflowValidationIssue] = []
        for tool in workflow_package.tool_policies:
            if tool.adapter not in {"mock", "mcp"}:
                issues.append(
                    self._error(
                        "unknown_tool_adapter",
                        f"tool_policies.{tool.tool_id}.adapter",
                        "tool adapter must be mock or mcp",
                    )
                )
            if tool.adapter == "mcp" and (not tool.server_id or not tool.external_tool_name):
                issues.append(
                    self._error(
                        "mcp_tool_missing_binding",
                        f"tool_policies.{tool.tool_id}",
                        "mcp tools must declare server_id and external_tool_name",
                    )
                )
        for node in workflow_package.process_spec.nodes:
            node_tools = [tool_by_id[tool_id] for tool_id in node.tool_ids if tool_id in tool_by_id]
            write_tools = [
                tool for tool in node_tools
                if tool.permission_level == PermissionLevel.WRITE_REQUIRES_APPROVAL or tool.requires_approval
            ]
            forbidden_tools = [tool for tool in node_tools if tool.permission_level == PermissionLevel.FORBIDDEN]

            if forbidden_tools:
                issues.append(
                    self._error(
                        "forbidden_tool_bound_to_node",
                        f"process_spec.nodes.{node.node_id}.tool_ids",
                        f"node binds forbidden tools: {[tool.tool_id for tool in forbidden_tools]}",
                    )
                )
            if node.node_type == NodeType.WRITE and not write_tools:
                issues.append(self._error("write_node_without_write_tool", f"process_spec.nodes.{node.node_id}", "write_node must bind a write_requires_approval tool"))
            if write_tools and not node.requires_approval:
                issues.append(self._error("write_tool_without_node_approval", f"process_spec.nodes.{node.node_id}", "node with write tool must require approval"))
            if node.node_type == NodeType.READ and write_tools:
                issues.append(self._error("read_node_with_write_tool", f"process_spec.nodes.{node.node_id}", "read_node cannot bind write tools"))
            for tool in write_tools:
                if not tool.allowed_roles:
                    issues.append(self._error("write_tool_without_allowed_roles", f"tool_policies.{tool.tool_id}", "write tool must declare allowed_roles"))
        return issues

    def _contract_schema_issues(self, workflow_package: WorkflowPackage) -> list[WorkflowValidationIssue]:
        issues: list[WorkflowValidationIssue] = []
        for contract in workflow_package.data_contracts:
            issues.extend(self._check_schema(contract.input_schema, f"data_contracts.{contract.contract_id}.input_schema"))
            issues.extend(self._check_schema(contract.output_schema, f"data_contracts.{contract.contract_id}.output_schema"))
        for tool in workflow_package.tool_policies:
            issues.extend(self._check_schema(tool.input_schema, f"tool_policies.{tool.tool_id}.input_schema"))
            issues.extend(self._check_schema(tool.output_schema, f"tool_policies.{tool.tool_id}.output_schema"))
        return issues

    def _agent_coverage_issues(self, workflow_package: WorkflowPackage) -> list[WorkflowValidationIssue]:
        issues: list[WorkflowValidationIssue] = []
        agents_by_contract_pair = {
            (agent.input_contract_id, agent.output_contract_id)
            for agent in workflow_package.agent_specs
        }
        for node in workflow_package.process_spec.nodes:
            if (node.input_contract_id, node.output_contract_id) not in agents_by_contract_pair:
                issues.append(self._warning("node_without_matching_agent", f"process_spec.nodes.{node.node_id}", "no agent spec matches node input/output contracts"))
        return issues

    def _eval_issues(self, workflow_package: WorkflowPackage) -> list[WorkflowValidationIssue]:
        issues: list[WorkflowValidationIssue] = []
        if not workflow_package.eval_specs:
            return [self._error("missing_eval_specs", "eval_specs", "workflow must include eval specs before import or promotion")]
        if not any(eval_spec.eval_type == EvalType.END_TO_END for eval_spec in workflow_package.eval_specs):
            issues.append(self._warning("missing_end_to_end_eval", "eval_specs", "workflow should include at least one end-to-end eval"))
        return issues

    def _check_schema(self, schema: dict, path: str) -> list[WorkflowValidationIssue]:
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            return [self._error("invalid_json_schema", path, exc.message)]
        return []

    def _walk(self, start_node_id: str, outgoing: dict[str, list[str]]) -> set[str]:
        visited: set[str] = set()
        queue: deque[str] = deque([start_node_id])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            queue.extend(outgoing.get(current, []))
        return visited

    def _duplicates(self, values: Iterable[str]) -> list[str]:
        counts = Counter(values)
        return sorted(value for value, count in counts.items() if count > 1)

    def _error(self, code: str, path: str, message: str) -> WorkflowValidationIssue:
        return WorkflowValidationIssue(severity="error", code=code, path=path, message=message)

    def _warning(self, code: str, path: str, message: str) -> WorkflowValidationIssue:
        return WorkflowValidationIssue(severity="warning", code=code, path=path, message=message)
