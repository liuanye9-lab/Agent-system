from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from packages.workflow_core.models import ProcessNode, SubAgentResult


@dataclass(frozen=True)
class SubAgentTrace:
    subagent_id: str
    node_id: str
    status: str
    duration_ms: int
    error: str | None = None


class MockSubAgentRuntime:
    def execute(
        self,
        node: ProcessNode,
        input_payload: dict[str, Any],
        output_schema: dict[str, Any] | None = None,
    ) -> SubAgentResult:
        started_at = datetime.now(timezone.utc)
        subagent_id = node.assigned_agent_id or node.owner_role
        if input_payload.get("force_fail_subagent") == subagent_id:
            return self._result(
                subagent_id=subagent_id,
                status="failed",
                summary="subagent failed by request",
                started_at=started_at,
                errors=["forced subagent failure for test or eval scenario"],
            )
        structured_output = {
            "summary": f"{subagent_id} completed {node.name}",
            "findings": [
                {
                    "node_id": node.node_id,
                    "context_policy": node.context_policy or "task_only",
                    "allowed_tool_count": len(node.tool_ids),
                }
            ],
            "risks": ["human review recommended"] if node.requires_approval else [],
            "next_actions": ["return_to_mother_agent"],
        }
        errors = self._schema_errors(output_schema, structured_output) if output_schema else []
        return self._result(
            subagent_id=subagent_id,
            status="failed" if errors else "success",
            summary=structured_output["summary"],
            started_at=started_at,
            structured_output=structured_output,
            errors=errors,
            requires_human_review=node.requires_approval,
        )

    def _result(
        self,
        *,
        subagent_id: str,
        status: str,
        summary: str,
        started_at: datetime,
        structured_output: dict[str, Any] | None = None,
        errors: list[str] | None = None,
        requires_human_review: bool = False,
    ) -> SubAgentResult:
        ended_at = datetime.now(timezone.utc)
        return SubAgentResult(
            subagent_id=subagent_id,
            status=status,
            summary=summary,
            structured_output=structured_output or {},
            confidence=0.72 if status == "success" else 0.0,
            errors=errors or [],
            tokens_estimate=max(8, len(summary.split()) * 4),
            duration_ms=max(0, int((ended_at - started_at).total_seconds() * 1000)),
            requires_human_review=requires_human_review,
        )

    def _schema_errors(self, schema: dict[str, Any], payload: dict[str, Any]) -> list[str]:
        try:
            Draft202012Validator(schema).validate(payload)
        except ValidationError as exc:
            path = ".".join(str(part) for part in exc.path)
            location = f" at {path}" if path else ""
            return [f"{exc.message}{location}"]
        return []
