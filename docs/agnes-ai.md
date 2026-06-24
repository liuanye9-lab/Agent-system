# Agnes AI Integration

Agnes AI is wired as an OpenAI-compatible text LLM provider for Builder Plane generation.

## Environment

```bash
export AGENT_WORKFLOW_LLM_PROVIDER=agnes
export AGENT_WORKFLOW_LLM_ENDPOINT=https://apihub.agnes-ai.com/v1
export AGENT_WORKFLOW_LLM_MODEL=agnes-2.0-flash
export AGENT_WORKFLOW_LLM_API_KEY=...
export AGENT_WORKFLOW_LLM_TIMEOUT_SECONDS=90
export AGENT_WORKFLOW_LLM_MAX_TOKENS=4096
export AGENT_WORKFLOW_LLM_JSON_RESPONSE_FORMAT=false
```

Do not commit the API key. Use a local shell export, `.env` file excluded by Git, or deployment secret manager.

## Endpoint Handling

When `AGENT_WORKFLOW_LLM_PROVIDER=agnes`, the API accepts either:

- `https://apihub.agnes-ai.com/v1`
- `https://apihub.agnes-ai.com/v1/chat/completions`

The backend normalizes the base URL to `/chat/completions` before calling the LLM adapter.

## Models

Validated model ids from `/v1/models` include:

- `agnes-2.0-flash`
- `agnes-image-2.0-flash`
- `agnes-video-v2.0`

The current Builder Plane uses `agnes-2.0-flash` for JSON-generating workflow and Agent Builder construction. Image and video models are reserved for future media adapters and should not be passed into the text Builder Plane.

## Agent Builder Smoke

Run the real conversational Agent Builder smoke against the local FastAPI app through `TestClient`:

```bash
PYTHONPATH=/tmp/agentflow-pydeps:$PWD \
  python tools/agent_builder_agnes_smoke.py --require-candidate --max-turns 4
```

The script uses low-sensitive output only. It checks:

- bearer-token login
- session creation
- follow-up message handling
- readiness change across turns
- Skill package draft count
- candidate workflow save
- `saved_as_current=false`

Agnes can be sensitive to OpenAI-compatible `response_format`; keep `AGENT_WORKFLOW_LLM_JSON_RESPONSE_FORMAT=false` unless the provider explicitly supports it.
