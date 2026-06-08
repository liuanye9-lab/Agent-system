from __future__ import annotations

from collections import defaultdict

from packages.workflow_core.models import TraceRecord, WorkflowRun


class TraceStore:
    def __init__(self) -> None:
        self._traces_by_run_id: dict[str, list[TraceRecord]] = defaultdict(list)

    def append(self, trace: TraceRecord) -> None:
        self._traces_by_run_id[trace.run_id].append(trace)

    def append_run(self, run: WorkflowRun) -> None:
        for trace in run.traces:
            self.append(trace)

    def list_by_run(self, run_id: str) -> list[TraceRecord]:
        return list(self._traces_by_run_id.get(run_id, []))

    def list_all(self) -> list[TraceRecord]:
        return [trace for traces in self._traces_by_run_id.values() for trace in traces]

    def list_by_workflow(self, workflow_id: str) -> list[TraceRecord]:
        return [
            trace
            for trace in self.list_all()
            if trace.workflow_id == workflow_id
        ]
