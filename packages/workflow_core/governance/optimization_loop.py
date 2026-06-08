from __future__ import annotations

from packages.workflow_core.builder.optimizer import OptimizerAgent
from packages.workflow_core.models import EvalResult, OptimizationSuggestion, PackageRepairPlan, TraceRecord, WorkflowPackage


class OptimizationLoop:
    def __init__(self, optimizer: OptimizerAgent | None = None) -> None:
        self.optimizer = optimizer or OptimizerAgent()

    def run(
        self,
        workflow_id: str,
        traces: list[TraceRecord],
        eval_results: list[EvalResult] | None = None,
    ) -> list[OptimizationSuggestion]:
        return self.optimizer.suggest(workflow_id, traces, eval_results)

    def repair_plan(
        self,
        workflow_package: WorkflowPackage,
        traces: list[TraceRecord],
        eval_results: list[EvalResult] | None = None,
    ) -> PackageRepairPlan:
        return self.optimizer.build_repair_plan(workflow_package, traces, eval_results)
