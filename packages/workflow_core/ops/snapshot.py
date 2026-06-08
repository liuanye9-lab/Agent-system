from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from pydantic import Field

from packages.workflow_core.models import AuditEvent, EvalResult, WorkflowPackage, WorkflowRun
from packages.workflow_core.models.common import StrictBaseModel, utc_now
from packages.workflow_core.storage import WorkflowRepository

SNAPSHOT_SCHEMA_VERSION = "agent-workflow-builder.snapshot.v1"


class RepositorySnapshot(StrictBaseModel):
    schema_version: str = SNAPSHOT_SCHEMA_VERSION
    exported_at: datetime = Field(default_factory=utc_now)
    workflow_id: str | None = None
    current_workflows: list[WorkflowPackage] = Field(default_factory=list)
    workflow_versions: list[WorkflowPackage] = Field(default_factory=list)
    runs: list[WorkflowRun] = Field(default_factory=list)
    eval_results: list[EvalResult] = Field(default_factory=list)
    audit_events: list[AuditEvent] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workflow_id": self.workflow_id,
            "current_workflow_count": len(self.current_workflows),
            "workflow_version_count": len(self.workflow_versions),
            "run_count": len(self.runs),
            "eval_result_count": len(self.eval_results),
            "audit_event_count": len(self.audit_events),
        }


class SnapshotImportReport(StrictBaseModel):
    schema_version: str
    current_workflows_imported: int = 0
    workflow_versions_imported: int = 0
    runs_imported: int = 0
    eval_results_imported: int = 0
    eval_results_skipped: int = 0
    audit_events_imported: int = 0


def export_repository_snapshot(
    repository: WorkflowRepository,
    workflow_id: str | None = None,
    include_runs: bool = True,
    include_eval_results: bool = True,
    include_audit_events: bool = True,
) -> RepositorySnapshot:
    current_workflows = repository.list_workflows()
    if workflow_id:
        current_workflows = [workflow for workflow in current_workflows if workflow.workflow_id == workflow_id]

    runs = repository.list_runs(workflow_id=workflow_id) if include_runs else []
    eval_results = repository.list_eval_results(workflow_id=workflow_id) if include_eval_results else []
    audit_events = repository.list_audit_events(workflow_id=workflow_id) if include_audit_events else []

    workflow_ids = _workflow_ids(current_workflows, runs, eval_results, audit_events, workflow_id)
    workflow_versions = [
        version
        for candidate_workflow_id in sorted(workflow_ids)
        for version in repository.list_workflow_versions(candidate_workflow_id)
    ]

    return RepositorySnapshot(
        workflow_id=workflow_id,
        current_workflows=_sort_workflows(current_workflows),
        workflow_versions=_sort_workflows(_deduplicate_versions(workflow_versions)),
        runs=sorted(runs, key=lambda run: run.created_at.isoformat()),
        eval_results=sorted(eval_results, key=lambda result: (result.workflow_id, result.eval_id)),
        audit_events=sorted(audit_events, key=lambda event: event.created_at.isoformat()),
    )


def import_repository_snapshot(
    repository: WorkflowRepository,
    snapshot: RepositorySnapshot,
    skip_existing_eval_results: bool = True,
) -> SnapshotImportReport:
    if snapshot.schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"unsupported snapshot schema version: {snapshot.schema_version}")

    report = preview_repository_snapshot_import(
        repository,
        snapshot,
        skip_existing_eval_results=skip_existing_eval_results,
    )

    for workflow in snapshot.workflow_versions:
        repository.save_workflow_version(workflow)
    for workflow in snapshot.current_workflows:
        repository.save_workflow(workflow)
    for run in snapshot.runs:
        repository.save_run(run)

    existing_eval_ids = {
        (result.workflow_id, result.eval_id)
        for result in repository.list_eval_results()
    } if skip_existing_eval_results else set()
    results_by_workflow_id: dict[str, list[EvalResult]] = defaultdict(list)
    for result in snapshot.eval_results:
        if (result.workflow_id, result.eval_id) in existing_eval_ids:
            continue
        results_by_workflow_id[result.workflow_id].append(result)
    for workflow_id, results in results_by_workflow_id.items():
        repository.save_eval_results(workflow_id, results)

    for event in snapshot.audit_events:
        repository.save_audit_event(event)

    return report


def preview_repository_snapshot_import(
    repository: WorkflowRepository,
    snapshot: RepositorySnapshot,
    skip_existing_eval_results: bool = True,
) -> SnapshotImportReport:
    if snapshot.schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"unsupported snapshot schema version: {snapshot.schema_version}")

    report = SnapshotImportReport(
        schema_version=snapshot.schema_version,
        current_workflows_imported=len(snapshot.current_workflows),
        workflow_versions_imported=len(snapshot.workflow_versions),
        runs_imported=len(snapshot.runs),
        audit_events_imported=len(snapshot.audit_events),
    )
    existing_eval_ids = {
        (result.workflow_id, result.eval_id)
        for result in repository.list_eval_results()
    } if skip_existing_eval_results else set()
    for result in snapshot.eval_results:
        if (result.workflow_id, result.eval_id) in existing_eval_ids:
            report.eval_results_skipped += 1
            continue
        report.eval_results_imported += 1
    return report


def snapshot_to_file(snapshot: RepositorySnapshot, path: Path | str) -> None:
    Path(path).write_text(
        snapshot.model_dump_json(by_alias=True, indent=2),
        encoding="utf-8",
    )


def snapshot_from_file(path: Path | str) -> RepositorySnapshot:
    return RepositorySnapshot.model_validate_json(Path(path).read_text(encoding="utf-8"))


def snapshot_summary_from_file(path: Path | str) -> dict[str, Any]:
    return snapshot_from_file(path).summary()


def main(argv: list[str] | None = None) -> int:
    from apps.api.settings import settings
    from packages.workflow_core.storage import SQLiteWorkflowRepository

    parser = argparse.ArgumentParser(description="Export, import, or inspect repository snapshots.")
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="Repository database URL. Defaults to AGENT_WORKFLOW_DATABASE_URL.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Export a repository snapshot to JSON.")
    export_parser.add_argument("path", help="Output snapshot JSON path.")
    export_parser.add_argument("--workflow-id", help="Export a single workflow account.")
    export_parser.add_argument("--exclude-runs", action="store_true", help="Exclude workflow runs and traces.")
    export_parser.add_argument("--exclude-evals", action="store_true", help="Exclude eval results.")
    export_parser.add_argument("--exclude-audit", action="store_true", help="Exclude audit events.")

    import_parser = subparsers.add_parser("import", help="Import a repository snapshot JSON file.")
    import_parser.add_argument("path", help="Input snapshot JSON path.")
    import_parser.add_argument(
        "--allow-duplicate-evals",
        action="store_true",
        help="Import eval results even when the target already has the same workflow_id/eval_id.",
    )

    summary_parser = subparsers.add_parser("summary", help="Print a snapshot summary.")
    summary_parser.add_argument("path", help="Input snapshot JSON path.")

    args = parser.parse_args(argv)

    if args.command == "summary":
        print(json.dumps(snapshot_summary_from_file(args.path), ensure_ascii=False, indent=2))
        return 0

    repository = SQLiteWorkflowRepository(args.database_url)
    if args.command == "export":
        snapshot = export_repository_snapshot(
            repository,
            workflow_id=args.workflow_id,
            include_runs=not args.exclude_runs,
            include_eval_results=not args.exclude_evals,
            include_audit_events=not args.exclude_audit,
        )
        snapshot_to_file(snapshot, args.path)
        print(json.dumps(snapshot.summary(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "import":
        snapshot = snapshot_from_file(args.path)
        report = import_repository_snapshot(
            repository,
            snapshot,
            skip_existing_eval_results=not args.allow_duplicate_evals,
        )
        print(report.model_dump_json(indent=2))
        return 0

    raise AssertionError(f"unknown command: {args.command}")


def _workflow_ids(
    workflows: list[WorkflowPackage],
    runs: list[WorkflowRun],
    eval_results: list[EvalResult],
    audit_events: list[AuditEvent],
    workflow_id: str | None,
) -> set[str]:
    workflow_ids = {workflow.workflow_id for workflow in workflows}
    workflow_ids.update(run.workflow_id for run in runs)
    workflow_ids.update(result.workflow_id for result in eval_results)
    workflow_ids.update(event.workflow_id for event in audit_events if event.workflow_id)
    if workflow_id:
        workflow_ids.add(workflow_id)
    return workflow_ids


def _deduplicate_versions(workflows: list[WorkflowPackage]) -> list[WorkflowPackage]:
    deduped: dict[tuple[str, str], WorkflowPackage] = {}
    for workflow in workflows:
        deduped[(workflow.workflow_id, workflow.version)] = workflow
    return list(deduped.values())


def _sort_workflows(workflows: list[WorkflowPackage]) -> list[WorkflowPackage]:
    return sorted(workflows, key=lambda workflow: (workflow.workflow_id, workflow.version))
