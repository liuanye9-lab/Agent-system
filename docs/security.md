# Security

## Tool Permission Levels

- `read_only`: may retrieve or summarize data, never changes external state.
- `draft_only`: may prepare drafts, plans, or recommendations.
- `write_requires_approval`: may write only after explicit human approval.
- `forbidden`: registered for visibility but cannot be executed.

## Approval Strategy

Write operations must go through `ApprovalPolicy`. Any tool with `write_requires_approval` pauses the run with `approval_required` and records a trace. A paused run can be resumed only through the approval API. Rejections are recorded as `rejected` runs with a skipped approval trace. Real external writes are not implemented.

Approval API calls should use `Authorization: Bearer <token>`. Tokens are HMAC-signed by `AGENT_WORKFLOW_AUTH_SECRET_KEY`, carry actor id, role, scopes, issue time, and expiry, and can be issued by `POST /api/auth/token`. Approval requires the `workflow:approve` scope before node-level role authorization is checked.

Local development can still allow `X-Actor-Id` and `X-Actor-Role` headers when `AGENT_WORKFLOW_ALLOW_DEV_ACTOR_HEADERS=true`. Production-like environments should set it to `false`. The actor role must match the paused node tool policy `allowed_roles`, unless the role is `workflow-admin`. Actor identity is copied into the approval payload for auditability.

Approval decisions write `run_approval` audit events with actor, run, workflow version, approval result, and reason.

## Read / Write Isolation

Read and draft tools can run in mock mode. Write tools return a draft payload and wait for approval. After approval, the runtime can execute mock tools or configured MCP-bound tools through the same registry boundary. Production integrations must split credentials and scopes by permission level.

The tool registry is a policy enforcement boundary. It blocks `forbidden` tools, blocks `write_requires_approval` tools without approval context, enforces allowed roles and required scopes when present, validates tool input schema before execution, and validates tool output schema before returning results to the node.

MCP-like tool descriptors must be compiled into `ToolPolicy` records before runtime use. An external MCP invoker or `MCPServerSessionPool` may be injected only behind `MCPToolRegistry`; it must not be called directly from nodes or routes because direct calls would bypass permission, approval, scope, schema, and sandbox metadata checks. MCP session configs and authorization providers may carry endpoint headers, but those headers are transport credentials and must not be copied into trace, audit, or error details.

API routes are scoped. Workflow, run, trace, eval result, metric, audit, and optimization read APIs require `workflow:read`. Package generation and import require `workflow:write`, workflow execution requires `workflow:run`, manual eval execution requires `workflow:evaluate`, approval requires `workflow:approve`, run cancellation requires `workflow:cancel`, and version promotion requires `workflow:promote`.

Structured workflow generation still requires `workflow:write`. Generation audit details may record request length, whether a structured brief was supplied, process node count, and whether the package was saved as current, but must not copy raw business brief text or raw process-node content into audit details.

LLM configuration must come from environment variables. `/ready` may report provider, model, endpoint presence, and whether an API key is configured, but it must never return the API key value. Builder failures may write error type, provider, model, request length, structured brief presence, node count, and save-as-current status, but must not copy raw prompts, raw model outputs, raw business context, or secrets into audit details.

Candidate workflow versions may be generated or imported without changing the current version. Candidate creation still requires `workflow:write`, requires an existing current workflow, and must be followed by the promotion gate before it affects default live execution.

Package import UI should parse pasted JSON locally before calling the API, use `POST /api/workflows/validate` for preflight checks, and call `POST /api/workflows/import` only with an actor that has `workflow:write`. Validation responses are not proof of persistence; only successful import responses should link into version review.

Candidate review UI may display package diffs to actors with `workflow:read`, but it must not use run input payloads, trace snapshots, actor tokens, or idempotency keys as diff material.

Run diagnostics require `workflow:read` and expose only low-sensitive operational summaries. They must not include raw run inputs, trace input snapshots, trace output snapshots, actor tokens, or idempotency keys.

List read APIs are also bounded. Workflow, workflow version, run, trace, eval result, and audit event lists default to `limit=50`, cap `limit` at `200`, and support `offset` for paging. Run status lists use the same `workflow:read` boundary as other run reads. Do not add unbounded list responses for traces, audit records, eval results, or workflow history.

Run reports require `workflow:read` and must remain aggregate-only. Queue samples must stay capped and may expose run ids, workflow ids, statuses, node ids, timestamps, trace counts, and reason codes, but must not expose raw input payloads, output payloads, trace snapshots, actor tokens, or idempotency keys.

Trace export requires `workflow:read` and must remain low-sensitive. OTLP span attributes may include workflow ids, run ids, versions, statuses, timing, retry metadata, key counts, sensitive-key counts, and bounded reason codes, but must not include raw run inputs, raw outputs, trace snapshots, actor tokens, idempotency keys, OTLP endpoint headers, or MCP credentials. Trace export attempts write `workflow_trace_export` audit events with span counts and endpoint/payload presence flags only.

Eval execution may run `EvalSpec.input_case` through the workflow runtime for either the current package or a saved candidate version, but `EvalResult.details` and promotion audit details must remain low-sensitive. They may include workflow version, run status, trace count, compared path counts, mismatched path names, and rule outcome codes; they must not copy raw eval input cases, raw expected outputs, raw run outputs, actor tokens, idempotency keys, endpoint headers, or MCP credentials.

Risk reports require `workflow:read` and must remain aggregate-only. They may expose workflow ids, run ids, tool ids, counts, severity labels, and low-sensitive reason codes, but must not expose raw input payloads, trace snapshots, expected outputs, actor tokens, or idempotency keys.

Quality reports require `workflow:read` and must remain aggregate-only. They may expose workflow ids, node ids, rates, counts, readiness reason codes, failure reason codes, and suggestion type counts, but must not expose raw input payloads, trace snapshots, expected outputs, actor tokens, or idempotency keys.

Package repair plans require `workflow:read` and must remain review-only. They may expose workflow ids, versions, target contract/tool/eval ids, operation codes, bounded rationales, proposed low-sensitive field changes, redacted evidence, diff summaries, and quality-gate counts. They must not expose raw payload snapshots, expected-output values, actor tokens, idempotency keys, endpoint headers, or MCP credentials. Previewing a repair candidate must not persist a workflow version. Applying a repair plan requires `workflow:write`, must accept only operation ids from the current repair plan, must save only a candidate version, must reject target-version overwrites, must run the package quality gate before saving, and must not mutate the current workflow package without the normal promotion gates.

Cost reports require `workflow:read` and must remain aggregate-only. They may expose workflow ids, node ids, estimated token counts, durations, retry counts, and human-touch counts, but must not expose raw input payloads, trace snapshots, expected outputs, actor tokens, or idempotency keys.

The same state-changing routes write audit events: `workflow_package_generation`, `workflow_package_import`, `workflow_run_start`, `workflow_run_idempotency_replay`, `workflow_run_cancel`, `workflow_eval_run`, `run_approval`, and `workflow_version_promotion`. Audit details should use summaries such as validation counts, input key names, statuses, and eval counts rather than raw payloads.

Retry policy must not hide control-plane violations. Permission failures, schema failures, approval failures, and contract failures are non-retryable. Only explicitly retryable transient execution failures should consume retry budget.

Run start and approval resume enforce a bounded `max_steps` execution budget from `1` to `200`. Do not expose unbounded execution or retry controls through API routes.

Run start supports optional idempotency keys. Idempotency keys are accepted only as bounded ASCII tokens, are not written into audit details, and are checked against a request fingerprint. Reusing a key with a different input payload, resolved workflow version, step budget, or retry budget is rejected and audited as a failed `workflow_run_idempotency_replay`.

Run launch UI must not hard-code workflow inputs. It should require operator-controlled JSON payloads and pass bounded step/retry budgets, selected workflow version, shadow mode, readiness enforcement, and optional idempotency key to the API.

Run detail UI must not hard-code approval, cancellation, rerun, or shadow-comparison payloads. Operator-supplied values still go through the scoped approval, cancellation, rerun, and eval APIs; UI controls must keep bounded max-step and retry budgets and must not expose raw trace snapshots as shortcut inputs.

Shadow mode must be used before enabling live writes for a new or materially changed workflow. Shadow runs must not execute approval-gated write tools; they may only produce draft outputs for human comparison. `shadow_mode` is included in idempotency fingerprints and audit details.

Shadow comparison requires `workflow:evaluate`. Comparison results may store path-level match summaries as eval details, but audit details must not include raw expected output or raw run output.

Live execution gates should use release readiness before enabling a new or materially changed workflow. Readiness requires version-tagged eval evidence and shadow evidence for the target workflow version. When `enforce_release_readiness` is set, failed gates must write low-sensitive audit evidence with blocking reason names, not raw input payloads.

Rerun is restricted to actors with `workflow:run` and terminal source runs. It reuses the source run's original input payload and workflow version to avoid accidental behavior drift. Rerun audit details record source and new run ids but not raw input payloads.

Run cancellation is restricted to actors with `workflow:cancel`. Cancellation is allowed only before a run reaches a terminal status, records a skipped trace, and writes a `workflow_run_cancel` audit event. Production deployments should keep `workflow:cancel` limited to operators or workflow administrators.

## Trace Redaction

Trace and run payloads are treated as sensitive. Run and trace API responses recursively redact sensitive keys such as tokens, passwords, secrets, API keys, and authorization headers. The repository still stores raw run state so paused workflows can resume correctly; production deployments should add storage-level encryption, retention controls, and redaction before exporting to external trace platforms.

Repository snapshots are operational backups, not redacted reports. They may include raw run payloads, trace snapshots, eval details, and audit event details, so they should stay out of Git and follow the same retention, encryption, and access controls as the production database.

Retention reports are dry-run governance artifacts. They may expose workflow ids, run ids, eval ids, audit event ids, status counts, event-type counts, cutoffs, and recommendations, but must not expose raw run payloads, trace snapshots, eval expected outputs, actor tokens, endpoint headers, or external credentials. Retention apply requires `workflow:write`, `workflow-admin` role for actual deletion, defaults to dry run, requires explicit confirmation, snapshot acknowledgement, and an operator reason, and may delete only expired terminal runs and expired eval results. Active runs and audit events are report-only so operators must snapshot, investigate, or follow a separate compliance-approved archive/purge process.

## Version Audit

Workflow package versions are preserved for audit and diff. Production deployments should add authenticated author identity, change rationale, approval state, and rollback controls around package version writes.

Paused runs resume against their original `workflow_version`. This prevents approval policy, tool bindings, or node definitions from changing underneath an in-flight workflow after a package update.

Run start, rerun, and approval resume checkpoint run state to the repository during execution. Checkpoints preserve raw run state for recovery and approval continuation, so normal run and trace response redaction still applies at HTTP boundaries. Do not write actor tokens, endpoint headers, or external tool credentials into checkpoint-only metadata.

Only an actor with `workflow-admin` role and `workflow:promote` scope may promote a saved workflow version as current. Promotion is used for both publish and rollback, and callers must include an actor token or enabled dev actor headers so the promotion response carries audit context. Promotion requests must include a reason and may include a low-sensitive release manifest: change-summary presence, risk-acceptance presence, diff-review flag, readiness acknowledgement, change count, live-readiness status, and blocking reason names.

Promotion attempts write `workflow_version_promotion` audit events for both succeeded and failed gates. Audit details must not copy raw package JSON or full raw diffs; they should store bounded release-context evidence and gate summaries. Audit event persistence is not a substitute for enterprise identity; production deployments should replace local users with SSO/OIDC or a verified session provider.

Promotion also runs the workflow quality gate and eval gate before changing the current version. A saved version with graph, tool policy, schema, missing eval coverage, or failed eval results cannot be promoted. Eval results are persisted even when promotion is blocked so the failed gate is auditable.

## Token Handling

External tool tokens must not be exposed directly to Agents. Remote MCP or API integrations must use scoped authorization, least privilege, and separate credentials for read and write operations.

LLM provider keys are external service credentials and follow the same no-passthrough rule. They may be attached only by the LLM adapter at request time and must not be serialized into workflow packages, traces, run payloads, audit events, readiness responses, or generated Agent instructions.

Application actor tokens must carry the scopes required by the action. Role alone is not sufficient for workflow writes, run execution, manual eval execution, approval, cancellation, or promotion.

Configured auth users should use `password_hash`, generated with `python tools/password.py`, rather than plaintext `password`. Plaintext configured passwords are tolerated only for local development compatibility; production and release preflight treat them as failed auth hardening.

Example workflow seeding is a local-development convenience. Production repositories should set `AGENT_WORKFLOW_SEED_EXAMPLE_WORKFLOW=false` and require workflow packages to enter through import, validation, diff review, promotion, and audit paths.

## Deployment Checks

`/health` proves only that the API process is alive. `/ready` should be used by deployment tooling before routing traffic because it checks repository access, reports low-sensitive repository schema metadata, rejects production-like auth configuration that disables dev actor headers while keeping the default `AGENT_WORKFLOW_AUTH_SECRET_KEY`, and reports whether real LLM mode has the required endpoint/model configuration.

## MVP Restrictions

- No real external writes.
- No hard-coded API keys.
- No token passthrough to Agents.
- No bypass of Pydantic validation.
- No write execution without approval trace.
- No approval identity trust without a future authenticated user context.
- No direct tool execution path may bypass permission and schema enforcement.
- No run-start endpoint may create duplicate executions for a repeated identical idempotency key.
- No terminal run may be mutated through approval, cancellation, or retry endpoints.
