from __future__ import annotations

from pydantic import Field

from packages.workflow_core.models.common import StrictBaseModel


class WorkflowValidationIssue(StrictBaseModel):
    severity: str
    code: str
    path: str
    message: str


class WorkflowValidationReport(StrictBaseModel):
    valid: bool
    errors: list[WorkflowValidationIssue] = Field(default_factory=list)
    warnings: list[WorkflowValidationIssue] = Field(default_factory=list)

    @property
    def issue_count(self) -> int:
        return len(self.errors) + len(self.warnings)
