from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEFAULT_INITIAL_REQUEST = "客户跟进 Agent：整理沟通记录，识别客户阶段，输出下一步建议和周报草稿。"

DEFAULT_FOLLOW_UPS = [
    (
        "补充：输入来自手动粘贴的客户沟通记录；输出必须包含客户阶段、风险、下一步动作、负责人、日期；"
        "只生成草稿和提醒建议，不自动发送；发布周报前必须人工确认；失败时返回缺失字段清单。"
    ),
    (
        "确认：工具权限只有草稿写入和提醒建议，不允许真实发送、发布或写外部系统；记忆范围只保存本会话需求、"
        "确认事实和版本变更；评估用例包括完整沟通记录、缺少负责人、需要人工审批三个场景；候选版本可以保存，"
        "但不能成为当前版本。"
    ),
    (
        "确认候选保存门槛：目标、输入、输出、工具权限、记忆范围、失败策略、评估用例、审批边界和候选发布策略都已确认；"
        "如果没有新的阻塞项，请生成可保存候选版本的蓝图和 Skill 包草案。"
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a real Agnes-backed Agent Builder HTTP smoke.")
    parser.add_argument("--initial-request", default=DEFAULT_INITIAL_REQUEST)
    parser.add_argument("--version", default="0.1.0-agnes-smoke")
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--json-response-format", action="store_true")
    parser.add_argument("--require-candidate", action="store_true")
    return parser.parse_args()


def configure_env(args: argparse.Namespace) -> None:
    os.environ.setdefault("AGENT_WORKFLOW_LLM_PROVIDER", "agnes")
    os.environ.setdefault("AGENT_WORKFLOW_LLM_ENDPOINT", "https://apihub.agnes-ai.com/v1")
    os.environ.setdefault("AGENT_WORKFLOW_LLM_MODEL", "agnes-2.0-flash")
    os.environ["AGENT_WORKFLOW_LLM_TIMEOUT_SECONDS"] = str(args.timeout_seconds)
    os.environ["AGENT_WORKFLOW_LLM_MAX_TOKENS"] = str(args.max_tokens)
    os.environ["AGENT_WORKFLOW_LLM_JSON_RESPONSE_FORMAT"] = "true" if args.json_response_format else "false"


def main() -> int:
    args = parse_args()
    configure_env(args)

    from fastapi.testclient import TestClient

    from apps.api import dependencies
    from apps.api.main import app
    from packages.workflow_core.storage import MemoryWorkflowRepository

    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        token_response = client.post("/api/auth/token", json={"username": "admin", "password": "admin"})
        print("auth_status", token_response.status_code)
        if token_response.status_code != 200:
            return 1
        headers = {"Authorization": f"Bearer {token_response.json()['access_token']}"}

        session_response = client.post(
            "/api/agent-systems/sessions",
            headers=headers,
            json={"user_request": args.initial_request},
        )
        print("turn1_status", session_response.status_code)
        if session_response.status_code != 200:
            print_detail("turn1_detail", session_response)
            return 1

        session = session_response.json()
        print_session_summary("turn1", session)

        for index, message in enumerate(DEFAULT_FOLLOW_UPS[: max(0, args.max_turns - 1)], start=2):
            if session["readiness_report"]["ready_for_candidate"]:
                break
            response = client.post(
                f"/api/agent-systems/sessions/{session['session_id']}/messages",
                headers=headers,
                json={"message": message},
            )
            print(f"turn{index}_status", response.status_code)
            if response.status_code != 200:
                print_detail(f"turn{index}_detail", response)
                return 1
            session = response.json()
            print_session_summary(f"turn{index}", session)

        candidate_response = client.post(
            f"/api/agent-systems/sessions/{session['session_id']}/candidate",
            headers=headers,
            json={"version": args.version},
        )
        print("candidate_status", candidate_response.status_code)
        if candidate_response.status_code == 200:
            candidate = candidate_response.json()
            print("candidate_saved_as_current", candidate["saved_as_current"])
            print("candidate_skill_count", len(candidate["skill_packages"]))
            print("candidate_workflow_id", candidate["workflow_package"]["workflow_id"])
            print("candidate_version", candidate["workflow_package"]["version"])
            return 0

        print_detail("candidate_detail", candidate_response)
        return 1 if args.require_candidate else 0
    finally:
        app.dependency_overrides.clear()


def print_session_summary(label: str, session: dict[str, Any]) -> None:
    report = session["readiness_report"]
    print(f"{label}_generation_mode", session["generation_mode"])
    print(f"{label}_llm_provider", session.get("llm_provider"))
    print(f"{label}_readiness", report["overall_score"], report["ready_for_candidate"])
    print(f"{label}_message_count", len(session["messages"]))
    print(f"{label}_skill_count", len(session["skill_packages"]))
    print(f"{label}_blocking_gap_count", len(report["blocking_gaps"]))
    print(f"{label}_question_count", len(session["clarifying_questions"]))


def print_detail(label: str, response: Any) -> None:
    try:
        detail = response.json().get("detail")
    except Exception:
        print(label, "unparseable_response")
        return
    if isinstance(detail, dict):
        print(label, detail.get("message", "structured_error"))
        if "overall_score" in detail:
            print(f"{label}_score", detail["overall_score"])
        if "blocking_gaps" in detail:
            print(f"{label}_blocking_gap_count", len(detail.get("blocking_gaps") or []))
        if "reason" in detail:
            print(f"{label}_reason", detail["reason"])
        if "validation_error_summary" in detail:
            print(f"{label}_validation_error_summary", detail["validation_error_summary"])
    else:
        print(label, detail)


if __name__ == "__main__":
    raise SystemExit(main())
