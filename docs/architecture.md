# Architecture

Agent Workflow Builder is split into three planes. The first version keeps orchestration lightweight while preserving production seams for typed LLM generation, sandboxed tools, tracing, and eval systems.

## Builder Plane

Builder Plane turns a user request or structured business brief into a versioned `WorkflowPackage`.

Components:

- `ProblemFramerAgent`: asks clarifying questions and emits `ProblemSpec`, using either deterministic framing or an environment-configured HTTP/OpenAI-compatible LLM response parsed as typed JSON.
- `ProcessArchitectAgent`: converts the problem into a DAG-style `ProcessSpec`, with optional typed LLM process design and deterministic fallback.
- `ContractDesignerAgent`: creates `DataContract` records for each node, accepting typed LLM JSON Schema suggestions when valid.
- `ToolMapperAgent`: assigns sandboxed mock or MCP tool policies with explicit permissions, risk levels, roles, scopes, and approval requirements.
- `GraphCompilerAgent`: compiles a package into an executable graph interface.
- `EvalGeneratorAgent`: creates golden, node, and regression eval specs, with optional typed LLM eval generation and deterministic fallback.
- `OptimizerAgent`: consumes trace and eval signals and emits suggestions.

`WorkflowBuilder` accepts an optional structured brief. Brief fields override the generated `ProblemSpec`, and an ordered process-node list is compiled into a linear DAG with generated contracts, mock tool policies, Agent specs, and eval specs. This keeps the default example path available while allowing non-demo business workflows to be packaged without hard-coding every process as a new template. Generated and imported packages can also be saved as candidate versions so teams can validate package JSON, review diffs, and pass promotion gates before changing the current runtime version.

LLM behavior is injected through the `LLMClient` adapter boundary. The default client is mock/offline. Real Builder Plane generation can be enabled with HTTP/OpenAI-compatible environment variables; responses are accepted only as typed JSON for problem framing, process architecture, contracts, tool policies, and eval specs. Invalid or unsafe LLM output falls back to deterministic defaults, and generated `AgentSpec.model_config` records provider/model metadata for later diff and audit review.

## Runtime Plane

Runtime Plane runs the workflow package.

Components:

- `WorkflowRunner`: compiles `ProcessSpec`, executes nodes, records traces, and returns `WorkflowRun`.
- `NodeExecutor`: executes read, reasoning, review, and write nodes through the tool registry boundary.
- `ContractValidator`: validates node input and output payloads against `DataContract` JSON Schema before data crosses node boundaries.
- `ToolRegistry`: registers tools from `ToolPolicy` and enforces permission level, approval context, role/scope authorization, and tool input/output schema before mock or MCP invocation.
- `ApprovalPolicy`: pauses write nodes when a tool is `write_requires_approval`.
- `WorkflowRunner.resume`: resumes a paused run after approval or records rejection.
- `ExecutableGraph`: lightweight DAG representation reserved for future LangGraph replacement.

Execution budget and retry behavior are owned by `WorkflowRunner`. Run start and approval resume receive a bounded `max_steps` budget, and the runner returns `max_steps_exceeded` only when there is still another node to execute after the budget is consumed. Each node execution records `attempt`, `max_attempts`, and `retryable` on its trace. Retryable failures can be retried up to the run's `max_retries` budget. Contract, schema, permission, and approval errors are treated as non-retryable control-plane failures.

Run execution supports durable checkpointing through an injected `RunCheckpoint` callback. API run start, rerun, and approval resume pass `repository.save_run`, so the repository sees the run at start, after node attempts, and at paused or terminal states. The runner sends deep-copied snapshots to the callback to avoid later in-memory mutation corrupting persisted checkpoints.

Run creation supports idempotency at the API and storage boundary. A caller can provide an `idempotency_key`; the API fingerprints the resolved workflow version, input payload, step budget, and retry budget before execution. A matching replay returns the original persisted `WorkflowRun`, while a mismatched replay is rejected with `409` and audited.

The dashboard run-launch surface is version-aware. Operators can run the current version or a saved candidate version, supply the JSON input payload, set step and retry budgets, choose shadow or live mode, and decide whether live readiness should be enforced. This keeps candidate validation usable without changing the current workflow version.

Shadow mode is the pre-production rollout path. A shadow run is a normal persisted `WorkflowRun` with `shadow_mode: true`: read, reasoning, and review nodes execute normally, while approval-gated write nodes generate draft outputs and skip write-tool execution. Shadow mode is part of the idempotency fingerprint and audit details, so operators can compare draft outcomes with human handling before enabling live writes.

Shadow comparison closes the rollout loop. Operators submit human expected output for a terminal shadow run, the API compares expected leaf paths against the run output, stores the result as an `EvalResult`, and writes a low-sensitive audit event with score and path counts. This makes shadow validation queryable through the existing eval and audit planes without storing raw comparison payloads in audit details.

Release readiness turns eval and shadow evidence into an execution gate. The readiness API checks package quality, version-tagged eval results for the target version, terminal shadow runs for the target version, and passing shadow comparisons. Live run requests can opt into `enforce_release_readiness`; when the gate is not satisfied, the API rejects the live run and writes a failed `workflow_run_start` audit event with blocking reason names.

Rerun is the explicit recovery primitive for terminal workflow runs. It resolves the source run's saved workflow version, reuses the original input payload, creates a new run with `rerun_of_run_id`, and writes `workflow_run_rerun` audit events for rerun attempts, idempotent replays, and conflicts.

Run cancellation is a control-plane operation on persisted runs. Actors with `workflow:cancel` can cancel created, running, or paused runs. Cancellation appends a skipped trace record, marks the run `canceled`, preserves the current node for diagnosis, and prevents later approval or duplicate cancellation.

Run diagnostics provide the runtime recovery summary for operators. They derive low-sensitive fields from the persisted run and trace list: failure node, error, retry exhaustion, step-budget exhaustion, approval state, trace counts, and recommended recovery actions. Raw input and output snapshots stay in the trace detail endpoint and continue to use response redaction.

The run detail screen is the operator recovery surface. It lets operators submit approval payload JSON and bounded resume budgets, cancel active runs with a reason, rerun terminal runs with bounded budgets, optional idempotency key, and explicit shadow/live override, and add notes to shadow comparisons. These controls call the same scoped APIs used by automation so the dashboard does not create a bypass path.

## Governance Plane

Governance Plane observes, evaluates, audits, and improves workflows.

Components:

- `TraceStore`: stores node-level trace records.
- `EvalRunner`: executes workflow golden, node, and regression eval specs and returns `EvalResult`.
- `MetricCollector`: summarizes success rate, approval count, failures, and duration.
- `OptimizationLoop`: turns traces and eval results into `OptimizationSuggestion` items.
- `AuditEvent`: captures actor, action, resource, workflow version, run id, reason, and details for sensitive control-plane actions.
- `RunReport`: aggregates run status, approval backlog, recovery queues, and shadow-validation work into a bounded operator report.
- `QualityReport`: aggregates node success, eval pass rate, shadow comparisons, release readiness, failed nodes, and optimizer signals.
- `CostReport`: estimates token footprint, duration, human touches, retry cost, and shadow/live run mix from persisted traces.
- `RiskReport`: aggregates tool, run, quality, and audit risk into a low-sensitive operator report.

The API uses the durable repository as the authoritative source for traces and metrics. Run and trace API responses pass through response-level redaction before leaving the service, while persisted run state remains intact for approval resume. `TraceStore` remains a reusable in-process component for tests or future streaming integrations.

`OTLPTraceExporter` converts a persisted `WorkflowRun` and its node traces into OTLP JSON `resourceSpans`. The exporter creates a root workflow span plus node child spans, uses deterministic trace/span ids, records only low-sensitive attributes such as workflow id, version, status, duration, retry counts, payload key counts, and sensitive-key counts, and never exports raw input or output payload values. `/api/governance/trace-export` can return the payload for inspection or send it to an OTLP-compatible endpoint, then writes a low-sensitive audit event.

`EvalRunner` uses the runtime runner to execute each `EvalSpec.input_case`, then scores the run against `expected_output` and scoring rules. End-to-end evals can check run status and terminal output, node evals can target a specific node trace, and regression evals use the same execution path. Operators can run evals against either the current workflow or a saved candidate version before promotion. Eval result details include workflow version, run status, trace count, path counts, mismatched path names, and rule outcomes, but not raw input cases or raw output values.

Risk reporting uses workflow packages, persisted runs, eval results, release-readiness state, and audit events to maintain the risk account. It surfaces high-risk tools, approval gaps, live runs on unready versions, failed shadow comparisons, release gate blocks, and idempotency conflicts as counts and reason codes so operators can triage without opening raw payloads.

Run reporting uses persisted workflow runs to maintain the run account. It summarizes state distribution, active versus terminal runs, live versus shadow mix, pending approvals, failed/rejected/canceled recovery queues, and completed shadow runs still waiting for human comparison. Queue samples are bounded and carry ids, statuses, nodes, timestamps, and reason codes only.

Quality reporting uses persisted traces, eval results, release-readiness state, and optimization suggestions to maintain the quality account. It computes node success rate, eval pass rate, shadow comparison coverage, readiness coverage, failed node counts, and improvement-suggestion counts without returning raw workflow payloads.

Package repair planning converts optimizer signals into reviewable operations against the workflow package. Failed traces produce data-contract repair operations, failed evals produce regression-eval operations, approval pauses produce tool-policy review operations, and quiet baselines produce coverage-expansion operations. Preview responses include field-level impact summaries with changed package sections, risk counts, release-gate impacts, and validation status without copying raw diff values into the summary. Plans are low-sensitive: evidence is bounded and redacted, and the endpoint does not mutate workflow packages or bypass candidate-version promotion gates.

Repair candidate preview applies selected operations to an in-memory copy, returns package diff impact and quality-gate results, and does not write a candidate version. Repair candidate generation uses the same preview path, rejects unknown operation ids and target-version overwrites, runs the existing package quality gate, saves only a candidate version, and writes a low-sensitive audit event with the bounded impact summary. Promotion remains a separate operator decision with eval gates and release context.

Cost reporting uses persisted runs and trace snapshots to maintain the cost account. It estimates input and output token footprint from serialized trace payload sizes, aggregates duration, retry traces, human approval touches, and live/shadow run mix, then returns only counts, estimates, node ids, and reason codes.

List responses in this plane are bounded at the API boundary. Runs, traces, eval results, and audit events support `limit` and `offset` so operational screens cannot accidentally return unbounded trace or governance history. Run lists also support status filters so operators can work focused queues such as paused approvals, failed executions, and canceled runs. Aggregate metrics intentionally read the matching trace set to preserve metric correctness.

Retention apply closes the governance cleanup loop without making destructive cleanup broad. Operators can dry-run retention through CLI or API, then workflow admins can explicitly confirm cleanup of expired terminal runs and expired eval results after acknowledging a reviewed snapshot and recording a reason. Active runs and audit events are never deleted by retention apply; they stay in the report for investigation, snapshot, archive, or compliance workflows.

The API exposes two operational probes: `/health` for process liveness and `/ready` for traffic readiness. Readiness performs a lightweight repository query, returns low-sensitive repository schema metadata, and checks whether production-like auth has changed the default local secret.

## Storage Plane

The default repository is SQLite. It stores the latest workflow package in `workflow_packages` and saved package versions in `workflow_package_versions`. Runs, eval results, and audit events are stored as validated JSON documents. A `repository_metadata` table records the storage schema version and initialization/update timestamps so readiness checks and release procedures can detect unexpected database state. This keeps the repository portable while preserving enough history for package rollback, audit, candidate-version review, and diff workflows.

Repository list methods accept optional `limit` and `offset`. Run list methods additionally accept status filters. SQLite-backed lists push pagination into SQL where the rows are stored directly; trace listing slices after expanding trace records from persisted run JSON.

Runs store `workflow_version` at creation time. Resume and approval lookup that exact saved package version, so long-running or paused workflows do not silently switch behavior after a workflow package update.

Saved package versions can be promoted to current. Promote is the lightweight publish/rollback primitive: it changes what new default runs use without mutating historical package snapshots or in-flight runs.

Candidate package versions can be saved without changing current runtime behavior. This is the safer path for generated or imported workflow changes: operators inspect the candidate, compare diffs, run promotion quality/eval gates, and then promote the version.

The workflow detail screen supports this release path by showing a version diff review whenever the selected run version differs from the current version. Operators can inspect package changes, run shadow validation for the candidate, and only then promote through the existing gate.

Promotion runs the package quality gate and then `EvalRunner` before changing the current version. Eval results are persisted for governance review; any failed eval blocks the promotion.

Promotion also captures an operator release manifest. The API records reason, change-summary presence, risk-acceptance presence, reviewed-diff flag, readiness acknowledgement, change count, live-readiness state, and blocking reason names in low-sensitive audit details so publish and rollback decisions can be reviewed without storing raw package payloads or full diffs.

Audit events are written for workflow package generation/import, workflow run start, run idempotency replays, run reruns, run cancellation, manual eval run, approval decisions, and workflow version promotion attempts, including failed quality, eval, or idempotency gates. They can be queried through `/api/governance/audit-events`.

Tool execution defaults to mock mode, but the same sandbox boundary can bind MCP-like tool descriptors through `MCPToolAdapter` and execute them through an injected external invoker. `MCPServerSessionPool` is the production-oriented invoker for JSON-RPC MCP servers: it owns one managed `MCPJSONRPCSession` per server id, performs initialize/initialized handshake, sends the negotiated protocol version and session id headers, retries once after expired-session `404`, calls `tools/call`, and maps `structuredContent` into the sandboxed result. `ScopedMCPAuthorizationProvider` can add transport credentials by permission level and required scope only after the registry has passed approval, role, scope, and schema checks. Both mock and MCP paths block forbidden tools, block write tools without approval context, validate role/scope permissions, validate payloads against `ToolPolicy.input_schema`, validate responses against `ToolPolicy.output_schema`, and return sandbox metadata in tool results.

API run, rerun, and approval resume paths pass actor ids and scopes into the runtime tool context. Platform actor role is not treated as every process-node owner role during normal workflow execution; approval role checks remain explicit at the approval boundary.

## Auth Boundary

Sensitive API actions resolve an `ActorContext` from a signed bearer token. Local development can fall back to explicit actor headers, but that fallback is controlled by `AGENT_WORKFLOW_ALLOW_DEV_ACTOR_HEADERS`. `require_scope` gates sensitive actions before route-specific role and policy checks: sensitive reads require `workflow:read`, package generation/import requires `workflow:write`, workflow execution requires `workflow:run`, manual eval execution requires `workflow:evaluate`, run approval requires `workflow:approve`, run cancellation requires `workflow:cancel`, and version promotion requires `workflow:promote`. The auth boundary is intentionally small so it can later be replaced by SSO/OIDC without changing workflow, approval, cancel, promote, or audit event logic.

When `AGENT_WORKFLOW_ALLOW_DEV_ACTOR_HEADERS=false`, `/ready` fails if `AGENT_WORKFLOW_AUTH_SECRET_KEY` is still the local default. This catches a common deployment misconfiguration before traffic is routed to the API.

## Quality Gate

`WorkflowPackageLinter` runs after Pydantic parsing and before package import or version promotion. It blocks duplicate IDs, unreachable nodes, non-terminal dead ends, nodes that cannot reach a terminal, forbidden tools bound to nodes, read nodes with write tools, write nodes without approval-ready tools, invalid JSON Schema, and missing eval specs. Warnings are returned for softer quality issues such as terminal nodes with outgoing edges or missing end-to-end eval coverage.

Version promotion has a second gate after linting: all `EvalSpec` records must pass through `EvalRunner`. Failed promotion quality or eval gates are audited, saved where applicable, and returned in the API response without changing the current workflow version.

## Data Flow

```text
User request
  -> ProblemFramerAgent
  -> ProcessArchitectAgent
  -> ContractDesignerAgent
  -> ToolMapperAgent
  -> EvalGeneratorAgent
  -> WorkflowPackage
  -> WorkflowRunner
  -> ContractValidator
  -> SQLite repository + EvalRunner
  -> OptimizationLoop
```

## Extension Points

- Add richer LLM adapter variants or provider-specific structured-output support behind `packages/workflow_core/adapters/llm.py`.
- Replace `ExecutableGraph` with `LangGraphAdapter` or Temporal-style orchestration when long-running distributed execution requires external scheduling.
- Expand MCP credential rotation and long-lived streaming transport support behind the existing `MCPToolAdapter`, `MCPServerSessionPool`, and `MCPToolRegistry` boundaries.
- Replace SQLite JSON-document repository with normalized PostgreSQL storage when package/query scale requires it.
- Connect vendor-specific Phoenix, Langfuse, or OpenTelemetry dashboards and collector deployment.
- Add DeepEval, Ragas, or Inspect adapters for non-mock scoring.
- Add React Flow in the frontend where `Process Nodes` are now listed.
