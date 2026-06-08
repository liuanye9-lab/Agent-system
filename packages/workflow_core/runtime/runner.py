from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from packages.workflow_core.models import DataContract, TraceRecord, WorkflowPackage, WorkflowRun
from packages.workflow_core.models.enums import NodeExecutionStatus, WorkflowRunStatus
from packages.workflow_core.runtime.contract_validator import ContractValidator
from packages.workflow_core.runtime.graph import ExecutableGraph
from packages.workflow_core.runtime.node_executor import NodeExecutionResult, NodeExecutor
from packages.workflow_core.runtime.tool_registry import MockToolRegistry, ToolExecutionContext


RunCheckpoint = Callable[[WorkflowRun], None]


class WorkflowRunner:
    def __init__(
        self,
        node_executor: NodeExecutor | None = None,
        contract_validator: ContractValidator | None = None,
    ) -> None:
        self.node_executor = node_executor
        self.contract_validator = contract_validator or ContractValidator()

    def run(
        self,
        workflow_package: WorkflowPackage,
        input_payload: dict[str, Any] | None = None,
        run_id: str | None = None,
        rerun_of_run_id: str | None = None,
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
        max_steps: int = 50,
        max_retries: int = 1,
        shadow_mode: bool = False,
        actor_context: ToolExecutionContext | None = None,
        checkpoint: RunCheckpoint | None = None,
    ) -> WorkflowRun:
        input_payload = input_payload or {}
        run = WorkflowRun(
            run_id=run_id or f"run-{uuid4().hex[:12]}",
            workflow_id=workflow_package.workflow_id,
            workflow_version=workflow_package.version,
            rerun_of_run_id=rerun_of_run_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            shadow_mode=shadow_mode,
            status=WorkflowRunStatus.RUNNING,
            current_node_id=workflow_package.process_spec.entry_node_id,
            input_payload=input_payload,
        )
        self._checkpoint(checkpoint, run)

        executor = self._executor_for(workflow_package)
        graph = ExecutableGraph.from_process_spec(workflow_package.process_spec)
        contracts = self._contracts_for(workflow_package)

        return self._continue(
            workflow_package=workflow_package,
            graph=graph,
            contracts=contracts,
            executor=executor,
            run=run,
            current_node_id=graph.entry_node_id,
            current_payload=input_payload,
            max_steps=max_steps,
            max_retries=max_retries,
            actor_context=actor_context,
            checkpoint=checkpoint,
        )

    def resume(
        self,
        workflow_package: WorkflowPackage,
        run: WorkflowRun,
        approved: bool,
        approval_payload: dict[str, Any] | None = None,
        max_steps: int = 50,
        max_retries: int = 1,
        actor_context: ToolExecutionContext | None = None,
        checkpoint: RunCheckpoint | None = None,
    ) -> WorkflowRun:
        if run.status != WorkflowRunStatus.PAUSED or run.current_node_id is None:
            raise ValueError("only paused runs can be resumed")

        approval_payload = approval_payload or {}
        if not approved:
            rejected_at = datetime.now(timezone.utc)
            run.status = WorkflowRunStatus.REJECTED
            run.output_payload = {
                **run.output_payload,
                "approval_required": False,
                "approval_decision": "rejected",
                "approval_payload": approval_payload,
            }
            run.traces.append(
                TraceRecord(
                    run_id=run.run_id,
                    workflow_id=run.workflow_id,
                    workflow_version=run.workflow_version,
                    node_id=run.current_node_id,
                    input_snapshot={"approval_payload": approval_payload},
                    output_snapshot=run.output_payload,
                    status=NodeExecutionStatus.SKIPPED,
                    error="human approval rejected",
                    started_at=rejected_at,
                    ended_at=rejected_at,
                    duration_ms=0,
                )
            )
            run.updated_at = rejected_at
            self._checkpoint(checkpoint, run)
            return run

        paused_trace = run.traces[-1] if run.traces else None
        current_payload = paused_trace.input_snapshot if paused_trace else run.input_payload
        current_payload = {
            **current_payload,
            "approval_granted": True,
            "approval_payload": approval_payload,
        }
        run.status = WorkflowRunStatus.RUNNING
        self._checkpoint(checkpoint, run)

        return self._continue(
            workflow_package=workflow_package,
            graph=ExecutableGraph.from_process_spec(workflow_package.process_spec),
            contracts=self._contracts_for(workflow_package),
            executor=self._executor_for(workflow_package),
            run=run,
            current_node_id=run.current_node_id,
            current_payload=current_payload,
            max_steps=max_steps,
            max_retries=max_retries,
            approval_granted_for_first_node=True,
            actor_context=actor_context,
            checkpoint=checkpoint,
        )

    def _continue(
        self,
        workflow_package: WorkflowPackage,
        graph: ExecutableGraph,
        contracts: dict[str, DataContract],
        executor: NodeExecutor,
        run: WorkflowRun,
        current_node_id: str | None,
        current_payload: dict[str, Any],
        max_steps: int,
        max_retries: int,
        approval_granted_for_first_node: bool = False,
        actor_context: ToolExecutionContext | None = None,
        checkpoint: RunCheckpoint | None = None,
    ) -> WorkflowRun:
        visited_steps = 0
        max_attempts = max(1, max_retries + 1)

        while current_node_id and visited_steps < max_steps:
            visited_steps += 1
            node = graph.nodes[current_node_id]
            attempt = 1
            while True:
                started_at = datetime.now(timezone.utc)
                input_contract = contracts[node.input_contract_id]
                input_validation = self.contract_validator.validate_input(input_contract, current_payload)
                if not input_validation.valid:
                    result = NodeExecutionResult(
                        node_id=node.node_id,
                        status=NodeExecutionStatus.FAILED,
                        error=f"input_contract_validation_failed: {'; '.join(input_validation.errors)}",
                        retryable=False,
                    )
                    effective_input_payload = current_payload
                else:
                    effective_input_payload = input_validation.normalized_payload
                    result = executor.execute(
                        node,
                        effective_input_payload,
                        workflow_package.tool_policies,
                        approval_granted=approval_granted_for_first_node,
                        shadow_mode=run.shadow_mode,
                        actor_context=actor_context,
                    )
                    if result.status == NodeExecutionStatus.SUCCESS:
                        output_contract = contracts[node.output_contract_id]
                        output_validation = self.contract_validator.validate_output(output_contract, result.output)
                        if not output_validation.valid:
                            result = NodeExecutionResult(
                                node_id=node.node_id,
                                status=NodeExecutionStatus.FAILED,
                                output=result.output,
                                error=f"output_contract_validation_failed: {'; '.join(output_validation.errors)}",
                                retryable=False,
                            )
                ended_at = datetime.now(timezone.utc)
                trace = TraceRecord(
                    run_id=run.run_id,
                    workflow_id=run.workflow_id,
                    workflow_version=run.workflow_version,
                    node_id=node.node_id,
                    input_snapshot=effective_input_payload,
                    output_snapshot=result.output,
                    status=result.status,
                    error=result.error,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    retryable=result.retryable,
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=max(0, int((ended_at - started_at).total_seconds() * 1000)),
                )
                run.traces.append(trace)
                run.current_node_id = node.node_id
                run.updated_at = ended_at

                if result.status == NodeExecutionStatus.FAILED and result.retryable and attempt < max_attempts:
                    self._checkpoint(checkpoint, run)
                    current_payload = self._consume_transient_failure(effective_input_payload, node.node_id)
                    attempt += 1
                    continue
                break

            approval_granted_for_first_node = False

            if result.status == NodeExecutionStatus.APPROVAL_REQUIRED:
                run.status = WorkflowRunStatus.PAUSED
                run.output_payload = result.output
                self._checkpoint(checkpoint, run)
                return run
            if result.status == NodeExecutionStatus.FAILED:
                run.status = WorkflowRunStatus.FAILED
                run.output_payload = {
                    "error": result.error,
                    "failed_node_id": node.node_id,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "retryable": result.retryable,
                }
                self._checkpoint(checkpoint, run)
                return run

            current_payload = {**effective_input_payload, node.node_id: result.output}
            run.output_payload = current_payload
            current_node_id = graph.next_node_id(node.node_id, result.output)
            run.current_node_id = current_node_id
            self._checkpoint(checkpoint, run)

        if current_node_id and visited_steps >= max_steps:
            run.status = WorkflowRunStatus.FAILED
            run.current_node_id = current_node_id
            run.output_payload = {
                "error": "max_steps_exceeded",
                "pending_node_id": current_node_id,
                "executed_steps": visited_steps,
                "max_steps": max_steps,
            }
        else:
            run.status = WorkflowRunStatus.COMPLETED
            run.current_node_id = None

        self._checkpoint(checkpoint, run)
        return run

    def _executor_for(self, workflow_package: WorkflowPackage) -> NodeExecutor:
        if self.node_executor:
            return self.node_executor
        tool_registry = MockToolRegistry()
        tool_registry.register_many(workflow_package.tool_policies)
        return NodeExecutor(tool_registry=tool_registry)

    def _contracts_for(self, workflow_package: WorkflowPackage) -> dict[str, DataContract]:
        return {contract.contract_id: contract for contract in workflow_package.data_contracts}

    def _checkpoint(self, checkpoint: RunCheckpoint | None, run: WorkflowRun) -> None:
        if checkpoint is None:
            return
        checkpoint(run.model_copy(deep=True))

    def _consume_transient_failure(self, payload: dict[str, Any], node_id: str) -> dict[str, Any]:
        updated = deepcopy(payload)
        transient_failures = updated.get("transient_failures")
        if isinstance(transient_failures, dict) and int(transient_failures.get(node_id, 0) or 0) > 0:
            transient_failures[node_id] = int(transient_failures[node_id]) - 1
            return updated

        context = updated.get("context")
        if isinstance(context, dict):
            nested = context.get("transient_failures")
            if isinstance(nested, dict) and int(nested.get(node_id, 0) or 0) > 0:
                nested[node_id] = int(nested[node_id]) - 1
        return updated
