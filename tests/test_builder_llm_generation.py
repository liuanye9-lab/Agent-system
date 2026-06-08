from __future__ import annotations

import json

from packages.workflow_core.builder import WorkflowBuilder
from packages.workflow_core.models.enums import PermissionLevel, RiskLevel


class FullBuilderLLMClient:
    provider = "fixture"
    model = "full-builder-v1"

    def complete(self, prompt: str) -> str:
        if "project-grade agent workflow problem" in prompt:
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
        if "Design a project-grade executable agent workflow process" in prompt:
            return json.dumps(
                {
                    "nodes": [
                        {
                            "node_id": "collect-health-signals",
                            "name": "汇总健康信号",
                            "node_type": "read_node",
                            "owner_role": "客户成功经理",
                            "description": "读取健康分、支持单、使用率和合同到期上下文。",
                            "done_condition": "续约上下文和风险信号字段完整。",
                        },
                        {
                            "node_id": "score-renewal-risk",
                            "name": "评估续约风险",
                            "node_type": "reasoning_node",
                            "owner_role": "客户成功负责人",
                            "description": "综合信号生成风险等级、理由和补救建议。",
                            "done_condition": "输出风险等级、证据和建议动作。",
                        },
                        {
                            "node_id": "approve-recovery-plan",
                            "name": "审批挽回方案",
                            "node_type": "review_node",
                            "owner_role": "销售负责人",
                            "description": "审查风险判断和挽回动作是否可执行。",
                            "done_condition": "审批通过或打回补充材料。",
                            "requires_approval": True,
                        },
                        {
                            "node_id": "write-crm-plan",
                            "name": "写入 CRM 挽回计划",
                            "node_type": "write_node",
                            "owner_role": "业务审批人",
                            "description": "生成 CRM 写入草稿并等待审批。",
                            "done_condition": "CRM 挽回计划写入或生成可审计草稿。",
                        },
                    ],
                    "edges": [
                        {"source_node_id": "collect-health-signals", "target_node_id": "score-renewal-risk"},
                        {"source_node_id": "score-renewal-risk", "target_node_id": "approve-recovery-plan"},
                        {"source_node_id": "approve-recovery-plan", "target_node_id": "write-crm-plan"},
                    ],
                    "entry_node_id": "collect-health-signals",
                    "terminal_node_ids": ["write-crm-plan"],
                },
                ensure_ascii=False,
            )
        if "Design node-level JSON data contracts" in prompt:
            return json.dumps(
                {
                    "contracts": [
                        {
                            "node_id": "score-renewal-risk",
                            "name": "续约风险评分契约",
                            "description": "约束风险评分节点的输入和结构化输出。",
                            "input_schema": {
                                "type": "object",
                                "properties": {"context": {"type": "object"}, "health_score": {"type": "number"}},
                                "required": ["context", "health_score"],
                            },
                            "output_schema": {
                                "type": "object",
                                "properties": {
                                    "summary": {"type": "string"},
                                    "risk_level": {"type": "string"},
                                    "next_actions": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["summary", "risk_level", "next_actions"],
                            },
                            "required_fields": ["context", "health_score", "summary", "risk_level"],
                            "validation_rules": ["risk_level must be explicit"],
                            "error_policy": "缺少健康分时暂停并要求客户成功补充。",
                            "example_input": {"context": {"account": "acme"}, "health_score": 42},
                            "example_output": {
                                "summary": "续约风险高",
                                "risk_level": "high",
                                "next_actions": ["发起销售介入"],
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            )
        if "Map each workflow node to one sandboxed tool policy" in prompt:
            return json.dumps(
                {
                    "tools": [
                        {
                            "node_id": "collect-health-signals",
                            "tool_id": "mcp-crm-health-read",
                            "name": "CRM Health Reader",
                            "description": "读取 CRM 健康度和续约上下文。",
                            "adapter": "mcp",
                            "server_id": "crm",
                            "external_tool_name": "account.health.read",
                            "permission_level": "read_only",
                            "risk_level": "low",
                            "required_scopes": ["crm:account:read"],
                            "input_schema": {"type": "object", "properties": {"payload": {"type": "object"}}},
                            "output_schema": {"type": "object", "properties": {"result": {"type": "object"}}},
                        },
                        {
                            "node_id": "write-crm-plan",
                            "tool_id": "crm-plan-write",
                            "name": "CRM Plan Writer",
                            "description": "写入续约挽回计划。",
                            "adapter": "mcp",
                            "permission_level": "read_only",
                            "risk_level": "low",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        if "Generate project-grade workflow eval specs" in prompt:
            return json.dumps(
                {
                    "evals": [
                        {
                            "eval_id": "renewal-risk-golden-001",
                            "name": "续约风险端到端 golden case",
                            "eval_type": "end_to_end",
                            "input_case": {"account": "acme", "health_score": 42},
                            "expected_output": {"must_include": ["risk_level", "next_actions"]},
                            "scoring_rules": ["必须输出风险等级", "写入 CRM 前必须审批"],
                        },
                        {
                            "eval_id": "renewal-risk-node-001",
                            "name": "续约风险评分节点评测",
                            "eval_type": "node",
                            "target_node_id": "score-renewal-risk",
                            "input_case": {"context": {"account": "acme"}, "health_score": 42},
                            "expected_output": {"risk_level": "high"},
                            "scoring_rules": ["risk_level 必须为 high"],
                        },
                    ]
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"unexpected prompt: {prompt}")


class BrokenProcessLLMClient:
    provider = "fixture"
    model = "broken-process-v1"

    def complete(self, prompt: str) -> str:
        if "project-grade agent workflow problem" in prompt:
            return json.dumps({"title": "坏流程测试"})
        if "Design a project-grade executable agent workflow process" in prompt:
            return json.dumps(
                {
                    "nodes": [
                        {
                            "node_id": "only-node",
                            "name": "唯一节点",
                            "node_type": "reasoning_node",
                            "owner_role": "Owner",
                            "description": "invalid graph",
                            "done_condition": "done",
                        }
                    ],
                    "edges": [{"source_node_id": "missing", "target_node_id": "only-node"}],
                    "entry_node_id": "missing",
                    "terminal_node_ids": ["only-node"],
                }
            )
        return "not-json"


def test_workflow_builder_uses_full_structured_llm_builder_outputs() -> None:
    result = WorkflowBuilder(llm=FullBuilderLLMClient()).generate("搭建客户续约风险流程", version="1.2.0")
    package = result.workflow_package

    assert package.problem_spec.title == "客户续约风险流程智能体"
    assert [node.node_id for node in package.process_spec.nodes] == [
        "collect-health-signals",
        "score-renewal-risk",
        "approve-recovery-plan",
        "write-crm-plan",
    ]
    assert package.process_spec.entry_node_id == "collect-health-signals"
    risk_contract = next(contract for contract in package.data_contracts if contract.contract_id == "contract-score-renewal-risk")
    assert risk_contract.name == "续约风险评分契约"
    assert risk_contract.output_schema["required"] == ["summary", "risk_level", "next_actions"]
    read_tool = next(tool for tool in package.tool_policies if tool.tool_id == "mcp-crm-health-read")
    assert read_tool.adapter == "mcp"
    assert read_tool.server_id == "crm"
    write_tool = next(tool for tool in package.tool_policies if tool.tool_id == "crm-plan-write")
    assert write_tool.adapter == "mock"
    assert write_tool.permission_level == PermissionLevel.WRITE_REQUIRES_APPROVAL
    assert write_tool.risk_level == RiskLevel.HIGH
    assert write_tool.requires_approval is True
    assert "business-approver" in write_tool.allowed_roles
    assert [eval_spec.eval_id for eval_spec in package.eval_specs] == [
        "renewal-risk-golden-001",
        "renewal-risk-node-001",
    ]
    assert package.agent_specs[0].model_settings == {"provider": "fixture", "model": "full-builder-v1"}
    assert package.agent_specs[0].guardrails == [
        "schema_validation_required",
        "sandboxed_tools_only",
        "approval_required_for_write",
    ]


def test_workflow_builder_falls_back_when_llm_process_graph_is_invalid() -> None:
    result = WorkflowBuilder(llm=BrokenProcessLLMClient()).generate("搭建坏流程测试")

    assert result.workflow_package.problem_spec.title == "坏流程测试"
    assert result.workflow_package.process_spec.entry_node_id == "intake-business-request"
    assert result.workflow_package.process_spec.terminal_node_ids == ["publish-control-decision"]
    assert result.workflow_package.eval_specs[0].eval_id == "workflow-draft-golden-case-001"
