from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from packages.workflow_core.models.common import StrictBaseModel, utc_now


class AuditEvent(StrictBaseModel):
    event_id: str
    event_type: str
    action: str
    status: str
    actor_id: str
    actor_role: str
    actor_display_name: str | None = None
    workflow_id: str | None = None
    workflow_version: str | None = None
    run_id: str | None = None
    resource_type: str
    resource_id: str
    reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
