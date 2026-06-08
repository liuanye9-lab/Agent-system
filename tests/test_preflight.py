from __future__ import annotations

from pathlib import Path

from apps.api.settings import DEFAULT_AUTH_SECRET_KEY, Settings, parse_csv_setting
from packages.workflow_core.ops.preflight import (
    PreflightCheck,
    PreflightReport,
    check_auth_config,
    check_ci_config,
    check_container_assets,
    check_frontend_toolchain,
    check_llm_config,
    check_retention_config,
    check_seed_config,
    check_vercel_publishability,
    format_report,
)
from packages.workflow_core.security import hash_password


def test_production_auth_preflight_blocks_local_defaults() -> None:
    settings = Settings(
        auth_secret_key=DEFAULT_AUTH_SECRET_KEY,
        allow_dev_actor_headers=True,
        auth_users_json=None,
    )

    check = check_auth_config(settings, profile="production")

    assert check.status == "failed"
    assert "production-safe" in check.message
    assert "AGENT_WORKFLOW_AUTH_SECRET_KEY" in check.details["problems"][0]
    assert "AGENT_WORKFLOW_AUTH_USERS_JSON" in check.details["problems"][2]


def test_production_auth_preflight_rejects_plaintext_configured_password() -> None:
    settings = Settings(
        auth_secret_key="production-secret",
        allow_dev_actor_headers=False,
        auth_users_json='{"admin":{"password":"change-me","role":"workflow-admin"}}',
    )

    check = check_auth_config(settings, profile="production")

    assert check.status == "failed"
    assert "plaintext password" in check.details["problems"][0]


def test_production_auth_preflight_accepts_hashed_configured_password() -> None:
    password_hash = hash_password("change-me", salt=b"0123456789abcdef")
    settings = Settings(
        auth_secret_key="production-secret",
        allow_dev_actor_headers=False,
        auth_users_json=f'{{"admin":{{"password_hash":"{password_hash}","role":"workflow-admin"}}}}',
    )

    check = check_auth_config(settings, profile="production")

    assert check.status == "passed"


def test_local_auth_preflight_warns_for_development_defaults() -> None:
    settings = Settings(
        auth_secret_key=DEFAULT_AUTH_SECRET_KEY,
        allow_dev_actor_headers=True,
        auth_users_json=None,
    )

    check = check_auth_config(settings, profile="local")

    assert check.status == "warning"
    assert len(check.details["warnings"]) == 3


def test_real_llm_preflight_never_exposes_secret_values() -> None:
    settings = Settings(
        llm_provider="openai-compatible",
        llm_endpoint="https://llm.example.test/v1/chat/completions",
        llm_model="workflow-framer",
        llm_api_key="super-secret-key",
    )

    check = check_llm_config(settings, profile="production")

    assert check.status == "passed"
    assert check.details["api_key_configured"] is True
    assert "super-secret-key" not in str(check.to_dict())


def test_production_preflight_blocks_example_workflow_seed() -> None:
    settings = Settings(seed_example_workflow=True)

    check = check_seed_config(settings, profile="production")

    assert check.status == "failed"
    assert "seeding must be disabled" in check.message


def test_local_preflight_allows_example_workflow_seed_with_warning() -> None:
    settings = Settings(seed_example_workflow=True)

    check = check_seed_config(settings, profile="local")

    assert check.status == "warning"


def test_production_preflight_accepts_disabled_example_workflow_seed() -> None:
    settings = Settings(seed_example_workflow=False)

    check = check_seed_config(settings, profile="production")

    assert check.status == "passed"


def test_preflight_validates_retention_day_settings() -> None:
    valid = Settings(run_retention_days=90, eval_retention_days=365, audit_retention_days=365)
    invalid = Settings(run_retention_days=0, eval_retention_days=365, audit_retention_days=-1)

    assert check_retention_config(valid).status == "passed"
    failed = check_retention_config(invalid)
    assert failed.status == "failed"
    assert "AGENT_WORKFLOW_RUN_RETENTION_DAYS" in failed.details["invalid"]
    assert "AGENT_WORKFLOW_AUDIT_RETENTION_DAYS" in failed.details["invalid"]


def test_settings_parse_comma_separated_cors_origins() -> None:
    parsed = parse_csv_setting(
        " https://dashboard.example.com, https://preview.example.com ,, ",
        ("http://localhost:3000",),
    )

    assert parsed == ("https://dashboard.example.com", "https://preview.example.com")
    assert parse_csv_setting("", ("http://localhost:3000",)) == ("http://localhost:3000",)


def test_vercel_release_preflight_requires_link_or_cli_token(tmp_path: Path) -> None:
    (tmp_path / "vercel.json").write_text('{"framework":"nextjs"}', encoding="utf-8")

    check = check_vercel_publishability(tmp_path, profile="release", environ={"PATH": ""})

    assert check.status == "failed"
    assert check.details["vercel_json_present"] is True
    assert check.details["vercel_project_linked"] is False
    assert check.details["vercel_cli_available"] is False
    assert check.details["vercel_token_configured"] is False


def test_frontend_toolchain_warns_when_package_manager_is_missing(tmp_path: Path) -> None:
    (tmp_path / "apps" / "web").mkdir(parents=True)
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "apps" / "web" / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "vercel.json").write_text("{}", encoding="utf-8")

    check = check_frontend_toolchain(tmp_path, environ={"PATH": ""})

    assert check.status == "warning"
    assert check.details["package_manager_commands"] == []


def test_ci_config_preflight_requires_project_gates(tmp_path: Path) -> None:
    missing = check_ci_config(tmp_path)
    assert missing.status == "failed"

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        """
name: CI
jobs:
  backend:
    steps:
      - run: python tools/preflight.py --profile production
      - run: pytest
      - run: docker build -f Dockerfile.api -t app .
      - run: python3 tools/smoke.py --base-url http://127.0.0.1:8000 --write
      - run: npm run lint
      - run: npm run typecheck
      - run: npm run build
""",
        encoding="utf-8",
    )

    check = check_ci_config(tmp_path)

    assert check.status == "passed"


def test_container_assets_preflight_requires_runtime_health_assets(tmp_path: Path) -> None:
    missing = check_container_assets(tmp_path)
    assert missing.status == "failed"

    (tmp_path / "Dockerfile.api").write_text(
        "FROM python:3.12-slim\nHEALTHCHECK CMD true\nCMD [\"uvicorn\"]\n",
        encoding="utf-8",
    )
    (tmp_path / ".dockerignore").write_text(".git\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text(
        """
services:
  api:
    environment:
      AGENT_WORKFLOW_DATABASE_URL: sqlite:////data/test.sqlite3
    volumes:
      - agent_workflow_data:/data
volumes:
  agent_workflow_data:
""",
        encoding="utf-8",
    )

    check = check_container_assets(tmp_path)

    assert check.status == "passed"


def test_preflight_report_summary_and_formatting() -> None:
    report = PreflightReport(
        profile="release",
        root="/repo",
        checks=[
            PreflightCheck(name="ok", status="passed", message="ready"),
            PreflightCheck(name="warn", status="warning", message="review"),
            PreflightCheck(name="bad", status="failed", message="fix"),
        ],
    )

    assert report.ready is False
    assert report.to_dict()["summary"] == {"passed": 1, "warnings": 1, "failed": 1}
    formatted = format_report(report)
    assert "Preflight profile: release" in formatted
    assert "[failed] bad: fix" in formatted
