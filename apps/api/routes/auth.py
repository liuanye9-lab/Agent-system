from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from apps.api.auth_tokens import actor_from_user_record, create_actor_token
from apps.api.settings import settings
from packages.workflow_core.security import verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    actor: dict[str, Any]


@router.post("/token")
def create_token(request: TokenRequest) -> TokenResponse:
    user_record = _load_auth_users().get(request.username)
    if not user_record or not _verify_user_password(request.password, user_record):
        raise HTTPException(status_code=401, detail="invalid username or password")

    actor = actor_from_user_record(request.username, user_record)
    token = create_actor_token(actor, settings.auth_secret_key, settings.auth_token_ttl_seconds)
    return TokenResponse(
        access_token=token,
        expires_in=settings.auth_token_ttl_seconds,
        actor=actor.model_dump(mode="json"),
    )


def _load_auth_users() -> dict[str, dict[str, Any]]:
    if settings.auth_users_json:
        try:
            loaded = json.loads(settings.auth_users_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail="invalid AGENT_WORKFLOW_AUTH_USERS_JSON") from exc
        if not isinstance(loaded, dict):
            raise HTTPException(status_code=500, detail="AGENT_WORKFLOW_AUTH_USERS_JSON must be an object")
        return loaded

    return {
        "admin": {
            "password": "admin",
            "actor_id": "admin-1",
            "role": "workflow-admin",
            "display_name": "Local Admin",
            "scopes": [
                "workflow:read",
                "workflow:write",
                "workflow:run",
                "workflow:evaluate",
                "workflow:approve",
                "workflow:cancel",
                "workflow:promote",
            ],
        },
        "approver": {
            "password": "approver",
            "actor_id": "approver-1",
            "role": "business-approver",
            "display_name": "Local Approver",
            "scopes": ["workflow:read", "workflow:approve"],
        },
    }


def _verify_user_password(password: str, user_record: dict[str, Any]) -> bool:
    password_hash = user_record.get("password_hash")
    if isinstance(password_hash, str) and password_hash:
        return verify_password(password, password_hash)
    return user_record.get("password") == password
