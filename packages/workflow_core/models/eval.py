from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from packages.workflow_core.models.common import StrictBaseModel, utc_now
from packages.workflow_core.models.enums import EvalType


class EvalSpec(StrictBaseModel):
    eval_id: str
    workflow_id: str
    name: str
    eval_type: EvalType
    target_node_id: str | None = None
    input_case: dict[str, Any]
    expected_output: dict[str, Any]
    scoring_rules: list[str] = Field(default_factory=list)


class EvalResult(StrictBaseModel):
    eval_id: str
    workflow_id: str
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
