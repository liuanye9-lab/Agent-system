from __future__ import annotations

import json

from packages.workflow_core.adapters import HttpJSONLLMClient
from packages.workflow_core.builder import WorkflowBuilder


class FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_http_json_llm_client_posts_openai_compatible_payload(monkeypatch) -> None:
    captured: dict = {}

    def fake_urlopen(http_request, timeout):
        captured["timeout"] = timeout
        captured["authorization"] = http_request.get_header("Authorization")
        captured["body"] = json.loads(http_request.data.decode("utf-8"))
        return FakeHTTPResponse({"choices": [{"message": {"content": "{\"title\":\"ok\"}"}}]})

    monkeypatch.setattr("packages.workflow_core.adapters.http_llm.request.urlopen", fake_urlopen)
    client = HttpJSONLLMClient(
        endpoint="https://llm.example.test/v1/chat/completions",
        model="workflow-framer",
        api_key="secret",
        provider="openai-compatible",
        timeout_seconds=7,
    )

    completion = client.complete("frame this workflow")

    assert completion == "{\"title\":\"ok\"}"
    assert captured["authorization"] == "Bearer secret"
    assert captured["timeout"] == 7
    assert captured["body"]["model"] == "workflow-framer"
    assert captured["body"]["messages"][1]["content"] == "frame this workflow"


class JSONFrameLLMClient:
    provider = "fixture"
    model = "problem-framer-v1"

    def complete(self, prompt: str) -> str:
        if "project-grade agent workflow problem" not in prompt:
            return "not-json"
        return json.dumps(
            {
                "title": "客户续约风险流程智能体",
                "target_users": ["客户成功经理", "销售负责人"],
                "business_goal": "提前识别续约风险并形成可审计的挽回动作。",
                "start_event": "客户健康分低于阈值。",
                "end_state": "形成续约风险判断、行动草案和审批记录。",
                "success_metrics": ["风险识别完整率", "挽回动作按时率"],
                "constraints": ["写入 CRM 前必须审批"],
                "risks": ["客户上下文不足导致误判"],
                "human_roles": ["客户成功负责人", "销售审批人"],
                "clarifying_questions": ["健康分阈值是多少？"],
            },
            ensure_ascii=False,
        )


def test_workflow_builder_uses_structured_llm_problem_frame_and_model_metadata() -> None:
    result = WorkflowBuilder(llm=JSONFrameLLMClient()).generate("搭建客户续约风险流程", version="1.2.0")

    assert result.workflow_package.problem_spec.title == "客户续约风险流程智能体"
    assert result.workflow_package.problem_spec.target_users == ["客户成功经理", "销售负责人"]
    assert result.clarifying_questions == ["健康分阈值是多少？"]
    assert result.workflow_package.agent_specs[0].model_settings == {
        "provider": "fixture",
        "model": "problem-framer-v1",
    }
