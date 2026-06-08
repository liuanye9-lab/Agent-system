from __future__ import annotations

import pytest

from packages.workflow_core.adapters import (
    DEFAULT_MCP_PROTOCOL_VERSION,
    HTTPMCPJSONRPCTransport,
    MCPJSONRPCSession,
    MCPProtocolError,
    MCPServerConfig,
    MCPServerSessionPool,
    MCPToolAdapter,
    MCPToolExecutionError,
    MCPTransportResponse,
    ScopedMCPAuthorizationProvider,
)
from packages.workflow_core.models import ToolPolicy
from packages.workflow_core.models.enums import PermissionLevel, RiskLevel
from packages.workflow_core.runtime import MCPToolRegistry, ToolExecutionContext


class RecordingInvoker:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[tuple[ToolPolicy, dict, ToolExecutionContext]] = []

    def invoke(
        self,
        tool_policy: ToolPolicy,
        payload: dict,
        context: ToolExecutionContext,
    ) -> dict:
        self.calls.append((tool_policy, payload, context))
        return self.result


class RecordingTransport:
    def __init__(self, responses: list[MCPTransportResponse]) -> None:
        self.responses = responses
        self.posts: list[dict] = []

    def post(
        self,
        endpoint_url: str,
        message: dict,
        *,
        headers: dict,
        timeout_seconds: float,
    ) -> MCPTransportResponse:
        self.posts.append(
            {
                "endpoint_url": endpoint_url,
                "message": message,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected MCP transport post")
        return self.responses.pop(0)


def bind_customer_lookup_policy() -> ToolPolicy:
    return MCPToolAdapter().bind_tool(
        {
            "server_id": "crm-prod",
            "name": "customer.lookup",
            "description": "Lookup a customer record from CRM.",
            "permission_level": "read_only",
            "risk_level": "low",
            "allowed_roles": ["customer-success"],
            "required_scopes": ["crm:read"],
            "input_schema": {
                "type": "object",
                "properties": {"customer_id": {"type": "string"}},
                "required": ["customer_id"],
            },
            "output_schema": {
                "type": "object",
                "properties": {"customer_id": {"type": "string"}, "health": {"type": "string"}},
                "required": ["customer_id", "health"],
            },
        }
    )


def test_mcp_adapter_binds_descriptor_to_tool_policy_envelope() -> None:
    policy = bind_customer_lookup_policy()

    assert policy.tool_id == "mcp-crm-prod-customer-lookup"
    assert policy.adapter == "mcp"
    assert policy.server_id == "crm-prod"
    assert policy.external_tool_name == "customer.lookup"
    assert policy.permission_level == PermissionLevel.READ_ONLY
    assert policy.required_scopes == ["crm:read"]
    assert policy.input_schema["properties"]["payload"]["required"] == ["customer_id"]
    assert policy.output_schema["properties"]["result"]["required"] == ["customer_id", "health"]


def test_mcp_tool_registry_invokes_external_tool_inside_sandbox() -> None:
    policy = bind_customer_lookup_policy()
    invoker = RecordingInvoker({"customer_id": "cus-1", "health": "at_risk"})
    registry = MCPToolRegistry(invoker)
    registry.register(policy)

    result = registry.execute(
        policy.tool_id,
        {"payload": {"customer_id": "cus-1"}, "node_id": "lookup-customer"},
        context=ToolExecutionContext(
            node_id="lookup-customer",
            actor_role="customer-success",
            actor_scopes=("crm:read",),
        ),
    )

    assert result["status"] == "mcp_success"
    assert result["result"] == {"customer_id": "cus-1", "health": "at_risk"}
    assert result["sandbox"]["permission_enforced"] is True
    assert result["sandbox"]["server_id"] == "crm-prod"
    assert invoker.calls[0][1] == {"customer_id": "cus-1"}


def test_mcp_tool_registry_blocks_missing_scope_before_invocation() -> None:
    policy = bind_customer_lookup_policy()
    invoker = RecordingInvoker({"customer_id": "cus-1", "health": "at_risk"})
    registry = MCPToolRegistry(invoker)
    registry.register(policy)

    with pytest.raises(PermissionError, match="actor scopes missing"):
        registry.execute(
            policy.tool_id,
            {"payload": {"customer_id": "cus-1"}},
            context=ToolExecutionContext(actor_role="customer-success"),
        )

    assert invoker.calls == []


def test_mcp_tool_registry_blocks_write_tool_without_approval() -> None:
    policy = MCPToolAdapter().bind_tool(
        {
            "server_id": "ticketing",
            "name": "ticket.create",
            "description": "Create a support ticket.",
            "permission_level": "write_requires_approval",
            "risk_level": "high",
            "allowed_roles": ["business-approver"],
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        }
    )
    invoker = RecordingInvoker({"ticket_id": "t-1"})
    registry = MCPToolRegistry(invoker)
    registry.register(policy)

    with pytest.raises(PermissionError, match="requires approval"):
        registry.execute(
            policy.tool_id,
            {"payload": {"title": "Escalation"}},
            context=ToolExecutionContext(actor_role="business-approver"),
        )

    assert invoker.calls == []


def test_mcp_tool_registry_validates_external_output_schema() -> None:
    policy = bind_customer_lookup_policy()
    invoker = RecordingInvoker({"customer_id": "cus-1"})
    registry = MCPToolRegistry(invoker)
    registry.register(policy)

    with pytest.raises(ValueError, match="tool output validation failed"):
        registry.execute(
            policy.tool_id,
            {"payload": {"customer_id": "cus-1"}},
            context=ToolExecutionContext(actor_role="customer-success", actor_scopes=("crm:read",)),
        )


def test_mcp_session_pool_initializes_and_invokes_tool_call_over_json_rpc() -> None:
    policy = bind_customer_lookup_policy()
    transport = RecordingTransport(
        [
            MCPTransportResponse(
                status_code=200,
                headers={"MCP-Session-Id": "sess-1"},
                body={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": DEFAULT_MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "crm", "version": "1.0"},
                    },
                },
            ),
            MCPTransportResponse(status_code=202),
            MCPTransportResponse(
                status_code=200,
                body={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "content": [{"type": "text", "text": "ok"}],
                        "structuredContent": {"customer_id": "cus-1", "health": "healthy"},
                    },
                },
            ),
        ]
    )
    pool = MCPServerSessionPool.from_configs(
        [{"server_id": "crm-prod", "endpoint_url": "https://mcp.example.test"}],
        transport=transport,
    )
    registry = MCPToolRegistry(pool)
    registry.register(policy)

    result = registry.execute(
        policy.tool_id,
        {"payload": {"customer_id": "cus-1"}, "node_id": "lookup-customer"},
        context=ToolExecutionContext(
            node_id="lookup-customer",
            actor_id="agent-operator",
            actor_role="customer-success",
            actor_scopes=("crm:read",),
        ),
    )

    assert result["status"] == "mcp_success"
    assert result["result"] == {"customer_id": "cus-1", "health": "healthy"}
    assert transport.posts[0]["message"]["method"] == "initialize"
    assert transport.posts[1]["message"]["method"] == "notifications/initialized"
    assert transport.posts[2]["message"]["method"] == "tools/call"
    assert transport.posts[2]["message"]["params"]["name"] == "customer.lookup"
    assert transport.posts[2]["message"]["params"]["arguments"] == {"customer_id": "cus-1"}
    assert transport.posts[2]["headers"]["MCP-Session-Id"] == "sess-1"
    assert transport.posts[2]["headers"]["MCP-Protocol-Version"] == DEFAULT_MCP_PROTOCOL_VERSION


def test_mcp_session_adds_scope_and_permission_authorization_headers_to_tool_call() -> None:
    policy = bind_customer_lookup_policy()
    transport = RecordingTransport(
        [
            MCPTransportResponse(
                status_code=200,
                headers={"MCP-Session-Id": "sess-1"},
                body={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": DEFAULT_MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "crm", "version": "1.0"},
                    },
                },
            ),
            MCPTransportResponse(status_code=202),
            MCPTransportResponse(
                status_code=200,
                body={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "structuredContent": {"customer_id": "cus-1", "health": "healthy"},
                    },
                },
            ),
        ]
    )
    provider = ScopedMCPAuthorizationProvider(
        default_headers={"Authorization": "Bearer default"},
        permission_headers={PermissionLevel.READ_ONLY: {"Authorization": "Bearer read-token"}},
        scope_headers={"crm:read": {"X-MCP-Scope": "crm-read"}},
        require_scope_credentials=True,
    )
    pool = MCPServerSessionPool.from_configs(
        [{"server_id": "crm-prod", "endpoint_url": "https://mcp.example.test"}],
        transport=transport,
        authorization_provider=provider,
    )
    registry = MCPToolRegistry(pool)
    registry.register(policy)

    result = registry.execute(
        policy.tool_id,
        {"payload": {"customer_id": "cus-1"}, "node_id": "lookup-customer"},
        context=ToolExecutionContext(
            node_id="lookup-customer",
            actor_role="customer-success",
            actor_scopes=("crm:read",),
        ),
    )

    assert result["result"] == {"customer_id": "cus-1", "health": "healthy"}
    assert "Authorization" not in transport.posts[0]["headers"]
    assert transport.posts[2]["headers"]["Authorization"] == "Bearer read-token"
    assert transport.posts[2]["headers"]["X-MCP-Scope"] == "crm-read"
    assert transport.posts[2]["headers"]["MCP-Session-Id"] == "sess-1"


def test_mcp_session_uses_write_permission_credential_after_registry_approval() -> None:
    policy = MCPToolAdapter().bind_tool(
        {
            "server_id": "ticketing",
            "name": "ticket.create",
            "description": "Create a support ticket.",
            "permission_level": "write_requires_approval",
            "risk_level": "high",
            "requires_approval": True,
            "allowed_roles": ["business-approver"],
            "required_scopes": ["ticket:write"],
            "input_schema": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
            "output_schema": {
                "type": "object",
                "properties": {"ticket_id": {"type": "string"}},
                "required": ["ticket_id"],
            },
        }
    )
    transport = RecordingTransport(
        [
            MCPTransportResponse(
                status_code=200,
                headers={"MCP-Session-Id": "ticket-session"},
                body={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": DEFAULT_MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "ticketing", "version": "1.0"},
                    },
                },
            ),
            MCPTransportResponse(status_code=202),
            MCPTransportResponse(
                status_code=200,
                body={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"structuredContent": {"ticket_id": "ticket-1"}},
                },
            ),
        ]
    )
    provider = ScopedMCPAuthorizationProvider(
        default_headers={"Authorization": "Bearer default"},
        permission_headers={
            PermissionLevel.READ_ONLY: {"Authorization": "Bearer read-token"},
            PermissionLevel.WRITE_REQUIRES_APPROVAL: {"Authorization": "Bearer write-token"},
        },
        scope_headers={"ticket:write": {"X-MCP-Scope": "ticket-write"}},
        require_scope_credentials=True,
    )
    pool = MCPServerSessionPool.from_configs(
        [{"server_id": "ticketing", "endpoint_url": "https://mcp.example.test"}],
        transport=transport,
        authorization_provider=provider,
    )
    registry = MCPToolRegistry(pool)
    registry.register(policy)

    result = registry.execute(
        policy.tool_id,
        {"payload": {"title": "Escalation"}, "node_id": "create-ticket"},
        context=ToolExecutionContext(
            node_id="create-ticket",
            approval_granted=True,
            actor_role="business-approver",
            actor_scopes=("ticket:write",),
        ),
    )

    assert result["result"] == {"ticket_id": "ticket-1"}
    assert transport.posts[2]["headers"]["Authorization"] == "Bearer write-token"
    assert transport.posts[2]["headers"]["X-MCP-Scope"] == "ticket-write"


def test_mcp_authorization_provider_blocks_missing_scope_credentials_before_transport() -> None:
    policy = bind_customer_lookup_policy()
    transport = RecordingTransport([])
    session = MCPJSONRPCSession(
        MCPServerConfig(server_id="crm-prod", endpoint_url="https://mcp.example.test"),
        transport=transport,
        authorization_provider=ScopedMCPAuthorizationProvider(require_scope_credentials=True),
    )

    with pytest.raises(PermissionError, match="MCP authorization credentials missing"):
        session.call_tool(
            policy,
            {"customer_id": "cus-1"},
            ToolExecutionContext(actor_role="customer-success", actor_scopes=("crm:read",)),
        )

    assert transport.posts == []


def test_mcp_session_reinitializes_when_server_rejects_expired_session() -> None:
    policy = bind_customer_lookup_policy()
    transport = RecordingTransport(
        [
            MCPTransportResponse(
                status_code=200,
                headers={"MCP-Session-Id": "old-session"},
                body={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": DEFAULT_MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "crm", "version": "1.0"},
                    },
                },
            ),
            MCPTransportResponse(status_code=202),
            MCPTransportResponse(status_code=404),
            MCPTransportResponse(
                status_code=200,
                headers={"MCP-Session-Id": "new-session"},
                body={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "result": {
                        "protocolVersion": DEFAULT_MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "crm", "version": "1.1"},
                    },
                },
            ),
            MCPTransportResponse(status_code=202),
            MCPTransportResponse(
                status_code=200,
                body={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "result": {
                        "structuredContent": {"customer_id": "cus-1", "health": "recovered"}
                    },
                },
            ),
        ]
    )
    session = MCPJSONRPCSession(
        MCPServerConfig(server_id="crm-prod", endpoint_url="https://mcp.example.test"),
        transport=transport,
    )

    result = session.call_tool(
        policy,
        {"customer_id": "cus-1"},
        ToolExecutionContext(actor_role="customer-success", actor_scopes=("crm:read",)),
    )

    assert result == {"customer_id": "cus-1", "health": "recovered"}
    assert [post["message"]["method"] for post in transport.posts] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    assert transport.posts[-1]["headers"]["MCP-Session-Id"] == "new-session"


def test_mcp_session_raises_on_tool_error_result() -> None:
    policy = bind_customer_lookup_policy()
    transport = RecordingTransport(
        [
            MCPTransportResponse(
                status_code=200,
                body={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": DEFAULT_MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "crm", "version": "1.0"},
                    },
                },
            ),
            MCPTransportResponse(status_code=202),
            MCPTransportResponse(
                status_code=200,
                body={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"isError": True, "content": [{"type": "text", "text": "denied"}]},
                },
            ),
        ]
    )
    session = MCPJSONRPCSession(
        MCPServerConfig(server_id="crm-prod", endpoint_url="https://mcp.example.test"),
        transport=transport,
    )

    with pytest.raises(MCPToolExecutionError, match="MCP tool returned an error"):
        session.call_tool(
            policy,
            {"customer_id": "cus-1"},
            ToolExecutionContext(actor_role="customer-success", actor_scopes=("crm:read",)),
        )


def test_mcp_server_pool_rejects_unconfigured_server() -> None:
    policy = bind_customer_lookup_policy()
    pool = MCPServerSessionPool([])

    with pytest.raises(MCPProtocolError, match="MCP server not configured"):
        pool.invoke(
            policy,
            {"customer_id": "cus-1"},
            ToolExecutionContext(actor_role="customer-success", actor_scopes=("crm:read",)),
        )


def test_http_mcp_transport_decodes_finite_sse_json_rpc_response() -> None:
    response = HTTPMCPJSONRPCTransport()._decode_response(
        b'event: message\ndata: {"jsonrpc":"2.0","id":7,"result":{"ok":true}}\n\n',
        "text/event-stream",
    )

    assert response == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}
