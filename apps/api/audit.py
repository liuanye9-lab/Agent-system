from __future__ import annotations

from typing import Any
from uuid import uuid4

from packages.workflow_core.models import ActorContext, AuditEvent


def build_audit_event(
    *,
    event_type: str,
    action: str,
    status: str,
    actor: ActorContext,
    resource_type: str,
    resource_id: str,
    workflow_id: str | None = None,
    workflow_version: str | None = None,
    run_id: str | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_id=f"audit-{uuid4().hex[:16]}",
        event_type=event_type,
        action=action,
        status=status,
        actor_id=actor.actor_id,
        actor_role=actor.role,
        actor_display_name=actor.display_name,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        run_id=run_id,
        resource_type=resource_type,
        resource_id=resource_id,
        reason=reason,
        details=details or {},
    )
