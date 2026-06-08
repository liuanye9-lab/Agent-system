# Deployment Guide

This project is split into a FastAPI backend and a Next.js operator dashboard. The current Vercel configuration deploys the dashboard from `apps/web`; the backend should run on a Python-capable host or container runtime and expose `NEXT_PUBLIC_API_BASE_URL` to the dashboard.

## Preflight

Run local checks before development:

```bash
python tools/preflight.py --profile local
```

Run production checks before exposing the backend:

```bash
python tools/preflight.py --profile production
```

Run release checks before pushing and deploying:

```bash
python tools/preflight.py --profile release
```

Release preflight fails deliberately when GitHub credentials, Vercel credentials, production auth, or deployment links are missing.

## Smoke Checks

After a backend is running, use the smoke CLI to verify the deployed API path:

```bash
python3 tools/smoke.py --base-url https://your-api.example.com
```

The default mode is read-only and checks liveness, traffic readiness, bearer-token login, and workflow-package validation. Credentials default to `admin` / `admin` for local development, and can be supplied with `--username`, `--password`, `AGENT_WORKFLOW_SMOKE_USERNAME`, and `AGENT_WORKFLOW_SMOKE_PASSWORD`.

For local or staging repositories where creating smoke artifacts is acceptable, run the write smoke:

```bash
python3 tools/smoke.py --base-url https://your-api.example.com --write
```

Write smoke imports a uniquely named `smoke-*` workflow package and starts a shadow run. It does not execute live writes, but it does persist the smoke workflow, run, traces, eval/audit side effects created by the API path, so do not use it against production data without an approved cleanup policy.

## Continuous Integration

`.github/workflows/ci.yml` runs on pushes and pull requests to `main`.

The workflow checks:

- backend dependency installation and `pytest`
- production preflight with hardened auth environment values
- API container build and `/ready` health check
- Next.js dashboard dependency installation and production build

## Backend

Configure environment values from `.env.example`:

```bash
python tools/password.py
AGENT_WORKFLOW_DATABASE_URL=sqlite:///./data/prod.sqlite3
AGENT_WORKFLOW_AUTH_SECRET_KEY=replace-with-a-long-random-secret
AGENT_WORKFLOW_ALLOW_DEV_ACTOR_HEADERS=false
AGENT_WORKFLOW_AUTH_USERS_JSON='{"admin":{"password_hash":"pbkdf2_sha256$310000$replace-salt$replace-digest","actor_id":"admin-1","role":"workflow-admin","display_name":"Admin"}}'
AGENT_WORKFLOW_SEED_EXAMPLE_WORKFLOW=false
AGENT_WORKFLOW_RUN_RETENTION_DAYS=90
AGENT_WORKFLOW_EVAL_RETENTION_DAYS=365
AGENT_WORKFLOW_AUDIT_RETENTION_DAYS=365
```

Start the service:

```bash
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
```

Use `/health` for liveness and `/ready` for traffic readiness. `/ready` checks repository access, reports low-sensitive repository schema metadata, and checks auth hardening and LLM configuration without returning secret values.

Local development may seed `examples/new_product_launch.workflow.json` automatically. Production environments should disable this with `AGENT_WORKFLOW_SEED_EXAMPLE_WORKFLOW=false` and import only reviewed workflow packages.

## Containerized API

Build and run the API container locally:

```bash
docker compose up --build api
```

The compose service stores SQLite data in the `agent_workflow_data` volume and exposes the API on `http://127.0.0.1:8000`.

For production container deployments, set at least:

```bash
python tools/password.py
AGENT_WORKFLOW_AUTH_SECRET_KEY=replace-with-a-long-random-secret
AGENT_WORKFLOW_ALLOW_DEV_ACTOR_HEADERS=false
AGENT_WORKFLOW_AUTH_USERS_JSON='{"admin":{"password_hash":"pbkdf2_sha256$310000$replace-salt$replace-digest","actor_id":"admin-1","role":"workflow-admin","display_name":"Admin"}}'
AGENT_WORKFLOW_SEED_EXAMPLE_WORKFLOW=false
```

## Snapshots and Recovery

Create a snapshot before release, migration, or risky workflow package changes:

```bash
mkdir -p snapshots
python tools/snapshot.py export snapshots/pre-release.json
```

Inspect a snapshot without importing it:

```bash
python tools/snapshot.py summary snapshots/pre-release.json
```

Restore into the configured repository:

```bash
python tools/snapshot.py import snapshots/pre-release.json
```

Snapshots include workflow packages, saved versions, workflow runs, trace payloads, eval results, and audit events. They may contain raw run payloads that HTTP responses normally redact, so store them with the same controls as database backups. The default `.gitignore` excludes `snapshots/`.

Build a low-sensitive retention report before cleanup windows:

```bash
python tools/retention.py --run-retention-days 90 --eval-retention-days 365 --audit-retention-days 365
```

The default retention report is a dry run. It does not delete data; it identifies expired terminal runs, active runs past retention, old eval results, and old audit events with bounded ID samples so operators can snapshot, investigate, archive, or purge through an approved process.

After a reviewed snapshot and dry-run report, apply the conservative cleanup:

```bash
python tools/retention.py --apply --confirm-retention-apply --snapshot-acknowledged --reason "cleanup after reviewed snapshot" --category terminal_runs --category eval_results
```

Retention apply requires explicit confirmation, snapshot acknowledgement, and an operator reason. It deletes only expired terminal runs and expired eval results. Active runs and audit events remain report-only and require a separate compliance-approved process.

## GitHub

The repository remote is expected to be:

```bash
git remote add origin https://github.com/liuanye9-lab/Agent-system.git
git push -u origin main
```

For local push, configure one of:

- GitHub HTTPS credentials in the macOS keychain
- SSH key access to `git@github.com`
- `GITHUB_TOKEN` or `GH_TOKEN` for CI/CLI automation

## Vercel Dashboard

Install and authenticate the Vercel CLI, then link the project:

```bash
npm install -g vercel
vercel link
vercel deploy
```

Set dashboard environment values:

```bash
NEXT_PUBLIC_API_BASE_URL=https://your-backend.example.com
```

The root `vercel.json` uses:

- `installCommand`: `npm install`
- `buildCommand`: `npm run build`
- `outputDirectory`: `apps/web/.next`
- `framework`: `nextjs`

If Vercel Git Integration is enabled, pushing to GitHub can trigger preview or production deployments after the project has been imported and linked.
