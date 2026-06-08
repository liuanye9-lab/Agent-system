from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from packages.workflow_core.adapters import LLMClient, MockLLMClient
from packages.workflow_core.builder.llm_json import extract_json_object, is_mock_llm
from packages.workflow_core.models import ProblemSpec


DEFAULT_CLARIFYING_QUESTIONS = [
    "这条流程具体是什么？",
    "流程起点和终点是什么？",
    "成功指标是什么？",
    "参与角色是谁？",
    "哪些节点需要人工审批？",
]


class ProblemFrameResult(BaseModel):
    problem_spec: ProblemSpec
    clarifying_questions: list[str] = Field(default_factory=list)


class ProblemFrameLLMOutput(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    target_users: list[str] | None = Field(default=None, max_length=20)
    business_goal: str | None = Field(default=None, min_length=1, max_length=1000)
    start_event: str | None = Field(default=None, min_length=1, max_length=500)
    end_state: str | None = Field(default=None, min_length=1, max_length=500)
    success_metrics: list[str] | None = Field(default=None, max_length=20)
    constraints: list[str] | None = Field(default=None, max_length=20)
    risks: list[str] | None = Field(default=None, max_length=20)
    human_roles: list[str] | None = Field(default=None, max_length=20)
    clarifying_questions: list[str] | None = Field(default=None, max_length=10)


class ProblemFramerAgent:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or MockLLMClient()

    def frame(self, user_request: str, workflow_id: str) -> ProblemFrameResult:
        fallback = self._deterministic_frame(user_request, workflow_id)
        if is_mock_llm(self.llm):
            return fallback
        completion = self.llm.complete(self._build_prompt(user_request))
        try:
            llm_output = ProblemFrameLLMOutput.model_validate(extract_json_object(completion))
        except (ValidationError, ValueError):
            return fallback
        problem_spec = fallback.problem_spec.model_copy(
            update={
                "title": llm_output.title or fallback.problem_spec.title,
                "target_users": self._nonempty_list(llm_output.target_users, fallback.problem_spec.target_users),
                "business_goal": llm_output.business_goal or fallback.problem_spec.business_goal,
                "start_event": llm_output.start_event or fallback.problem_spec.start_event,
                "end_state": llm_output.end_state or fallback.problem_spec.end_state,
                "success_metrics": self._nonempty_list(
                    llm_output.success_metrics,
                    fallback.problem_spec.success_metrics,
                ),
                "constraints": self._nonempty_list(llm_output.constraints, fallback.problem_spec.constraints),
                "risks": self._nonempty_list(llm_output.risks, fallback.problem_spec.risks),
                "human_roles": self._nonempty_list(llm_output.human_roles, fallback.problem_spec.human_roles),
            }
        )
        return ProblemFrameResult(
            problem_spec=problem_spec,
            clarifying_questions=self._nonempty_list(llm_output.clarifying_questions, fallback.clarifying_questions),
        )

    def _deterministic_frame(self, user_request: str, workflow_id: str) -> ProblemFrameResult:
        title = "新品上市流程智能体" if "新品" in user_request or "launch" in user_request.lower() else "业务流程智能体"
        problem_spec = ProblemSpec(
            id=f"{workflow_id}-problem",
            title=title,
            description=user_request,
            target_users=["产品经理", "市场负责人", "渠道负责人", "业务审批人"],
            business_goal="减少跨角色交接中的信息损耗，并把上市 know-how 沉淀为可执行、可评估的流程。",
            start_event="业务方提交流程建设需求或新品上市任务。",
            end_state="形成 Go / No-Go 决策、运行 trace、eval 结果和优化建议。",
            success_metrics=["流程节点可追踪", "关键字段完整率提升", "人工返工减少", "审批风险可审计"],
            constraints=["MVP 阶段只使用 mock LLM 和 mock tools", "写操作必须审批", "所有输出必须通过 schema 校验"],
            risks=["字段缺失导致流程误判", "高风险写操作绕过审批", "评测样例不足导致优化方向偏差"],
            human_roles=["业务 Owner", "流程架构师", "审批人", "治理负责人"],
        )
        return ProblemFrameResult(
            problem_spec=problem_spec,
            clarifying_questions=DEFAULT_CLARIFYING_QUESTIONS,
        )

    def _build_prompt(self, user_request: str) -> str:
        return (
            "Frame this business workflow request as a project-grade agent workflow problem. "
            "Return a JSON object with title, target_users, business_goal, start_event, end_state, "
            "success_metrics, constraints, risks, human_roles, and clarifying_questions. "
            f"Request: {user_request}"
        )

    def _nonempty_list(self, value: list[str] | None, fallback: list[str]) -> list[str]:
        items = [item.strip() for item in value or [] if item.strip()]
        return items or fallback
