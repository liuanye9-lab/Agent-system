# Conversational Agent Builder

Conversational Agent Builder is the low-friction product layer above the existing WorkflowPackage runtime. It lets a user describe an Agent need in a chat box, then keeps asking focused questions until it can produce a production-grade Agent blueprint, draft Skill packages, and a candidate WorkflowPackage without requiring the user to fill out technical forms.

## Product Positioning

The audience is broader than business process operators: personal users, students, creators, designers, operators, product managers, developers, researchers, small teams, and enterprise teams can all start from natural language.

The default path is:

1. User describes the Agent system they want.
2. The Builder stores an `AgentBuildSession` with conversation messages, confirmed facts, missing information, current blueprint, readiness report, Skill package drafts, and change log.
3. A real LLM adapter, or the deterministic rules fallback in mock mode, returns one structured delta for the current turn.
4. Pydantic validates the delta before it is merged into the persisted session.
5. The UI shows the next assistant question plus inline cards for maturity, blueprint, Skill packages, and change log.
6. Candidate save is enabled only when readiness is at least 70 and no blocking gaps remain.
7. Candidate save persists a workflow package version with `save_workflow_version`, returns the Skill package drafts, writes an audit event, and does not change the current workflow version.
8. Existing diff, eval, shadow-run, promotion, and rollback gates handle release readiness.

This is not a generic chatbot surface. The chat is the input method; the product artifact remains a versioned workflow candidate with low-sensitive governance evidence.

## Session State

`AgentBuildSession` is the durable state machine for each build conversation.

It stores:

- user request and message history
- `AgentRequirementState`: summary, confirmed facts, missing information, assumptions, and constraints
- `AgentTopologyRecommendation`
- current `AgentSystemBlueprint`
- `AgentProductionReadinessReport`
- `AgentSkillPackage` drafts
- version change log
- candidate workflow id/version after save
- generation mode and LLM provider/model metadata

The SQLite repository persists sessions in `agent_build_sessions`. The memory repository keeps the same protocol for tests and local smoke runs.

## AgentSystemBlueprint

`AgentSystemBlueprint` is the product-facing artifact. It includes target users, skill level, primary goal, expected outputs, interaction mode, topology, mother Agent, Subagents, workflow nodes, tool needs, memory needs, evaluation needs, approval needs, observability needs, risk level, and release policy.

The blueprint is not a replacement for `WorkflowPackage`. It is mapped into `WorkflowPackage` so runtime, trace, eval, governance, shadow mode, approval gate, version history, and promotion behavior stay intact.

## Production Readiness

The Builder scores eight dimensions:

- `goal_clarity`
- `io_contract`
- `tool_permissions`
- `memory_strategy`
- `failure_handling`
- `evaluation_cases`
- `approval_boundaries`
- `release_readiness`

`ready_for_candidate` is accepted only when `overall_score >= 70` and `blocking_gaps` is empty. If an LLM returns a contradictory report, such as a low score with `ready_for_candidate=true`, the model validator clamps it back to false.

Candidate readiness is a draft asset gate, not production launch. Promotion still requires normal package quality, eval, and release-readiness checks.

## Skill Package Drafts

Every build can produce `AgentSkillPackage` records for the mother Agent and useful Subagents.

Each draft includes:

- `skill_id`
- trigger scenarios
- system prompt
- input/output JSON Schema
- least-privilege tool permissions
- memory scope
- failure policy
- evaluation cases
- usage notes

The first version stores Skill packages as candidate assets inside the build session and returns them from:

- `GET /api/agent-systems/sessions/{session_id}/skills`
- `POST /api/agent-systems/sessions/{session_id}/candidate`

They are not auto-installed into a real Skill directory. Installation remains a separate reviewed action.

## LLM Contract

Real LLM behavior is adapter-injected through `LLMClient`. Agnes AI and other OpenAI-compatible providers use `HttpJSONLLMClient`.

The Agent Builder prompt requires one JSON object with:

- assistant message
- clarifying questions
- requirement state
- topology recommendation
- current blueprint
- readiness report
- Skill package drafts
- change summary

The API validates this with `AgentBuildLLMOutput`. Invalid output is retried up to three times with a low-sensitive validation summary. If all attempts fail, the API returns a low-sensitive `502` without copying the raw prompt or model output.

The mapper also normalizes common LLM JSON Schema shorthand, such as `"field": "string"`, into valid JSON Schema objects before linting candidate packages.

## Topology Classifier

Rules:

- Simple Q&A, one output, low risk: `single_agent`.
- Multi-step but one responsibility: `workflow_agent`.
- Multiple specialist roles with one final synthesis: `manager_subagents`.
- Long-running, multi-branch, stateful, approval-heavy collaboration: `multi_agent_workflow`.

The classifier defaults toward fewer agents. It recommends Subagents only when specialty, context boundary, tool permission, or risk boundary is clear.

## SubAgentPlanner

The first implementation supports `manager_as_tool_caller` by default. `handoff_to_specialist` is reserved in the model, and `graph_orchestrated` maps to the existing workflow graph.

Planner output:

- `mother_agent`
- `subagents`
- `workflow_nodes`
- `routing_policy`
- `collaboration_mode`

## Mapping To WorkflowPackage

Mapping rules:

- `single_agent`: one reasoning node.
- `workflow_agent`: ordered reasoning/tool/final nodes.
- `manager_subagents`: `subagent_call` nodes for each Subagent.
- `multi_agent_workflow`: graph nodes with assigned Agent ids.

Mapped candidates keep JSON Schema data contracts, mock tool policies, Agent specs, eval specs, and process edges. Candidate save uses `save_workflow_version`, not `save_workflow`, so it does not change the current version.

Tool policies are compiled from blueprint-level tool requirements, mother-agent tools, and subagent tools. Tool ids are deduplicated after normalized id generation so different LLM spellings do not create duplicate package ids.

Write-like tools are approval-gated. The first implementation treats names containing terms such as `write`, `writer`, `send`, `publish`, `database`, `reminder`, or `calendar` as write-like.

## Shadow Run And Release Readiness

Agent System candidates use the existing runtime. Shadow mode remains available at run launch, write-like operations still require approval, and promotion still runs quality and eval gates before changing the current version.

## API Surface

The chat-first API surface is:

- `POST /api/agent-systems/sessions`
- `GET /api/agent-systems/sessions/{session_id}`
- `POST /api/agent-systems/sessions/{session_id}/messages`
- `POST /api/agent-systems/sessions/{session_id}/blueprint`
- `GET /api/agent-systems/sessions/{session_id}/skills`
- `POST /api/agent-systems/sessions/{session_id}/candidate`

Reads require `workflow:read`. Session creation, message updates, blueprint reads through the write path, and candidate saves require `workflow:write`.

## Frontend UX

`/agent-systems` is intentionally a single chat workspace. Users do not fill a long form. The page shows:

- the conversation
- the latest readiness card
- the latest blueprint card
- the latest Skill package card
- the latest change-log card
- a candidate-save button gated by readiness

Local test mode can use development actor headers when no stored `workflow-admin` token exists. Production should disable dev actor headers and use bearer-token auth.

## Smoke Verification

Real Agnes API smoke:

```bash
PYTHONPATH=/tmp/agentflow-pydeps:$PWD \
  python tools/agent_builder_agnes_smoke.py --require-candidate --max-turns 4
```

Expected successful low-sensitive shape:

```text
turn1_status 200
turn1_readiness 55 False
turn2_status 200
turn2_readiness 85 True
candidate_status 200
candidate_saved_as_current False
candidate_skill_count 3
```

Core repository smoke without FastAPI import:

```bash
python tools/agent_builder_smoke.py
```

Browser E2E evidence from the local verification run:

- API: `127.0.0.1:8002`
- Web: `127.0.0.1:8014`
- Flow: create chat session, answer clarifying question, reach 100% readiness, save candidate
- Persisted candidate: `customer-followup-agent-v2@0.1.0-chat-202606240243`
- `saved_as_current=False`
- Skill packages: 3
- Screenshot: `/tmp/agent-builder-e2e-success.png`

The screenshot is a local evidence artifact, not a repository asset.
