# Roadmap

## Phase 1: MVP Skeleton

- Pydantic workflow package schemas.
- Mock Builder Agents.
- HTTP/OpenAI-compatible LLM adapter for typed Builder Plane generation across problem framing, process architecture, contracts, tools, and evals.
- Structured business brief input for non-demo workflow generation.
- Candidate workflow version save path for generated and imported package changes.
- Dashboard package JSON validation and candidate/current import path.
- Lightweight DAG runtime.
- Version-aware run launch UI with JSON input, budgets, idempotency key, shadow mode, and readiness enforcement.
- Candidate version diff review in the workflow detail UI.
- Approval pause for write nodes.
- Approval resume/reject API.
- Durable SQLite repository.
- Repository schema metadata surfaced through readiness checks.
- Workflow package import/export.
- Workflow package version history and diff API.
- Workflow version promote/rollback API.
- Workflow promotion release manifest with change summary, risk acceptance, diff review, readiness acknowledgement, and audit context.
- Runtime data-contract validation.
- Idempotent workflow run start with conflict detection and replay audit events.
- Shadow run mode for pre-production validation without executing approval-gated write tools.
- Shadow comparison evals against human expected output with audit evidence.
- Release readiness gate for live execution based on quality and shadow evidence.
- Governance run report for status queues, approval backlog, recovery items, and pending shadow validation.
- Governance quality report for node success, eval pass rate, shadow comparisons, release readiness, and optimizer signals.
- Governance cost report for estimated tokens, duration, retries, human touches, and shadow/live run mix.
- Governance risk report for tool, run, quality, and audit risk.
- Governance retention report and confirmed apply for expired terminal runs and eval results.
- Package repair plan for failed traces, failed evals, approval pauses, and baseline coverage gaps.
- Selective repair candidate preview and version generation with quality gate and audit evidence.
- Field-level repair impact previews with release-gate impact summaries before candidate-version generation.
- Candidate workflow version eval run before promotion.
- Terminal run rerun with original version/input lineage and audit events.
- Workflow-executed golden, node, and regression eval runner for promotion gates.
- Approval actor headers and role authorization.
- Run cancellation with dedicated scope, cancellation trace, and audit event.
- HMAC bearer token auth with configurable local users and dev header fallback.
- Scope authorization for workflow reads, writes, runs, evals, approvals, cancellation, and version promotion.
- Persistent audit events for workflow writes, run starts, manual evals, approval, and successful or failed version promotion.
- Tool sandbox enforcement for permission, approval context, and input/output schema.
- Workflow quality gate for import and version promotion.
- Workflow eval gate for version promotion.
- Bounded max-step execution budget for run start and approval resume.
- Runtime retry attempts with trace metadata and per-run retry budget.
- Durable run checkpoints at start, after node attempts, and at paused or terminal states.
- Response-level redaction for sensitive run and trace payload keys.
- Bounded pagination for workflow, version, run, trace, eval result, and audit event list APIs.
- Run status filtering for operational queues.
- Run diagnostics for failure, approval, retry, and step-budget recovery.
- Run detail operator forms for approval payloads, cancellation reasons, rerun controls, and shadow-comparison notes.
- MCP-like descriptor binding to `ToolPolicy` plus sandboxed registry invocation through an injected external invoker.
- MCP JSON-RPC session transport with initialize handshake, protocol/session headers, expired-session recovery, and `tools/call` structured-output mapping.
- MCP scoped authorization provider for permission-level and required-scope transport credentials.
- Trace, eval, metrics, and optimizer skeleton.
- FastAPI and Next.js dashboard.
- Liveness and readiness probes for repository access, production-sensitive auth configuration, and LLM configuration.

## Phase 2: Real LLM Adapter

- Use typed LLM output for problem framing, process architecture, contract design, tool mapping, and eval generation.
- Keep typed outputs and Pydantic validation mandatory.
- Add prompt/version metadata to Agent specs.

## Phase 3: LangGraph / PydanticAI

- Replace lightweight graph execution with LangGraph where needed.
- Use PydanticAI for typed deps, typed outputs, tool approval, and durable agents.
- Preserve the workflow package as the source artifact.

## Phase 4: MCP Tool Ecosystem

- Expand descriptor sync and review UI for read-only, draft, and write tools.
- Add external secret-manager backed credential rotation for MCP authorization providers.
- Add richer streaming transports when remote MCP servers require long-lived response streams.

## Phase 5: Trace / Eval Platform

- OTLP JSON trace export payload and governance API for low-sensitive run spans.
- Connect vendor-specific Phoenix, Langfuse, or OpenTelemetry collector dashboards.
- Add DeepEval and Ragas test runners.
- Add external regression dataset storage and curation workflows.

## Phase 6: Low-Code Visual Canvas

- Add React Flow process visualization.
- Support node editing and contract inspection.
- Keep JSON package validation as the source of truth.

## Phase 7: Enterprise Permission, Audit, Collaboration

- Add user, role, and team permissions.
- Add approval workflow and audit log.
- Add package version history, diff, rollback, and multi-user review.
