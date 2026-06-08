from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from packages.workflow_core.models.common import StrictBaseModel, utc_now
from packages.workflow_core.models.enums import SuggestionType


class OptimizationSuggestion(StrictBaseModel):
    suggestion_id: str
    workflow_id: str
    suggestion_type: SuggestionType
    title: str
    rationale: str
    recommended_action: str
    related_node_id: str | None = None
    evidence: list[str] = Field(default_factory=list)


class PackageRepairOperation(StrictBaseModel):
    operation_id: str
    suggestion_id: str
    target_type: str
    target_id: str
    action: str
    rationale: str
    proposed_changes: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)


class PackageRepairPlan(StrictBaseModel):
    plan_id: str
    workflow_id: str
    workflow_version: str
    generated_at: datetime = Field(default_factory=utc_now)
    suggestions: list[OptimizationSuggestion] = Field(default_factory=list)
    operations: list[PackageRepairOperation] = Field(default_factory=list)

    @property
    def has_operations(self) -> bool:
        return bool(self.operations)
