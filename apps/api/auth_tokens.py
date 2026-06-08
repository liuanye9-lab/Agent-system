from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from packages.workflow_core.models import ActorContext


class TokenError(ValueError):
    pass


def create_actor_token(actor: ActorContext, secret_key: str, ttl_seconds: int, now: int | None = None) -> str:
    issued_at = now if now is not None else int(time.time())
    payload = {
        "sub": actor.actor_id,
        "role": actor.role,
        "display_name": actor.display_name,
        "scopes": actor.scopes,
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
    }
    payload_segment = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature_segment = _sign(payload_segment, secret_key)
    return f"{payload_segment}.{signature_segment}"


def verify_actor_token(token: str, secret_key: str, now: int | None = None) -> ActorContext:
    try:
        payload_segment, signature_segment = token.split(".", 1)
    except ValueError as exc:
        raise TokenError("invalid token format") from exc

    expected_signature = _sign(payload_segment, secret_key)
    if not hmac.compare_digest(signature_segment, expected_signature):
        raise TokenError("invalid token signature")

    try:
        payload = json.loads(_b64decode(payload_segment))
    except (json.JSONDecodeError, ValueError) as exc:
        raise TokenError("invalid token payload") from exc

    current_time = now if now is not None else int(time.time())
    if int(payload.get("exp", 0)) < current_time:
        raise TokenError("token expired")

    actor_id = payload.get("sub")
    role = payload.get("role")
    if not actor_id or not role:
        raise TokenError("token missing actor identity")

    return ActorContext(
        actor_id=str(actor_id),
        role=str(role),
        display_name=payload.get("display_name"),
        scopes=[str(scope) for scope in payload.get("scopes", [])],
    )


def _sign(payload_segment: str, secret_key: str) -> str:
    digest = hmac.new(secret_key.encode("utf-8"), payload_segment.encode("utf-8"), hashlib.sha256).digest()
    return _b64encode(digest)


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def actor_from_user_record(username: str, record: dict[str, Any]) -> ActorContext:
    return ActorContext(
        actor_id=str(record.get("actor_id") or username),
        role=str(record["role"]),
        display_name=record.get("display_name") or username,
        scopes=[str(scope) for scope in record.get("scopes", _scopes_for_role(str(record["role"])))],
    )


def _scopes_for_role(role: str) -> list[str]:
    if role == "workflow-admin":
        return [
            "workflow:read",
            "workflow:write",
            "workflow:run",
            "workflow:evaluate",
            "workflow:approve",
            "workflow:cancel",
            "workflow:promote",
        ]
    if role == "business-approver":
        return ["workflow:read", "workflow:approve"]
    return ["workflow:read"]
