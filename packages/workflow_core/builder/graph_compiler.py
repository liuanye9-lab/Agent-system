from __future__ import annotations

from packages.workflow_core.models import WorkflowPackage
from packages.workflow_core.runtime.graph import ExecutableGraph


class GraphCompilerAgent:
    def compile(self, workflow_package: WorkflowPackage) -> ExecutableGraph:
        return ExecutableGraph.from_process_spec(workflow_package.process_spec)
