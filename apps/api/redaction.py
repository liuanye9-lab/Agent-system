from __future__ import annotations

from typing import Any


REDACTED_VALUE = "[REDACTED]"
SENSITIVE_KEY_FRAGMENTS = (
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


def dump_redacted_model(model: Any) -> Any:
    payload = model.model_dump(mode="json", by_alias=True) if hasattr(model, "model_dump") else model
    return redact_sensitive_payload(payload)


def redact_sensitive_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            key: REDACTED_VALUE if _is_sensitive_key(key) else redact_sensitive_payload(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_sensitive_payload(item) for item in payload]
    return payload


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)
