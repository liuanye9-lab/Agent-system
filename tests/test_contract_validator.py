from __future__ import annotations

from packages.workflow_core.models import DataContract
from packages.workflow_core.runtime import ContractValidator


def test_contract_validator_normalizes_workflow_state_into_context() -> None:
    contract = DataContract(
        contract_id="contract-1",
        name="Input contract",
        description="Requires context.",
        input_schema={
            "type": "object",
            "properties": {"context": {"type": "object"}},
            "required": ["context"],
        },
        output_schema={"type": "object"},
        error_policy="fail",
    )

    result = ContractValidator().validate_input(contract, {"product": "AI workflow platform"})

    assert result.valid is True
    assert result.normalized_payload == {"context": {"product": "AI workflow platform"}}


def test_contract_validator_rejects_invalid_output() -> None:
    contract = DataContract(
        contract_id="contract-1",
        name="Output contract",
        description="Requires summary.",
        input_schema={"type": "object"},
        output_schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
        error_policy="fail",
    )

    result = ContractValidator().validate_output(contract, {"next_actions": []})

    assert result.valid is False
    assert "summary" in result.errors[0]
