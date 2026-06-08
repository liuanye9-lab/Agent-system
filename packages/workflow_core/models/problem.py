from __future__ import annotations

from datetime import datetime

from pydantic import Field

from packages.workflow_core.models.common import StrictBaseModel, utc_now


class ProblemSpec(StrictBaseModel):
    id: str
    title: str
    description: str
    target_users: list[str] = Field(default_factory=list)
    business_goal: str
    start_event: str
    end_state: str
    success_metrics: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    human_roles: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
