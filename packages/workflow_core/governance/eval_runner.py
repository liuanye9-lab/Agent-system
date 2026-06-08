from __future__ import annotations

from typing import Any

from packages.workflow_core.models import EvalResult, EvalSpec, TraceRecord, WorkflowPackage, WorkflowRun
from packages.workflow_core.models.enums import NodeExecutionStatus, WorkflowRunStatus
from packages.workflow_core.runtime import WorkflowRunner


class EvalRunner:
    def __init__(
        self,
        workflow_runner: WorkflowRunner | None = None,
        pass_threshold: float = 0.8,
    ) -> None:
        self.workflow_runner = workflow_runner or WorkflowRunner()
        self.pass_threshold = pass_threshold

    def run_eval(self, workflow_package: WorkflowPackage, eval_spec: EvalSpec) -> EvalResult:
        if any("force_fail" in rule.lower() for rule in eval_spec.scoring_rules):
            return EvalResult(
                eval_id=eval_spec.eval_id,
                workflow_id=workflow_package.workflow_id,
                score=0.0,
                passed=False,
                reason="mock eval failed because scoring_rules contains force_fail",
                details={
                    "eval_type": eval_spec.eval_type,
                    "target_node_id": eval_spec.target_node_id,
                    "rules_checked": eval_spec.scoring_rules,
                },
            )

        run = self.workflow_runner.run(
            workflow_package,
            input_payload=eval_spec.input_case,
            run_id=f"eval-{eval_spec.eval_id}",
            max_steps=200,
            max_retries=1,
            shadow_mode=_requires_shadow_mode(eval_spec.scoring_rules),
        )
        target_trace = _target_trace(run, eval_spec.target_node_id)
        target_payload = target_trace.output_snapshot if target_trace else run.output_payload
        comparison = _compare_expected_output(
            run=run,
            target_payload=target_payload,
            expected_output=eval_spec.expected_output,
        )
        rule_results = _evaluate_rules(run=run, target_payload=target_payload, rules=eval_spec.scoring_rules)
        total_checks = comparison["compared_path_count"] + len(rule_results)
        passed_checks = comparison["matched_path_count"] + sum(1 for item in rule_results if item["passed"])
        score = (passed_checks / total_checks) if total_checks else 1.0
        passed = score >= self.pass_threshold

        return EvalResult(
            eval_id=eval_spec.eval_id,
            workflow_id=workflow_package.workflow_id,
            score=score,
            passed=passed,
            reason=(
                "golden eval passed"
                if passed
                else "golden eval failed expected output or scoring rules"
            ),
            details={
                "eval_type": eval_spec.eval_type,
                "target_node_id": eval_spec.target_node_id,
                "run_id": run.run_id,
                "run_status": run.status,
                "trace_count": len(run.traces),
                "target_trace_found": target_trace is not None if eval_spec.target_node_id else None,
                "compared_path_count": comparison["compared_path_count"],
                "matched_path_count": comparison["matched_path_count"],
                "missing_paths": comparison["missing_paths"],
                "mismatched_paths": comparison["mismatched_paths"],
                "rule_results": rule_results,
                "pass_threshold": self.pass_threshold,
            },
        )

    def run_all(self, workflow_package: WorkflowPackage) -> list[EvalResult]:
        return [self.run_eval(workflow_package, eval_spec) for eval_spec in workflow_package.eval_specs]


def _requires_shadow_mode(scoring_rules: list[str]) -> bool:
    return any("shadow" in rule.lower() or "影子" in rule for rule in scoring_rules)


def _target_trace(run: WorkflowRun, target_node_id: str | None) -> TraceRecord | None:
    if target_node_id is None:
        return None
    for trace in reversed(run.traces):
        if trace.node_id == target_node_id and trace.status == NodeExecutionStatus.SUCCESS:
            return trace
    return next((trace for trace in reversed(run.traces) if trace.node_id == target_node_id), None)


def _compare_expected_output(
    *,
    run: WorkflowRun,
    target_payload: dict[str, Any],
    expected_output: dict[str, Any],
) -> dict[str, Any]:
    expected_paths = _leaf_paths(expected_output)
    missing_paths: list[str] = []
    mismatched_paths: list[str] = []
    matched_path_count = 0

    for path, expected in expected_paths.items():
        actual_exists, actual = _actual_value(run, target_payload, path)
        if not actual_exists:
            missing_paths.append(path)
            continue
        if _matches_expected(actual, expected):
            matched_path_count += 1
            continue
        mismatched_paths.append(path)

    return {
        "compared_path_count": len(expected_paths),
        "matched_path_count": matched_path_count,
        "missing_paths": missing_paths,
        "mismatched_paths": mismatched_paths,
    }


def _evaluate_rules(
    *,
    run: WorkflowRun,
    target_payload: dict[str, Any],
    rules: list[str],
) -> list[dict[str, Any]]:
    return [_evaluate_rule(run, target_payload, rule) for rule in rules]


def _evaluate_rule(run: WorkflowRun, target_payload: dict[str, Any], rule: str) -> dict[str, Any]:
    normalized = rule.lower()
    if "trace" in normalized or "轨迹" in rule:
        return _rule_result(rule, len(run.traces) > 0, "trace_count")
    if "approval" in normalized or "审批" in rule:
        return _rule_result(
            rule,
            any(trace.status == NodeExecutionStatus.APPROVAL_REQUIRED for trace in run.traces),
            "approval_required_trace",
        )
    if "next_actions" in normalized:
        return _rule_result(rule, _contains_key(target_payload, "next_actions") or _contains_key(run.output_payload, "next_actions"), "next_actions_present")
    if "decision" in normalized:
        return _rule_result(rule, _contains_key(target_payload, "decision"), "decision_present")
    if "risk" in normalized or "风险" in rule:
        return _rule_result(rule, _contains_key(target_payload, "risks"), "risks_present")
    if "completed" in normalized or "完成" in rule:
        return _rule_result(
            rule,
            run.status in {WorkflowRunStatus.COMPLETED, WorkflowRunStatus.PAUSED},
            "completed_or_paused",
        )
    return _rule_result(rule, True, "rule_not_mapped")


def _rule_result(rule: str, passed: bool, code: str) -> dict[str, Any]:
    return {"rule": rule, "passed": passed, "code": code}


def _leaf_paths(payload: dict[str, Any]) -> dict[str, Any]:
    paths: dict[str, Any] = {}

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict) and value:
            for key, nested in value.items():
                walk(nested, f"{path}.{key}" if path else str(key))
            return
        paths[path] = value

    walk(payload, "")
    return paths


def _actual_value(run: WorkflowRun, target_payload: dict[str, Any], path: str) -> tuple[bool, Any]:
    if path == "status":
        return True, run.status
    return _lookup_path(target_payload, path)


def _lookup_path(payload: dict[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _matches_expected(actual: Any, expected: Any) -> bool:
    actual_value = getattr(actual, "value", actual)
    if isinstance(expected, str):
        alternatives = _expected_alternatives(expected)
        if len(alternatives) > 1:
            return str(actual_value) in alternatives
    return actual_value == expected


def _expected_alternatives(expected: str) -> list[str]:
    alternatives = expected.split("_or_")
    normalized: list[str] = []
    for alternative in alternatives:
        normalized.append(alternative)
        if alternative.endswith("_for_approval"):
            normalized.append(alternative.removesuffix("_for_approval"))
    return normalized


def _contains_key(payload: Any, key: str) -> bool:
    if isinstance(payload, dict):
        return key in payload or any(_contains_key(value, key) for value in payload.values())
    if isinstance(payload, list):
        return any(_contains_key(item, key) for item in payload)
    return False
