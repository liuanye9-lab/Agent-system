from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from pydantic import Field

from packages.workflow_core.models import ToolPolicy
from packages.workflow_core.models.common import StrictBaseModel
from packages.workflow_core.models.enums import PermissionLevel
from packages.workflow_core.runtime import ToolExecutionContext


DEFAULT_MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"
MCP_SESSION_ID_HEADER = "MCP-Session-Id"


class MCPTransportError(RuntimeError):
    """Raised when the MCP transport cannot send or decode a response."""


class MCPProtocolError(RuntimeError):
    """Raised when an MCP server returns an invalid JSON-RPC response."""


class MCPSessionExpired(MCPProtocolError):
    """Raised when a stateful MCP server rejects an expired session id."""


class MCPToolExecutionError(RuntimeError):
    """Raised when an MCP tool call returns an MCP-level tool error."""


@dataclass(frozen=True)
class MCPTransportResponse:
    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: dict[str, Any] | None = None


class MCPTransport(Protocol):
    def post(
        self,
        endpoint_url: str,
        message: dict[str, Any],
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> MCPTransportResponse:
        """Send one JSON-RPC message to an MCP endpoint."""


@dataclass(frozen=True)
class MCPAuthorizationContext:
    server_id: str
    method: str
    tool_name: str | None = None
    permission_level: PermissionLevel | None = None
    required_scopes: tuple[str, ...] = ()
    actor_id: str | None = None
    actor_role: str | None = None
    actor_scopes: tuple[str, ...] = ()
    approval_granted: bool = False


class MCPAuthorizationProvider(Protocol):
    def headers_for(self, context: MCPAuthorizationContext) -> Mapping[str, str]:
        """Return transport headers for an authorized MCP request."""


@dataclass(frozen=True)
class ScopedMCPAuthorizationProvider:
    """Map MCP request context to least-privilege transport headers.

    This provider is intentionally simple and deterministic: production callers
    can wrap a secret manager or token service behind the same protocol while
    preserving the registry's permission and schema enforcement boundary.
    """

    default_headers: Mapping[str, str] = field(default_factory=dict)
    permission_headers: Mapping[PermissionLevel, Mapping[str, str]] = field(default_factory=dict)
    scope_headers: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    require_scope_credentials: bool = False

    def headers_for(self, context: MCPAuthorizationContext) -> Mapping[str, str]:
        headers = dict(self.default_headers)
        if context.permission_level in self.permission_headers:
            headers.update(self.permission_headers[context.permission_level])

        missing_scope_credentials: list[str] = []
        for scope in context.required_scopes:
            scoped_headers = self.scope_headers.get(scope)
            if scoped_headers is None:
                if self.require_scope_credentials:
                    missing_scope_credentials.append(scope)
                continue
            headers.update(scoped_headers)

        if missing_scope_credentials:
            raise PermissionError(
                "MCP authorization credentials missing for scopes: "
                f"{sorted(missing_scope_credentials)}"
            )
        return headers


class HTTPMCPJSONRPCTransport:
    """Small Streamable HTTP compatible JSON-RPC transport.

    The transport supports regular JSON responses and finite text/event-stream
    responses. Long-lived streaming should be handled by a richer transport
    implementation behind the same protocol.
    """

    def post(
        self,
        endpoint_url: str,
        message: dict[str, Any],
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> MCPTransportResponse:
        request_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **dict(headers),
        }
        request = urllib.request.Request(
            endpoint_url,
            data=json.dumps(message).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read()
                return MCPTransportResponse(
                    status_code=response.status,
                    headers=dict(response.headers.items()),
                    body=self._decode_response(body, response.headers.get("Content-Type", "")),
                )
        except urllib.error.HTTPError as exc:
            body = exc.read()
            return MCPTransportResponse(
                status_code=exc.code,
                headers=dict(exc.headers.items()),
                body=self._decode_response(body, exc.headers.get("Content-Type", "")),
            )
        except urllib.error.URLError as exc:
            raise MCPTransportError(f"MCP transport request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise MCPTransportError("MCP transport request timed out") from exc

    def _decode_response(self, raw_body: bytes, content_type: str) -> dict[str, Any] | None:
        if not raw_body:
            return None
        text = raw_body.decode("utf-8")
        if "text/event-stream" in content_type:
            return self._decode_sse_response(text)
        try:
            body = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MCPTransportError("MCP transport returned non-JSON response") from exc
        if not isinstance(body, dict):
            raise MCPTransportError("MCP transport response must be a JSON object")
        return body

    def _decode_sse_response(self, text: str) -> dict[str, Any]:
        for event in text.strip().split("\n\n"):
            data_lines = [
                line.removeprefix("data:").strip()
                for line in event.splitlines()
                if line.startswith("data:")
            ]
            if not data_lines:
                continue
            try:
                body = json.loads("\n".join(data_lines))
            except json.JSONDecodeError as exc:
                raise MCPTransportError("MCP SSE event returned non-JSON data") from exc
            if isinstance(body, dict) and body.get("jsonrpc") == "2.0":
                return body
        raise MCPTransportError("MCP SSE response did not include a JSON-RPC message")


class MCPServerConfig(StrictBaseModel):
    server_id: str = Field(min_length=1, max_length=120)
    endpoint_url: str = Field(min_length=1, max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    protocol_version: str = DEFAULT_MCP_PROTOCOL_VERSION
    client_name: str = "agent-workflow-builder"
    client_version: str = "0.1.0"
    client_capabilities: dict[str, Any] = Field(default_factory=dict)
    auto_initialize: bool = True


class MCPJSONRPCSession:
    def __init__(
        self,
        config: MCPServerConfig,
        transport: MCPTransport | None = None,
        authorization_provider: MCPAuthorizationProvider | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or HTTPMCPJSONRPCTransport()
        self.authorization_provider = authorization_provider
        self.session_id: str | None = None
        self.negotiated_protocol_version: str | None = None
        self.server_capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}
        self._next_request_id = 1
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": self.config.protocol_version,
                "capabilities": self.config.client_capabilities,
                "clientInfo": {
                    "name": self.config.client_name,
                    "version": self.config.client_version,
                },
            },
            require_initialized=False,
            retry_expired=False,
        )
        protocol_version = result.get("protocolVersion")
        if not isinstance(protocol_version, str) or not protocol_version:
            raise MCPProtocolError("MCP initialize result missing protocolVersion")
        capabilities = result.get("capabilities", {})
        if not isinstance(capabilities, dict):
            raise MCPProtocolError("MCP initialize result capabilities must be an object")
        server_info = result.get("serverInfo", {})
        if not isinstance(server_info, dict):
            raise MCPProtocolError("MCP initialize result serverInfo must be an object")
        self.negotiated_protocol_version = protocol_version
        self.server_capabilities = capabilities
        self.server_info = server_info
        self._initialized = True
        self.notify("notifications/initialized")
        return result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        response = self.transport.post(
            self.config.endpoint_url,
            message,
            headers=self._headers(),
            timeout_seconds=self.config.timeout_seconds,
        )
        if response.status_code >= 400:
            raise MCPTransportError(f"MCP notification failed with HTTP {response.status_code}")

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        require_initialized: bool = True,
        retry_expired: bool = True,
        authorization_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if require_initialized and not self._initialized:
            if self.config.auto_initialize:
                self.initialize()
            else:
                raise MCPProtocolError("MCP session is not initialized")
        request_id = self._allocate_request_id()
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        response = self.transport.post(
            self.config.endpoint_url,
            message,
            headers=self._headers(
                include_session=method != "initialize",
                authorization_headers=authorization_headers,
            ),
            timeout_seconds=self.config.timeout_seconds,
        )
        self._capture_session_id(response.headers)
        if response.status_code == 404 and self.session_id and retry_expired:
            self._reset_session()
            self.initialize()
            return self.request(
                method,
                params,
                require_initialized=False,
                retry_expired=False,
                authorization_headers=authorization_headers,
            )
        if response.status_code >= 400:
            raise MCPTransportError(f"MCP request failed with HTTP {response.status_code}")
        return self._extract_result(response.body, request_id)

    def call_tool(
        self,
        tool_policy: ToolPolicy,
        payload: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if tool_policy.adapter != "mcp":
            raise MCPProtocolError(f"tool is not MCP-backed: {tool_policy.tool_id}")
        if tool_policy.server_id != self.config.server_id:
            raise MCPProtocolError(
                f"tool server mismatch for {tool_policy.tool_id}: {tool_policy.server_id}"
            )
        tool_name = tool_policy.external_tool_name or tool_policy.name
        authorization_headers = self._authorization_headers(tool_policy, tool_name, context)
        result = self.request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": payload,
                "_meta": {
                    "workflow_node_id": context.node_id,
                    "actor_id": context.actor_id,
                    "approval_granted": context.approval_granted,
                },
            },
            authorization_headers=authorization_headers,
        )
        if result.get("isError") is True:
            raise MCPToolExecutionError(f"MCP tool returned an error: {tool_policy.tool_id}")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        if "content" in result:
            return {"content": result["content"]}
        return result

    def _authorization_headers(
        self,
        tool_policy: ToolPolicy,
        tool_name: str,
        context: ToolExecutionContext,
    ) -> Mapping[str, str]:
        if self.authorization_provider is None:
            return {}
        return self.authorization_provider.headers_for(
            MCPAuthorizationContext(
                server_id=self.config.server_id,
                method="tools/call",
                tool_name=tool_name,
                permission_level=tool_policy.permission_level,
                required_scopes=tuple(tool_policy.required_scopes),
                actor_id=context.actor_id,
                actor_role=context.actor_role,
                actor_scopes=context.actor_scopes,
                approval_granted=context.approval_granted,
            )
        )

    def _headers(
        self,
        *,
        include_session: bool = True,
        authorization_headers: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        headers = dict(self.config.headers)
        if authorization_headers:
            headers.update(authorization_headers)
        headers[MCP_PROTOCOL_VERSION_HEADER] = (
            self.negotiated_protocol_version or self.config.protocol_version
        )
        if include_session and self.session_id:
            headers[MCP_SESSION_ID_HEADER] = self.session_id
        return headers

    def _allocate_request_id(self) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        return request_id

    def _capture_session_id(self, headers: Mapping[str, str]) -> None:
        for name, value in headers.items():
            if name.lower() == MCP_SESSION_ID_HEADER.lower() and value:
                self.session_id = value
                return

    def _reset_session(self) -> None:
        self.session_id = None
        self.negotiated_protocol_version = None
        self.server_capabilities = {}
        self.server_info = {}
        self._initialized = False

    def _extract_result(self, response_body: dict[str, Any] | None, request_id: int) -> dict[str, Any]:
        if response_body is None:
            raise MCPProtocolError("MCP request returned no JSON-RPC response")
        if response_body.get("jsonrpc") != "2.0":
            raise MCPProtocolError("MCP response missing jsonrpc 2.0 marker")
        if response_body.get("id") != request_id:
            raise MCPProtocolError("MCP response id does not match request id")
        if "error" in response_body:
            raise MCPProtocolError(f"MCP server returned error: {response_body['error']}")
        result = response_body.get("result")
        if not isinstance(result, dict):
            raise MCPProtocolError("MCP response result must be an object")
        return result


class MCPServerSessionPool:
    """Route MCP tool calls to one managed session per server id."""

    def __init__(self, sessions: list[MCPJSONRPCSession]) -> None:
        self.sessions = {session.config.server_id: session for session in sessions}

    @classmethod
    def from_configs(
        cls,
        configs: list[MCPServerConfig | dict[str, Any]],
        *,
        transport: MCPTransport | None = None,
        authorization_provider: MCPAuthorizationProvider | None = None,
        authorization_providers: Mapping[str, MCPAuthorizationProvider] | None = None,
    ) -> MCPServerSessionPool:
        sessions: list[MCPJSONRPCSession] = []
        for config in configs:
            server_config = config if isinstance(config, MCPServerConfig) else MCPServerConfig.model_validate(config)
            sessions.append(
                MCPJSONRPCSession(
                    server_config,
                    transport=transport,
                    authorization_provider=(
                        authorization_providers.get(server_config.server_id)
                        if authorization_providers and server_config.server_id in authorization_providers
                        else authorization_provider
                    ),
                )
            )
        return cls(sessions)

    def invoke(
        self,
        tool_policy: ToolPolicy,
        payload: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if tool_policy.server_id is None:
            raise MCPProtocolError(f"MCP tool missing server_id: {tool_policy.tool_id}")
        session = self.sessions.get(tool_policy.server_id)
        if session is None:
            raise MCPProtocolError(f"MCP server not configured: {tool_policy.server_id}")
        return session.call_tool(tool_policy, payload, context)
