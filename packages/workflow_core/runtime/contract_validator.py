from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from packages.workflow_core.models import DataContract


@dataclass(frozen=True)
class ContractValidationResult:
    valid: bool
    normalized_payload: dict[str, Any]
    errors: list[str]


class ContractValidator:
    def validate_input(self, contract: DataContract, payload: dict[str, Any]) -> ContractValidationResult:
        normalized_payload = self._normalize_input_payload(contract, payload)
        return self._validate(contract.input_schema, normalized_payload)

    def validate_output(self, contract: DataContract, payload: dict[str, Any]) -> ContractValidationResult:
        return self._validate(contract.output_schema, payload)

    def _validate(self, schema: dict[str, Any], payload: dict[str, Any]) -> ContractValidationResult:
        try:
            Draft202012Validator(schema).validate(payload)
        except ValidationError as exc:
            path = ".".join(str(part) for part in exc.path)
            location = f" at {path}" if path else ""
            return ContractValidationResult(
                valid=False,
                normalized_payload=payload,
                errors=[f"{exc.message}{location}"],
            )
        return ContractValidationResult(valid=True, normalized_payload=payload, errors=[])

    def _normalize_input_payload(self, contract: DataContract, payload: dict[str, Any]) -> dict[str, Any]:
        required_fields = set(contract.input_schema.get("required", []))
        properties = set(contract.input_schema.get("properties", {}).keys())
        if "context" in required_fields and "context" not in payload:
            normalized = {"context": payload}
            for optional_field in properties - {"context"}:
                if optional_field == "artifacts":
                    normalized[optional_field] = payload.get(optional_field, [])
                elif optional_field == "assumptions":
                    normalized[optional_field] = payload.get(optional_field, [])
            return normalized
        return payload
