from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.workflow_core.agent_system import AgentSystemBlueprintMapper
from packages.workflow_core.models import (
    AgentBuildChange,
    AgentBuildMessage,
    AgentBuildSession,
    AgentProductionReadinessReport,
    AgentReadinessDimension,
    AgentRequirementState,
    AgentSkillPackage,
    AgentSystemBlueprint,
    AgentTopologyRecommendation,
    AgentTopologyType,
)
from packages.workflow_core.models.enums import RiskLevel
from packages.workflow_core.storage import SQLiteWorkflowRepository
from packages.workflow_core.validation import WorkflowPackageLinter


def main() -> int:
    parser = ArgumentParser(description="Smoke test the production Agent Builder loop.")
    parser.add_argument("--database-url", default="sqlite:///:memory:")
    parser.add_argument(
        "--request",
        default="帮我做一个客户跟进 Agent：自动整理沟通记录，提醒下一步，并输出周报",
    )
    parser.add_argument(
        "--follow-up",
        default="周报发布前需要人工确认，提醒写入日历前也需要确认",
    )
    args = parser.parse_args()

    repository = SQLiteWorkflowRepository(args.database_url)
    blueprint = AgentSystemBlueprint(
        system_id="customer-followup-agent-system",
        name="客户跟进 Agent System",
        description=args.request,
        target_user_groups=["个人用户", "小团队"],
        primary_goal=args.request,
        expected_outputs=["沟通记录摘要", "下一步提醒", "周报草稿"],
        topology_type=AgentTopologyType.WORKFLOW_AGENT,
        tool_requirements=["document_writer", "reminder_writer"],
        memory_requirements=["conversation requirement state", "iteration history"],
        evaluation_requirements=["schema validation", "end-to-end smoke eval"],
        approval_requirements=["external write and publish review"],
        risk_level=RiskLevel.MEDIUM,
    )
    readiness = AgentProductionReadinessReport(
        dimensions=[
            AgentReadinessDimension(name="goal_clarity", score=85),
            AgentReadinessDimension(name="io_contract", score=80),
            AgentReadinessDimension(name="tool_permissions", score=78),
            AgentReadinessDimension(name="memory_strategy", score=76),
            AgentReadinessDimension(name="failure_handling", score=72),
            AgentReadinessDimension(name="evaluation_cases", score=72),
            AgentReadinessDimension(name="approval_boundaries", score=82),
            AgentReadinessDimension(name="release_readiness", score=74),
        ],
        overall_score=77,
        ready_for_candidate=True,
    )
    session = AgentBuildSession(
        session_id="asb-smoke",
        user_request=args.request,
        messages=[
            AgentBuildMessage(role="user", content=args.request),
            AgentBuildMessage(role="user", content=args.follow_up),
            AgentBuildMessage(role="assistant", content="已生成客户跟进 Agent 的候选方案和 Skill 包草案"),
        ],
        assistant_message="已生成客户跟进 Agent 的候选方案和 Skill 包草案",
        requirement_state=AgentRequirementState(
            summary=args.request,
            confirmed_facts=[args.follow_up],
            constraints=["candidate versions do not replace current workflow"],
        ),
        topology_recommendation=AgentTopologyRecommendation(
            topology_type=AgentTopologyType.WORKFLOW_AGENT,
            confidence=0.82,
            reason="客户跟进需要多步骤处理，但第一版不需要多 Agent 编排",
        ),
        current_blueprint=blueprint,
        readiness_report=readiness,
        skill_packages=[
            AgentSkillPackage(
                skill_id="skill-customer-followup",
                name="客户跟进 Skill",
                agent_id="workflow-agent",
                trigger_scenarios=["需要整理客户沟通记录、提醒下一步、生成周报时"],
                system_prompt="整理客户沟通记录，识别下一步动作，生成可审查周报草稿",
                tool_permissions=["document_writer", "reminder_writer"],
                memory_scope="session",
                failure_policy="return_structured_error",
                evaluation_cases=[{"name": "weekly_report", "input": {"request": args.request}, "expected": {"status": "draft_created"}}],
                usage_notes="Draft skill package saved with the candidate; it is not auto-installed.",
            )
        ],
        change_log=[AgentBuildChange(summary="created smoke candidate", changed_sections=["requirements", "blueprint", "skills"])],
    )
    repository.save_agent_build_session(session)
    persisted = repository.get_agent_build_session(session.session_id)
    if persisted is None:
        raise SystemExit("agent build session was not persisted")
    if not persisted.skill_packages:
        raise SystemExit("agent build session did not produce skill packages")
    if not persisted.readiness_report.ready_for_candidate:
        raise SystemExit(f"agent build is not candidate-ready: {persisted.readiness_report.blocking_gaps}")

    workflow_package = AgentSystemBlueprintMapper().to_workflow_package(persisted.current_blueprint, version="0.1.0-smoke")
    validation_report = WorkflowPackageLinter().lint(workflow_package)
    if not validation_report.valid:
        raise SystemExit(validation_report.model_dump_json(indent=2))

    print(
        "agent builder smoke ok:",
        f"readiness={persisted.readiness_report.overall_score}",
        f"skills={len(persisted.skill_packages)}",
        f"workflow={workflow_package.workflow_id}@{workflow_package.version}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
