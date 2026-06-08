from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from jsonschema import Draft202012Validator, ValidationError

from packages.workflow_core.models import ToolPolicy
from packages.workflow_core.models.enums import PermissionLevel


@dataclass(frozen=True)
class ToolExecutionContext:
    node_id: str | None = None
    approval_granted: bool = False
    actor_id: str | None = None
    actor_role: str | None = None
    actor_scopes: tuple[str, ...] = ()


class ExternalToolInvoker(Protocol):
    def invoke(
        self,
        tool_policy: ToolPolicy,
        payload: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        """Call an external tool and return the external tool result payload."""


class ToolRegistry(Protocol):
    def register(self, tool_policy: ToolPolicy) -> None:
        """Register a tool policy."""

    def execute(
        self,
        tool_id: str,
        payload: dict[str, Any],
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        """Execute a tool by id."""

    def get(self, tool_id: str) -> ToolPolicy | None:
        """Return a tool policy by id."""


class MockToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolPolicy] = {}

    def register(self, tool_policy: ToolPolicy) -> None:
        self._tools[tool_policy.tool_id] = tool_policy

    def register_many(self, tool_policies: list[ToolPolicy]) -> None:
        for tool_policy in tool_policies:
            self.register(tool_policy)

    def get(self, tool_id: str) -> ToolPolicy | None:
        return self._tools.get(tool_id)

    def execute(
        self,
        tool_id: str,
        payload: dict[str, Any],
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        context = context or self._context_from_payload(payload)
        tool = self._require_tool(tool_id)
        self._enforce_access(tool, context)
        self._validate_schema(tool.input_schema, payload, f"tool input validation failed for {tool_id}")
        result = self._mock_result(tool, payload, context)
        self._validate_schema(tool.output_schema, result, f"tool output validation failed for {tool_id}")
        return result

    def _context_from_payload(self, payload: dict[str, Any]) -> ToolExecutionContext:
        return ToolExecutionContext(
            node_id=payload.get("node_id"),
            approval_granted=bool(payload.get("approval_granted", False)),
        )

    def _require_tool(self, tool_id: str) -> ToolPolicy:
        tool = self._tools.get(tool_id)
        if tool is None:
            raise KeyError(f"tool not registered: {tool_id}")
        return tool

    def _enforce_access(self, tool: ToolPolicy, context: ToolExecutionContext) -> None:
        if tool.permission_level == PermissionLevel.FORBIDDEN:
            raise PermissionError(f"tool forbidden: {tool.tool_id}")
        if (
            tool.permission_level == PermissionLevel.WRITE_REQUIRES_APPROVAL
            or tool.requires_approval
        ) and not context.approval_granted:
            raise PermissionError(f"tool requires approval before execution: {tool.tool_id}")
        if (
            context.actor_role
            and context.actor_role != "workflow-admin"
            and tool.allowed_roles
            and context.actor_role not in tool.allowed_roles
        ):
            raise PermissionError(f"actor role is not allowed to execute tool: {tool.tool_id}")
        missing_scopes = set(tool.required_scopes) - set(context.actor_scopes)
        if missing_scopes:
            raise PermissionError(f"actor scopes missing for tool {tool.tool_id}: {sorted(missing_scopes)}")

    def _mock_result(
        self,
        tool: ToolPolicy,
        payload: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        return {
            "tool_id": tool.tool_id,
            "status": "mock_success",
            "result": {
                "permission_level": tool.permission_level,
                "risk_level": tool.risk_level,
                "echo": payload,
            },
            "sandbox": {
                "mock_adapter": True,
                "permission_enforced": True,
                "schema_enforced": True,
                "approval_granted": context.approval_granted,
                "node_id": context.node_id,
            },
        }

    def _validate_schema(self, schema: dict[str, Any], payload: dict[str, Any], prefix: str) -> None:
        try:
            Draft202012Validator(schema).validate(payload)
        except ValidationError as exc:
            path = ".".join(str(part) for part in exc.path)
            location = f" at {path}" if path else ""
            raise ValueError(f"{prefix}: {exc.message}{location}") from exc


class MCPToolRegistry(MockToolRegistry):
    def __init__(self, invoker: ExternalToolInvoker) -> None:
        super().__init__()
        self.invoker = invoker

    def execute(
        self,
        tool_id: str,
        payload: dict[str, Any],
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        context = context or self._context_from_payload(payload)
        tool = self._require_tool(tool_id)
        if tool.adapter != "mcp":
            return super().execute(tool_id, payload, context=context)
        self._enforce_access(tool, context)
        self._validate_schema(tool.input_schema, payload, f"tool input validation failed for {tool_id}")
        external_payload = payload.get("payload")
        if not isinstance(external_payload, dict):
            raise ValueError(f"tool input validation failed for {tool_id}: payload must be an object")
        external_result = self.invoker.invoke(tool, external_payload, context)
        result = {
            "tool_id": tool.tool_id,
            "status": "mcp_success",
            "result": external_result,
            "sandbox": {
                "mcp_adapter": True,
                "permission_enforced": True,
                "schema_enforced": True,
                "approval_granted": context.approval_granted,
                "node_id": context.node_id,
                "server_id": tool.server_id,
                "external_tool_name": tool.external_tool_name,
            },
        }
        self._validate_schema(tool.output_schema, result, f"tool output validation failed for {tool_id}")
        return result
