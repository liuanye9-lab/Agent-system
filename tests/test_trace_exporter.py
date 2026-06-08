from __future__ import annotations

import json
from pathlib import Path

from packages.workflow_core.governance import OTLPTraceExporter, OTLPTraceExporterConfig, TraceExportResponse
from packages.workflow_core.models import WorkflowPackage
from packages.workflow_core.runtime import WorkflowRunner


class RecordingTraceTransport:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    def post(
        self,
        endpoint_url: str,
        payload: dict,
        *,
        headers: dict,
        timeout_seconds: float,
    ) -> TraceExportResponse:
        self.posts.append(
            {
                "endpoint_url": endpoint_url,
                "payload": payload,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        return TraceExportResponse(status_code=200, body="{}")


def load_example() -> WorkflowPackage:
    payload = json.loads(Path("examples/new_product_launch.workflow.json").read_text(encoding="utf-8"))
    return WorkflowPackage.model_validate(payload)


def test_otlp_trace_exporter_builds_low_sensitive_run_payload() -> None:
    run = WorkflowRunner().run(
        load_example(),
        {
            "product": "AI workflow platform",
            "api_key": "secret-value",
            "nested": {"authorization": "Bearer secret"},
        },
    )
    payload = OTLPTraceExporter().build_run_payload(run)
    encoded = json.dumps(payload, ensure_ascii=False)
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root_span = spans[0]
    first_node_span = spans[1]

    assert len(spans) == len(run.traces) + 1
    assert root_span["name"] == "workflow.run"
    assert first_node_span["parentSpanId"] == root_span["spanId"]
    assert first_node_span["name"].startswith("workflow.node.")
    assert len(root_span["traceId"]) == 32
    assert len(root_span["spanId"]) == 16
    assert "secret-value" not in encoded
    assert "Bearer secret" not in encoded
    root_attributes = _attributes(root_span)
    assert root_attributes["workflow.run.input_sensitive_key_count"]["intValue"] == "2"
    assert root_attributes["workflow.run.trace_count"]["intValue"] == str(len(run.traces))


def test_otlp_trace_exporter_sends_payload_through_injected_transport() -> None:
    run = WorkflowRunner().run(load_example(), {"product": "AI workflow platform"}, shadow_mode=True)
    transport = RecordingTraceTransport()
    exporter = OTLPTraceExporter(
        OTLPTraceExporterConfig(
            endpoint_url="https://otel.example.test/v1/traces",
            headers={"Authorization": "Bearer trace-token"},
            deployment_environment="test",
        ),
        transport=transport,
    )

    result = exporter.export_run(run)

    assert result["status"] == "exported"
    assert result["status_code"] == 200
    assert result["span_count"] == len(run.traces) + 1
    assert transport.posts[0]["endpoint_url"] == "https://otel.example.test/v1/traces"
    assert transport.posts[0]["headers"]["Authorization"] == "Bearer trace-token"
    resource_attrs = _attributes(transport.posts[0]["payload"]["resourceSpans"][0]["resource"])
    assert resource_attrs["deployment.environment"]["stringValue"] == "test"


def _attributes(span_or_resource: dict) -> dict[str, dict]:
    return {
        item["key"]: item["value"]
        for item in span_or_resource.get("attributes", [])
    }
