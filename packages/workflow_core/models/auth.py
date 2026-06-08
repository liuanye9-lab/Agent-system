from __future__ import annotations

from pydantic import Field

from packages.workflow_core.models.common import StrictBaseModel


class ActorContext(StrictBaseModel):
    actor_id: str
    role: str
    display_name: str | None = None
    scopes: list[str] = Field(default_factory=list)
