from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from pydantic import Field

from packages.workflow_core.models import TraceRecord, WorkflowRun
from packages.workflow_core.models.common import StrictBaseModel
from packages.workflow_core.models.enums import NodeExecutionStatus, WorkflowRunStatus


class TraceExportError(RuntimeError):
    """Raised when trace export transport fails."""


@dataclass(frozen=True)
class TraceExportResponse:
    status_code: int
    body: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


class TraceExportTransport(Protocol):
    def post(
        self,
        endpoint_url: str,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> TraceExportResponse:
        """Send one trace export payload to an external collector."""


class HTTPOTLPJSONTransport:
    def post(
        self,
        endpoint_url: str,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> TraceExportResponse:
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **dict(headers),
        }
        request = urllib.request.Request(
            endpoint_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw_body = response.read()
                body = raw_body.decode("utf-8") if raw_body else None
                return TraceExportResponse(
                    status_code=response.status,
                    body=body,
                    headers=dict(response.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            raw_body = exc.read()
            body = raw_body.decode("utf-8") if raw_body else None
            return TraceExportResponse(
                status_code=exc.code,
                body=body,
                headers=dict(exc.headers.items()),
            )
        except urllib.error.URLError as exc:
            raise TraceExportError(f"trace export request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TraceExportError("trace export request timed out") from exc


class OTLPTraceExporterConfig(StrictBaseModel):
    endpoint_url: str | None = Field(default=None, max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    service_name: str = Field(default="agent-workflow-builder", min_length=1, max_length=200)
    service_version: str = Field(default="0.1.0", min_length=1, max_length=80)
    deployment_environment: str | None = Field(default=None, max_length=120)
    scope_name: str = Field(default="agent-workflow-builder.workflow-runner", min_length=1, max_length=200)
    scope_version: str = Field(default="0.1.0", min_length=1, max_length=80)
    max_attribute_length: int = Field(default=500, ge=50, le=5000)


class OTLPTraceExporter:
    def __init__(
        self,
        config: OTLPTraceExporterConfig | None = None,
        transport: TraceExportTransport | None = None,
    ) -> None:
        self.config = config or OTLPTraceExporterConfig()
        self.transport = transport or HTTPOTLPJSONTransport()

    def build_run_payload(self, run: WorkflowRun) -> dict[str, Any]:
        trace_id = _hex_id(f"trace:{run.run_id}", 32)
        root_span_id = _hex_id(f"span:{run.run_id}:workflow_run", 16)
        spans = [self._run_span(run, trace_id, root_span_id)]
        spans.extend(
            self._trace_span(
                run=run,
                trace=trace,
                index=index,
                trace_id=trace_id,
                parent_span_id=root_span_id,
            )
            for index, trace in enumerate(run.traces)
        )
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": self._resource_attributes(),
                    },
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": self.config.scope_name,
                                "version": self.config.scope_version,
                            },
                            "spans": spans,
                        }
                    ],
                }
            ]
        }

    def export_run(self, run: WorkflowRun) -> dict[str, Any]:
        if not self.config.endpoint_url:
            raise TraceExportError("OTLP endpoint_url is required to export traces")
        payload = self.build_run_payload(run)
        response = self.transport.post(
            self.config.endpoint_url,
            payload,
            headers=self.config.headers,
            timeout_seconds=self.config.timeout_seconds,
        )
        if response.status_code >= 400:
            raise TraceExportError(f"trace export failed with HTTP {response.status_code}")
        return {
            "status": "exported",
            "status_code": response.status_code,
            "span_count": _span_count(payload),
        }

    def _resource_attributes(self) -> list[dict[str, Any]]:
        attributes = [
            _attribute("service.name", self.config.service_name),
            _attribute("service.version", self.config.service_version),
        ]
        if self.config.deployment_environment:
            attributes.append(_attribute("deployment.environment", self.config.deployment_environment))
        return attributes

    def _run_span(self, run: WorkflowRun, trace_id: str, span_id: str) -> dict[str, Any]:
        start = _run_start(run)
        end = _run_end(run)
        return {
            "traceId": trace_id,
            "spanId": span_id,
            "name": "workflow.run",
            "kind": 1,
            "startTimeUnixNano": _unix_nano(start),
            "endTimeUnixNano": _unix_nano(end),
            "attributes": [
                _attribute("workflow.id", run.workflow_id),
                _attribute("workflow.version", run.workflow_version),
                _attribute("workflow.run_id", run.run_id),
                _attribute("workflow.run.status", str(run.status)),
                _attribute("workflow.run.shadow_mode", run.shadow_mode),
                _attribute("workflow.run.trace_count", len(run.traces)),
                _attribute("workflow.run.input_key_count", _key_count(run.input_payload)),
                _attribute("workflow.run.output_key_count", _key_count(run.output_payload)),
                _attribute("workflow.run.input_sensitive_key_count", _sensitive_key_count(run.input_payload)),
                _attribute("workflow.run.output_sensitive_key_count", _sensitive_key_count(run.output_payload)),
                _attribute("workflow.run.rerun_of_present", run.rerun_of_run_id is not None),
                _attribute("workflow.run.idempotency_key_present", run.idempotency_key is not None),
            ],
            "status": _span_status_for_run(run.status),
        }

    def _trace_span(
        self,
        *,
        run: WorkflowRun,
        trace: TraceRecord,
        index: int,
        trace_id: str,
        parent_span_id: str,
    ) -> dict[str, Any]:
        attributes = [
            _attribute("workflow.id", trace.workflow_id),
            _attribute("workflow.version", trace.workflow_version or run.workflow_version),
            _attribute("workflow.run_id", trace.run_id),
            _attribute("workflow.run.shadow_mode", run.shadow_mode),
            _attribute("workflow.node.id", trace.node_id),
            _attribute("workflow.node.status", str(trace.status)),
            _attribute("workflow.node.attempt", trace.attempt),
            _attribute("workflow.node.max_attempts", trace.max_attempts),
            _attribute("workflow.node.retryable", trace.retryable),
            _attribute("workflow.node.duration_ms", trace.duration_ms or 0),
            _attribute("workflow.node.input_key_count", _key_count(trace.input_snapshot)),
            _attribute("workflow.node.output_key_count", _key_count(trace.output_snapshot)),
            _attribute("workflow.node.input_sensitive_key_count", _sensitive_key_count(trace.input_snapshot)),
            _attribute("workflow.node.output_sensitive_key_count", _sensitive_key_count(trace.output_snapshot)),
            _attribute("workflow.node.error_present", trace.error is not None),
        ]
        if trace.error:
            attributes.append(
                _attribute(
                    "workflow.node.error_reason_code",
                    _bounded_string(_reason_code(trace.error), self.config.max_attribute_length),
                )
            )
        return {
            "traceId": trace_id,
            "spanId": _hex_id(f"span:{run.run_id}:{index}:{trace.node_id}:{trace.attempt}", 16),
            "parentSpanId": parent_span_id,
            "name": f"workflow.node.{trace.node_id}",
            "kind": 1,
            "startTimeUnixNano": _unix_nano(trace.started_at),
            "endTimeUnixNano": _unix_nano(trace.ended_at or trace.started_at),
            "attributes": attributes,
            "status": _span_status_for_trace(trace.status),
        }


def _span_count(payload: dict[str, Any]) -> int:
    return sum(
        len(scope_spans.get("spans", []))
        for resource_spans in payload.get("resourceSpans", [])
        for scope_spans in resource_spans.get("scopeSpans", [])
    )


def _hex_id(value: str, length: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _unix_nano(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return str(int(value.timestamp() * 1_000_000_000))


def _run_start(run: WorkflowRun) -> datetime:
    if run.traces:
        return min([run.created_at, *(trace.started_at for trace in run.traces)])
    return run.created_at


def _run_end(run: WorkflowRun) -> datetime:
    trace_ends = [trace.ended_at or trace.started_at for trace in run.traces]
    if trace_ends:
        return max([run.updated_at, *trace_ends])
    return run.updated_at


def _span_status_for_run(status: WorkflowRunStatus) -> dict[str, int]:
    if status == WorkflowRunStatus.COMPLETED:
        return {"code": 1}
    if status in {WorkflowRunStatus.FAILED, WorkflowRunStatus.REJECTED, WorkflowRunStatus.CANCELED}:
        return {"code": 2}
    return {"code": 0}


def _span_status_for_trace(status: NodeExecutionStatus) -> dict[str, int]:
    if status in {NodeExecutionStatus.SUCCESS, NodeExecutionStatus.SKIPPED}:
        return {"code": 1}
    if status == NodeExecutionStatus.FAILED:
        return {"code": 2}
    return {"code": 0}


def _attribute(key: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {"key": key, "value": {"stringValue": ""}}
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _key_count(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    return len(payload)


_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "access_token",
    "refresh_token",
    "bearer",
    "client_secret",
    "password",
    "secret",
    "token",
)


def _sensitive_key_count(payload: Any) -> int:
    if isinstance(payload, dict):
        return sum(
            (1 if _is_sensitive_key(key) else 0) + _sensitive_key_count(value)
            for key, value in payload.items()
        )
    if isinstance(payload, list):
        return sum(_sensitive_key_count(item) for item in payload)
    return 0


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _reason_code(error: str) -> str:
    return error.split(":", 1)[0].strip() or "unknown_error"


def _bounded_string(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:max(0, max_length - 3)]}..."
