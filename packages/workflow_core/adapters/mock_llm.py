from __future__ import annotations


class MockLLMClient:
    provider = "mock"
    model = "mock-llm-v1"

    def complete(self, prompt: str) -> str:
        return f"[mock-llm] {prompt[:160]}"
