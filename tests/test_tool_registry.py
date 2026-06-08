from __future__ import annotations

import pytest

from packages.workflow_core.models import ToolPolicy
from packages.workflow_core.models.enums import PermissionLevel, RiskLevel
from packages.workflow_core.runtime import MockToolRegistry, ToolExecutionContext


def make_tool_policy(
    *,
    tool_id: str = "tool-1",
    permission_level: PermissionLevel = PermissionLevel.READ_ONLY,
    requires_approval: bool = False,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
) -> ToolPolicy:
    return ToolPolicy(
        tool_id=tool_id,
        name="Tool",
        description="Test tool",
        permission_level=permission_level,
        risk_level=RiskLevel.LOW,
        requires_approval=requires_approval,
        allowed_roles=["tester"],
        input_schema=input_schema or {
            "type": "object",
            "properties": {"payload": {"type": "object"}},
            "required": ["payload"],
        },
        output_schema=output_schema or {"type": "object"},
    )


def test_tool_registry_blocks_forbidden_tool() -> None:
    registry = MockToolRegistry()
    registry.register(make_tool_policy(permission_level=PermissionLevel.FORBIDDEN))

    with pytest.raises(PermissionError, match="tool forbidden"):
        registry.execute("tool-1", {"payload": {}})


def test_tool_registry_blocks_write_tool_without_approval() -> None:
    registry = MockToolRegistry()
    registry.register(make_tool_policy(permission_level=PermissionLevel.WRITE_REQUIRES_APPROVAL))

    with pytest.raises(PermissionError, match="requires approval"):
        registry.execute("tool-1", {"payload": {}}, context=ToolExecutionContext(approval_granted=False))


def test_tool_registry_executes_write_tool_with_approval_context() -> None:
    registry = MockToolRegistry()
    registry.register(make_tool_policy(permission_level=PermissionLevel.WRITE_REQUIRES_APPROVAL))

    result = registry.execute(
        "tool-1",
        {"payload": {"decision": "go"}},
        context=ToolExecutionContext(node_id="write-node", approval_granted=True),
    )

    assert result["status"] == "mock_success"
    assert result["sandbox"]["permission_enforced"] is True
    assert result["sandbox"]["schema_enforced"] is True
    assert result["sandbox"]["approval_granted"] is True
    assert result["sandbox"]["node_id"] == "write-node"


def test_tool_registry_validates_tool_input_schema() -> None:
    registry = MockToolRegistry()
    registry.register(make_tool_policy())

    with pytest.raises(ValueError, match="tool input validation failed"):
        registry.execute("tool-1", {"missing_payload": {}})


def test_tool_registry_validates_tool_output_schema() -> None:
    registry = MockToolRegistry()
    registry.register(
        make_tool_policy(
            output_schema={
                "type": "object",
                "properties": {"must_exist": {"type": "string"}},
                "required": ["must_exist"],
            }
        )
    )

    with pytest.raises(ValueError, match="tool output validation failed"):
        registry.execute("tool-1", {"payload": {}})
