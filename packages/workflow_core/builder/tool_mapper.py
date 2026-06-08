from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from packages.workflow_core.adapters import LLMClient, MockLLMClient
from packages.workflow_core.builder.llm_json import compact_json, extract_json_object, is_mock_llm
from packages.workflow_core.models import ProcessSpec, ToolPolicy
from packages.workflow_core.models.enums import NodeType, PermissionLevel, RiskLevel


class ToolPolicyLLMOutput(BaseModel):
    node_id: str
    tool_id: str | None = Field(default=None, max_length=160)
    name: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    adapter: str = "mock"
    server_id: str | None = Field(default=None, max_length=160)
    external_tool_name: str | None = Field(default=None, max_length=160)
    permission_level: PermissionLevel | None = None
    risk_level: RiskLevel | None = None
    required_scopes: list[str] | None = Field(default=None, max_length=20)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class ToolMappingLLMOutput(BaseModel):
    tools: list[ToolPolicyLLMOutput] = Field(default_factory=list, max_length=40)


class ToolMapperAgent:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or MockLLMClient()

    def map_tools(self, process_spec: ProcessSpec) -> tuple[ProcessSpec, list[ToolPolicy]]:
        fallback_tools_by_node_id = {
            node.node_id: self._tool_policy(
                f"mock-{node.node_id}-tool",
                node.name,
                *self._permission_for_node_type(node.node_type),
                node.owner_role,
            )
            for node in process_spec.nodes
        }
        llm_tools_by_node_id: dict[str, ToolPolicyLLMOutput] = {}
        if not is_mock_llm(self.llm):
            try:
                llm_output = ToolMappingLLMOutput.model_validate(
                    extract_json_object(self.llm.complete(self._build_prompt(process_spec)))
                )
                llm_tools_by_node_id = {item.node_id: item for item in llm_output.tools}
            except (ValidationError, ValueError):
                llm_tools_by_node_id = {}

        tool_policies_by_node_id: dict[str, ToolPolicy] = {}
        updated_nodes = []
        for node in process_spec.nodes:
            fallback_tool = fallback_tools_by_node_id[node.node_id]
            tool_policy = self._merge_tool_policy(
                fallback_tool,
                llm_tools_by_node_id.get(node.node_id),
                node.node_type,
                node.owner_role,
            )
            tool_policies_by_node_id[node.node_id] = tool_policy
            updated_nodes.append(
                node.model_copy(
                    update={
                        "tool_ids": [tool_policy.tool_id],
                        "requires_approval": node.requires_approval
                        or tool_policy.permission_level == PermissionLevel.WRITE_REQUIRES_APPROVAL
                        or tool_policy.requires_approval,
                    }
                )
            )
        updated_process = process_spec.model_copy(update={"nodes": updated_nodes})
        return updated_process, [tool_policies_by_node_id[node.node_id] for node in process_spec.nodes]

    def _permission_for_node_type(self, node_type: NodeType) -> tuple[PermissionLevel, RiskLevel]:
        if node_type == NodeType.READ:
            return PermissionLevel.READ_ONLY, RiskLevel.LOW
        if node_type == NodeType.WRITE:
            return PermissionLevel.WRITE_REQUIRES_APPROVAL, RiskLevel.HIGH
        if node_type == NodeType.REVIEW:
            return PermissionLevel.DRAFT_ONLY, RiskLevel.MEDIUM
        return PermissionLevel.DRAFT_ONLY, RiskLevel.MEDIUM

    def _tool_policy(
        self,
        tool_id: str,
        node_name: str,
        permission_level: PermissionLevel,
        risk_level: RiskLevel,
        owner_role: str,
    ) -> ToolPolicy:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"payload": {"type": "object"}},
            "required": ["payload"],
        }
        return ToolPolicy(
            tool_id=tool_id,
            name=f"{node_name} Mock Tool",
            description=f"{node_name} 的沙箱 mock 工具，不访问真实外部系统。",
            permission_level=permission_level,
            risk_level=risk_level,
            requires_approval=permission_level == PermissionLevel.WRITE_REQUIRES_APPROVAL,
            allowed_roles=self._allowed_roles(owner_role, permission_level),
            input_schema=schema,
            output_schema={
                "type": "object",
                "properties": {
                    "tool_id": {"type": "string"},
                    "status": {"type": "string"},
                    "result": {"type": "object"},
                },
            },
        )

    def _allowed_roles(self, owner_role: str, permission_level: PermissionLevel) -> list[str]:
        roles = [owner_role, "workflow-admin"]
        if permission_level == PermissionLevel.WRITE_REQUIRES_APPROVAL:
            roles.append("business-approver")
        return roles

    def _merge_tool_policy(
        self,
        fallback: ToolPolicy,
        item: ToolPolicyLLMOutput | None,
        node_type: NodeType,
        owner_role: str,
    ) -> ToolPolicy:
        if item is None:
            return fallback
        baseline_permission, baseline_risk = self._permission_for_node_type(node_type)
        permission = self._safe_permission(item.permission_level, baseline_permission, node_type)
        risk = self._safe_risk(item.risk_level, baseline_risk, permission)
        adapter = item.adapter if item.adapter in {"mock", "mcp"} else "mock"
        server_id = item.server_id if adapter == "mcp" and item.server_id and item.external_tool_name else None
        external_tool_name = (
            item.external_tool_name if adapter == "mcp" and item.server_id and item.external_tool_name else None
        )
        if adapter == "mcp" and not (server_id and external_tool_name):
            adapter = "mock"
        return fallback.model_copy(
            update={
                "tool_id": item.tool_id or fallback.tool_id,
                "name": item.name or fallback.name,
                "description": item.description or fallback.description,
                "adapter": adapter,
                "server_id": server_id,
                "external_tool_name": external_tool_name,
                "permission_level": permission,
                "risk_level": risk,
                "requires_approval": permission == PermissionLevel.WRITE_REQUIRES_APPROVAL,
                "allowed_roles": self._allowed_roles(owner_role, permission),
                "required_scopes": self._nonempty_list(item.required_scopes, fallback.required_scopes),
                "input_schema": self._object_schema(item.input_schema, fallback.input_schema),
                "output_schema": self._object_schema(item.output_schema, fallback.output_schema),
            }
        )

    def _safe_permission(
        self,
        value: PermissionLevel | None,
        fallback: PermissionLevel,
        node_type: NodeType,
    ) -> PermissionLevel:
        if node_type == NodeType.WRITE:
            return PermissionLevel.WRITE_REQUIRES_APPROVAL
        if node_type == NodeType.READ and value == PermissionLevel.WRITE_REQUIRES_APPROVAL:
            return fallback
        if value == PermissionLevel.FORBIDDEN:
            return fallback
        return value or fallback

    def _safe_risk(
        self,
        value: RiskLevel | None,
        fallback: RiskLevel,
        permission: PermissionLevel,
    ) -> RiskLevel:
        if permission == PermissionLevel.WRITE_REQUIRES_APPROVAL:
            return RiskLevel.HIGH
        return value or fallback

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
                    "requires_approval": node.requires_approval,
                }
                for node in process_spec.nodes
            ],
            "allowed_adapters": ["mock", "mcp"],
            "allowed_permission_levels": [item.value for item in PermissionLevel],
            "allowed_risk_levels": [item.value for item in RiskLevel],
        }
        return (
            "Map each workflow node to one sandboxed tool policy. Return one JSON object with tools. "
            "Each tool must include node_id and may include tool_id, name, description, adapter, server_id, "
            "external_tool_name, permission_level, risk_level, required_scopes, input_schema, and output_schema. "
            "Write tools must require approval; MCP tools must include server_id and external_tool_name. "
            f"Input: {compact_json(payload)}"
        )

    def _object_schema(self, value: dict[str, Any] | None, fallback: dict[str, Any]) -> dict[str, Any]:
        if not value or value.get("type") != "object":
            return fallback
        return value

    def _nonempty_list(self, value: list[str] | None, fallback: list[str]) -> list[str]:
        items = [item.strip() for item in value or [] if item.strip()]
        return items or fallback
