from __future__ import annotations

from enum import StrEnum


class NodeType(StrEnum):
    READ = "read_node"
    REASONING = "reasoning_node"
    REVIEW = "review_node"
    WRITE = "write_node"
    SUBAGENT_CALL = "subagent_call"


class EdgeType(StrEnum):
    DEFAULT = "default"
    CONDITIONAL = "conditional"
    APPROVAL = "approval"
    REJECT = "reject"


class PermissionLevel(StrEnum):
    READ_ONLY = "read_only"
    DRAFT_ONLY = "draft_only"
    WRITE_REQUIRES_APPROVAL = "write_requires_approval"
    FORBIDDEN = "forbidden"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class WorkflowRunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELED = "canceled"


class NodeExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    APPROVAL_REQUIRED = "approval_required"
    SKIPPED = "skipped"


class EvalType(StrEnum):
    NODE = "node"
    END_TO_END = "end_to_end"
    REGRESSION = "regression"


class SuggestionType(StrEnum):
    PROBLEM_SPEC_ISSUE = "problem_spec_issue"
    PROCESS_SPEC_ISSUE = "process_spec_issue"
    DATA_CONTRACT_ISSUE = "data_contract_issue"
    TOOL_POLICY_ISSUE = "tool_policy_issue"
    AGENT_PROMPT_ISSUE = "agent_prompt_issue"
    EVAL_GAP = "eval_gap"
