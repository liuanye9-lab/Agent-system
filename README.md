# Agent Workflow Builder

Agent Workflow Builder is a project-grade foundation for a business process compiler: it turns business know-how into workflow packages, executable DAGs, traces, eval results, and optimization suggestions.

The first version defaults to mock LLM and mock tools, with an optional HTTP/OpenAI-compatible LLM adapter for typed Builder Plane generation across problem framing, process architecture, data contracts, tool policies, and eval specs. It is designed to be extended later with LangGraph, PydanticAI, A2A, Phoenix, Langfuse, DeepEval, Ragas, Temporal, PostgreSQL, and a visual graph canvas.

## Quick Start

Backend:

```bash
source .venv/bin/activate
uvicorn apps.api.main:app --reload
```

The API uses SQLite by default at `./data/agent_workflow_builder.sqlite3`. Local development seeds `examples/new_product_launch.workflow.json` on first startup; production deployments should set `AGENT_WORKFLOW_SEED_EXAMPLE_WORKFLOW=false`.

```bash
AGENT_WORKFLOW_DATABASE_URL=sqlite:///./data/prod.sqlite3 uvicorn apps.api.main:app --reload
```

Local auth settings:

```bash
python tools/password.py
AGENT_WORKFLOW_AUTH_SECRET_KEY=replace-with-a-long-random-secret
AGENT_WORKFLOW_ALLOW_DEV_ACTOR_HEADERS=false
AGENT_WORKFLOW_AUTH_USERS_JSON='{"admin":{"password_hash":"pbkdf2_sha256$310000$replace-salt$replace-digest","actor_id":"admin-1","role":"workflow-admin","display_name":"Admin"}}'
AGENT_WORKFLOW_SEED_EXAMPLE_WORKFLOW=false
```

Use `.env.example` as the release configuration template.

Optional LLM settings for Builder Plane generation:

```bash
AGENT_WORKFLOW_LLM_PROVIDER=openai-compatible
AGENT_WORKFLOW_LLM_ENDPOINT=https://api.example.com/v1/chat/completions
AGENT_WORKFLOW_LLM_MODEL=workflow-framer
AGENT_WORKFLOW_LLM_API_KEY=replace-with-provider-key
AGENT_WORKFLOW_LLM_TIMEOUT_SECONDS=30
```

Frontend:

```bash
cd apps/web
npm install
npm run dev
```

Tests:

```bash
pytest
```

Operational preflight:

```bash
python tools/preflight.py --profile local
python tools/preflight.py --profile production --json
python tools/preflight.py --profile release
```

`local` checks developer readiness, `production` tightens auth and LLM configuration, and `release` also checks GitHub/Vercel publishability. The command exits non-zero when a required release condition fails, while warnings identify acceptable local defaults that should not be promoted.

Post-deployment smoke check:

```bash
python3 tools/smoke.py --base-url http://127.0.0.1:8000
python3 tools/smoke.py --base-url http://127.0.0.1:8000 --write
```

The default smoke check is read-only: it verifies `/health`, `/ready`, bearer-token login, and package validation. `--write` imports an isolated `smoke-*` workflow package and starts a shadow run, so use it only against local or staging repositories where creating smoke artifacts is acceptable.

Containerized API:

```bash
docker compose up --build api
```

Repository snapshot:

```bash
python tools/snapshot.py export snapshots/pre-release.json
python tools/snapshot.py summary snapshots/pre-release.json
python tools/snapshot.py import snapshots/pre-release.json
```

Snapshots include workflow packages, versions, runs, eval results, and audit events. Treat them as sensitive operational backups.

Retention report:

```bash
python tools/retention.py --run-retention-days 90 --eval-retention-days 365 --audit-retention-days 365
python tools/retention.py --apply --confirm-retention-apply --snapshot-acknowledged --reason "cleanup after reviewed snapshot" --category terminal_runs --category eval_results
```

The default report is a dry run: it identifies expired terminal runs, active runs past retention, old eval results, and old audit events using low-sensitive counts and ID samples. Applying retention requires admin role, explicit confirmation, snapshot acknowledgement, and an operator reason; it only deletes expired terminal runs and expired eval results. Active runs and audit events are reported but not deleted.

Frontend check:

```bash
cd apps/web
npm run lint
```

## API Examples

Health:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

`/health` is a lightweight liveness check. `/ready` checks repository access, production-sensitive auth configuration, and LLM configuration; it returns `503` when the service should not receive traffic.

Create a bearer token for state-changing actions:

```bash
curl -X POST http://127.0.0.1:8000/api/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}'
```

Generate a workflow package:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  -d '{"user_request":"我想搭建一个新品上市流程智能体"}'
```

Generate from a structured business brief and node list:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  -d '{
    "user_request":"搭建客户升级处理流程",
    "workflow_id":"customer-escalation",
    "name":"客户升级处理流程智能体",
    "business_goal":"缩短高优先级客户问题从发现到解决的周期",
    "start_event":"客户提交高优先级问题",
    "end_state":"形成升级处理方案、责任人和客户回访计划",
    "process_nodes":[
      {"name":"接收升级信号","node_type":"read_node","owner_role":"客服负责人"},
      {"name":"诊断根因","node_type":"reasoning_node","owner_role":"技术支持"},
      {"name":"审查处理方案","node_type":"review_node","owner_role":"销售负责人"},
      {"name":"写入升级计划","node_type":"write_node","owner_role":"业务审批人"}
    ]
  }'
```

Generate a candidate version without changing the current version:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  -d '{"user_request":"更新新品上市流程","workflow_id":"new-product-launch","version":"0.2.0","save_as_current":false}'
```

Run a workflow:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/new-product-launch/runs \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  -d '{"input_payload":{"market":"global","product":"AI workflow platform"},"workflow_version":"0.1.0","max_steps":50,"max_retries":1,"shadow_mode":true,"idempotency_key":"launch-run-001"}'
```

`shadow_mode` lets operators run the workflow end-to-end before enabling live writes. Write nodes produce draft outputs and do not execute approval-gated write tools.

Approve a paused write node:

```bash
curl -X POST http://127.0.0.1:8000/api/runs/{run_id}/approval \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  -d '{"approved":true,"max_steps":50,"max_retries":1,"approval_payload":{"approver":"business-owner","decision":"go"}}'
```

Cancel a paused or running workflow run:

```bash
curl -X POST http://127.0.0.1:8000/api/runs/{run_id}/cancel \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  -d '{"reason":"duplicate launch request"}'
```

Rerun a terminal workflow run with its original input and workflow version:

```bash
curl -X POST http://127.0.0.1:8000/api/runs/{run_id}/rerun \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  -d '{"reason":"retry after fixing budget","max_steps":50,"max_retries":1,"idempotency_key":"rerun-001"}'
```

List runs for an operational status queue:

```bash
curl "http://127.0.0.1:8000/api/runs?workflow_id=new-product-launch&status=paused&limit=50&offset=0" \
  -H "Authorization: Bearer {token}"
```

Inspect run diagnostics before deciding whether to approve, cancel, or rerun:

```bash
curl http://127.0.0.1:8000/api/runs/{run_id}/diagnostics \
  -H "Authorization: Bearer {token}"
```

Compare a completed shadow run with human expected output:

```bash
curl -X POST http://127.0.0.1:8000/api/runs/{run_id}/shadow-comparisons \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  -d '{"expected_output":{"go-no-go-decision":{"decision":"shadow_draft_created"}},"pass_threshold":0.8,"notes":"matched historical handling"}'
```

Check whether a workflow version is ready for live execution:

```bash
curl http://127.0.0.1:8000/api/workflows/new-product-launch/release-readiness \
  -H "Authorization: Bearer {token}"
```

Live run requests can set `enforce_release_readiness: true` to block execution until the workflow version has passed package quality, version-tagged evals, a terminal shadow run, and a passing shadow comparison.

Import/export a workflow package:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/validate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  --data @examples/new_product_launch.workflow.json

curl -X POST http://127.0.0.1:8000/api/workflows/import \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  --data @examples/new_product_launch.workflow.json

curl -X POST "http://127.0.0.1:8000/api/workflows/import?save_as_current=false" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  --data @candidate.workflow.json

curl http://127.0.0.1:8000/api/workflows/new-product-launch/export \
  -H "Authorization: Bearer {token}"
```

Inspect version history and diff:

```bash
curl http://127.0.0.1:8000/api/workflows/new-product-launch/versions \
  -H "Authorization: Bearer {token}"

curl http://127.0.0.1:8000/api/workflows/new-product-launch/versions/0.1.0 \
  -H "Authorization: Bearer {token}"

curl "http://127.0.0.1:8000/api/workflows/new-product-launch/diff?from_version=0.1.0&to_version=0.2.0" \
  -H "Authorization: Bearer {token}"
```

Promote or roll back the current workflow version:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/new-product-launch/versions/0.1.0/promote \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  -d '{"reason":"rollback to stable package","change_summary":"restore previously validated package","risk_acceptance":"rollback risk accepted by workflow owner","reviewed_diff":true,"readiness_acknowledged":true}'
```

Inspect audit events:

```bash
curl "http://127.0.0.1:8000/api/governance/audit-events?workflow_id=new-product-launch&limit=50&offset=0" \
  -H "Authorization: Bearer {token}"

curl "http://127.0.0.1:8000/api/governance/audit-events?run_id={run_id}&limit=50&offset=0" \
  -H "Authorization: Bearer {token}"
```

Inspect the governance run account:

```bash
curl "http://127.0.0.1:8000/api/governance/run-report?workflow_id=new-product-launch" \
  -H "Authorization: Bearer {token}"
```

Inspect the governance risk account:

```bash
curl "http://127.0.0.1:8000/api/governance/risk-report?workflow_id=new-product-launch" \
  -H "Authorization: Bearer {token}"
```

Inspect the governance quality account:

```bash
curl "http://127.0.0.1:8000/api/governance/quality-report?workflow_id=new-product-launch" \
  -H "Authorization: Bearer {token}"
```

Inspect the governance cost account:

```bash
curl "http://127.0.0.1:8000/api/governance/cost-report?workflow_id=new-product-launch" \
  -H "Authorization: Bearer {token}"
```

Inspect the governance retention account:

```bash
curl "http://127.0.0.1:8000/api/governance/retention-report?workflow_id=new-product-launch" \
  -H "Authorization: Bearer {token}"
```

Apply retention through the governance API after reviewing a snapshot and dry-run report:

```bash
curl -X POST http://127.0.0.1:8000/api/governance/retention-apply \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  -d '{"workflow_id":"new-product-launch","run_retention_days":90,"eval_retention_days":365,"dry_run":false,"confirm_apply":true,"snapshot_acknowledged":true,"reason":"cleanup after reviewed snapshot"}'
```

Inspect the package repair plan generated from traces, evals, and optimizer signals:

```bash
curl "http://127.0.0.1:8000/api/workflows/new-product-launch/repair-plan?version=0.1.0" \
  -H "Authorization: Bearer {token}"
```

Run evals against a saved candidate version before promotion:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/new-product-launch/versions/0.1.1-repair/evals/run \
  -H "Authorization: Bearer {token}"
```

Create a repair candidate version from that plan without changing the current version:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/new-product-launch/repair-plan/preview \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  -d '{"source_version":"0.1.0","target_version":"0.1.1-repair","reason":"preview failed contract repair","selected_operation_ids":["new-product-launch-business-case-contract-repair"]}'

curl -X POST http://127.0.0.1:8000/api/workflows/new-product-launch/repair-plan/apply \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  -d '{"source_version":"0.1.0","target_version":"0.1.1-repair","reason":"repair failed contract and regression coverage","selected_operation_ids":["new-product-launch-business-case-contract-repair"]}'
```

The preview returns an `impact_preview` summary with changed sections, field paths, risk counts, release-gate impacts, and validation status so operators can review candidate impact without opening raw package values.

List APIs are bounded by default. Workflow, workflow version, run, run trace, eval result, and audit event list endpoints accept `limit` and `offset`; `limit` defaults to `50` and is capped at `200`. Run lists also accept `status` for operational queues such as `paused`, `failed`, and `canceled`.

## Directory Structure

```text
apps/api                 FastAPI app and routes
apps/web                 Next.js dashboard
packages/workflow_core   Core schemas, builder, runtime, governance, storage, adapters
examples                 Versioned workflow package examples
docs                     Architecture and operating documents
tests                    Pytest coverage
```

Deployment details are documented in `docs/deployment.md`.

## Current Project Scope

- Builder Plane can use mock LLM or an environment-configured HTTP/OpenAI-compatible LLM to generate typed problem frames, process architecture, node data contracts, sandboxed tool policies, and eval specs; invalid LLM output falls back to deterministic safe defaults.
- Generated Agent specs record provider/model metadata.
- Workflow generation accepts a structured business brief and optional process node list instead of forcing every request into the default example flow.
- Workflow generation and import can save candidate versions without changing the current version, so diff and promotion gates can run before release.
- Dashboard operators can validate pasted workflow package JSON, import it as a candidate or current version, and open an imported candidate with its version preselected for diff review.
- Runtime Plane compiles and runs a lightweight DAG.
- Workflow detail UI can launch a selected workflow version with operator-supplied JSON input, run budget, retry budget, idempotency key, shadow mode, and release-readiness enforcement.
- Workflow detail UI shows a candidate version diff review before promotion so operators can inspect package changes alongside readiness and shadow-run evidence.
- Workflow detail UI can run evals against the selected current or candidate version before promotion.
- Workflow detail UI promotes selected versions with an operator release manifest instead of a hard-coded reason.
- Run detail UI supports operator-supplied approval payload JSON, approval budgets, cancellation reason, rerun budgets, rerun idempotency key, rerun shadow/live mode, diagnostics, and shadow-comparison notes.
- Runtime validates node inputs and outputs against each node's JSON Schema data contract.
- Runtime enforces a bounded `max_steps` execution budget for run start and approval resume.
- Runtime records retry attempts and can retry retryable node failures up to a per-run budget.
- Runtime checkpoints workflow runs to the repository at start, after node attempts, and at terminal or paused states so operators can inspect partial progress after interruption.
- `/ready` reports repository schema metadata, auth, and LLM configuration readiness without exposing secret values.
- Run and trace API responses recursively redact sensitive keys such as tokens, passwords, secrets, API keys, and authorization headers.
- Tool execution runs through a sandbox boundary that enforces tool permission, role/scope checks, approval, and input/output schema before mock or MCP-bound invocation.
- MCP-bound tools can use a managed JSON-RPC session pool with initialize/initialized handshake, protocol-version headers, session-id reuse, expired-session reinitialization, and `tools/call` structured output mapping.
- MCP sessions can use a scoped authorization provider that selects transport headers by tool permission level and required scopes after the registry has passed approval, role, scope, and schema checks.
- Write nodes with `write_requires_approval` pause before execution and can resume after approval.
- Approval API resolves actor identity from bearer token or dev header fallback, then checks `workflow:approve` scope and role authorization against tool policy `allowed_roles`.
- Sensitive actions support HMAC-signed bearer tokens with scope authorization; dev actor headers can be disabled with `AGENT_WORKFLOW_ALLOW_DEV_ACTOR_HEADERS=false`.
- Configured auth users support PBKDF2-SHA256 `password_hash` values; production and release preflight reject plaintext configured passwords.
- Production and release preflight reject automatic example workflow seeding so demo data is not injected into live repositories.
- Workflow run start accepts an optional `idempotency_key`; retried identical requests return the original run, while reusing a key for a different payload or version returns `409`.
- Workflow run start supports `shadow_mode` for pre-production and rollout validation; write nodes generate drafts without executing approval-gated write tools.
- Shadow comparisons persist human expected-output checks as eval results and audit events without storing raw comparison payloads in audit details.
- Release readiness checks combine package quality, version-tagged evals, terminal shadow runs, and passing shadow comparisons. Live run requests may enforce this gate before execution.
- Governance run reports summarize status queues, pending approvals, recovery items, and shadow validation work using bounded low-sensitive samples.
- Governance quality reports summarize node success, eval pass rate, shadow comparisons, release readiness, and optimizer signals using low-sensitive counts and reason codes.
- Governance cost reports estimate input/output tokens, trace duration, retry traces, and human touches without exposing raw payloads.
- Governance risk reports summarize tool risk, run risk, quality risk, and audit risk using low-sensitive counts and reason codes.
- Governance trace export can build low-sensitive OTLP JSON spans for a run, optionally send them to an OTLP-compatible endpoint, and audit the export without copying raw run or trace payloads.
- Package repair plans turn failed traces, failed evals, approval pauses, and baseline gaps into low-sensitive contract, eval, and tool-policy operations for review before generating or promoting a candidate version.
- Repair plans can preview and create candidate workflow versions with selected contract, tool-policy, and eval-spec updates; the current version is not changed until the normal promotion gates pass.
- Terminal workflow runs can be rerun against their original input and workflow package version, with optional idempotency and audit lineage.
- Run diagnostics summarize failed nodes, retry or step-budget exhaustion, approval state, trace counts, and recommended recovery actions without returning raw payload snapshots.
- Workflow, run, trace, eval result, metric, audit, and optimization read APIs require `workflow:read`.
- Workflow, workflow version, run, trace, eval result, and audit event list APIs use bounded `limit`/`offset` pagination with a default limit of 50 and maximum limit of 200.
- Run list APIs support status filtering for operational queues.
- Workflow generation/import require `workflow:write`, workflow runs require `workflow:run`, run cancellation requires `workflow:cancel`, and manual eval execution requires `workflow:evaluate`.
- Operational preflight checks report local, production, and release readiness across Python runtime, required files, CI coverage, container assets, backend configuration, auth hardening, LLM setup, frontend tooling, GitHub publishability, and Vercel publishability without exposing secret values.
- Release configuration is captured in `.env.example`, and GitHub/Vercel deployment steps are documented in `docs/deployment.md`.
- GitHub Actions CI covers backend tests, production preflight, API container health, and Next.js dashboard build.
- `Dockerfile.api` and `docker-compose.yml` provide a containerized API runtime with persistent SQLite storage and health checks.
- Repository snapshots can export and restore workflow packages, versions, runs, eval results, and audit events for migration, disaster recovery, and release evidence.
- Retention reports provide low-sensitive dry-run counts and ID samples for terminal runs, active runs past retention, eval results, and audit events.
- Retention apply can purge expired terminal runs and eval results after admin role, explicit confirmation, snapshot acknowledgement, and operator reason; active runs and audit events remain report-only.
- Governance UI exposes retention dry run and confirmed apply controls alongside eligible, deleted, and skipped counts.
- Governance Plane records traces, runs workflow-executed golden/regression evals, collects metrics, suggests optimizations, builds package repair plans, and can save repair candidates for review.
- Repository layer defaults to durable SQLite storage for workflow packages, run checkpoints, traces, eval results, audit events, and repository schema metadata.
- Workflow packages can be imported, exported, validated, run, evaluated, and inspected through API routes.
- Import and promote use a workflow quality gate that blocks invalid package structure, graph reachability issues, tool permission violations, invalid JSON Schema, and missing eval specs.
- Promote also runs package eval specs before changing the current version; failed evals are persisted and block publish or rollback.
- Candidate workflow versions can run evals before promotion; persisted eval results include the workflow version for governance filtering.
- Workflow package version history is preserved, and saved versions can be exported or diffed.
- Saved versions can be promoted as the current workflow version by actors with `workflow-admin` role and `workflow:promote` scope, enabling controlled publish and rollback.
- Workflow runs bind to the package version selected at start; approval resume uses the original version even if the current workflow changes later.
- Workflow generation/import, generation failures, run start, run idempotency replays, run reruns, run cancellation, manual eval runs, approval decisions, and workflow version promotion attempts are persisted as audit events with low-sensitive summaries, including failed promotion gates.

## Not Implemented Yet

- Real LangGraph orchestration
- External secret-manager backed rotation for MCP authorization provider credentials
- External identity-provider integration and RBAC administration UI
- Vendor-specific trace platform setup, sampling, and dashboards
- External DeepEval/Ragas runner and regression dataset management
- React Flow visual canvas
- Organization-grade SSO, user lifecycle management, and multi-tenant authorization policy administration

## Extension Direction

1. Expand repair review with richer field-level impact previews before candidate-version generation.
2. Replace the lightweight runner with a LangGraph adapter.
3. Expand MCP credential rotation, descriptor sync, and richer streaming transports.
4. Add normalized PostgreSQL persistence when query scale requires it.
5. Add React Flow process visualization.
6. Connect Phoenix, Langfuse, DeepEval, and Ragas dashboards/runners.
