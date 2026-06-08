from __future__ import annotations

from dataclasses import dataclass

from packages.workflow_core.models import ProcessNode, ToolPolicy
from packages.workflow_core.models.enums import PermissionLevel


@dataclass(frozen=True)
class ApprovalDecision:
    approval_required: bool
    reason: str | None = None


class ApprovalPolicy:
    def requires_approval(self, node: ProcessNode, tool_policies: list[ToolPolicy]) -> ApprovalDecision:
        node_tool_policies = [policy for policy in tool_policies if policy.tool_id in node.tool_ids]
        for policy in node_tool_policies:
            if policy.permission_level == PermissionLevel.WRITE_REQUIRES_APPROVAL or policy.requires_approval:
                return ApprovalDecision(
                    approval_required=True,
                    reason=f"tool {policy.tool_id} requires human approval before write execution",
                )
        return ApprovalDecision(approval_required=False)
