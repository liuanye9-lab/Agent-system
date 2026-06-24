from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, delete, func, insert, inspect, select, text
from sqlalchemy.engine import make_url

from packages.workflow_core.models import AgentBuildSession, AuditEvent, EvalResult, TraceRecord, WorkflowPackage, WorkflowRun
from packages.workflow_core.models.common import utc_now
from packages.workflow_core.models.enums import WorkflowRunStatus

T = TypeVar("T")
SQLITE_REPOSITORY_SCHEMA_VERSION = "agent-workflow-builder.sqlite.v3"


class SQLiteWorkflowRepository:
    """Durable repository that stores validated Pydantic models as JSON in SQLite.

    Workflow packages and runs remain the source of truth as versioned JSON
    documents, while indexed columns support listing and lookup. Repository
    metadata stores the current storage schema version so readiness checks and
    release procedures can detect unexpected database state before traffic moves.
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._ensure_sqlite_parent(database_url)
        self.engine = create_engine(database_url, future=True)
        self.metadata = MetaData()
        self.workflows = Table(
            "workflow_packages",
            self.metadata,
            Column("workflow_id", String, primary_key=True),
            Column("name", String, nullable=False),
            Column("version", String, nullable=False),
            Column("created_at", String, nullable=False),
            Column("updated_at", String, nullable=False),
            Column("payload_json", Text, nullable=False),
        )
        self.workflow_versions = Table(
            "workflow_package_versions",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("workflow_id", String, nullable=False, index=True),
            Column("version", String, nullable=False, index=True),
            Column("name", String, nullable=False),
            Column("created_at", String, nullable=False),
            Column("updated_at", String, nullable=False),
            Column("payload_json", Text, nullable=False),
        )
        self.runs = Table(
            "workflow_runs",
            self.metadata,
            Column("run_id", String, primary_key=True),
            Column("workflow_id", String, nullable=False, index=True),
            Column("idempotency_key", String, nullable=True, index=True),
            Column("request_fingerprint", String, nullable=True),
            Column("status", String, nullable=False),
            Column("created_at", String, nullable=False),
            Column("updated_at", String, nullable=False),
            Column("payload_json", Text, nullable=False),
        )
        self.agent_build_sessions = Table(
            "agent_build_sessions",
            self.metadata,
            Column("session_id", String, primary_key=True),
            Column("created_at", String, nullable=False),
            Column("updated_at", String, nullable=False, index=True),
            Column("payload_json", Text, nullable=False),
        )
        self.eval_results = Table(
            "eval_results",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("workflow_id", String, nullable=False, index=True),
            Column("eval_id", String, nullable=False, index=True),
            Column("payload_json", Text, nullable=False),
        )
        self.audit_events = Table(
            "audit_events",
            self.metadata,
            Column("event_id", String, primary_key=True),
            Column("event_type", String, nullable=False, index=True),
            Column("action", String, nullable=False),
            Column("status", String, nullable=False),
            Column("actor_id", String, nullable=False, index=True),
            Column("actor_role", String, nullable=False, index=True),
            Column("workflow_id", String, nullable=True, index=True),
            Column("workflow_version", String, nullable=True),
            Column("run_id", String, nullable=True, index=True),
            Column("resource_type", String, nullable=False),
            Column("resource_id", String, nullable=False, index=True),
            Column("created_at", String, nullable=False, index=True),
            Column("payload_json", Text, nullable=False),
        )
        self.repository_metadata = Table(
            "repository_metadata",
            self.metadata,
            Column("key", String, primary_key=True),
            Column("value", String, nullable=False),
            Column("updated_at", String, nullable=False),
        )
        self.metadata.create_all(self.engine)
        self._ensure_run_schema()
        self._ensure_repository_metadata()
        self._backfill_workflow_versions()

    def get_repository_status(self) -> dict[str, object]:
        inspector = inspect(self.engine)
        tables = inspector.get_table_names()
        metadata = self._repository_metadata_values()
        with self.engine.connect() as connection:
            workflow_count = connection.execute(select(func.count()).select_from(self.workflows)).scalar_one()
            agent_build_session_count = connection.execute(select(func.count()).select_from(self.agent_build_sessions)).scalar_one()
        return {
            "backend": "sqlite",
            "schema_version": metadata.get("schema_version", "unknown"),
            "schema_initialized_at": metadata.get("schema_initialized_at"),
            "schema_updated_at": metadata.get("schema_updated_at"),
            "table_count": len(tables),
            "tables": sorted(tables),
            "workflow_count": workflow_count,
            "agent_build_session_count": agent_build_session_count,
        }

    def save_agent_build_session(self, session: AgentBuildSession) -> AgentBuildSession:
        payload_json = session.model_dump_json(by_alias=True)
        with self.engine.begin() as connection:
            connection.execute(delete(self.agent_build_sessions).where(self.agent_build_sessions.c.session_id == session.session_id))
            connection.execute(
                insert(self.agent_build_sessions).values(
                    session_id=session.session_id,
                    created_at=session.created_at.isoformat(),
                    updated_at=session.updated_at.isoformat(),
                    payload_json=payload_json,
                )
            )
        return session

    def get_agent_build_session(self, session_id: str) -> AgentBuildSession | None:
        statement = select(self.agent_build_sessions.c.payload_json).where(self.agent_build_sessions.c.session_id == session_id)
        with self.engine.connect() as connection:
            row = connection.execute(statement).first()
        if row is None:
            return None
        return AgentBuildSession.model_validate_json(row.payload_json)

    def save_workflow(self, workflow_package: WorkflowPackage) -> WorkflowPackage:
        payload_json = workflow_package.model_dump_json(by_alias=True)
        with self.engine.begin() as connection:
            connection.execute(delete(self.workflows).where(self.workflows.c.workflow_id == workflow_package.workflow_id))
            connection.execute(
                insert(self.workflows).values(
                    workflow_id=workflow_package.workflow_id,
                    name=workflow_package.name,
                    version=workflow_package.version,
                    created_at=workflow_package.created_at.isoformat(),
                    updated_at=workflow_package.updated_at.isoformat(),
                    payload_json=payload_json,
                )
            )
            connection.execute(
                delete(self.workflow_versions).where(
                    (self.workflow_versions.c.workflow_id == workflow_package.workflow_id)
                    & (self.workflow_versions.c.version == workflow_package.version)
                )
            )
            connection.execute(
                insert(self.workflow_versions).values(
                    workflow_id=workflow_package.workflow_id,
                    version=workflow_package.version,
                    name=workflow_package.name,
                    created_at=workflow_package.created_at.isoformat(),
                    updated_at=workflow_package.updated_at.isoformat(),
                    payload_json=payload_json,
                )
            )
        return workflow_package

    def save_workflow_version(self, workflow_package: WorkflowPackage) -> WorkflowPackage:
        payload_json = workflow_package.model_dump_json(by_alias=True)
        with self.engine.begin() as connection:
            connection.execute(
                delete(self.workflow_versions).where(
                    (self.workflow_versions.c.workflow_id == workflow_package.workflow_id)
                    & (self.workflow_versions.c.version == workflow_package.version)
                )
            )
            connection.execute(
                insert(self.workflow_versions).values(
                    workflow_id=workflow_package.workflow_id,
                    version=workflow_package.version,
                    name=workflow_package.name,
                    created_at=workflow_package.created_at.isoformat(),
                    updated_at=workflow_package.updated_at.isoformat(),
                    payload_json=payload_json,
                )
            )
        return workflow_package

    def list_workflows(self, limit: int | None = None, offset: int = 0) -> list[WorkflowPackage]:
        statement = select(self.workflows.c.payload_json).order_by(self.workflows.c.created_at.desc())
        statement = _paginate_statement(statement, limit, offset)
        with self.engine.connect() as connection:
            return [
                WorkflowPackage.model_validate_json(row.payload_json)
                for row in connection.execute(statement).all()
            ]

    def get_workflow(self, workflow_id: str) -> WorkflowPackage | None:
        statement = select(self.workflows.c.payload_json).where(self.workflows.c.workflow_id == workflow_id)
        with self.engine.connect() as connection:
            row = connection.execute(statement).first()
        if row is None:
            return None
        return WorkflowPackage.model_validate_json(row.payload_json)

    def list_workflow_versions(self, workflow_id: str, limit: int | None = None, offset: int = 0) -> list[WorkflowPackage]:
        statement = (
            select(self.workflow_versions.c.payload_json)
            .where(self.workflow_versions.c.workflow_id == workflow_id)
            .order_by(self.workflow_versions.c.created_at.desc(), self.workflow_versions.c.id.desc())
        )
        statement = _paginate_statement(statement, limit, offset)
        with self.engine.connect() as connection:
            return [
                WorkflowPackage.model_validate_json(row.payload_json)
                for row in connection.execute(statement).all()
            ]

    def get_workflow_version(self, workflow_id: str, version: str) -> WorkflowPackage | None:
        statement = select(self.workflow_versions.c.payload_json).where(
            (self.workflow_versions.c.workflow_id == workflow_id)
            & (self.workflow_versions.c.version == version)
        )
        with self.engine.connect() as connection:
            row = connection.execute(statement).first()
        if row is None:
            return None
        return WorkflowPackage.model_validate_json(row.payload_json)

    def promote_workflow_version(self, workflow_id: str, version: str) -> WorkflowPackage | None:
        workflow_package = self.get_workflow_version(workflow_id, version)
        if workflow_package is None:
            return None
        payload_json = workflow_package.model_dump_json(by_alias=True)
        with self.engine.begin() as connection:
            connection.execute(delete(self.workflows).where(self.workflows.c.workflow_id == workflow_id))
            connection.execute(
                insert(self.workflows).values(
                    workflow_id=workflow_package.workflow_id,
                    name=workflow_package.name,
                    version=workflow_package.version,
                    created_at=workflow_package.created_at.isoformat(),
                    updated_at=workflow_package.updated_at.isoformat(),
                    payload_json=payload_json,
                )
            )
        return workflow_package

    def save_run(self, run: WorkflowRun) -> WorkflowRun:
        payload_json = run.model_dump_json(by_alias=True)
        with self.engine.begin() as connection:
            connection.execute(delete(self.runs).where(self.runs.c.run_id == run.run_id))
            connection.execute(
                insert(self.runs).values(
                    run_id=run.run_id,
                    workflow_id=run.workflow_id,
                    idempotency_key=run.idempotency_key,
                    request_fingerprint=run.request_fingerprint,
                    status=run.status,
                    created_at=run.created_at.isoformat(),
                    updated_at=run.updated_at.isoformat(),
                    payload_json=payload_json,
                )
            )
        return run

    def get_run(self, run_id: str) -> WorkflowRun | None:
        statement = select(self.runs.c.payload_json).where(self.runs.c.run_id == run_id)
        with self.engine.connect() as connection:
            row = connection.execute(statement).first()
        if row is None:
            return None
        return WorkflowRun.model_validate_json(row.payload_json)

    def get_run_by_idempotency_key(self, workflow_id: str, idempotency_key: str) -> WorkflowRun | None:
        statement = select(self.runs.c.payload_json).where(
            (self.runs.c.workflow_id == workflow_id)
            & (self.runs.c.idempotency_key == idempotency_key)
        )
        with self.engine.connect() as connection:
            row = connection.execute(statement).first()
        if row is None:
            return None
        return WorkflowRun.model_validate_json(row.payload_json)

    def list_runs(
        self,
        workflow_id: str | None = None,
        status: WorkflowRunStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[WorkflowRun]:
        statement = select(self.runs.c.payload_json)
        if workflow_id:
            statement = statement.where(self.runs.c.workflow_id == workflow_id)
        if status:
            statement = statement.where(self.runs.c.status == status.value)
        statement = statement.order_by(self.runs.c.created_at.desc())
        statement = _paginate_statement(statement, limit, offset)
        with self.engine.connect() as connection:
            return [WorkflowRun.model_validate_json(row.payload_json) for row in connection.execute(statement).all()]

    def delete_runs(self, run_ids: list[str]) -> int:
        if not run_ids:
            return 0
        with self.engine.begin() as connection:
            result = connection.execute(delete(self.runs).where(self.runs.c.run_id.in_(set(run_ids))))
        return result.rowcount or 0

    def list_traces(
        self,
        workflow_id: str | None = None,
        run_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[TraceRecord]:
        runs = self.list_runs(workflow_id=workflow_id)
        if run_id:
            runs = [run for run in runs if run.run_id == run_id]
        traces = [trace for run in runs for trace in run.traces]
        return _slice_items(traces, limit, offset)

    def save_eval_results(self, workflow_id: str, results: list[EvalResult]) -> list[EvalResult]:
        with self.engine.begin() as connection:
            for result in results:
                connection.execute(
                    insert(self.eval_results).values(
                        workflow_id=workflow_id,
                        eval_id=result.eval_id,
                        payload_json=result.model_dump_json(by_alias=True),
                    )
                )
        return results

    def list_eval_results(self, workflow_id: str | None = None, limit: int | None = None, offset: int = 0) -> list[EvalResult]:
        statement = select(self.eval_results.c.payload_json).order_by(self.eval_results.c.id.desc())
        if workflow_id:
            statement = statement.where(self.eval_results.c.workflow_id == workflow_id)
        statement = _paginate_statement(statement, limit, offset)
        with self.engine.connect() as connection:
            return [EvalResult.model_validate_json(row.payload_json) for row in connection.execute(statement).all()]

    def delete_eval_results(self, eval_ids: list[str], workflow_id: str | None = None) -> int:
        if not eval_ids:
            return 0
        statement = delete(self.eval_results).where(self.eval_results.c.eval_id.in_(set(eval_ids)))
        if workflow_id:
            statement = statement.where(self.eval_results.c.workflow_id == workflow_id)
        with self.engine.begin() as connection:
            result = connection.execute(statement)
        return result.rowcount or 0

    def save_audit_event(self, event: AuditEvent) -> AuditEvent:
        with self.engine.begin() as connection:
            connection.execute(delete(self.audit_events).where(self.audit_events.c.event_id == event.event_id))
            connection.execute(
                insert(self.audit_events).values(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    action=event.action,
                    status=event.status,
                    actor_id=event.actor_id,
                    actor_role=event.actor_role,
                    workflow_id=event.workflow_id,
                    workflow_version=event.workflow_version,
                    run_id=event.run_id,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    created_at=event.created_at.isoformat(),
                    payload_json=event.model_dump_json(by_alias=True),
                )
            )
        return event

    def list_audit_events(
        self,
        workflow_id: str | None = None,
        run_id: str | None = None,
        event_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AuditEvent]:
        statement = select(self.audit_events.c.payload_json)
        if workflow_id:
            statement = statement.where(self.audit_events.c.workflow_id == workflow_id)
        if run_id:
            statement = statement.where(self.audit_events.c.run_id == run_id)
        if event_type:
            statement = statement.where(self.audit_events.c.event_type == event_type)
        statement = statement.order_by(self.audit_events.c.created_at.desc())
        statement = _paginate_statement(statement, limit, offset)
        with self.engine.connect() as connection:
            return [AuditEvent.model_validate_json(row.payload_json) for row in connection.execute(statement).all()]

    def _ensure_sqlite_parent(self, database_url: str) -> None:
        url = make_url(database_url)
        if url.drivername != "sqlite" or not url.database or url.database == ":memory:":
            return
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def _ensure_run_schema(self) -> None:
        columns = {column["name"] for column in inspect(self.engine).get_columns("workflow_runs")}
        statements: list[str] = []
        if "idempotency_key" not in columns:
            statements.append("ALTER TABLE workflow_runs ADD COLUMN idempotency_key VARCHAR")
        if "request_fingerprint" not in columns:
            statements.append("ALTER TABLE workflow_runs ADD COLUMN request_fingerprint VARCHAR")
        statements.append(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_runs_idempotency "
            "ON workflow_runs(workflow_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
        )
        with self.engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))

    def _ensure_repository_metadata(self) -> None:
        now = utc_now().isoformat()
        existing = self._repository_metadata_values()
        with self.engine.begin() as connection:
            if "schema_initialized_at" not in existing:
                connection.execute(
                    insert(self.repository_metadata).values(
                        key="schema_initialized_at",
                        value=now,
                        updated_at=now,
                    )
                )
            self._upsert_repository_metadata(
                connection,
                key="schema_version",
                value=SQLITE_REPOSITORY_SCHEMA_VERSION,
                updated_at=now,
            )
            self._upsert_repository_metadata(
                connection,
                key="schema_updated_at",
                value=now,
                updated_at=now,
            )

    def _repository_metadata_values(self) -> dict[str, str]:
        if "repository_metadata" not in inspect(self.engine).get_table_names():
            return {}
        statement = select(self.repository_metadata.c.key, self.repository_metadata.c.value)
        with self.engine.connect() as connection:
            return {
                row.key: row.value
                for row in connection.execute(statement).all()
            }

    def _upsert_repository_metadata(self, connection, *, key: str, value: str, updated_at: str) -> None:
        existing = connection.execute(
            select(self.repository_metadata.c.key).where(self.repository_metadata.c.key == key)
        ).first()
        if existing is not None:
            connection.execute(
                self.repository_metadata.update()
                .where(self.repository_metadata.c.key == key)
                .values(value=value, updated_at=updated_at)
            )
            return
        connection.execute(
            insert(self.repository_metadata).values(
                key=key,
                value=value,
                updated_at=updated_at,
            )
        )

    def _backfill_workflow_versions(self) -> None:
        current_workflows = self.list_workflows()
        with self.engine.begin() as connection:
            for workflow_package in current_workflows:
                existing = connection.execute(
                    select(self.workflow_versions.c.id).where(
                        (self.workflow_versions.c.workflow_id == workflow_package.workflow_id)
                        & (self.workflow_versions.c.version == workflow_package.version)
                    )
                ).first()
                if existing is not None:
                    continue
                connection.execute(
                    insert(self.workflow_versions).values(
                        workflow_id=workflow_package.workflow_id,
                        version=workflow_package.version,
                        name=workflow_package.name,
                        created_at=workflow_package.created_at.isoformat(),
                        updated_at=workflow_package.updated_at.isoformat(),
                        payload_json=workflow_package.model_dump_json(by_alias=True),
                    )
                )


def _paginate_statement(statement, limit: int | None, offset: int):
    if offset:
        statement = statement.offset(offset)
    if limit is not None:
        statement = statement.limit(limit)
    return statement


def _slice_items(items: list[T], limit: int | None, offset: int) -> list[T]:
    start = max(0, offset)
    if limit is None:
        return items[start:]
    return items[start:start + max(0, limit)]
