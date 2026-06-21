# Agnes AI Integration

Agnes AI is wired as an OpenAI-compatible text LLM provider for Builder Plane generation.

## Environment

```bash
export AGENT_WORKFLOW_LLM_PROVIDER=agnes
export AGENT_WORKFLOW_LLM_ENDPOINT=https://apihub.agnes-ai.com/v1
export AGENT_WORKFLOW_LLM_MODEL=agnes-2.0-flash
export AGENT_WORKFLOW_LLM_API_KEY=...
export AGENT_WORKFLOW_LLM_TIMEOUT_SECONDS=60
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

The current Builder Plane uses `agnes-2.0-flash` for JSON-generating workflow construction. Image and video models are reserved for future media adapters and should not be passed into the text Builder Plane.
