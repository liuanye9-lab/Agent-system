from __future__ import annotations

import hashlib
import json
from typing import Any


def fingerprint_run_request(
    *,
    request_kind: str,
    workflow_id: str,
    workflow_version: str,
    input_payload: dict[str, Any],
    max_steps: int,
    max_retries: int,
    shadow_mode: bool = False,
    enforce_release_readiness: bool = False,
    source_run_id: str | None = None,
) -> str:
    fingerprint_payload = {
        "request_kind": request_kind,
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
        "input_payload": input_payload,
        "max_steps": max_steps,
        "max_retries": max_retries,
        "shadow_mode": shadow_mode,
        "enforce_release_readiness": enforce_release_readiness,
        "source_run_id": source_run_id,
    }
    encoded = json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
