from pathlib import Path

from packages.workflow_core.models import WorkflowPackage
from packages.workflow_core.ops.smoke import _load_smoke_package


def test_smoke_package_rewrites_workflow_id_without_breaking_references() -> None:
    payload = _load_smoke_package(Path("examples/new_product_launch.workflow.json"))

    workflow = WorkflowPackage.model_validate(payload)

    assert workflow.workflow_id.startswith("smoke-")
    assert workflow.process_spec.workflow_id == workflow.workflow_id
    assert all(eval_spec.workflow_id == workflow.workflow_id for eval_spec in workflow.eval_specs)
    assert workflow.name.startswith("Smoke Check Workflow")
