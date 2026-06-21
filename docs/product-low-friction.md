# Low-Friction Product Principles

The main interface must be a conversation window because the product promise is that users can create useful Agent systems without learning internal concepts first.

## Why Chat First

Most users know the result they want before they know the topology. A chat-first builder lets the system infer the shape, ask focused questions, and produce a blueprint that can be reviewed visually.

## Where Complexity Goes

Advanced configuration belongs behind the blueprint:

- WorkflowPackage JSON
- DAG details
- Tool policies
- Eval specs
- Release readiness
- Trace exports
- Governance reports

These remain available for operators, but they should not be the default first screen for non-technical users.

## Making It Useful For Non-Technical Users

The builder asks at most three questions:

- Who will use it?
- What concrete output should it produce?
- What tools or data can it use, and what needs approval?

The UI then shows understandable cards: current understanding, missing information, recommended Agent shape, mother Agent, Subagents, workflow nodes, tool needs, memory needs, eval needs, and launch risk.

## Preventing Low Friction From Becoming Low Quality

Low friction does not mean bypassing controls. Candidate versions are schema-validated, saved without promotion, and still depend on approval gates, shadow runs, release readiness, evals, audit events, and low-sensitive traces before live use.
