from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from packages.workflow_core.models.common import StrictBaseModel, utc_now
from packages.workflow_core.models.enums import WorkflowRunStatus
from packages.workflow_core.storage import SQLiteWorkflowRepository, WorkflowRepository

RETENTION_DELETABLE_CATEGORIES = {"terminal_runs", "eval_results"}
_TERMINAL_RUN_STATUSES = {
    WorkflowRunStatus.COMPLETED,
    WorkflowRunStatus.FAILED,
    WorkflowRunStatus.REJECTED,
    WorkflowRunStatus.CANCELED,
}


@dataclass(frozen=True)
class RetentionPolicy:
    run_retention_days: int = 90
    eval_retention_days: int = 365
    audit_retention_days: int = 365
    sample_limit: int = 20

    def validate(self) -> None:
        for name, value in (
            ("run_retention_days", self.run_retention_days),
            ("eval_retention_days", self.eval_retention_days),
            ("audit_retention_days", self.audit_retention_days),
            ("sample_limit", self.sample_limit),
        ):
            if value < 1:
                raise ValueError(f"{name} must be at least 1")


class RetentionReport(StrictBaseModel):
    generated_at: datetime
    workflow_id: str | None = None
    policy: dict[str, int]
    cutoffs: dict[str, str]
    run_account: dict[str, Any]
    eval_account: dict[str, Any]
    audit_account: dict[str, Any]
    retention_items: list[dict[str, Any]]

    @property
    def has_expired_items(self) -> bool:
        return any(item["count"] > 0 for item in self.retention_items)


class RetentionApplyReport(StrictBaseModel):
    generated_at: datetime
    workflow_id: str | None = None
    dry_run: bool
    requested_categories: list[str]
    eligible_counts: dict[str, int]
    deleted_counts: dict[str, int]
    skipped_counts: dict[str, int]
    retention_report: RetentionReport


def build_retention_report(
    repository: WorkflowRepository,
    policy: RetentionPolicy,
    workflow_id: str | None = None,
    now: datetime | None = None,
) -> RetentionReport:
    policy.validate()
    candidates = _retention_candidates(repository, policy, workflow_id, now)

    retention_items = [
        _retention_item(
            category="terminal_runs",
            count=len(candidates["expired_terminal_runs"]),
            sample_ids=[run.run_id for run in candidates["expired_terminal_runs"]],
            recommendation="snapshot_then_archive_or_purge_terminal_runs",
            sample_limit=policy.sample_limit,
        ),
        _retention_item(
            category="active_runs_past_retention",
            count=len(candidates["old_active_runs"]),
            sample_ids=[run.run_id for run in candidates["old_active_runs"]],
            recommendation="investigate_or_cancel_before_retention_action",
            sample_limit=policy.sample_limit,
        ),
        _retention_item(
            category="eval_results",
            count=len(candidates["expired_eval_results"]),
            sample_ids=[result.eval_id for result in candidates["expired_eval_results"]],
            recommendation="snapshot_then_archive_or_purge_old_eval_results",
            sample_limit=policy.sample_limit,
        ),
        _retention_item(
            category="audit_events",
            count=len(candidates["expired_audit_events"]),
            sample_ids=[event.event_id for event in candidates["expired_audit_events"]],
            recommendation="retain_for_compliance_or_export_before_archive",
            sample_limit=policy.sample_limit,
        ),
    ]

    return RetentionReport(
        generated_at=candidates["current_time"],
        workflow_id=workflow_id,
        policy={
            "run_retention_days": policy.run_retention_days,
            "eval_retention_days": policy.eval_retention_days,
            "audit_retention_days": policy.audit_retention_days,
            "sample_limit": policy.sample_limit,
        },
        cutoffs={
            "runs_before": candidates["run_cutoff"].isoformat(),
            "evals_before": candidates["eval_cutoff"].isoformat(),
            "audit_events_before": candidates["audit_cutoff"].isoformat(),
        },
        run_account={
            "run_count": len(candidates["runs"]),
            "terminal_run_count": sum(1 for run in candidates["runs"] if run.status in _TERMINAL_RUN_STATUSES),
            "active_run_count": sum(1 for run in candidates["runs"] if run.status not in _TERMINAL_RUN_STATUSES),
            "expired_terminal_run_count": len(candidates["expired_terminal_runs"]),
            "active_run_past_retention_count": len(candidates["old_active_runs"]),
            "status_counts": dict(Counter(run.status.value for run in candidates["runs"])),
        },
        eval_account={
            "eval_result_count": len(candidates["eval_results"]),
            "expired_eval_result_count": len(candidates["expired_eval_results"]),
            "passed_count": sum(1 for result in candidates["eval_results"] if result.passed),
            "failed_count": sum(1 for result in candidates["eval_results"] if not result.passed),
        },
        audit_account={
            "audit_event_count": len(candidates["audit_events"]),
            "expired_audit_event_count": len(candidates["expired_audit_events"]),
            "event_type_counts": dict(Counter(event.event_type for event in candidates["audit_events"])),
        },
        retention_items=retention_items,
    )


def apply_retention_policy(
    repository: WorkflowRepository,
    policy: RetentionPolicy,
    workflow_id: str | None = None,
    categories: list[str] | None = None,
    dry_run: bool = True,
    now: datetime | None = None,
) -> RetentionApplyReport:
    policy.validate()
    requested_categories = categories or sorted(RETENTION_DELETABLE_CATEGORIES)
    unknown_categories = sorted(set(requested_categories) - {"terminal_runs", "eval_results", "active_runs_past_retention", "audit_events"})
    if unknown_categories:
        raise ValueError(f"unknown retention categories: {', '.join(unknown_categories)}")
    non_deletable_categories = sorted(set(requested_categories) - RETENTION_DELETABLE_CATEGORIES)
    if non_deletable_categories:
        raise ValueError(f"retention categories are report-only and cannot be applied: {', '.join(non_deletable_categories)}")
    candidates = _retention_candidates(repository, policy, workflow_id, now)
    report = build_retention_report(repository, policy, workflow_id=workflow_id, now=candidates["current_time"])
    terminal_run_ids = [run.run_id for run in candidates["expired_terminal_runs"]]
    eval_ids = [result.eval_id for result in candidates["expired_eval_results"]]
    eligible_counts = {
        "terminal_runs": len(terminal_run_ids) if "terminal_runs" in requested_categories else 0,
        "eval_results": len(eval_ids) if "eval_results" in requested_categories else 0,
    }
    deleted_counts = {
        "terminal_runs": 0,
        "eval_results": 0,
    }
    if not dry_run:
        if "terminal_runs" in requested_categories:
            deleted_counts["terminal_runs"] = repository.delete_runs(terminal_run_ids)
        if "eval_results" in requested_categories:
            deleted_counts["eval_results"] = repository.delete_eval_results(eval_ids, workflow_id=workflow_id)
    return RetentionApplyReport(
        generated_at=candidates["current_time"],
        workflow_id=workflow_id,
        dry_run=dry_run,
        requested_categories=requested_categories,
        eligible_counts=eligible_counts,
        deleted_counts=deleted_counts,
        skipped_counts={
            "active_runs_past_retention": len(candidates["old_active_runs"]),
            "audit_events": len(candidates["expired_audit_events"]),
        },
        retention_report=report,
    )


def main(argv: list[str] | None = None) -> int:
    from apps.api.settings import settings

    parser = argparse.ArgumentParser(description="Build a low-sensitive repository retention report.")
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--workflow-id")
    parser.add_argument("--run-retention-days", type=int, default=settings.run_retention_days)
    parser.add_argument("--eval-retention-days", type=int, default=settings.eval_retention_days)
    parser.add_argument("--audit-retention-days", type=int, default=settings.audit_retention_days)
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete expired terminal runs and eval results. Active runs and audit events are never deleted.",
    )
    parser.add_argument(
        "--category",
        action="append",
        choices=sorted(RETENTION_DELETABLE_CATEGORIES),
        help="Retention category to apply. May be passed multiple times. Defaults to terminal_runs and eval_results.",
    )
    parser.add_argument(
        "--confirm-retention-apply",
        action="store_true",
        help="Required with --apply to perform deletion. Without it, --apply exits with an error.",
    )
    parser.add_argument(
        "--snapshot-acknowledged",
        action="store_true",
        help="Required with --apply to confirm a repository snapshot was reviewed before deletion.",
    )
    parser.add_argument(
        "--reason",
        help="Required with --apply to record why retention deletion is being performed.",
    )
    args = parser.parse_args(argv)

    policy = RetentionPolicy(
        run_retention_days=args.run_retention_days,
        eval_retention_days=args.eval_retention_days,
        audit_retention_days=args.audit_retention_days,
        sample_limit=args.sample_limit,
    )
    if args.apply and not args.confirm_retention_apply:
        parser.error("--apply requires --confirm-retention-apply")
    if args.apply and not args.snapshot_acknowledged:
        parser.error("--apply requires --snapshot-acknowledged")
    if args.apply and not args.reason:
        parser.error("--apply requires --reason")
    repository = SQLiteWorkflowRepository(args.database_url)
    if args.apply:
        report = apply_retention_policy(
            repository,
            policy,
            workflow_id=args.workflow_id,
            categories=args.category,
            dry_run=False,
        )
    else:
        report = build_retention_report(
            repository,
            policy,
            workflow_id=args.workflow_id,
        )
    print(report.model_dump_json(indent=2))
    return 0


def _retention_item(
    category: str,
    count: int,
    sample_ids: list[str],
    recommendation: str,
    sample_limit: int,
) -> dict[str, Any]:
    return {
        "category": category,
        "count": count,
        "sample_ids": sample_ids[:sample_limit],
        "sample_truncated": len(sample_ids) > sample_limit,
        "recommendation": recommendation,
    }


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _retention_candidates(
    repository: WorkflowRepository,
    policy: RetentionPolicy,
    workflow_id: str | None,
    now: datetime | None,
) -> dict[str, Any]:
    current_time = _ensure_aware(now or utc_now())
    run_cutoff = current_time - timedelta(days=policy.run_retention_days)
    eval_cutoff = current_time - timedelta(days=policy.eval_retention_days)
    audit_cutoff = current_time - timedelta(days=policy.audit_retention_days)
    runs = repository.list_runs(workflow_id=workflow_id)
    eval_results = repository.list_eval_results(workflow_id=workflow_id)
    audit_events = repository.list_audit_events(workflow_id=workflow_id)
    return {
        "current_time": current_time,
        "run_cutoff": run_cutoff,
        "eval_cutoff": eval_cutoff,
        "audit_cutoff": audit_cutoff,
        "runs": runs,
        "eval_results": eval_results,
        "audit_events": audit_events,
        "expired_terminal_runs": [
            run for run in runs
            if run.status in _TERMINAL_RUN_STATUSES and _ensure_aware(run.updated_at) < run_cutoff
        ],
        "old_active_runs": [
            run for run in runs
            if run.status not in _TERMINAL_RUN_STATUSES and _ensure_aware(run.updated_at) < run_cutoff
        ],
        "expired_eval_results": [
            result for result in eval_results
            if _ensure_aware(result.created_at) < eval_cutoff
        ],
        "expired_audit_events": [
            event for event in audit_events
            if _ensure_aware(event.created_at) < audit_cutoff
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
