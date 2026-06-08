from __future__ import annotations

from dataclasses import dataclass
import os


DEFAULT_AUTH_SECRET_KEY = "local-dev-change-me"


@dataclass(frozen=True)
class Settings:
    service_name: str = "agent-workflow-builder"
    api_prefix: str = "/api"
    database_url: str = os.getenv(
        "AGENT_WORKFLOW_DATABASE_URL",
        "sqlite:///./data/agent_workflow_builder.sqlite3",
    )
    auth_secret_key: str = os.getenv("AGENT_WORKFLOW_AUTH_SECRET_KEY", DEFAULT_AUTH_SECRET_KEY)
    auth_token_ttl_seconds: int = int(os.getenv("AGENT_WORKFLOW_AUTH_TOKEN_TTL_SECONDS", "3600"))
    allow_dev_actor_headers: bool = os.getenv("AGENT_WORKFLOW_ALLOW_DEV_ACTOR_HEADERS", "true").lower() == "true"
    auth_users_json: str | None = os.getenv("AGENT_WORKFLOW_AUTH_USERS_JSON")
    llm_provider: str = os.getenv("AGENT_WORKFLOW_LLM_PROVIDER", "mock").lower()
    llm_endpoint: str | None = os.getenv("AGENT_WORKFLOW_LLM_ENDPOINT")
    llm_api_key: str | None = os.getenv("AGENT_WORKFLOW_LLM_API_KEY")
    llm_model: str | None = os.getenv("AGENT_WORKFLOW_LLM_MODEL")
    llm_timeout_seconds: float = float(os.getenv("AGENT_WORKFLOW_LLM_TIMEOUT_SECONDS", "30"))
    seed_example_workflow: bool = os.getenv("AGENT_WORKFLOW_SEED_EXAMPLE_WORKFLOW", "true").lower() == "true"
    run_retention_days: int = int(os.getenv("AGENT_WORKFLOW_RUN_RETENTION_DAYS", "90"))
    eval_retention_days: int = int(os.getenv("AGENT_WORKFLOW_EVAL_RETENTION_DAYS", "365"))
    audit_retention_days: int = int(os.getenv("AGENT_WORKFLOW_AUDIT_RETENTION_DAYS", "365"))

    def uses_default_auth_secret(self) -> bool:
        return self.auth_secret_key == DEFAULT_AUTH_SECRET_KEY

    def uses_mock_llm(self) -> bool:
        return self.llm_provider == "mock"


settings = Settings()
