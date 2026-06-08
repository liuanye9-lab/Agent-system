from __future__ import annotations

import pytest

from apps.api.auth_tokens import TokenError, create_actor_token, verify_actor_token
from packages.workflow_core.models import ActorContext
from packages.workflow_core.security import hash_password, verify_password


def test_actor_token_round_trip() -> None:
    actor = ActorContext(
        actor_id="admin-1",
        role="workflow-admin",
        display_name="Admin",
        scopes=["workflow:read", "workflow:promote"],
    )

    token = create_actor_token(actor, "secret", ttl_seconds=60, now=1000)
    verified = verify_actor_token(token, "secret", now=1020)

    assert verified.actor_id == "admin-1"
    assert verified.role == "workflow-admin"
    assert verified.scopes == ["workflow:read", "workflow:promote"]


def test_actor_token_rejects_tampering() -> None:
    actor = ActorContext(actor_id="admin-1", role="workflow-admin")
    token = create_actor_token(actor, "secret", ttl_seconds=60, now=1000)
    tampered = f"{token[:-1]}x"

    with pytest.raises(TokenError, match="invalid token signature"):
        verify_actor_token(tampered, "secret", now=1001)


def test_actor_token_rejects_expired_token() -> None:
    actor = ActorContext(actor_id="admin-1", role="workflow-admin")
    token = create_actor_token(actor, "secret", ttl_seconds=60, now=1000)

    with pytest.raises(TokenError, match="token expired"):
        verify_actor_token(token, "secret", now=2000)


def test_password_hash_round_trip_and_rejects_wrong_password() -> None:
    password_hash = hash_password("correct horse battery staple", salt=b"0123456789abcdef")

    assert password_hash.startswith("pbkdf2_sha256$310000$")
    assert verify_password("correct horse battery staple", password_hash) is True
    assert verify_password("wrong password", password_hash) is False
    assert verify_password("correct horse battery staple", "not-a-valid-hash") is False
