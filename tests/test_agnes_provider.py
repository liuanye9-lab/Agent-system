from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api import dependencies
from apps.api.dependencies import _llm_endpoint_for_provider
from apps.api.main import app
from apps.api.settings import settings
from packages.workflow_core.storage import MemoryWorkflowRepository


def test_agnes_provider_accepts_base_url_and_normalizes_chat_completions() -> None:
    assert (
        _llm_endpoint_for_provider("agnes", "https://apihub.agnes-ai.com/v1")
        == "https://apihub.agnes-ai.com/v1/chat/completions"
    )


def test_agnes_provider_keeps_explicit_chat_completions_endpoint() -> None:
    endpoint = "https://apihub.agnes-ai.com/v1/chat/completions"
    assert _llm_endpoint_for_provider("agnes", endpoint) == endpoint


def test_non_agnes_provider_keeps_endpoint_unchanged() -> None:
    endpoint = "https://api.example.com/custom"
    assert _llm_endpoint_for_provider("openai-compatible", endpoint) == endpoint


def test_ready_accepts_agnes_provider_without_exposing_secret() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    original_provider = settings.llm_provider
    original_endpoint = settings.llm_endpoint
    original_model = settings.llm_model
    original_api_key = settings.llm_api_key
    try:
        object.__setattr__(settings, "llm_provider", "agnes")
        object.__setattr__(settings, "llm_endpoint", "https://apihub.agnes-ai.com/v1")
        object.__setattr__(settings, "llm_model", "agnes-2.0-flash")
        object.__setattr__(settings, "llm_api_key", "super-secret-agnes-key")
        client = TestClient(app)

        response = client.get("/ready")

        assert response.status_code == 200
        checks = {check["name"]: check for check in response.json()["checks"]}
        assert checks["llm_configuration"]["status"] == "passed"
        assert checks["llm_configuration"]["provider"] == "agnes"
        assert checks["llm_configuration"]["api_key_configured"] is True
        assert "super-secret-agnes-key" not in response.text
    finally:
        object.__setattr__(settings, "llm_provider", original_provider)
        object.__setattr__(settings, "llm_endpoint", original_endpoint)
        object.__setattr__(settings, "llm_model", original_model)
        object.__setattr__(settings, "llm_api_key", original_api_key)
        app.dependency_overrides.clear()
