from __future__ import annotations

from collections import Counter
from typing import Any

from packages.workflow_core.models import TraceRecord
from packages.workflow_core.models.enums import NodeExecutionStatus


class MetricCollector:
    def collect(self, traces: list[TraceRecord]) -> dict[str, Any]:
        total = len(traces)
        statuses = Counter(trace.status for trace in traces)
        failed = [trace for trace in traces if trace.status == NodeExecutionStatus.FAILED]
        durations = [trace.duration_ms or 0 for trace in traces]
        return {
            "node_success_rate": (statuses[NodeExecutionStatus.SUCCESS] / total) if total else 0,
            "tool_success_rate": (statuses[NodeExecutionStatus.SUCCESS] / total) if total else 0,
            "approval_count": statuses[NodeExecutionStatus.APPROVAL_REQUIRED],
            "failure_reason_distribution": Counter(trace.error or "unknown" for trace in failed),
            "average_duration_ms": (sum(durations) / total) if total else 0,
            "total_traces": total,
        }
