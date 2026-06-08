from __future__ import annotations

import json
from pathlib import Path

from packages.workflow_core.governance import EvalRunner
from packages.workflow_core.models import EvalSpec, WorkflowPackage
from packages.workflow_core.models.enums import EvalType


def test_eval_runner_executes_workflow_eval_case() -> None:
    payload = json.loads(Path("examples/new_product_launch.workflow.json").read_text(encoding="utf-8"))
    workflow_package = WorkflowPackage.model_validate(payload)

    results = EvalRunner().run_all(workflow_package)

    assert len(results) >= 1
    assert results[0].workflow_id == workflow_package.workflow_id
    assert results[0].passed is True
    assert 0 <= results[0].score <= 1
    assert results[0].details["run_status"] == "paused"
    assert results[0].details["trace_count"] == 7
    assert results[0].details["compared_path_count"] == 1
    assert results[0].details["matched_path_count"] == 1
    assert all(item["passed"] for item in results[0].details["rule_results"])


def test_eval_runner_can_emit_deterministic_failure() -> None:
    payload = json.loads(Path("examples/new_product_launch.workflow.json").read_text(encoding="utf-8"))
    payload["eval_specs"][0]["scoring_rules"] = ["force_fail"]
    workflow_package = WorkflowPackage.model_validate(payload)

    results = EvalRunner().run_all(workflow_package)

    assert results[0].passed is False
    assert results[0].score == 0
    assert "force_fail" in results[0].reason


def test_eval_runner_scores_expected_output_mismatch_from_real_node_output() -> None:
    payload = json.loads(Path("examples/new_product_launch.workflow.json").read_text(encoding="utf-8"))
    workflow_package = WorkflowPackage.model_validate(payload)
    failing_eval = EvalSpec(
        eval_id="node-mismatch",
        workflow_id=workflow_package.workflow_id,
        name="Node mismatch",
        eval_type=EvalType.NODE,
        target_node_id="business-case",
        input_case={"product": "AI workflow platform"},
        expected_output={"decision": "rework", "risks": ["must-not-match"]},
        scoring_rules=["review_node 必须输出 decision", "风险必须显式列出"],
    )

    result = EvalRunner().run_eval(workflow_package, failing_eval)

    assert result.passed is False
    assert result.score < 0.8
    assert result.details["target_trace_found"] is True
    assert result.details["compared_path_count"] == 2
    assert result.details["matched_path_count"] == 0
    assert set(result.details["mismatched_paths"]) == {"decision", "risks"}
