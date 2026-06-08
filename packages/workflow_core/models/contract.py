from __future__ import annotations

from typing import Any

from pydantic import Field

from packages.workflow_core.models.common import StrictBaseModel


class DataContract(StrictBaseModel):
    contract_id: str
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_fields: list[str] = Field(default_factory=list)
    validation_rules: list[str] = Field(default_factory=list)
    error_policy: str
    example_input: dict[str, Any] = Field(default_factory=dict)
    example_output: dict[str, Any] = Field(default_factory=dict)
