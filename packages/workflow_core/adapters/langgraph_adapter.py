from __future__ import annotations

from typing import Any

from packages.workflow_core.models import WorkflowPackage


class LangGraphAdapter:
    """Future extension point for compiling workflow packages to LangGraph."""

    def compile(self, workflow_package: WorkflowPackage) -> Any:
        raise NotImplementedError("LangGraph integration is reserved for a later phase.")
