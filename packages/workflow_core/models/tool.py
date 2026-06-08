from __future__ import annotations

from typing import Any

from pydantic import Field

from packages.workflow_core.models.common import StrictBaseModel
from packages.workflow_core.models.enums import PermissionLevel, RiskLevel


class ToolPolicy(StrictBaseModel):
    tool_id: str
    name: str
    description: str
    adapter: str = "mock"
    server_id: str | None = None
    external_tool_name: str | None = None
    permission_level: PermissionLevel
    risk_level: RiskLevel
    requires_approval: bool = False
    allowed_roles: list[str] = Field(default_factory=list)
    required_scopes: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
