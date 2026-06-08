from __future__ import annotations

import re
from typing import Any

from pydantic import Field

from packages.workflow_core.models import ToolPolicy
from packages.workflow_core.models.common import StrictBaseModel
from packages.workflow_core.models.enums import PermissionLevel, RiskLevel


class MCPToolDescriptor(StrictBaseModel):
    server_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1000)
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    permission_level: PermissionLevel
    risk_level: RiskLevel = RiskLevel.MEDIUM
    requires_approval: bool | None = None
    allowed_roles: list[str] = Field(default_factory=list)
    required_scopes: list[str] = Field(default_factory=list)


class MCPToolAdapter:
    """Bind MCP-like tool descriptors into workflow ToolPolicy records."""

    def bind_tool(self, descriptor: MCPToolDescriptor | dict[str, Any], tool_id: str | None = None) -> ToolPolicy:
        descriptor = (
            descriptor
            if isinstance(descriptor, MCPToolDescriptor)
            else MCPToolDescriptor.model_validate(descriptor)
        )
        requires_approval = (
            descriptor.requires_approval
            if descriptor.requires_approval is not None
            else descriptor.permission_level == PermissionLevel.WRITE_REQUIRES_APPROVAL
        )
        return ToolPolicy(
            tool_id=tool_id or self._tool_id(descriptor.server_id, descriptor.name),
            name=descriptor.name,
            description=descriptor.description,
            adapter="mcp",
            server_id=descriptor.server_id,
            external_tool_name=descriptor.name,
            permission_level=descriptor.permission_level,
            risk_level=descriptor.risk_level,
            requires_approval=requires_approval,
            allowed_roles=descriptor.allowed_roles,
            required_scopes=descriptor.required_scopes,
            input_schema=self._input_envelope_schema(descriptor.input_schema),
            output_schema=self._output_envelope_schema(descriptor.output_schema),
        )

    def bind_many(self, descriptors: list[MCPToolDescriptor | dict[str, Any]]) -> list[ToolPolicy]:
        return [self.bind_tool(descriptor) for descriptor in descriptors]

    def _tool_id(self, server_id: str, tool_name: str) -> str:
        return f"mcp-{self._slug(server_id)}-{self._slug(tool_name)}"[:120]

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug or "tool"

    def _input_envelope_schema(self, payload_schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "payload": payload_schema,
                "node_id": {"type": "string"},
                "approval_granted": {"type": "boolean"},
                "shadow_mode": {"type": "boolean"},
            },
            "required": ["payload"],
        }

    def _output_envelope_schema(self, result_schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_id": {"type": "string"},
                "status": {"type": "string", "const": "mcp_success"},
                "result": result_schema,
                "sandbox": {"type": "object"},
            },
            "required": ["tool_id", "status", "result", "sandbox"],
        }
