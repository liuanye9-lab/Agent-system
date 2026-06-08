from __future__ import annotations

from typing import Any

from packages.workflow_core.models import WorkflowPackage


def diff_workflow_packages(
    from_package: WorkflowPackage,
    to_package: WorkflowPackage,
) -> list[dict[str, Any]]:
    from_payload = from_package.model_dump(mode="json", by_alias=True)
    to_payload = to_package.model_dump(mode="json", by_alias=True)
    return _diff_values(from_payload, to_payload, path="$")


def _diff_values(from_value: Any, to_value: Any, path: str) -> list[dict[str, Any]]:
    if isinstance(from_value, dict) and isinstance(to_value, dict):
        changes: list[dict[str, Any]] = []
        keys = sorted(set(from_value) | set(to_value))
        for key in keys:
            child_path = f"{path}.{key}"
            if key not in from_value:
                changes.append({"op": "added", "path": child_path, "to": to_value[key]})
            elif key not in to_value:
                changes.append({"op": "removed", "path": child_path, "from": from_value[key]})
            else:
                changes.extend(_diff_values(from_value[key], to_value[key], child_path))
        return changes

    if isinstance(from_value, list) and isinstance(to_value, list):
        changes = []
        max_length = max(len(from_value), len(to_value))
        for index in range(max_length):
            child_path = f"{path}[{index}]"
            if index >= len(from_value):
                changes.append({"op": "added", "path": child_path, "to": to_value[index]})
            elif index >= len(to_value):
                changes.append({"op": "removed", "path": child_path, "from": from_value[index]})
            else:
                changes.extend(_diff_values(from_value[index], to_value[index], child_path))
        return changes

    if from_value != to_value:
        return [{"op": "changed", "path": path, "from": from_value, "to": to_value}]
    return []
