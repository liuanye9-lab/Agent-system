from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterable, Literal

from apps.api.settings import DEFAULT_AUTH_SECRET_KEY, Settings
from packages.workflow_core.security import PASSWORD_HASH_SCHEME

PreflightStatus = Literal["passed", "warning", "failed"]
PreflightProfile = Literal["local", "production", "release"]


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    status: PreflightStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class PreflightReport:
    profile: PreflightProfile
    root: str
    checks: list[PreflightCheck]

    @property
    def failed_count(self) -> int:
        return sum(1 for check in self.checks if check.status == "failed")

    @property
    def warning_count(self) -> int:
        return sum(1 for check in self.checks if check.status == "warning")

    @property
    def passed_count(self) -> int:
        return sum(1 for check in self.checks if check.status == "passed")

    @property
    def ready(self) -> bool:
        return self.failed_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "root": self.root,
            "ready": self.ready,
            "summary": {
                "passed": self.passed_count,
                "warnings": self.warning_count,
                "failed": self.failed_count,
            },
            "checks": [check.to_dict() for check in self.checks],
        }


def run_preflight(
    root: Path | str = ".",
    profile: PreflightProfile = "local",
    settings: Settings | None = None,
    environ: dict[str, str] | None = None,
) -> PreflightReport:
    root_path = Path(root).resolve()
    environment = dict(os.environ if environ is None else environ)
    app_settings = settings or Settings()
    checks = [
        check_python_version(),
        check_project_files(root_path),
        check_ci_config(root_path),
        check_container_assets(root_path),
        check_backend_config(app_settings),
        check_seed_config(app_settings, profile=profile),
        check_retention_config(app_settings),
        check_auth_config(app_settings, profile=profile),
        check_llm_config(app_settings, profile=profile),
        check_frontend_toolchain(root_path, environment),
        check_git_publishability(root_path, profile=profile),
        check_vercel_publishability(root_path, profile=profile, environ=environment),
    ]
    return PreflightReport(profile=profile, root=str(root_path), checks=checks)


def check_python_version() -> PreflightCheck:
    version = sys.version_info
    if version >= (3, 11):
        return PreflightCheck(
            name="python_runtime",
            status="passed",
            message=f"Python {version.major}.{version.minor}.{version.micro} satisfies >=3.11.",
        )
    return PreflightCheck(
        name="python_runtime",
        status="failed",
        message=f"Python {version.major}.{version.minor}.{version.micro} is too old; use Python >=3.11.",
    )


def check_project_files(root: Path) -> PreflightCheck:
    required_files = [
        "pyproject.toml",
        "apps/api/main.py",
        "packages/workflow_core/runtime/runner.py",
        "examples/new_product_launch.workflow.json",
        "README.md",
    ]
    missing = [path for path in required_files if not (root / path).exists()]
    if missing:
        return PreflightCheck(
            name="project_files",
            status="failed",
            message="Required project files are missing.",
            details={"missing": missing},
        )
    return PreflightCheck(
        name="project_files",
        status="passed",
        message="Core project files are present.",
        details={"checked": required_files},
    )


def check_ci_config(root: Path) -> PreflightCheck:
    workflow_path = root / ".github" / "workflows" / "ci.yml"
    if not workflow_path.exists():
        return PreflightCheck(
            name="ci_config",
            status="failed",
            message="GitHub Actions CI workflow is missing.",
            details={"expected": ".github/workflows/ci.yml"},
        )
    content = workflow_path.read_text(encoding="utf-8")
    required_snippets = [
        "pytest",
        "tools/preflight.py --profile production",
        "docker build",
        "tools/smoke.py --base-url",
        "npm run lint",
        "npm run typecheck",
        "npm run build",
    ]
    missing_snippets = [snippet for snippet in required_snippets if snippet not in content]
    if missing_snippets:
        return PreflightCheck(
            name="ci_config",
            status="failed",
            message="CI workflow exists but is missing required project-grade gates.",
            details={"missing_snippets": missing_snippets},
        )
    return PreflightCheck(
        name="ci_config",
        status="passed",
        message="CI workflow covers backend tests, preflight, container health, smoke checks, frontend lint, typecheck, and build.",
        details={"path": ".github/workflows/ci.yml"},
    )


def check_container_assets(root: Path) -> PreflightCheck:
    required_files = ["Dockerfile.api", ".dockerignore", "docker-compose.yml"]
    missing = [path for path in required_files if not (root / path).exists()]
    if missing:
        return PreflightCheck(
            name="container_assets",
            status="failed",
            message="Container deployment assets are missing.",
            details={"missing": missing},
        )
    dockerfile = (root / "Dockerfile.api").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    required_snippets = [
        "HEALTHCHECK",
        "uvicorn",
        "AGENT_WORKFLOW_DATABASE_URL",
        "agent_workflow_data",
    ]
    missing_snippets = [
        snippet
        for snippet in required_snippets
        if snippet not in dockerfile and snippet not in compose
    ]
    if missing_snippets:
        return PreflightCheck(
            name="container_assets",
            status="failed",
            message="Container assets exist but are missing runtime or health-check configuration.",
            details={"missing_snippets": missing_snippets},
        )
    return PreflightCheck(
        name="container_assets",
        status="passed",
        message="Container assets provide API image, compose service, persistent data, and health checks.",
        details={"files": required_files},
    )


def check_backend_config(settings: Settings) -> PreflightCheck:
    if not settings.database_url:
        return PreflightCheck(
            name="backend_config",
            status="failed",
            message="AGENT_WORKFLOW_DATABASE_URL resolved to an empty value.",
        )
    return PreflightCheck(
        name="backend_config",
        status="passed",
        message="Backend database URL is configured.",
        details={
            "database_scheme": settings.database_url.partition("://")[0] or "unknown",
            "service_name": settings.service_name,
        },
    )


def check_seed_config(settings: Settings, profile: PreflightProfile) -> PreflightCheck:
    if settings.seed_example_workflow and profile in {"production", "release"}:
        return PreflightCheck(
            name="seed_config",
            status="failed",
            message="Example workflow seeding must be disabled outside local development.",
            details={"AGENT_WORKFLOW_SEED_EXAMPLE_WORKFLOW": True},
        )
    if settings.seed_example_workflow:
        return PreflightCheck(
            name="seed_config",
            status="warning",
            message="Example workflow seeding is enabled for local development.",
            details={"AGENT_WORKFLOW_SEED_EXAMPLE_WORKFLOW": True},
        )
    return PreflightCheck(
        name="seed_config",
        status="passed",
        message="Example workflow seeding is disabled.",
        details={"AGENT_WORKFLOW_SEED_EXAMPLE_WORKFLOW": False},
    )


def check_retention_config(settings: Settings) -> PreflightCheck:
    values = {
        "AGENT_WORKFLOW_RUN_RETENTION_DAYS": settings.run_retention_days,
        "AGENT_WORKFLOW_EVAL_RETENTION_DAYS": settings.eval_retention_days,
        "AGENT_WORKFLOW_AUDIT_RETENTION_DAYS": settings.audit_retention_days,
    }
    invalid = {name: value for name, value in values.items() if value < 1}
    if invalid:
        return PreflightCheck(
            name="retention_config",
            status="failed",
            message="Retention settings must be positive day counts.",
            details={"invalid": invalid},
        )
    return PreflightCheck(
        name="retention_config",
        status="passed",
        message="Retention settings are configured.",
        details=values,
    )


def check_auth_config(settings: Settings, profile: PreflightProfile) -> PreflightCheck:
    problems: list[str] = []
    warnings: list[str] = []
    if settings.auth_secret_key == DEFAULT_AUTH_SECRET_KEY:
        if profile in {"production", "release"}:
            problems.append("AGENT_WORKFLOW_AUTH_SECRET_KEY still uses the local development default.")
        else:
            warnings.append("AGENT_WORKFLOW_AUTH_SECRET_KEY uses the local development default.")
    if settings.allow_dev_actor_headers:
        if profile in {"production", "release"}:
            problems.append("AGENT_WORKFLOW_ALLOW_DEV_ACTOR_HEADERS must be false outside local development.")
        else:
            warnings.append("Developer actor headers are enabled for local development.")
    if not settings.auth_users_json:
        if profile in {"production", "release"}:
            problems.append("AGENT_WORKFLOW_AUTH_USERS_JSON is required for token issuance.")
        else:
            warnings.append("AGENT_WORKFLOW_AUTH_USERS_JSON is not set; built-in local users will be used.")
    else:
        auth_user_problems, auth_user_warnings = _auth_users_password_findings(settings.auth_users_json, profile)
        problems.extend(auth_user_problems)
        warnings.extend(auth_user_warnings)

    if problems:
        return PreflightCheck(
            name="auth_config",
            status="failed",
            message="Authentication configuration is not production-safe.",
            details={"problems": problems, "warnings": warnings},
        )
    if warnings:
        return PreflightCheck(
            name="auth_config",
            status="warning",
            message="Authentication is acceptable for local use but not production-hardened.",
            details={"warnings": warnings},
        )
    return PreflightCheck(
        name="auth_config",
        status="passed",
        message="Authentication configuration is production-safe.",
        details={"dev_actor_headers_enabled": settings.allow_dev_actor_headers},
    )


def _auth_users_password_findings(auth_users_json: str, profile: PreflightProfile) -> tuple[list[str], list[str]]:
    try:
        loaded = json.loads(auth_users_json)
    except json.JSONDecodeError:
        return ["AGENT_WORKFLOW_AUTH_USERS_JSON must be valid JSON."], []
    if not isinstance(loaded, dict) or not loaded:
        return ["AGENT_WORKFLOW_AUTH_USERS_JSON must be a non-empty object."], []

    problems: list[str] = []
    warnings: list[str] = []
    for username, record in loaded.items():
        if not isinstance(record, dict):
            problems.append(f"auth user {username!r} must be an object.")
            continue
        has_password_hash = isinstance(record.get("password_hash"), str) and bool(record.get("password_hash"))
        has_plain_password = isinstance(record.get("password"), str) and bool(record.get("password"))
        if has_password_hash:
            password_hash = str(record["password_hash"])
            if not password_hash.startswith(f"{PASSWORD_HASH_SCHEME}$"):
                problems.append(f"auth user {username!r} has an unsupported password_hash scheme.")
            continue
        if has_plain_password:
            if profile in {"production", "release"}:
                problems.append(f"auth user {username!r} uses plaintext password; use password_hash instead.")
            else:
                warnings.append(f"auth user {username!r} uses plaintext password for local development.")
            continue
        problems.append(f"auth user {username!r} must define password_hash.")
    return problems, warnings


def check_llm_config(settings: Settings, profile: PreflightProfile) -> PreflightCheck:
    if settings.llm_provider == "mock":
        status: PreflightStatus = "warning" if profile in {"production", "release"} else "passed"
        return PreflightCheck(
            name="llm_config",
            status=status,
            message=(
                "Mock LLM is configured; this is useful for local tests but not a production builder backend."
                if status == "warning"
                else "Mock LLM is configured for deterministic local use."
            ),
            details={"provider": "mock"},
        )

    missing = [
        name
        for name, value in {
            "AGENT_WORKFLOW_LLM_ENDPOINT": settings.llm_endpoint,
            "AGENT_WORKFLOW_LLM_MODEL": settings.llm_model,
            "AGENT_WORKFLOW_LLM_API_KEY": settings.llm_api_key,
        }.items()
        if not value
    ]
    if missing:
        return PreflightCheck(
            name="llm_config",
            status="failed",
            message="Real LLM provider is selected but required settings are missing.",
            details={"provider": settings.llm_provider, "missing": missing},
        )
    return PreflightCheck(
        name="llm_config",
        status="passed",
        message="Real LLM provider settings are present.",
        details={
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "endpoint_configured": bool(settings.llm_endpoint),
            "api_key_configured": bool(settings.llm_api_key),
        },
    )


def check_frontend_toolchain(root: Path, environ: dict[str, str]) -> PreflightCheck:
    package_files = ["package.json", "apps/web/package.json", "vercel.json"]
    missing = [path for path in package_files if not (root / path).exists()]
    node_tools = _available_commands(["npm", "pnpm", "yarn"], environ)
    if missing:
        return PreflightCheck(
            name="frontend_toolchain",
            status="failed",
            message="Frontend package or deployment files are missing.",
            details={"missing": missing, "package_manager_commands": node_tools},
        )
    if not node_tools:
        return PreflightCheck(
            name="frontend_toolchain",
            status="warning",
            message="Frontend package files exist, but no npm/pnpm/yarn command is available on PATH.",
            details={"package_files": package_files, "package_manager_commands": node_tools},
        )
    return PreflightCheck(
        name="frontend_toolchain",
        status="passed",
        message="Frontend package files and package manager command are available.",
        details={"package_files": package_files, "package_manager_commands": node_tools},
    )


def check_git_publishability(root: Path, profile: PreflightProfile) -> PreflightCheck:
    if not (root / ".git").exists():
        return PreflightCheck(
            name="git_publishability",
            status="failed" if profile == "release" else "warning",
            message="Project is not initialized as a Git repository.",
        )

    remote = _run_command(["git", "remote", "get-url", "origin"], cwd=root)
    status = _run_command(["git", "status", "--short"], cwd=root)
    branch = _run_command(["git", "branch", "--show-current"], cwd=root)
    details: dict[str, Any] = {
        "branch": branch.stdout.strip() if branch.returncode == 0 else None,
        "remote_origin_configured": remote.returncode == 0 and bool(remote.stdout.strip()),
        "worktree_clean": status.returncode == 0 and not status.stdout.strip(),
    }
    if remote.returncode == 0:
        details["remote_origin"] = remote.stdout.strip()

    if not details["remote_origin_configured"]:
        return PreflightCheck(
            name="git_publishability",
            status="failed" if profile == "release" else "warning",
            message="Git remote origin is not configured.",
            details=details,
        )
    if not details["worktree_clean"]:
        return PreflightCheck(
            name="git_publishability",
            status="warning",
            message="Git worktree has uncommitted changes.",
            details={**details, "status": status.stdout.strip().splitlines()},
        )

    credential_status = _github_credential_status(root)
    details.update(credential_status)
    if profile == "release" and not (
        credential_status["https_credentials_available"]
        or credential_status["ssh_publickey_available"]
        or os.getenv("GITHUB_TOKEN")
        or os.getenv("GH_TOKEN")
    ):
        return PreflightCheck(
            name="git_publishability",
            status="failed",
            message="GitHub remote is configured, but no local push credential was detected.",
            details=details,
        )
    return PreflightCheck(
        name="git_publishability",
        status="passed",
        message="Git repository and remote are configured.",
        details=details,
    )


def check_vercel_publishability(root: Path, profile: PreflightProfile, environ: dict[str, str]) -> PreflightCheck:
    vercel_json = root / "vercel.json"
    linked_project = root / ".vercel" / "project.json"
    vercel_command = shutil.which("vercel", path=environ.get("PATH"))
    token_configured = bool(environ.get("VERCEL_TOKEN"))
    details = {
        "vercel_json_present": vercel_json.exists(),
        "vercel_project_linked": linked_project.exists(),
        "vercel_cli_available": bool(vercel_command),
        "vercel_token_configured": token_configured,
    }

    if not vercel_json.exists():
        return PreflightCheck(
            name="vercel_publishability",
            status="failed" if profile == "release" else "warning",
            message="vercel.json is missing.",
            details=details,
        )
    if profile == "release" and not (linked_project.exists() or (vercel_command and token_configured)):
        return PreflightCheck(
            name="vercel_publishability",
            status="failed",
            message="Vercel deployment config exists, but no linked project or CLI token was detected.",
            details=details,
        )
    if not (linked_project.exists() or vercel_command):
        return PreflightCheck(
            name="vercel_publishability",
            status="warning",
            message="Vercel config exists, but Vercel CLI/project link is not available locally.",
            details=details,
        )
    return PreflightCheck(
        name="vercel_publishability",
        status="passed",
        message="Vercel deployment prerequisites are available.",
        details=details,
    )


def _available_commands(commands: Iterable[str], environ: dict[str, str]) -> list[str]:
    path = environ.get("PATH")
    return [command for command in commands if shutil.which(command, path=path)]


def _github_credential_status(root: Path) -> dict[str, Any]:
    credential = _run_command(
        ["git", "credential", "fill"],
        cwd=root,
        input_text="protocol=https\nhost=github.com\n\n",
        extra_env={"GIT_TERMINAL_PROMPT": "0"},
        timeout_seconds=3,
    )
    ssh = _run_command(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=3",
            "-T",
            "git@github.com",
        ],
        cwd=root,
        timeout_seconds=5,
    )
    return {
        "https_credentials_available": credential.returncode == 0 and "username=" in credential.stdout,
        "ssh_publickey_available": ssh.returncode == 1 and "successfully authenticated" in ssh.stderr,
        "github_token_configured": bool(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")),
    }


def _run_command(
    command: list[str],
    cwd: Path,
    input_text: str | None = None,
    extra_env: dict[str, str] | None = None,
    timeout_seconds: int = 10,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, returncode=127, stdout="", stderr=str(exc))


def format_report(report: PreflightReport) -> str:
    lines = [
        f"Preflight profile: {report.profile}",
        f"Root: {report.root}",
        f"Ready: {'yes' if report.ready else 'no'}",
        f"Summary: {report.passed_count} passed, {report.warning_count} warnings, {report.failed_count} failed",
        "",
    ]
    for check in report.checks:
        lines.append(f"[{check.status}] {check.name}: {check.message}")
        if check.details:
            lines.append(f"  details: {json.dumps(check.details, ensure_ascii=False, sort_keys=True)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deployment and operations preflight checks.")
    parser.add_argument("--root", default=".", help="Project root to inspect.")
    parser.add_argument(
        "--profile",
        choices=["local", "production", "release"],
        default="local",
        help="Preflight strictness profile.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    report = run_preflight(root=args.root, profile=args.profile)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
