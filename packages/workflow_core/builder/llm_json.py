from __future__ import annotations

import json
from typing import Any

from packages.workflow_core.adapters.llm import LLMClient


def is_mock_llm(llm: LLMClient) -> bool:
    return getattr(llm, "provider", "mock") == "mock"


def extract_json_object(value: str) -> dict[str, Any]:
    payload = _load_json(value, "{", "}")
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object")
    return payload


def extract_json_array(value: str) -> list[Any]:
    payload = _load_json(value, "[", "]")
    if not isinstance(payload, list):
        raise ValueError("LLM response must be a JSON array")
    return payload


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: str, start_token: str, end_token: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        start = value.find(start_token)
        end = value.rfind(end_token)
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(value[start : end + 1])
