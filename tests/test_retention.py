from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from packages.workflow_core.models import AuditEvent, EvalResult, WorkflowRun
from packages.workflow_core.models.enums import WorkflowRunStatus
from packages.workflow_core.ops.retention import RetentionPolicy, apply_retention_policy, build_retention_report, main
from packages.workflow_core.storage import MemoryWorkflowRepository


NOW = datetime(2026, 6, 8, tzinfo=timezone.utc)


def test_retention_report_counts_expired_low_sensitive_accounts() -> None:
    repository = MemoryWorkflowRepository()
    old_time = NOW - timedelta(days=120)
    recent_time = NOW - timedelta(days=10)
    repository.save_run(
        WorkflowRun(
            run_id="old-terminal-run",
            workflow_id="workflow-1",
            status=WorkflowRunStatus.COMPLETED,
            updated_at=old_time,
            input_payload={"secret": "raw-value"},
        )
    )
    repository.save_run(
        WorkflowRun(
            run_id="old-active-run",
            workflow_id="workflow-1",
            status=WorkflowRunStatus.RUNNING,
            updated_at=old_time,
        )
    )
    repository.save_run(
        WorkflowRun(
            run_id="recent-terminal-run",
            workflow_id="workflow-1",
            status=WorkflowRunStatus.COMPLETED,
            updated_at=recent_time,
        )
    )
    repository.save_eval_results(
        "workflow-1",
        [
            EvalResult(
                eval_id="old-eval",
                workflow_id="workflow-1",
                score=1,
                passed=True,
                reason="old",
                created_at=old_time,
            )
        ],
    )
    repository.save_audit_event(
        AuditEvent(
            event_id="old-audit",
            event_type="workflow_run_start",
            action="start",
            status="succeeded",
            actor_id="admin",
            actor_role="workflow-admin",
            workflow_id="workflow-1",
            resource_type="workflow_run",
            resource_id="old-terminal-run",
            created_at=old_time,
        )
    )

    report = build_retention_report(
        repository,
        RetentionPolicy(run_retention_days=90, eval_retention_days=90, audit_retention_days=90),
        workflow_id="workflow-1",
        now=NOW,
    )

    assert report.run_account["expired_terminal_run_count"] == 1
    assert report.run_account["active_run_past_retention_count"] == 1
    assert report.eval_account["expired_eval_result_count"] == 1
    assert report.audit_account["expired_audit_event_count"] == 1
    items = {item["category"]: item for item in report.retention_items}
    assert items["terminal_runs"]["sample_ids"] == ["old-terminal-run"]
    assert items["active_runs_past_retention"]["recommendation"] == "investigate_or_cancel_before_retention_action"
    assert "raw-value" not in report.model_dump_json()


def test_retention_policy_rejects_non_positive_values() -> None:
    with pytest.raises(ValueError, match="run_retention_days"):
        build_retention_report(
            MemoryWorkflowRepository(),
            RetentionPolicy(run_retention_days=0),
            now=NOW,
        )


def test_retention_apply_deletes_only_terminal_runs_and_eval_results() -> None:
    repository = MemoryWorkflowRepository()
    old_time = NOW - timedelta(days=120)
    repository.save_run(
        WorkflowRun(
            run_id="old-terminal-run",
            workflow_id="workflow-1",
            status=WorkflowRunStatus.COMPLETED,
            updated_at=old_time,
        )
    )
    repository.save_run(
        WorkflowRun(
            run_id="old-active-run",
            workflow_id="workflow-1",
            status=WorkflowRunStatus.RUNNING,
            updated_at=old_time,
        )
    )
    repository.save_eval_results(
        "workflow-1",
        [
            EvalResult(
                eval_id="old-eval",
                workflow_id="workflow-1",
                score=1,
                passed=True,
                reason="old",
                created_at=old_time,
            )
        ],
    )
    repository.save_audit_event(
        AuditEvent(
            event_id="old-audit",
            event_type="workflow_run_start",
            action="start",
            status="succeeded",
            actor_id="admin",
            actor_role="workflow-admin",
            workflow_id="workflow-1",
            resource_type="workflow_run",
            resource_id="old-terminal-run",
            created_at=old_time,
        )
    )
    policy = RetentionPolicy(run_retention_days=90, eval_retention_days=90, audit_retention_days=90)

    dry_run = apply_retention_policy(repository, policy, workflow_id="workflow-1", dry_run=True, now=NOW)
    applied = apply_retention_policy(repository, policy, workflow_id="workflow-1", dry_run=False, now=NOW)

    assert dry_run.eligible_counts == {"terminal_runs": 1, "eval_results": 1}
    assert dry_run.deleted_counts == {"terminal_runs": 0, "eval_results": 0}
    assert applied.deleted_counts == {"terminal_runs": 1, "eval_results": 1}
    assert applied.skipped_counts == {"active_runs_past_retention": 1, "audit_events": 1}
    assert repository.get_run("old-terminal-run") is None
    assert repository.get_run("old-active-run") is not None
    assert repository.list_eval_results("workflow-1") == []
    assert repository.list_audit_events(workflow_id="workflow-1")


def test_retention_apply_rejects_report_only_categories() -> None:
    with pytest.raises(ValueError, match="report-only"):
        apply_retention_policy(
            MemoryWorkflowRepository(),
            RetentionPolicy(),
            categories=["audit_events"],
            dry_run=False,
            now=NOW,
        )


def test_retention_apply_cli_requires_snapshot_and_reason(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.sqlite3'}"

    with pytest.raises(SystemExit):
        main(["--database-url", database_url, "--apply", "--confirm-retention-apply"])

    with pytest.raises(SystemExit):
        main([
            "--database-url",
            database_url,
            "--apply",
            "--confirm-retention-apply",
            "--snapshot-acknowledged",
        ])
