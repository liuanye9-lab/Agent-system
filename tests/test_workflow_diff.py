from __future__ import annotations

import json
from pathlib import Path

from packages.workflow_core.models import WorkflowPackage
from packages.workflow_core.storage import diff_workflow_packages


def test_workflow_diff_reports_changed_fields() -> None:
    payload = json.loads(Path("examples/new_product_launch.workflow.json").read_text(encoding="utf-8"))
    first = WorkflowPackage.model_validate(payload)
    second = first.model_copy(update={"version": "0.2.0", "name": "新品上市流程智能体 v2"})

    changes = diff_workflow_packages(first, second)

    paths = {change["path"] for change in changes}
    assert "$.version" in paths
    assert "$.name" in paths
