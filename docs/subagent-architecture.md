# Subagent Architecture

Subagents are used only when they reduce real complexity. They are not a default decoration layer.

## When To Use Subagents

Use Subagents when at least one boundary is clear:

- Specialist capability differs, such as research, analysis, review, or report generation.
- Context must be narrowed for privacy, focus, or token control.
- Tool permissions differ.
- Risk level differs and a review boundary is needed.
- A graph needs durable state, trace, eval, and recovery at separate nodes.

## When Not To Split

Do not split when a single Agent can answer directly, when the roles overlap completely, when the only reason is visual complexity, or when a Subagent would receive all context and all tools by default.

## Collaboration Modes

`manager_as_tool_caller`: the mother Agent calls Subagents and owns the final answer. This is the default.

`handoff_to_specialist`: reserved for a future conversation-control handoff.

`graph_orchestrated`: maps multiple Agent nodes into the existing workflow graph.

## Permission Isolation

Each Subagent declares `allowed_tools` and `permission_scope`. The mapper creates `ToolPolicy` records, and runtime execution still goes through the registry sandbox. Write-like tools must require approval.

## Context Isolation

Each Subagent declares a `context_policy`:

- `task_only`
- `filtered_context`
- `full_context`

The first implementation records the policy on mapped process nodes and uses mock runtime behavior. Real transports must enforce the same boundary before external execution.

## Trace And Eval

`subagent_call` is a first-class node type. Runtime records a normal node trace whose output includes `subagent_result` with status, summary, structured output, confidence, error list, token estimate, duration, and human-review flag.

Eval details stay low-sensitive. They should count path matches, statuses, trace counts, and rule outcomes rather than copying raw private payloads into governance artifacts.
