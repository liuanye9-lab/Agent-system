# Agent System Builder

Agent System Builder is the low-friction product layer above the existing WorkflowPackage runtime. It lets a user describe an Agent need in a chat box, then produces a validated `AgentSystemBlueprint` and a candidate WorkflowPackage without requiring technical setup.

## Product Positioning

The audience is broader than business process operators: personal users, students, creators, designers, operators, product managers, developers, researchers, small teams, and enterprise teams can all start from natural language.

The default path is:

1. User describes the Agent system they want.
2. `ClarificationEngine` asks up to three general task questions.
3. `AgentTopologyClassifier` recommends `single_agent`, `workflow_agent`, `manager_subagents`, or `multi_agent_workflow`.
4. `SubAgentPlanner` creates the mother Agent, Subagents, workflow nodes, routing policy, and collaboration mode.
5. The UI shows a simple blueprint.
6. User saves a candidate.
7. Existing promotion gates handle release readiness.

## AgentSystemBlueprint

`AgentSystemBlueprint` is the product-facing artifact. It includes target users, skill level, primary goal, expected outputs, interaction mode, topology, mother Agent, Subagents, workflow nodes, tool needs, memory needs, evaluation needs, approval needs, observability needs, risk level, and release policy.

The blueprint is not a replacement for `WorkflowPackage`. It is mapped into `WorkflowPackage` so runtime, trace, eval, governance, shadow mode, approval gate, version history, and promotion behavior stay intact.

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

## Shadow Run And Release Readiness

Agent System candidates use the existing runtime. Shadow mode remains available at run launch, write-like operations still require approval, and promotion still runs quality and eval gates before changing the current version.
