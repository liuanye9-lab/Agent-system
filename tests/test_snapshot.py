from __future__ import annotations

import json
from pathlib import Path

from packages.workflow_core.models import AuditEvent, EvalResult, WorkflowPackage, WorkflowRun
from packages.workflow_core.models.enums import WorkflowRunStatus
from packages.workflow_core.ops.snapshot import (
    export_repository_snapshot,
    import_repository_snapshot,
    snapshot_from_file,
    snapshot_to_file,
)
from packages.workflow_core.storage import MemoryWorkflowRepository


def load_example_workflow() -> WorkflowPackage:
    payload = json.loads(Path("examples/new_product_launch.workflow.json").read_text(encoding="utf-8"))
    return WorkflowPackage.model_validate(payload)


def populated_repository() -> MemoryWorkflowRepository:
    repository = MemoryWorkflowRepository()
    workflow = load_example_workflow()
    candidate = workflow.model_copy(update={"version": "0.2.0"})
    repository.save_workflow(workflow)
    repository.save_workflow_version(candidate)
    repository.save_run(
        WorkflowRun(
            run_id="run-1",
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.version,
            status=WorkflowRunStatus.COMPLETED,
            input_payload={"product": "platform"},
            output_payload={"decision": "ready"},
        )
    )
    repository.save_eval_results(
        workflow.workflow_id,
        [
            EvalResult(
                eval_id="eval-1",
                workflow_id=workflow.workflow_id,
                score=1.0,
                passed=True,
                reason="matched expected output",
            )
        ],
    )
    repository.save_audit_event(
        AuditEvent(
            event_id="audit-1",
            event_type="workflow_snapshot_test",
            action="seed",
            status="succeeded",
            actor_id="tester",
            actor_role="workflow-admin",
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.version,
            resource_type="workflow_package",
            resource_id=f"{workflow.workflow_id}@{workflow.version}",
        )
    )
    return repository


def test_repository_snapshot_export_import_round_trip() -> None:
    source = populated_repository()

    snapshot = export_repository_snapshot(source)

    assert snapshot.summary()["current_workflow_count"] == 1
    assert snapshot.summary()["workflow_version_count"] == 2
    assert snapshot.summary()["run_count"] == 1
    assert snapshot.summary()["eval_result_count"] == 1
    assert snapshot.summary()["audit_event_count"] == 1

    target = MemoryWorkflowRepository()
    report = import_repository_snapshot(target, snapshot)

    assert report.current_workflows_imported == 1
    assert report.workflow_versions_imported == 2
    assert report.runs_imported == 1
    assert report.eval_results_imported == 1
    assert report.audit_events_imported == 1
    assert target.get_workflow("new-product-launch") is not None
    assert target.get_workflow_version("new-product-launch", "0.2.0") is not None
    assert target.get_run("run-1") is not None
    assert len(target.list_eval_results("new-product-launch")) == 1
    assert len(target.list_audit_events(workflow_id="new-product-launch")) == 1


def test_repository_snapshot_import_skips_existing_eval_results() -> None:
    target = populated_repository()
    snapshot = export_repository_snapshot(target)

    report = import_repository_snapshot(target, snapshot)

    assert report.eval_results_imported == 0
    assert report.eval_results_skipped == 1
    assert len(target.list_eval_results("new-product-launch")) == 1


def test_repository_snapshot_file_round_trip(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot = export_repository_snapshot(populated_repository(), workflow_id="new-product-launch")

    snapshot_to_file(snapshot, snapshot_path)
    loaded = snapshot_from_file(snapshot_path)

    assert loaded.schema_version == snapshot.schema_version
    assert loaded.workflow_id == "new-product-launch"
    assert loaded.summary() == snapshot.summary()
