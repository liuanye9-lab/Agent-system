from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from packages.workflow_core.adapters import LLMClient, MockLLMClient
from packages.workflow_core.builder.llm_json import compact_json, extract_json_object, is_mock_llm
from packages.workflow_core.models import EvalSpec, ProblemSpec, ProcessSpec, WorkflowPackage
from packages.workflow_core.models.enums import EvalType


class EvalSpecLLMOutput(BaseModel):
    eval_id: str | None = Field(default=None, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    eval_type: EvalType
    target_node_id: str | None = Field(default=None, max_length=160)
    input_case: dict[str, Any]
    expected_output: dict[str, Any]
    scoring_rules: list[str] = Field(default_factory=list, max_length=20)


class EvalGenerationLLMOutput(BaseModel):
    evals: list[EvalSpecLLMOutput] = Field(default_factory=list, min_length=1, max_length=20)


class EvalGeneratorAgent:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or MockLLMClient()

    def generate(
        self,
        workflow_id: str,
        target_node_id: str | None = None,
        process_spec: ProcessSpec | None = None,
        problem_spec: ProblemSpec | None = None,
    ) -> list[EvalSpec]:
        fallback = self._deterministic_evals(workflow_id, target_node_id)
        if is_mock_llm(self.llm) or process_spec is None:
            return fallback
        try:
            llm_output = EvalGenerationLLMOutput.model_validate(
                extract_json_object(
                    self.llm.complete(self._build_prompt(workflow_id, target_node_id, process_spec, problem_spec))
                )
            )
            evals = self._evals_from_llm_output(workflow_id, llm_output)
        except (ValidationError, ValueError):
            return fallback
        if not any(eval_spec.eval_type == EvalType.END_TO_END for eval_spec in evals):
            evals.insert(0, fallback[0])
        return evals

    def generate_for_package(self, workflow_package: WorkflowPackage) -> list[EvalSpec]:
        return self.generate(
            workflow_package.workflow_id,
            process_spec=workflow_package.process_spec,
            problem_spec=workflow_package.problem_spec,
        )

    def _deterministic_evals(self, workflow_id: str, target_node_id: str | None = None) -> list[EvalSpec]:
        return [
            EvalSpec(
                eval_id=f"{workflow_id}-golden-case-001",
                workflow_id=workflow_id,
                name="业务流程端到端 golden case",
                eval_type=EvalType.END_TO_END,
                target_node_id=None,
                input_case={
                    "context": {"request_type": "workflow_operation", "priority": "normal"},
                    "artifacts": [],
                    "assumptions": [],
                },
                expected_output={
                    "status": "completed_or_paused_for_approval",
                    "must_include": ["trace", "approval_state", "next_actions"],
                },
                scoring_rules=["必须产生 trace", "写节点必须要求审批", "最终输出必须包含 next_actions"],
            ),
            EvalSpec(
                eval_id=f"{workflow_id}-node-review-001",
                workflow_id=workflow_id,
                name="治理审查节点评测",
                eval_type=EvalType.NODE,
                target_node_id=target_node_id or "governance-review",
                input_case={"context": {"plan": "draft", "tool_permissions": "review_required"}},
                expected_output={"decision": "continue_or_rework", "risks": [], "next_actions": []},
                scoring_rules=["review_node 必须输出 decision", "风险必须显式列出"],
            ),
        ]

    def _evals_from_llm_output(self, workflow_id: str, llm_output: EvalGenerationLLMOutput) -> list[EvalSpec]:
        evals: list[EvalSpec] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(llm_output.evals, start=1):
            eval_id = item.eval_id or f"{workflow_id}-{item.eval_type.value}-{index:03d}"
            if eval_id in seen_ids:
                eval_id = f"{eval_id}-{index}"
            seen_ids.add(eval_id)
            evals.append(
                EvalSpec(
                    eval_id=eval_id,
                    workflow_id=workflow_id,
                    name=item.name,
                    eval_type=item.eval_type,
                    target_node_id=item.target_node_id,
                    input_case=item.input_case,
                    expected_output=item.expected_output,
                    scoring_rules=item.scoring_rules,
                )
            )
        return evals

    def _build_prompt(
        self,
        workflow_id: str,
        target_node_id: str | None,
        process_spec: ProcessSpec,
        problem_spec: ProblemSpec | None,
    ) -> str:
        payload = {
            "workflow_id": workflow_id,
            "target_node_id": target_node_id,
            "problem_spec": problem_spec.model_dump(mode="json") if problem_spec else None,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "name": node.name,
                    "node_type": node.node_type,
                    "owner_role": node.owner_role,
                    "done_condition": node.done_condition,
                }
                for node in process_spec.nodes
            ],
            "allowed_eval_types": [item.value for item in EvalType],
        }
        return (
            "Generate project-grade workflow eval specs for release gates. Return one JSON object with evals. "
            "Include at least one end_to_end eval and one node or regression eval. Each eval must include "
            "name, eval_type, input_case, expected_output, and scoring_rules. "
            f"Input: {compact_json(payload)}"
        )
