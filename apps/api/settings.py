from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_AUTH_SECRET_KEY = "local-dev-change-me"
DEFAULT_CORS_ALLOWED_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


def parse_csv_setting(value: str | None, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    parsed = tuple(item.strip() for item in value.split(",") if item.strip())
    return parsed or default


def load_local_env(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_local_env()


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
    cors_allowed_origins: tuple[str, ...] = parse_csv_setting(
        os.getenv("AGENT_WORKFLOW_CORS_ALLOWED_ORIGINS"),
        DEFAULT_CORS_ALLOWED_ORIGINS,
    )
    cors_allow_origin_regex: str | None = os.getenv("AGENT_WORKFLOW_CORS_ALLOW_ORIGIN_REGEX") or None
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
