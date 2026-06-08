from __future__ import annotations

from packages.workflow_core.governance import TraceStore
from packages.workflow_core.models import TraceRecord
from packages.workflow_core.models.enums import NodeExecutionStatus


def test_trace_store_records_node_trace() -> None:
    store = TraceStore()
    trace = TraceRecord(
        run_id="run-1",
        workflow_id="workflow-1",
        node_id="node-1",
        input_snapshot={"foo": "bar"},
        output_snapshot={"ok": True},
        status=NodeExecutionStatus.SUCCESS,
    )

    store.append(trace)

    assert store.list_by_run("run-1") == [trace]
    assert store.list_by_workflow("workflow-1") == [trace]
