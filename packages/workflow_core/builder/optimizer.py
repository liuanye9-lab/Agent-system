from __future__ import annotations

from collections import Counter
import re

from packages.workflow_core.models import (
    EvalResult,
    OptimizationSuggestion,
    PackageRepairOperation,
    PackageRepairPlan,
    TraceRecord,
    WorkflowPackage,
)
from packages.workflow_core.models.enums import NodeExecutionStatus, SuggestionType

_SENSITIVE_EVIDENCE_PATTERN = re.compile(
    r"(authorization|api[_-]?key|bearer|client[_-]?secret|password|secret|token)",
    re.IGNORECASE,
)


class OptimizerAgent:
    def suggest(
        self,
        workflow_id: str,
        traces: list[TraceRecord],
        eval_results: list[EvalResult] | None = None,
    ) -> list[OptimizationSuggestion]:
        suggestions: list[OptimizationSuggestion] = []
        failed_traces = [trace for trace in traces if trace.status == NodeExecutionStatus.FAILED]
        approval_traces = [trace for trace in traces if trace.status == NodeExecutionStatus.APPROVAL_REQUIRED]
        eval_results = eval_results or []

        if failed_traces:
            most_common_node = Counter(trace.node_id for trace in failed_traces).most_common(1)[0][0]
            suggestions.append(
                OptimizationSuggestion(
                    suggestion_id=f"{workflow_id}-data-contract-tighten",
                    workflow_id=workflow_id,
                    suggestion_type=SuggestionType.DATA_CONTRACT_ISSUE,
                    title="收紧失败节点的数据契约",
                    rationale="失败 trace 集中出现在同一节点，优先检查输入字段完整性和错误策略。",
                    recommended_action="为该节点补充 required_fields、validation_rules 和失败样本 eval case。",
                    related_node_id=most_common_node,
                    evidence=[self._low_sensitive_text(trace.error or "unknown failure") for trace in failed_traces[:3]],
                )
            )

        if approval_traces:
            suggestions.append(
                OptimizationSuggestion(
                    suggestion_id=f"{workflow_id}-approval-review",
                    workflow_id=workflow_id,
                    suggestion_type=SuggestionType.TOOL_POLICY_ISSUE,
                    title="复查写操作审批策略",
                    rationale="运行过程中触发了人工审批暂停，需要确认审批点、角色和恢复路径是否清晰。",
                    recommended_action="在 tool_policies 中补充 allowed_roles，并在前端提供审批恢复入口。",
                    related_node_id=approval_traces[0].node_id,
                    evidence=[self._low_sensitive_text(f"approval_required at {trace.node_id}") for trace in approval_traces[:3]],
                )
            )

        failed_evals = [result for result in eval_results if not result.passed]
        if failed_evals:
            suggestions.append(
                OptimizationSuggestion(
                    suggestion_id=f"{workflow_id}-eval-gap",
                    workflow_id=workflow_id,
                    suggestion_type=SuggestionType.EVAL_GAP,
                    title="补充失败样本回归评测",
                    rationale="已有 eval 未通过，说明当前 golden cases 或 scoring rules 需要更细。",
                    recommended_action="把失败输入沉淀为 regression eval，并关联到具体节点。",
                    evidence=[self._low_sensitive_text(result.reason) for result in failed_evals[:3]],
                )
            )

        if not suggestions:
            suggestions.append(
                OptimizationSuggestion(
                    suggestion_id=f"{workflow_id}-baseline-improvement",
                    workflow_id=workflow_id,
                    suggestion_type=SuggestionType.PROCESS_SPEC_ISSUE,
                    title="增加影子运行对比样本",
                    rationale="当前没有明显失败 trace，下一步应扩大 golden set 以提升优化置信度。",
                    recommended_action="采集 3-5 个历史流程案例，补充端到端 eval 和节点级 eval。",
                    evidence=["no_failed_trace_detected"],
                )
            )

        return suggestions

    def build_repair_plan(
        self,
        workflow_package: WorkflowPackage,
        traces: list[TraceRecord],
        eval_results: list[EvalResult] | None = None,
    ) -> PackageRepairPlan:
        eval_results = eval_results or []
        suggestions = self.suggest(workflow_package.workflow_id, traces, eval_results)
        operations: list[PackageRepairOperation] = []
        failed_traces = [trace for trace in traces if trace.status == NodeExecutionStatus.FAILED]
        approval_traces = [trace for trace in traces if trace.status == NodeExecutionStatus.APPROVAL_REQUIRED]
        failed_evals = [result for result in eval_results if not result.passed]

        if failed_traces:
            operations.append(self._contract_repair_operation(workflow_package, failed_traces))
        if failed_evals:
            operations.append(self._regression_eval_operation(workflow_package, failed_evals))
        if approval_traces:
            operations.append(self._approval_policy_operation(workflow_package, approval_traces))
        if not operations:
            operations.append(self._baseline_eval_operation(workflow_package))

        return PackageRepairPlan(
            plan_id=f"{workflow_package.workflow_id}-{workflow_package.version}-repair-plan",
            workflow_id=workflow_package.workflow_id,
            workflow_version=workflow_package.version,
            suggestions=suggestions,
            operations=operations,
        )

    def _contract_repair_operation(
        self,
        workflow_package: WorkflowPackage,
        failed_traces: list[TraceRecord],
    ) -> PackageRepairOperation:
        node_id = Counter(trace.node_id for trace in failed_traces).most_common(1)[0][0]
        node = next((item for item in workflow_package.process_spec.nodes if item.node_id == node_id), None)
        contract_id = node.output_contract_id if node else f"contract-{node_id}"
        evidence = [self._low_sensitive_text(trace.error or "unknown failure") for trace in failed_traces[:5]]
        return PackageRepairOperation(
            operation_id=f"{workflow_package.workflow_id}-{node_id}-contract-repair",
            suggestion_id=f"{workflow_package.workflow_id}-data-contract-tighten",
            target_type="data_contract",
            target_id=contract_id,
            action="tighten_contract_and_error_policy",
            rationale="Failed traces concentrate on this node, so the package should make required fields and recovery policy explicit before promotion.",
            proposed_changes={
                "required_fields_append": ["context", "summary", "next_actions"],
                "validation_rules_append": [
                    "failed trace reason codes must map to explicit validation rules",
                    "high-risk outputs must include risks and next_actions",
                ],
                "error_policy": "pause_or_fail_fast_with_low_sensitive_reason_code_before_downstream_execution",
                "add_regression_eval_for_node": node_id,
            },
            evidence=evidence,
        )

    def _regression_eval_operation(
        self,
        workflow_package: WorkflowPackage,
        failed_evals: list[EvalResult],
    ) -> PackageRepairOperation:
        eval_ids = [result.eval_id for result in failed_evals[:5]]
        return PackageRepairOperation(
            operation_id=f"{workflow_package.workflow_id}-failed-eval-regression",
            suggestion_id=f"{workflow_package.workflow_id}-eval-gap",
            target_type="eval_specs",
            target_id=workflow_package.workflow_id,
            action="add_regression_eval_specs",
            rationale="Failed evals should become named regression cases so future package versions cannot silently reintroduce the same failure.",
            proposed_changes={
                "eval_type": "regression",
                "source_eval_ids": eval_ids,
                "expected_output_strategy": "copy_expected_low_sensitive_shape_and_scoring_rules_from_failed_eval",
                "promotion_gate": "must_pass_before_promote",
            },
            evidence=[self._low_sensitive_text(result.reason) for result in failed_evals[:5]],
        )

    def _approval_policy_operation(
        self,
        workflow_package: WorkflowPackage,
        approval_traces: list[TraceRecord],
    ) -> PackageRepairOperation:
        node_id = approval_traces[0].node_id
        node = next((item for item in workflow_package.process_spec.nodes if item.node_id == node_id), None)
        tool_ids = node.tool_ids if node else []
        return PackageRepairOperation(
            operation_id=f"{workflow_package.workflow_id}-{node_id}-approval-policy",
            suggestion_id=f"{workflow_package.workflow_id}-approval-review",
            target_type="tool_policy",
            target_id=",".join(tool_ids) if tool_ids else node_id,
            action="review_approval_roles_and_resume_path",
            rationale="Approval pauses are expected for write tools, but the package should make allowed roles and resume evidence explicit.",
            proposed_changes={
                "requires_approval": True,
                "allowed_roles_append": ["business-approver"],
                "required_scopes_append": ["workflow:approve"],
                "operator_resume_fields": ["approval_payload", "max_steps", "max_retries"],
            },
            evidence=[self._low_sensitive_text(f"approval_required at {trace.node_id}") for trace in approval_traces[:5]],
        )

    def _baseline_eval_operation(self, workflow_package: WorkflowPackage) -> PackageRepairOperation:
        return PackageRepairOperation(
            operation_id=f"{workflow_package.workflow_id}-baseline-shadow-evals",
            suggestion_id=f"{workflow_package.workflow_id}-baseline-improvement",
            target_type="eval_specs",
            target_id=workflow_package.workflow_id,
            action="add_shadow_and_historical_baseline_evals",
            rationale="No clear failed trace was detected, so the next improvement is broader validation coverage before live rollout.",
            proposed_changes={
                "end_to_end_eval_count": 3,
                "node_eval_count": min(5, len(workflow_package.process_spec.nodes)),
                "include_shadow_comparison_cases": True,
                "promotion_gate": "increase_eval_coverage_before_live_runs",
            },
            evidence=["no_failed_trace_detected"],
        )

    def _low_sensitive_text(self, value: str, max_length: int = 240) -> str:
        normalized = " ".join(str(value).split())
        if _SENSITIVE_EVIDENCE_PATTERN.search(normalized):
            return "[redacted-sensitive-evidence]"
        return normalized[:max_length] or "unknown"
