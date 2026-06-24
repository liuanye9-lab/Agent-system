from __future__ import annotations

import json
from typing import Any
from urllib import error, request


class HttpJSONLLMClient:
    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str | None = None,
        provider: str = "http",
        timeout_seconds: float = 30.0,
        max_tokens: int | None = None,
        json_response_format: bool = True,
    ) -> None:
        if not endpoint:
            raise ValueError("LLM endpoint is required")
        if not model:
            raise ValueError("LLM model is required")
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.json_response_format = json_response_format

    def complete(self, prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Return only valid JSON. Do not include markdown fences."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if self.json_response_format:
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        http_request = request.Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raise RuntimeError(f"LLM request failed with HTTP {exc.code}") from exc
        except error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise RuntimeError(f"LLM request failed: {reason}") from exc
        payload = json.loads(response_body)
        return self._extract_text(payload)

    def _extract_text(self, payload: Any) -> str:
        if isinstance(payload, dict):
            output_text = payload.get("output_text")
            if isinstance(output_text, str):
                return output_text
            content = payload.get("content")
            if isinstance(content, str):
                return content
            text = payload.get("text")
            if isinstance(text, str):
                return text
            choices = payload.get("choices")
            if isinstance(choices, list) and choices:
                first_choice = choices[0]
                if isinstance(first_choice, dict):
                    message = first_choice.get("message")
                    if isinstance(message, dict) and isinstance(message.get("content"), str):
                        return message["content"]
                    if isinstance(first_choice.get("text"), str):
                        return first_choice["text"]
        return json.dumps(payload, ensure_ascii=False)
