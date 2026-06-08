# Workflow Package Spec

A `WorkflowPackage` is the versioned artifact produced by Builder Plane and consumed by Runtime Plane. Packages can be validated with `POST /api/workflows/validate`, persisted with `POST /api/workflows/import`, exported with `GET /api/workflows/{workflow_id}/export`, and executed with `POST /api/workflows/{workflow_id}/runs`. The dashboard new-workflow page also supports pasted package JSON validation and import, including candidate saves with `save_as_current=false`.

Read endpoints require `workflow:read`. State-changing endpoints require actor scopes: generation and import require `workflow:write`, workflow execution requires `workflow:run`, run cancellation requires `workflow:cancel`, manual eval execution requires `workflow:evaluate`, and promotion requires `workflow:promote`.

`POST /api/workflows/generate` accepts either a plain `user_request` or a structured business brief with `workflow_id`, `version`, `name`, `business_goal`, `start_event`, `end_state`, role and metric lists, and an optional ordered `process_nodes` list. When process nodes are supplied, Builder Plane compiles them into a linear `ProcessSpec`, data contracts, tool policies, agent specs, and eval specs. Write nodes automatically require approval. Generation defaults to `save_as_current: true`; when `save_as_current: false`, it stores a candidate package version without changing the current version. Candidate versions require an existing current workflow for that `workflow_id`.

List-style read endpoints are bounded. Workflow lists, workflow version lists, run lists, run traces, eval results, and audit events accept `limit` and `offset`; the API defaults `limit` to `50` and rejects values above `200`. Run lists also accept `status` filters for `created`, `running`, `paused`, `completed`, `failed`, `rejected`, and `canceled`.

`POST /api/workflows/{workflow_id}/runs` accepts an optional `idempotency_key` for safe client retries. The API stores a request fingerprint that includes workflow id, resolved workflow version, input payload, retry budget, and `shadow_mode`. Replaying the same key with the same request returns the original run; replaying the same key with a different request returns `409`.

Run launch surfaces should pass the operator-selected `workflow_version`, JSON `input_payload`, `max_steps`, `max_retries`, `shadow_mode`, `enforce_release_readiness`, and optional `idempotency_key` through to this API. This is required for candidate-version shadow validation before promotion.

Run start also accepts `shadow_mode`. Shadow runs store `shadow_mode: true`, execute read/reasoning/review nodes normally, and turn approval-gated write nodes into draft-producing nodes without calling the write tool. This supports shadow rollout and human-result comparison before enabling live writes.

`POST /api/runs/{run_id}/shadow-comparisons` compares a terminal shadow run's output against caller-supplied human expected output. It stores an `EvalResult` with `eval_type: shadow_comparison`, exact-match path counts, score, pass/fail status, and mismatch path names. It also writes a `workflow_run_shadow_comparison` audit event. Raw expected output and raw run output are not copied into audit details.

`GET /api/workflows/{workflow_id}/release-readiness` checks whether a workflow version is ready for live execution. It requires package quality to pass, version-tagged eval results to pass, at least one terminal shadow run for the version, and at least one passing shadow comparison tied to that shadow run. `POST /api/workflows/{workflow_id}/runs` may set `enforce_release_readiness: true`; when set for a live run, the API rejects unready versions with `409` and writes a failed `workflow_run_start` audit event.

`GET /api/governance/run-report` returns the Governance Plane run account. It aggregates run status counts, active and terminal counts, live and shadow counts, pending approval queues, recovery queues, shadow validation queues, and run-level reason codes. Queue samples are capped and do not include raw workflow inputs, outputs, or trace snapshots.

`GET /api/governance/risk-report` returns the Governance Plane risk account. It aggregates tool risk levels, write-tool approval gaps, run status risk, live runs on unready versions, failed evals, shadow comparison failures, release gate blocks, and idempotency conflicts. The report returns counts, ids, and reason codes, not raw workflow inputs or trace snapshots.

`GET /api/governance/quality-report` returns the Governance Plane quality account. It aggregates node success rate, eval pass rate, average eval score, shadow comparison pass counts, release readiness, failed node counts, and optimizer suggestion counts. The report returns counts, rates, node ids, and reason codes, not raw workflow inputs or trace snapshots.

`GET /api/governance/cost-report` returns the Governance Plane cost account. It aggregates estimated input and output tokens, trace duration, retry traces, human approval touches, live run counts, and shadow run counts. The report returns estimates, counts, node ids, and reason codes, not raw workflow inputs or trace snapshots.

`POST /api/governance/retention-apply` applies conservative retention cleanup. It defaults to dry run; actual deletion requires `dry_run: false`, `confirm_apply: true`, `snapshot_acknowledged: true`, an operator `reason`, and `workflow-admin` role. The endpoint deletes only expired terminal runs and expired eval results, writes a low-sensitive `workflow_retention_apply` audit event, and leaves active runs and audit events as report-only categories.

`POST /api/workflows/{workflow_id}/repair-plan/preview` returns a non-mutating candidate repair preview. Its `impact_preview` summarizes changed field paths, package sections, risk counts, release-gate impacts, operation impacts, and validation status without copying raw before/after values into the summary.

`POST /api/runs/{run_id}/cancel` cancels a `created`, `running`, or `paused` run, appends a skipped cancellation trace, and writes a `workflow_run_cancel` audit event. Terminal runs cannot be canceled again.

`POST /api/runs/{run_id}/rerun` creates a new run from a terminal source run's original `input_payload` and original `workflow_version`. Rerun is blocked for non-terminal source runs. The new run stores `rerun_of_run_id`, and the API writes a `workflow_run_rerun` audit event. Optional rerun idempotency keys are checked against a fingerprint that includes the source run id and resolved `shadow_mode`. If omitted, rerun preserves the source run's shadow mode.

`GET /api/runs/{run_id}/diagnostics` returns an operational summary for a run. It includes status, terminal state, trace counts, approval state, failure node, failure error, retry exhaustion, step-budget exhaustion, and recommended recovery actions. It intentionally does not return raw trace snapshots.

Run start and approval resume accept `max_steps` from `1` to `200`, defaulting to `50`. The runner stops with `max_steps_exceeded` when a workflow still has another node to execute after consuming its step budget. Completing on the final allowed step is valid.

Runtime callers may provide a `RunCheckpoint` callback. API run start, rerun, and approval resume pass repository persistence as that callback, producing checkpoints at run start, after node attempts, and at paused or terminal states.

Validation is two-stage:

1. Pydantic parses and validates schema references.
2. `WorkflowPackageLinter` runs quality gates for graph reachability, duplicate IDs, tool policy consistency, JSON Schema validity, agent coverage, and eval coverage.

Promotion adds a runtime governance gate after validation: `EvalRunner` executes every `EvalSpec.input_case` through the workflow runtime, compares expected output paths and scoring rules, persists the resulting `EvalResult` records, and blocks the promotion if any eval fails.

Saved versions can be inspected with:

- `GET /api/workflows/{workflow_id}/versions`
- `GET /api/workflows/{workflow_id}/versions/{version}`
- `GET /api/workflows/{workflow_id}/diff?from_version=...&to_version=...`
- `POST /api/workflows/{workflow_id}/versions/{version}/promote`

## Top-Level Fields

- `workflow_id`: stable workflow identifier.
- `name`: human-readable workflow name.
- `version`: package version.
- `problem_spec`: problem definition and business goal.
- `process_spec`: DAG nodes, edges, entry node, terminal nodes, and version.
- `data_contracts`: input/output schema and validation policy per node.
- `tool_policies`: tool permissions, risk level, approval policy, and schema.
- `agent_specs`: node Agent role, goal, instructions, tools, guardrails, and model config.
- `eval_specs`: golden cases, node evals, end-to-end evals, and scoring rules.
- `created_at`: package creation timestamp.
- `updated_at`: package update timestamp.

## Important Subschemas

`ProblemSpec` captures target users, business goal, start event, end state, success metrics, constraints, risks, and human roles.

`ProcessSpec` contains `ProcessNode` and `ProcessEdge` records. Each node must reference existing input and output contracts. Edges must reference existing nodes.

`DataContract` contains `input_schema`, `output_schema`, `required_fields`, `validation_rules`, `error_policy`, and examples.

At runtime, `input_schema` is validated before a node executes and `output_schema` is validated before the node output is merged into workflow state. If a node contract requires `context`, raw workflow state is normalized into `{ "context": <state> }` before validation.

`ToolPolicy` uses one of four permission levels:

- `read_only`
- `draft_only`
- `write_requires_approval`
- `forbidden`

`ToolPolicy.adapter` defaults to `mock`. MCP-bound tools use `adapter: "mcp"` plus `server_id`, `external_tool_name`, optional `required_scopes`, and the same permission/risk/approval fields. `MCPToolAdapter` wraps descriptor schemas in the runtime envelope: `payload` is validated against the external tool input schema, and `result` is validated against the external tool output schema. `MCPServerSessionPool` can then route approved MCP calls to per-server JSON-RPC sessions that initialize the server, reuse session ids, recover expired sessions once, and map `tools/call` `structuredContent` back into the registry result envelope. `ScopedMCPAuthorizationProvider` can add transport headers selected by permission level and required scopes, but only after registry validation has accepted the tool call.

At runtime, tool calls are validated against `ToolPolicy.input_schema` and returned tool results are validated against `ToolPolicy.output_schema`. `write_requires_approval` tools require approval context even if called directly through the registry.

`AgentSpec` stores the serialized field `model_config`. Internally the Python model uses `model_settings` because Pydantic reserves `model_config`. Generated packages record the Builder Plane LLM provider and model in this field, whether the run used the default mock client or an environment-configured HTTP/OpenAI-compatible client.

`EvalSpec` supports `node`, `end_to_end`, and `regression` eval types.

## Versioning Rules

- `workflow_id` is the stable identity across versions.
- `version` identifies a saved package snapshot.
- Saving a package updates the current package for `workflow_id` and also upserts the matching historical version.
- Saving a candidate package version stores it in version history without changing the current package. Candidate versions are intended for diff review and promotion gates.
- Candidate review surfaces should compare the selected candidate with the current version using `GET /api/workflows/{workflow_id}/diff` before promotion.
- Import review surfaces should let operators validate package JSON before import, choose whether the import is a candidate or current save, and deep-link to the imported candidate version for diff review.
- Promoting a saved version sets it as the current package for future default runs. The caller must have `workflow-admin` role and `workflow:promote` scope, and the request carries a release manifest with reason plus optional change summary, risk acceptance, diff review confirmation, and readiness acknowledgement.
- Import requires `workflow:write`, and import/promotion are blocked when the quality gate returns errors.
- Promotion is also blocked when any eval spec fails; failed eval results are saved for inspection and the current version is left unchanged.
- Historical versions are not used for running by default; the current package is used unless a future API explicitly starts a run from a selected version.
- A workflow run stores `workflow_version` at creation time. If a run is started with `workflow_version`, approval resume and audit use that exact package snapshot.
- Diff output uses JSON paths such as `$.process_spec.nodes[0].name`.

## Audit Events

Audit events are stored separately from workflow packages and runs. They record sensitive control-plane actions such as `workflow_package_generation`, `workflow_package_import`, `workflow_run_start`, `workflow_run_idempotency_replay`, `workflow_run_rerun`, `workflow_run_cancel`, `workflow_eval_run`, `run_approval`, and `workflow_version_promotion`.

Audit `details` should store operational summaries such as validation counts, eval counts, input key names, run status, and failed promotion gate summaries. It should not store raw workflow run inputs or large payload snapshots.

Key fields:

- `event_id`
- `event_type`
- `action`
- `status`
- `actor_id`
- `actor_role`
- `workflow_id`
- `workflow_version`
- `run_id`
- `resource_type`
- `resource_id`
- `reason`
- `details`

## Run Retry Metadata

Each `TraceRecord` contains retry metadata:

- `attempt`
- `max_attempts`
- `retryable`

`POST /api/workflows/{workflow_id}/runs` and `POST /api/runs/{run_id}/approval` accept `max_retries` from `0` to `5`. A run with `max_retries: 1` can attempt a node twice. Retryable transient node failures record a failed trace before retrying the same node. Non-retryable failures stop the run immediately.

Each `WorkflowRun` may also contain `rerun_of_run_id`, `idempotency_key`, and `request_fingerprint` when it was started through an idempotent or rerun API request.

Run and trace API responses redact sensitive key names recursively. Redaction is applied to HTTP responses, not to the persisted run state used for workflow resume.

## Example JSON

The full example lives at `examples/new_product_launch.workflow.json`.

```json
{
  "workflow_id": "new-product-launch",
  "name": "新品上市流程智能体",
  "version": "0.1.0",
  "problem_spec": {
    "id": "new-product-launch-problem",
    "title": "新品上市流程智能体",
    "business_goal": "减少新品上市跨角色交接的信息损耗，沉淀可复用流程资产。"
  },
  "process_spec": {
    "entry_node_id": "define-launch-goal",
    "terminal_node_ids": ["go-no-go-decision"],
    "nodes": [
      {
        "node_id": "go-no-go-decision",
        "node_type": "write_node",
        "requires_approval": true,
        "tool_ids": ["mock-go-no-go-decision-tool"]
      }
    ]
  },
  "tool_policies": [
    {
      "tool_id": "mock-go-no-go-decision-tool",
      "permission_level": "write_requires_approval",
      "risk_level": "high",
      "requires_approval": true
    }
  ]
}
```
