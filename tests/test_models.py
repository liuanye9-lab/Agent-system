from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.workflow_core.models import WorkflowPackage


EXAMPLE_PATH = Path("examples/new_product_launch.workflow.json")


def load_example_payload() -> dict:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def test_workflow_package_schema_validates() -> None:
    workflow_package = WorkflowPackage.model_validate(load_example_payload())

    assert workflow_package.workflow_id == "new-product-launch"
    assert len(workflow_package.process_spec.nodes) == 7
    assert workflow_package.agent_specs[0].model_settings["provider"] == "mock"


def test_missing_required_field_raises_validation_error() -> None:
    payload = load_example_payload()
    payload.pop("name")

    with pytest.raises(ValidationError):
        WorkflowPackage.model_validate(payload)
