from __future__ import annotations

from typing import Protocol

from packages.workflow_core.models import AuditEvent, EvalResult, TraceRecord, WorkflowPackage, WorkflowRun
from packages.workflow_core.models.enums import WorkflowRunStatus


class WorkflowRepository(Protocol):
    def get_repository_status(self) -> dict[str, object]:
        """Return low-sensitive repository health and schema metadata."""

    def save_workflow(self, workflow_package: WorkflowPackage) -> WorkflowPackage:
        """Persist a workflow package."""

    def save_workflow_version(self, workflow_package: WorkflowPackage) -> WorkflowPackage:
        """Persist a candidate workflow package version without making it current."""

    def list_workflows(self, limit: int | None = None, offset: int = 0) -> list[WorkflowPackage]:
        """List workflow packages."""

    def get_workflow(self, workflow_id: str) -> WorkflowPackage | None:
        """Get a workflow by id."""

    def list_workflow_versions(self, workflow_id: str, limit: int | None = None, offset: int = 0) -> list[WorkflowPackage]:
        """List all saved versions for a workflow id."""

    def get_workflow_version(self, workflow_id: str, version: str) -> WorkflowPackage | None:
        """Get a specific workflow package version."""

    def promote_workflow_version(self, workflow_id: str, version: str) -> WorkflowPackage | None:
        """Set a saved workflow package version as the current version."""

    def save_run(self, run: WorkflowRun) -> WorkflowRun:
        """Persist a workflow run."""

    def get_run(self, run_id: str) -> WorkflowRun | None:
        """Get a workflow run by id."""

    def get_run_by_idempotency_key(self, workflow_id: str, idempotency_key: str) -> WorkflowRun | None:
        """Get a workflow run by workflow id and idempotency key."""

    def list_runs(
        self,
        workflow_id: str | None = None,
        status: WorkflowRunStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[WorkflowRun]:
        """List workflow runs."""

    def delete_runs(self, run_ids: list[str]) -> int:
        """Delete workflow runs by id and return the deleted count."""

    def list_traces(
        self,
        workflow_id: str | None = None,
        run_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[TraceRecord]:
        """List traces from persisted workflow runs."""

    def save_eval_results(self, workflow_id: str, results: list[EvalResult]) -> list[EvalResult]:
        """Persist eval results for a workflow."""

    def list_eval_results(self, workflow_id: str | None = None, limit: int | None = None, offset: int = 0) -> list[EvalResult]:
        """List eval results."""

    def delete_eval_results(self, eval_ids: list[str], workflow_id: str | None = None) -> int:
        """Delete eval results by id and optional workflow scope."""

    def save_audit_event(self, event: AuditEvent) -> AuditEvent:
        """Persist an audit event."""

    def list_audit_events(
        self,
        workflow_id: str | None = None,
        run_id: str | None = None,
        event_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AuditEvent]:
        """List audit events."""
