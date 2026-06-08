from __future__ import annotations

import json
from pathlib import Path

from packages.workflow_core.models import WorkflowPackage
from packages.workflow_core.models.enums import PermissionLevel
from packages.workflow_core.validation import WorkflowPackageLinter


def load_example() -> WorkflowPackage:
    payload = json.loads(Path("examples/new_product_launch.workflow.json").read_text(encoding="utf-8"))
    return WorkflowPackage.model_validate(payload)


def issue_codes(report) -> set[str]:
    return {issue.code for issue in report.errors + report.warnings}


def test_workflow_linter_accepts_example_package() -> None:
    report = WorkflowPackageLinter().lint(load_example())

    assert report.valid is True
    assert report.errors == []


def test_workflow_linter_detects_unreachable_node() -> None:
    workflow_package = load_example()
    isolated_node = workflow_package.process_spec.nodes[0].model_copy(update={"node_id": "isolated-node"})
    process_spec = workflow_package.process_spec.model_copy(
        update={"nodes": [*workflow_package.process_spec.nodes, isolated_node]}
    )
    workflow_package = workflow_package.model_copy(update={"process_spec": process_spec})

    report = WorkflowPackageLinter().lint(workflow_package)

    assert report.valid is False
    assert "unreachable_node" in issue_codes(report)


def test_workflow_linter_detects_read_node_bound_to_write_tool() -> None:
    workflow_package = load_example()
    write_tool = next(tool for tool in workflow_package.tool_policies if tool.tool_id == "mock-go-no-go-decision-tool")
    read_node = next(node for node in workflow_package.process_spec.nodes if node.node_id == "collect-market-insights")
    updated_read_node = read_node.model_copy(
        update={
            "tool_ids": [write_tool.tool_id],
            "requires_approval": True,
        }
    )
    process_spec = workflow_package.process_spec.model_copy(
        update={
            "nodes": [
                updated_read_node if node.node_id == read_node.node_id else node
                for node in workflow_package.process_spec.nodes
            ]
        }
    )
    workflow_package = workflow_package.model_copy(update={"process_spec": process_spec})

    report = WorkflowPackageLinter().lint(workflow_package)

    assert report.valid is False
    assert "read_node_with_write_tool" in issue_codes(report)


def test_workflow_linter_detects_invalid_tool_schema() -> None:
    workflow_package = load_example()
    tool = workflow_package.tool_policies[0]
    invalid_tool = tool.model_copy(update={"input_schema": {"type": "not-a-json-schema-type"}})
    workflow_package = workflow_package.model_copy(
        update={
            "tool_policies": [
                invalid_tool if candidate.tool_id == tool.tool_id else candidate
                for candidate in workflow_package.tool_policies
            ]
        }
    )

    report = WorkflowPackageLinter().lint(workflow_package)

    assert report.valid is False
    assert "invalid_json_schema" in issue_codes(report)


def test_workflow_linter_detects_forbidden_tool_binding() -> None:
    workflow_package = load_example()
    tool = workflow_package.tool_policies[0].model_copy(update={"permission_level": PermissionLevel.FORBIDDEN})
    workflow_package = workflow_package.model_copy(
        update={
            "tool_policies": [
                tool if candidate.tool_id == tool.tool_id else candidate
                for candidate in workflow_package.tool_policies
            ]
        }
    )

    report = WorkflowPackageLinter().lint(workflow_package)

    assert report.valid is False
    assert "forbidden_tool_bound_to_node" in issue_codes(report)


def test_workflow_linter_detects_unknown_tool_adapter() -> None:
    workflow_package = load_example()
    tool = workflow_package.tool_policies[0].model_copy(update={"adapter": "unsafe-http"})
    workflow_package = workflow_package.model_copy(
        update={
            "tool_policies": [
                tool if candidate.tool_id == tool.tool_id else candidate
                for candidate in workflow_package.tool_policies
            ]
        }
    )

    report = WorkflowPackageLinter().lint(workflow_package)

    assert report.valid is False
    assert "unknown_tool_adapter" in issue_codes(report)


def test_workflow_linter_detects_incomplete_mcp_binding() -> None:
    workflow_package = load_example()
    tool = workflow_package.tool_policies[0].model_copy(update={"adapter": "mcp", "server_id": "crm"})
    workflow_package = workflow_package.model_copy(
        update={
            "tool_policies": [
                tool if candidate.tool_id == tool.tool_id else candidate
                for candidate in workflow_package.tool_policies
            ]
        }
    )

    report = WorkflowPackageLinter().lint(workflow_package)

    assert report.valid is False
    assert "mcp_tool_missing_binding" in issue_codes(report)
