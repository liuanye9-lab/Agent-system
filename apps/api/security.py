from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException

from apps.api.auth_tokens import TokenError, verify_actor_token
from apps.api.settings import settings
from packages.workflow_core.models import ActorContext


def get_actor_context(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    actor_role: Annotated[str | None, Header(alias="X-Actor-Role")] = None,
    actor_name: Annotated[str | None, Header(alias="X-Actor-Name")] = None,
) -> ActorContext:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="Authorization header must be Bearer token")
        try:
            return verify_actor_token(token, settings.auth_secret_key)
        except TokenError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    if settings.allow_dev_actor_headers and actor_id and actor_role:
        scopes = _scopes_for_role(actor_role)
        return ActorContext(actor_id=actor_id, role=actor_role, display_name=actor_name, scopes=scopes)

    raise HTTPException(
        status_code=401,
        detail="actor authentication requires Bearer token",
    )


def require_scope(required_scope: str):
    def dependency(actor: ActorContext = Depends(get_actor_context)) -> ActorContext:
        if required_scope in actor.scopes:
            return actor
        raise HTTPException(
            status_code=403,
            detail={
                "message": "actor token is missing required scope",
                "required_scope": required_scope,
                "actor_scopes": actor.scopes,
            },
        )

    return dependency


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
