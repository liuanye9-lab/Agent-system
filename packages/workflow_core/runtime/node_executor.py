from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from packages.workflow_core.models import ProcessNode, ToolPolicy
from packages.workflow_core.models.enums import NodeExecutionStatus, NodeType
from packages.workflow_core.runtime.approval_policy import ApprovalPolicy
from packages.workflow_core.runtime.tool_registry import MockToolRegistry, ToolExecutionContext, ToolRegistry


@dataclass(frozen=True)
class NodeExecutionResult:
    node_id: str
    status: NodeExecutionStatus
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    retryable: bool = False


class NodeExecutor:
    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ) -> None:
        self.tool_registry = tool_registry or MockToolRegistry()
        self.approval_policy = approval_policy or ApprovalPolicy()

    def execute(
        self,
        node: ProcessNode,
        input_payload: dict[str, Any],
        tool_policies: list[ToolPolicy],
        approval_granted: bool = False,
        shadow_mode: bool = False,
        actor_context: ToolExecutionContext | None = None,
    ) -> NodeExecutionResult:
        if self._control_value(input_payload, "force_fail_node") == node.node_id:
            return NodeExecutionResult(
                node_id=node.node_id,
                status=NodeExecutionStatus.FAILED,
                error="forced failure for test or eval scenario",
                retryable=False,
            )

        transient_failures = self._transient_failures_for_node(input_payload, node.node_id)
        if transient_failures > 0:
            return NodeExecutionResult(
                node_id=node.node_id,
                status=NodeExecutionStatus.FAILED,
                error=f"transient failure requested for node {node.node_id}",
                retryable=True,
            )

        approval = self.approval_policy.requires_approval(node, tool_policies)
        if approval.approval_required and not approval_granted:
            if shadow_mode:
                return NodeExecutionResult(
                    node_id=node.node_id,
                    status=NodeExecutionStatus.SUCCESS,
                    output=self._node_output(node, input_payload, tool_results=[], shadow_mode=True),
                )
            return NodeExecutionResult(
                node_id=node.node_id,
                status=NodeExecutionStatus.APPROVAL_REQUIRED,
                output={
                    "approval_required": True,
                    "reason": approval.reason,
                    "draft_payload": self._node_output(node, input_payload, tool_results=[]),
                },
            )

        try:
            actor_context = actor_context or ToolExecutionContext()
            tool_results = [
                self.tool_registry.execute(
                    tool_id,
                    {
                        "payload": input_payload,
                        "node_id": node.node_id,
                        "approval_granted": approval_granted,
                        "shadow_mode": shadow_mode,
                    },
                    context=ToolExecutionContext(
                        node_id=node.node_id,
                        approval_granted=approval_granted,
                        actor_id=actor_context.actor_id,
                        actor_role=actor_context.actor_role,
                        actor_scopes=actor_context.actor_scopes,
                    ),
                )
                for tool_id in node.tool_ids
            ]
            return NodeExecutionResult(
                node_id=node.node_id,
                status=NodeExecutionStatus.SUCCESS,
                output=self._node_output(node, input_payload, tool_results, shadow_mode=shadow_mode),
            )
        except Exception as exc:
            return NodeExecutionResult(
                node_id=node.node_id,
                status=NodeExecutionStatus.FAILED,
                error=str(exc),
                retryable=self._is_retryable_exception(exc),
            )

    def _node_output(
        self,
        node: ProcessNode,
        input_payload: dict[str, Any],
        tool_results: list[dict[str, Any]],
        shadow_mode: bool = False,
    ) -> dict[str, Any]:
        decision = "continue"
        if node.node_type == NodeType.REVIEW:
            decision = "continue"
        if node.node_type == NodeType.WRITE:
            if shadow_mode:
                decision = "shadow_draft_created"
            else:
                decision = "approved_write_completed" if input_payload.get("approval_granted") else "pending_approval"
        return {
            "summary": f"{node.name} mock execution completed",
            "decision": decision,
            "risks": (
                []
                if node.node_type != NodeType.WRITE
                else ["shadow mode did not execute external writes"] if shadow_mode
                else ["write operation requires approval"]
            ),
            "next_actions": (
                ["continue_to_next_node"]
                if node.node_type != NodeType.WRITE
                else ["review_shadow_output"] if shadow_mode
                else ["workflow_complete" if input_payload.get("approval_granted") else "wait_for_human_approval"]
            ),
            "tool_results": tool_results,
            "input_digest": sorted(input_payload.keys()),
            "shadow_mode": shadow_mode,
        }

    def _control_value(self, input_payload: dict[str, Any], key: str) -> Any:
        if key in input_payload:
            return input_payload[key]
        context = input_payload.get("context")
        if isinstance(context, dict):
            return context.get(key)
        return None

    def _transient_failures_for_node(self, input_payload: dict[str, Any], node_id: str) -> int:
        transient_failures = self._control_value(input_payload, "transient_failures")
        if isinstance(transient_failures, dict):
            return int(transient_failures.get(node_id, 0) or 0)
        return 0

    def _is_retryable_exception(self, exc: Exception) -> bool:
        return not isinstance(exc, (PermissionError, ValueError, KeyError))
