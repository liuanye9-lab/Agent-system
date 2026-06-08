from __future__ import annotations

from collections import defaultdict
from typing import TypeVar

from packages.workflow_core.models import AuditEvent, EvalResult, TraceRecord, WorkflowPackage, WorkflowRun
from packages.workflow_core.models.enums import WorkflowRunStatus

T = TypeVar("T")


class MemoryWorkflowRepository:
    schema_version = "memory"

    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowPackage] = {}
        self._workflow_versions: dict[str, dict[str, WorkflowPackage]] = defaultdict(dict)
        self._runs: dict[str, WorkflowRun] = {}
        self._eval_results_by_workflow_id: dict[str, list[EvalResult]] = defaultdict(list)
        self._audit_events: dict[str, AuditEvent] = {}

    def get_repository_status(self) -> dict[str, object]:
        return {
            "backend": "memory",
            "schema_version": self.schema_version,
            "workflow_count": len(self._workflows),
            "run_count": len(self._runs),
            "eval_result_count": sum(len(results) for results in self._eval_results_by_workflow_id.values()),
            "audit_event_count": len(self._audit_events),
        }

    def save_workflow(self, workflow_package: WorkflowPackage) -> WorkflowPackage:
        self._workflows[workflow_package.workflow_id] = workflow_package
        self._workflow_versions[workflow_package.workflow_id][workflow_package.version] = workflow_package
        return workflow_package

    def save_workflow_version(self, workflow_package: WorkflowPackage) -> WorkflowPackage:
        self._workflow_versions[workflow_package.workflow_id][workflow_package.version] = workflow_package
        return workflow_package

    def list_workflows(self, limit: int | None = None, offset: int = 0) -> list[WorkflowPackage]:
        workflows = sorted(self._workflows.values(), key=lambda workflow: workflow.created_at, reverse=True)
        return _slice_items(workflows, limit, offset)

    def get_workflow(self, workflow_id: str) -> WorkflowPackage | None:
        return self._workflows.get(workflow_id)

    def list_workflow_versions(self, workflow_id: str, limit: int | None = None, offset: int = 0) -> list[WorkflowPackage]:
        versions = sorted(
            self._workflow_versions.get(workflow_id, {}).values(),
            key=lambda workflow: workflow.created_at,
            reverse=True,
        )
        return _slice_items(versions, limit, offset)

    def get_workflow_version(self, workflow_id: str, version: str) -> WorkflowPackage | None:
        return self._workflow_versions.get(workflow_id, {}).get(version)

    def promote_workflow_version(self, workflow_id: str, version: str) -> WorkflowPackage | None:
        workflow_package = self.get_workflow_version(workflow_id, version)
        if workflow_package is None:
            return None
        self._workflows[workflow_id] = workflow_package
        return workflow_package

    def save_run(self, run: WorkflowRun) -> WorkflowRun:
        self._runs[run.run_id] = run
        return run

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    def get_run_by_idempotency_key(self, workflow_id: str, idempotency_key: str) -> WorkflowRun | None:
        return next(
            (
                run for run in self._runs.values()
                if run.workflow_id == workflow_id and run.idempotency_key == idempotency_key
            ),
            None,
        )

    def list_runs(
        self,
        workflow_id: str | None = None,
        status: WorkflowRunStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[WorkflowRun]:
        runs = list(self._runs.values())
        if workflow_id:
            runs = [run for run in runs if run.workflow_id == workflow_id]
        if status:
            runs = [run for run in runs if run.status == status]
        runs = sorted(runs, key=lambda run: run.created_at, reverse=True)
        return _slice_items(runs, limit, offset)

    def delete_runs(self, run_ids: list[str]) -> int:
        deleted_count = 0
        for run_id in set(run_ids):
            if self._runs.pop(run_id, None) is not None:
                deleted_count += 1
        return deleted_count

    def list_traces(
        self,
        workflow_id: str | None = None,
        run_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[TraceRecord]:
        runs = self.list_runs(workflow_id=workflow_id)
        if run_id:
            runs = [run for run in runs if run.run_id == run_id]
        traces = [trace for run in runs for trace in run.traces]
        return _slice_items(traces, limit, offset)

    def save_eval_results(self, workflow_id: str, results: list[EvalResult]) -> list[EvalResult]:
        self._eval_results_by_workflow_id[workflow_id].extend(results)
        return results

    def list_eval_results(self, workflow_id: str | None = None, limit: int | None = None, offset: int = 0) -> list[EvalResult]:
        if workflow_id:
            results = list(self._eval_results_by_workflow_id.get(workflow_id, []))
        else:
            results = [result for results in self._eval_results_by_workflow_id.values() for result in results]
        return _slice_items(results, limit, offset)

    def delete_eval_results(self, eval_ids: list[str], workflow_id: str | None = None) -> int:
        requested_ids = set(eval_ids)
        deleted_count = 0
        workflow_ids = [workflow_id] if workflow_id else list(self._eval_results_by_workflow_id)
        for item_workflow_id in workflow_ids:
            existing_results = self._eval_results_by_workflow_id.get(item_workflow_id, [])
            kept_results = [result for result in existing_results if result.eval_id not in requested_ids]
            deleted_count += len(existing_results) - len(kept_results)
            self._eval_results_by_workflow_id[item_workflow_id] = kept_results
        return deleted_count

    def save_audit_event(self, event: AuditEvent) -> AuditEvent:
        self._audit_events[event.event_id] = event
        return event

    def list_audit_events(
        self,
        workflow_id: str | None = None,
        run_id: str | None = None,
        event_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AuditEvent]:
        events = list(self._audit_events.values())
        if workflow_id:
            events = [event for event in events if event.workflow_id == workflow_id]
        if run_id:
            events = [event for event in events if event.run_id == run_id]
        if event_type:
            events = [event for event in events if event.event_type == event_type]
        events = sorted(events, key=lambda event: event.created_at, reverse=True)
        return _slice_items(events, limit, offset)


def _slice_items(items: list[T], limit: int | None, offset: int) -> list[T]:
    start = max(0, offset)
    if limit is None:
        return items[start:]
    return items[start:start + max(0, limit)]
