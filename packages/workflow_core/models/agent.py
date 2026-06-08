from __future__ import annotations

from typing import Any

from pydantic import Field

from packages.workflow_core.models.common import StrictBaseModel


class AgentSpec(StrictBaseModel):
    agent_id: str
    name: str
    role: str
    goal: str
    instructions: str
    input_contract_id: str
    output_contract_id: str
    tools: list[str] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    # Pydantic reserves the attribute name `model_config`, so the Python field
    # is `model_settings` while serialized workflow packages keep `model_config`.
    model_settings: dict[str, Any] = Field(default_factory=dict, alias="model_config")
