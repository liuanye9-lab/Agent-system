# Agent Workflow Builder System Instructions

## 1. Project Mission

Build an Agent Workflow Builder System: a business process compiler that turns business know-how into versioned workflow packages, executable agent graphs, traces, evals, and optimization suggestions.

This project is not a generic chatbot. The core product is a system that can help generate, validate, run, observe, evaluate, and improve Agent workflows.

## 2. Architecture Boundaries

- `packages/workflow_core`: Core DSL, schemas, Builder Plane, Runtime Plane, Governance Plane, storage abstractions, and adapters.
- `apps/api`: FastAPI service surface. API routes should call core services through repository and runtime interfaces.
- `apps/web`: Next.js dashboard. UI should visualize and operate workflows, runs, traces, evals, and governance summaries.
- `examples`: Versioned workflow package examples.
- `docs`: Architecture, package spec, roadmap, and security documents.
- `tests`: Pytest coverage for core models, runtime, trace, eval, and optimizer behavior.

Do not collapse these layers into one file or one large service module.

## 3. Coding Standards

- Use Python 3.11+ and Pydantic v2 for all core schemas.
- All Agent outputs must be validated with Pydantic schemas before being stored, compiled, or executed.
- Workflow generation should accept structured business briefs and ordered process node lists, not only a single free-form prompt or the fixed example flow.
- Workflow generation and import should support saving candidate versions without changing the current version; candidate versions must still require `workflow:write` and go through promotion before live default use.
- Workflow package import UI should support package JSON validation before import and let operators choose candidate save or current-version save.
- LLM behavior must remain adapter-injected. Real LLM configuration must use environment variables, readiness checks, typed JSON parsing, and low-sensitive generation-failure audit details.
- MCP tool behavior must remain adapter-injected. MCP descriptors must compile into `ToolPolicy` records and pass permission, approval, scope, and JSON Schema checks before any external invoker or MCP session transport is called.
- MCP authorization providers may add transport headers only behind `MCPServerSessionPool`; never copy MCP credentials into traces, audit events, or user-facing diagnostics.
- Runtime node payloads must be validated against `DataContract` input/output JSON Schema before crossing node boundaries.
- Workflow run launch UI must accept operator-supplied JSON input, selected workflow version, bounded step/retry budgets, shadow mode, readiness enforcement, and optional idempotency key; do not hard-code example payloads as the only runnable path.
- Run detail UI must let operators supply approval payload JSON, approval resume budgets, cancellation reason, rerun budgets, rerun idempotency key, rerun shadow/live mode, and shadow-comparison notes.
- Workflow detail UI should show candidate version diffs before promotion, using package diff data rather than runtime payloads or trace snapshots.
- Workflow promotion UI/API should carry an operator release manifest: reason, change summary, risk acceptance, diff review flag, readiness acknowledgement, and low-sensitive gate evidence.
- Keep Builder, Runtime, Governance, storage, and adapter responsibilities separate.
- External model/tool behavior must be injected through interfaces such as LLM adapters and tool adapters.
- Do not hard-code real API keys, tokens, credentials, or private endpoints.
- Sensitive API actions must resolve actor identity from signed bearer tokens or an explicitly enabled development fallback.
- Sensitive API actions must enforce required token scopes before role-specific authorization.
- Workflow reads, generation/import, run execution, manual eval execution, approval, run cancellation, and promotion must require `workflow:read`, `workflow:write`, `workflow:run`, `workflow:evaluate`, `workflow:approve`, `workflow:cancel`, and `workflow:promote` respectively.
- Prefer small, focused modules over broad manager classes.
- Workflow packages must be versionable and serializable as JSON.
- Saving a workflow package must preserve version history; do not silently overwrite historical versions.
- Changing the current workflow version must go through promote/rollback behavior with actor authorization.
- Workflow import and version promotion must pass the package quality gate; do not save or promote packages with blocking lint errors.
- Workflow version promotion must run eval specs after the quality gate; failed evals must block publish or rollback without changing the current version.
- Workflow runs must bind to the workflow package version selected at start; never resume a paused run against a newer current version by accident.
- Workflow run start must support idempotency when a client supplies an idempotency key; identical retries must return the original run, and mismatched key reuse must fail with audit evidence.
- Workflow run start should support shadow mode for rollout validation; shadow runs must not execute approval-gated write tools and must include shadow mode in idempotency fingerprints and audit details.
- Shadow comparisons should require `workflow:evaluate`, persist eval results, and write low-sensitive audit evidence without copying raw expected output or raw run output into audit details.
- Live run requests should be able to enforce release readiness using quality, terminal shadow run, and passing shadow comparison evidence; failed gates must be audited with low-sensitive blocking reason names.
- Governance run reports should aggregate status queues, approval backlog, recovery items, and pending shadow validation using bounded low-sensitive samples; do not expose raw inputs, outputs, or trace snapshots.
- Governance quality reports should aggregate node success, eval pass rate, shadow comparisons, readiness, failed nodes, and optimizer signals using low-sensitive counts and reason codes; do not expose raw inputs or trace snapshots.
- Governance cost reports should aggregate estimated tokens, durations, retries, and human touches using low-sensitive counts and node ids; do not expose raw inputs or trace snapshots.
- Governance risk reports should aggregate tool, run, quality, and audit risks using low-sensitive counts, ids, severity labels, and reason codes; do not expose raw inputs or trace snapshots.
- EvalRunner should execute workflow input cases for golden, node, and regression evals, but EvalResult details must expose only low-sensitive path counts, mismatched path names, rule outcomes, run status, and trace counts.
- OTLP trace export must stay low-sensitive: export run/node status, timing, retry metadata, key counts, sensitive-key counts, and bounded reason codes, not raw payload values, actor tokens, idempotency keys, endpoint headers, or MCP credentials.
- Workflow run rerun must use the source run's original input and workflow version, require `workflow:run`, preserve rerun lineage, and write an audit event.
- Workflow run cancellation must require `workflow:cancel`, mutate only non-terminal runs, append a skipped trace, and write an audit event.
- Workflow run start and approval resume must enforce bounded `max_steps`; do not expose unbounded graph execution through API routes.
- Workflow run start, rerun, and approval resume should pass repository persistence as the runner checkpoint callback so partial progress is durable during execution.
- Retryable runtime failures must record attempt metadata in trace; do not retry schema, permission, approval, or contract failures.
- List endpoints must expose bounded `limit`/`offset` pagination and must not return unbounded workflow history, runs, traces, eval results, or audit records by default.
- Run list endpoints should support status filtering for operational queues such as paused approvals, failed executions, and cancellations.
- Run diagnostics should expose low-sensitive recovery summaries, not raw run inputs or trace snapshots.

## 4. Testing Standards

- Every phase must run available tests or static checks when feasible.
- Minimum backend command: `pytest`.
- Frontend command: `npm run lint` or `npm run build` when dependencies are installed.
- Tests should cover schema validation, workflow execution, approval pause behavior, trace capture, eval output, and optimizer suggestions.

## 5. Directory Guide

- `apps/api/routes`: HTTP route modules only.
- `apps/api/dependencies.py`: Repository and service dependency wiring.
- `packages/workflow_core/models`: Pydantic schemas.
- `packages/workflow_core/builder`: Mock Builder Agent classes and graph compiler interface.
- `packages/workflow_core/runtime`: DAG runner, node executor, tool registry, and approval policy.
- `packages/workflow_core/governance`: Trace store, eval runner, metrics, and optimization loop.
- `packages/workflow_core/storage`: Repository protocols and implementations.
- `packages/workflow_core/adapters`: LLM, LangGraph, and MCP adapter interfaces.

## 6. Security Standards

- First version must use mock adapters for all external tools.
- Do not directly implement real external write operations.
- Write operations must go through approval policy.
- Tool registries must enforce permission level, approval context, and tool input/output schema even for mock adapters.
- Approval actions must include auditable actor identity and role authorization.
- Approval, cancellation, and promotion actions must require `workflow:approve`, `workflow:cancel`, and `workflow:promote` scopes respectively; role alone is not enough.
- Sensitive control-plane actions such as workflow writes, run starts, run cancellation, manual evals, approval decisions, and successful or failed version promotion must write audit events with low-sensitive summaries.
- Workflow generation audit details may include request length, structured brief presence, process node count, and save-as-current status; do not copy raw brief text or node content into audit details.
- Nodes with `write_requires_approval` must pause before execution.
- Tool permissions must be explicit: `read_only`, `draft_only`, `write_requires_approval`, or `forbidden`.
- Run and trace API responses must redact sensitive payload keys; do not break persisted run state needed for approval resume.
- External tool tokens must never be exposed directly to Agents.
- Deployment readiness must check repository access and fail production-like auth configuration that still uses the default local secret.

## 7. Progress Output Standard

All tasks must output progress percentages. For this MVP build, use the requested phase format:

```text
[进度] 5% █░░░░░░░░░ 仓库扫描完成
[进度] 15% ██░░░░░░░░ 项目基础文件完成
[进度] 30% ████░░░░░░ 核心数据模型完成
[进度] 45% ██████░░░░ Builder Agent 骨架完成
[进度] 60% ████████░░ Runtime 骨架完成
[进度] 72% █████████░ Governance 骨架完成
[进度] 82% ██████████ API 骨架完成
[进度] 90% ██████████ 前端骨架完成
[进度] 96% ██████████ 示例与测试完成
[进度] 100% ██████████ MVP 骨架搭建完成
```

## 8. Disallowed Work

- Do not put all logic in one huge file.
- Do not bypass Pydantic validation for workflow package generation.
- Do not implement real writes to external systems in the MVP.
- Do not run write tools without approval policy.
- Do not add direct tool execution paths that bypass registry sandbox checks.
- Do not create overlapping Agent abstractions without a clear boundary.
- Do not make workflow packages that cannot be saved, loaded, and validated.
- Do not hide failing tests or checks in final reporting.
