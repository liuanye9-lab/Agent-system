from __future__ import annotations

import argparse
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


class SmokeError(RuntimeError):
    pass


def run_smoke(
    *,
    base_url: str,
    username: str,
    password: str,
    package_path: Path,
    write: bool,
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    health = _request_json(base_url, "/health", timeout_seconds=timeout_seconds)
    checks.append(_check("health", health["status_code"] == 200 and health["json"].get("status") == "ok", health))

    ready = _request_json(base_url, "/ready", timeout_seconds=timeout_seconds)
    checks.append(_check("readiness", ready["status_code"] == 200 and ready["json"].get("status") == "ready", ready))

    token_response = _request_json(
        base_url,
        "/api/auth/token",
        method="POST",
        payload={"username": username, "password": password},
        timeout_seconds=timeout_seconds,
    )
    token = token_response["json"].get("access_token")
    checks.append(_check("auth_token", token_response["status_code"] == 200 and bool(token), token_response))
    if not token:
        raise SmokeError("authentication did not return an access token")

    workflow_package = _load_smoke_package(package_path)
    validation = _request_json(
        base_url,
        "/api/workflows/validate",
        method="POST",
        token=token,
        payload=workflow_package,
        timeout_seconds=timeout_seconds,
    )
    checks.append(_check("package_validation", validation["json"].get("valid") is True, validation))

    run_summary: dict[str, Any] | None = None
    if write:
        imported = _request_json(
            base_url,
            "/api/workflows/import?save_as_current=true",
            method="POST",
            token=token,
            payload=workflow_package,
            timeout_seconds=timeout_seconds,
        )
        checks.append(_check("package_import", imported["json"].get("valid") is True, imported))

        workflow_id = workflow_package["workflow_id"]
        started = _request_json(
            base_url,
            f"/api/workflows/{workflow_id}/runs",
            method="POST",
            token=token,
            payload={
                "input_payload": {"context": {"smoke_check": True}, "artifacts": [], "assumptions": []},
                "workflow_version": workflow_package["version"],
                "max_steps": 50,
                "max_retries": 1,
                "shadow_mode": True,
                "idempotency_key": f"{workflow_id}-shadow-run",
            },
            timeout_seconds=timeout_seconds,
        )
        run_id = started["json"].get("run_id")
        run_status = started["json"].get("status")
        checks.append(_check("shadow_run_start", bool(run_id) and run_status in {"completed", "paused"}, started))
        if not run_id:
            raise SmokeError("shadow run did not return run_id")

        traces = _request_json(
            base_url,
            f"/api/runs/{run_id}/traces",
            token=token,
            timeout_seconds=timeout_seconds,
        )
        trace_count = len(traces["json"]) if isinstance(traces["json"], list) else 0
        checks.append(_check("trace_fetch", trace_count > 0, traces))
        run_summary = {
            "workflow_id": workflow_id,
            "workflow_version": workflow_package["version"],
            "run_id": run_id,
            "run_status": run_status,
            "trace_count": trace_count,
        }

    failed = [check for check in checks if not check["passed"]]
    return {
        "ready": not failed,
        "base_url": base_url,
        "write_mode": write,
        "checks": checks,
        "run_summary": run_summary,
    }


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")), data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            return {"status_code": response.status, "json": json.loads(raw) if raw else {}}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload_json = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload_json = {"message": raw}
        return {"status_code": exc.code, "json": payload_json}
    except URLError as exc:
        raise SmokeError(f"request failed for {path}: {exc.reason}") from exc


def _load_smoke_package(package_path: Path) -> dict[str, Any]:
    payload = json.loads(package_path.read_text(encoding="utf-8"))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    original_workflow_id = str(payload["workflow_id"])
    workflow_id = f"smoke-{timestamp}"
    rewritten = _rewrite_id(deepcopy(payload), original_workflow_id, workflow_id)
    rewritten["workflow_id"] = workflow_id
    rewritten["name"] = f"Smoke Check Workflow {timestamp}"
    return rewritten


def _rewrite_id(value: Any, old_id: str, new_id: str) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_id(item, old_id, new_id) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_id(item, old_id, new_id) for item in value]
    if isinstance(value, str):
        if value == old_id:
            return new_id
        if value.startswith(f"{old_id}-"):
            return f"{new_id}-{value[len(old_id) + 1:]}"
    return value


def _check(name: str, passed: bool, response: dict[str, Any]) -> dict[str, Any]:
    body = response.get("json", {})
    return {
        "name": name,
        "passed": passed,
        "status_code": response.get("status_code"),
        "status": body.get("status") if isinstance(body, dict) else None,
        "workflow_id": body.get("workflow_id") if isinstance(body, dict) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run post-deployment smoke checks against the Agent Workflow API.")
    parser.add_argument("--base-url", default=os.getenv("AGENT_WORKFLOW_API_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.getenv("AGENT_WORKFLOW_SMOKE_USERNAME", "admin"))
    parser.add_argument("--password", default=os.getenv("AGENT_WORKFLOW_SMOKE_PASSWORD", "admin"))
    parser.add_argument("--package", default="examples/new_product_launch.workflow.json")
    parser.add_argument("--write", action="store_true", help="Import an isolated smoke workflow and start a shadow run.")
    parser.add_argument("--timeout-seconds", type=float, default=10)
    args = parser.parse_args()

    try:
        report = run_smoke(
            base_url=args.base_url,
            username=args.username,
            password=args.password,
            package_path=Path(args.package),
            write=args.write,
            timeout_seconds=args.timeout_seconds,
        )
    except SmokeError as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 1
