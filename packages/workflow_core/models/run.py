from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from packages.workflow_core.models.common import StrictBaseModel, utc_now
from packages.workflow_core.models.enums import NodeExecutionStatus, WorkflowRunStatus
from packages.workflow_core.models.eval import EvalResult


class TraceRecord(StrictBaseModel):
    run_id: str
    workflow_id: str
    workflow_version: str | None = None
    node_id: str
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    output_snapshot: dict[str, Any] = Field(default_factory=dict)
    status: NodeExecutionStatus
    error: str | None = None
    attempt: int = 1
    max_attempts: int = 1
    retryable: bool = False
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    duration_ms: int | None = None


class WorkflowRun(StrictBaseModel):
    run_id: str
    workflow_id: str
    workflow_version: str | None = None
    rerun_of_run_id: str | None = None
    idempotency_key: str | None = None
    request_fingerprint: str | None = None
    shadow_mode: bool = False
    status: WorkflowRunStatus = WorkflowRunStatus.CREATED
    current_node_id: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    traces: list[TraceRecord] = Field(default_factory=list)
    eval_results: list[EvalResult] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
