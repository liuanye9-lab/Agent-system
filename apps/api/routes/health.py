from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from apps.api.dependencies import get_repository
from apps.api.settings import settings
from packages.workflow_core.storage import WorkflowRepository

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name}


@router.get("/ready")
def ready(repository: WorkflowRepository = Depends(get_repository)) -> JSONResponse:
    checks = [_repository_check(repository), _auth_configuration_check(), _llm_configuration_check()]
    is_ready = all(check["status"] == "passed" for check in checks)
    payload = {
        "status": "ready" if is_ready else "not_ready",
        "service": settings.service_name,
        "checks": checks,
    }
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content=jsonable_encoder(payload),
    )


def _repository_check(repository: WorkflowRepository) -> dict[str, Any]:
    try:
        status = (
            repository.get_repository_status()
            if hasattr(repository, "get_repository_status")
            else None
        )
        workflows = repository.list_workflows(limit=1)
    except Exception as exc:  # pragma: no cover - exact database errors depend on the driver.
        return {
            "name": "repository",
            "status": "failed",
            "message": exc.__class__.__name__,
        }
    return {
        "name": "repository",
        "status": "passed",
        "sample_workflow_count": len(workflows),
        "repository_status": status,
    }


def _auth_configuration_check() -> dict[str, Any]:
    problems: list[str] = []
    if not settings.allow_dev_actor_headers and settings.uses_default_auth_secret():
        problems.append("AGENT_WORKFLOW_AUTH_SECRET_KEY must be changed when dev actor headers are disabled")
    if not settings.allow_dev_actor_headers and not settings.auth_users_json:
        problems.append("AGENT_WORKFLOW_AUTH_USERS_JSON is required when dev actor headers are disabled")

    if problems:
        return {
            "name": "auth_configuration",
            "status": "failed",
            "message": "; ".join(problems),
        }
    return {
        "name": "auth_configuration",
        "status": "passed",
        "dev_actor_headers_enabled": settings.allow_dev_actor_headers,
        "default_secret_in_use": settings.uses_default_auth_secret(),
        "configured_auth_users": settings.auth_users_json is not None,
    }


def _llm_configuration_check() -> dict[str, Any]:
    if settings.uses_mock_llm():
        return {
            "name": "llm_configuration",
            "status": "passed",
            "provider": "mock",
            "mode": "offline",
        }
    if settings.llm_provider not in {"http", "openai-compatible", "agnes"}:
        return {
            "name": "llm_configuration",
            "status": "failed",
            "message": "AGENT_WORKFLOW_LLM_PROVIDER must be mock, http, openai-compatible, or agnes",
            "provider": settings.llm_provider,
        }
    if not settings.llm_endpoint or not settings.llm_model:
        return {
            "name": "llm_configuration",
            "status": "failed",
            "message": "AGENT_WORKFLOW_LLM_ENDPOINT and AGENT_WORKFLOW_LLM_MODEL are required for real LLM mode",
            "provider": settings.llm_provider,
            "endpoint_configured": bool(settings.llm_endpoint),
            "model_configured": bool(settings.llm_model),
        }
    return {
        "name": "llm_configuration",
        "status": "passed",
        "provider": settings.llm_provider,
        "endpoint_configured": True,
        "model": settings.llm_model,
        "api_key_configured": bool(settings.llm_api_key),
    }
