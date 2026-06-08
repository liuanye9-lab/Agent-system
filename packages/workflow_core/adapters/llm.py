from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(self, prompt: str) -> str:
        """Return a text completion for builder agents."""
