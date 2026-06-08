from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient

from apps.api import dependencies
from apps.api.auth_tokens import create_actor_token
from apps.api.main import app
from apps.api.settings import DEFAULT_AUTH_SECRET_KEY, settings
from packages.workflow_core.security import hash_password
from packages.workflow_core.models import ActorContext, EvalResult, TraceRecord, WorkflowPackage, WorkflowRun
from packages.workflow_core.models.enums import NodeExecutionStatus, WorkflowRunStatus
from packages.workflow_core.storage import MemoryWorkflowRepository


def load_example_payload() -> dict:
    return json.loads(Path("examples/new_product_launch.workflow.json").read_text(encoding="utf-8"))


def token_headers(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/token", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def actor_token_headers(actor: ActorContext) -> dict[str, str]:
    token = create_actor_token(actor, settings.auth_secret_key, settings.auth_token_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}


def admin_headers(client: TestClient) -> dict[str, str]:
    return token_headers(client, "admin", "admin")


def import_workflow(client: TestClient, payload: dict) -> object:
    return client.post("/api/workflows/import", headers=admin_headers(client), json=payload)


def start_workflow_run(client: TestClient, payload: dict) -> object:
    return client.post("/api/workflows/new-product-launch/runs", headers=admin_headers(client), json=payload)


def get_read(client: TestClient, path: str) -> object:
    return client.get(path, headers=admin_headers(client))


class BrokenReadinessRepository:
    def list_workflows(self, limit: int | None = None, offset: int = 0) -> list[object]:
        raise RuntimeError("database unavailable")


class CountingRunSaveRepository(MemoryWorkflowRepository):
    def __init__(self) -> None:
        super().__init__()
        self.save_run_count = 0

    def save_run(self, run):
        self.save_run_count += 1
        return super().save_run(run)


class FailingWorkflowBuilder:
    class LLM:
        provider = "http"
        model = "broken-model"

    llm = LLM()

    def generate(self, user_request: str, version: str = "0.1.0", brief: object | None = None) -> object:
        raise RuntimeError("upstream unavailable")


def test_health_and_ready_routes_report_liveness_and_dependencies() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)

        health = client.get("/health")
        ready = client.get("/ready")

        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        checks = {check["name"]: check for check in ready.json()["checks"]}
        assert checks["repository"]["status"] == "passed"
        assert checks["repository"]["repository_status"]["backend"] == "memory"
        assert checks["repository"]["repository_status"]["schema_version"] == "memory"
        assert checks["auth_configuration"]["status"] == "passed"
        assert checks["llm_configuration"]["status"] == "passed"
        assert checks["llm_configuration"]["provider"] == "mock"

        app.dependency_overrides[dependencies.get_repository] = lambda: BrokenReadinessRepository()
        not_ready = client.get("/ready")
        assert not_ready.status_code == 503
        assert not_ready.json()["status"] == "not_ready"
        failed_checks = {check["name"]: check for check in not_ready.json()["checks"]}
        assert failed_checks["repository"]["status"] == "failed"
    finally:
        app.dependency_overrides.clear()


def test_api_cors_allows_configured_local_dashboard_origin() -> None:
    client = TestClient(app)

    response = client.options(
        "/api/workflows",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_repository_startup_seed_can_be_disabled() -> None:
    original_database_url = settings.database_url
    original_seed_example_workflow = settings.seed_example_workflow
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            object.__setattr__(settings, "database_url", f"sqlite:///{Path(tmpdir) / 'no-seed.sqlite3'}")
            object.__setattr__(settings, "seed_example_workflow", False)
            repository = dependencies._build_repository()

            assert repository.get_workflow("new-product-launch") is None
        finally:
            object.__setattr__(settings, "database_url", original_database_url)
            object.__setattr__(settings, "seed_example_workflow", original_seed_example_workflow)


def test_repository_startup_seed_defaults_to_example_workflow() -> None:
    original_database_url = settings.database_url
    original_seed_example_workflow = settings.seed_example_workflow
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            object.__setattr__(settings, "database_url", f"sqlite:///{Path(tmpdir) / 'seed.sqlite3'}")
            object.__setattr__(settings, "seed_example_workflow", True)
            repository = dependencies._build_repository()

            assert repository.get_workflow("new-product-launch") is not None
        finally:
            object.__setattr__(settings, "database_url", original_database_url)
            object.__setattr__(settings, "seed_example_workflow", original_seed_example_workflow)


def test_ready_fails_when_production_auth_uses_default_secret() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    original_allow_dev_headers = settings.allow_dev_actor_headers
    original_secret = settings.auth_secret_key
    try:
        object.__setattr__(settings, "allow_dev_actor_headers", False)
        object.__setattr__(settings, "auth_secret_key", DEFAULT_AUTH_SECRET_KEY)
        client = TestClient(app)

        response = client.get("/ready")

        assert response.status_code == 503
        checks = {check["name"]: check for check in response.json()["checks"]}
        assert checks["auth_configuration"]["status"] == "failed"
        assert "AGENT_WORKFLOW_AUTH_SECRET_KEY" in checks["auth_configuration"]["message"]
    finally:
        object.__setattr__(settings, "allow_dev_actor_headers", original_allow_dev_headers)
        object.__setattr__(settings, "auth_secret_key", original_secret)
        app.dependency_overrides.clear()


def test_ready_reports_invalid_real_llm_configuration_without_secret_values() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    original_provider = settings.llm_provider
    original_endpoint = settings.llm_endpoint
    original_model = settings.llm_model
    original_api_key = settings.llm_api_key
    try:
        object.__setattr__(settings, "llm_provider", "http")
        object.__setattr__(settings, "llm_endpoint", None)
        object.__setattr__(settings, "llm_model", "workflow-framer")
        object.__setattr__(settings, "llm_api_key", "super-secret")
        client = TestClient(app)

        response = client.get("/ready")

        assert response.status_code == 503
        checks = {check["name"]: check for check in response.json()["checks"]}
        assert checks["llm_configuration"]["status"] == "failed"
        assert checks["llm_configuration"]["endpoint_configured"] is False
        assert "super-secret" not in response.text
    finally:
        object.__setattr__(settings, "llm_provider", original_provider)
        object.__setattr__(settings, "llm_endpoint", original_endpoint)
        object.__setattr__(settings, "llm_model", original_model)
        object.__setattr__(settings, "llm_api_key", original_api_key)
        app.dependency_overrides.clear()


def test_api_generation_builder_failure_is_audited_without_raw_prompt() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    app.dependency_overrides[dependencies.get_workflow_builder] = lambda: FailingWorkflowBuilder()
    try:
        client = TestClient(app)

        response = client.post(
            "/api/workflows/generate",
            headers=admin_headers(client),
            json={"user_request": "contains sensitive customer context", "workflow_id": "customer-risk"},
        )

        assert response.status_code == 502
        event = repository.list_audit_events(event_type="workflow_package_generation")[0]
        assert event.status == "failed"
        assert event.resource_id == "customer-risk"
        assert event.details["builder_error_type"] == "RuntimeError"
        assert event.details["llm_provider"] == "http"
        assert event.details["llm_model"] == "broken-model"
        assert "contains sensitive customer context" not in json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    finally:
        app.dependency_overrides.clear()


def test_api_import_export_run_approval_and_persisted_traces() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)

        imported = import_workflow(client, load_example_payload())
        assert imported.status_code == 200
        assert imported.json()["valid"] is True

        exported = get_read(client, "/api/workflows/new-product-launch/export")
        assert exported.status_code == 200
        assert exported.json()["workflow_id"] == "new-product-launch"

        run = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform"}})
        assert run.status_code == 200
        assert run.json()["status"] == "paused"
        run_id = run.json()["run_id"]

        approved = client.post(
            f"/api/runs/{run_id}/approval",
            headers=token_headers(client, "approver", "approver"),
            json={"approved": True, "approval_payload": {"approver": "business-owner"}},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "completed"
        assert approved.json()["output_payload"]["approval_payload"]["actor_id"] == "approver-1"

        traces = get_read(client, f"/api/runs/{run_id}/traces")
        assert traces.status_code == 200
        assert len(traces.json()) == 8

        audit_events = get_read(client, f"/api/governance/audit-events?run_id={run_id}")
        assert audit_events.status_code == 200
        run_audit_events = audit_events.json()
        assert {event["event_type"] for event in run_audit_events} == {"workflow_run_start", "run_approval"}
        approval_events = [event for event in run_audit_events if event["event_type"] == "run_approval"]
        assert approval_events[0]["actor_id"] == "approver-1"
        start_events = [event for event in run_audit_events if event["event_type"] == "workflow_run_start"]
        assert start_events[0]["actor_id"] == "admin-1"
        assert start_events[0]["details"]["input_keys"] == ["product"]

        workflow_audit_events = get_read(client, "/api/governance/audit-events?workflow_id=new-product-launch")
        assert workflow_audit_events.status_code == 200
        assert "workflow_package_import" in {event["event_type"] for event in workflow_audit_events.json()}
    finally:
        app.dependency_overrides.clear()


def test_api_auth_token_rejects_invalid_credentials() -> None:
    client = TestClient(app)

    response = client.post("/api/auth/token", json={"username": "admin", "password": "wrong"})

    assert response.status_code == 401


def test_api_auth_token_accepts_hashed_configured_password() -> None:
    original_auth_users_json = settings.auth_users_json
    try:
        object.__setattr__(
            settings,
            "auth_users_json",
            json.dumps(
                {
                    "ops-admin": {
                        "password_hash": hash_password("correct-password", salt=b"0123456789abcdef"),
                        "actor_id": "ops-admin-1",
                        "role": "workflow-admin",
                        "display_name": "Ops Admin",
                    }
                }
            ),
        )
        client = TestClient(app)

        accepted = client.post("/api/auth/token", json={"username": "ops-admin", "password": "correct-password"})
        rejected = client.post("/api/auth/token", json={"username": "ops-admin", "password": "wrong"})

        assert accepted.status_code == 200
        assert accepted.json()["actor"]["actor_id"] == "ops-admin-1"
        assert rejected.status_code == 401
    finally:
        object.__setattr__(settings, "auth_users_json", original_auth_users_json)


def test_api_read_routes_require_read_scope() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        assert import_workflow(client, load_example_payload()).json()["valid"] is True

        anonymous = client.get("/api/workflows")
        assert anonymous.status_code == 401

        no_read_actor = ActorContext(
            actor_id="runner-only-1",
            role="workflow-admin",
            scopes=["workflow:run"],
        )
        forbidden = client.get("/api/governance/audit-events", headers=actor_token_headers(no_read_actor))
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"]["required_scope"] == "workflow:read"

        read_only_actor = ActorContext(
            actor_id="reader-1",
            role="reader",
            scopes=["workflow:read"],
        )
        allowed = client.get("/api/workflows", headers=actor_token_headers(read_only_actor))
        assert allowed.status_code == 200
        assert allowed.json()[0]["workflow_id"] == "new-product-launch"
    finally:
        app.dependency_overrides.clear()


def test_api_generate_workflow_accepts_structured_business_brief() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        generated = client.post(
            "/api/workflows/generate",
            headers=admin_headers(client),
            json={
                "user_request": "搭建客户升级处理流程，减少客服、销售、技术支持之间的信息损耗。",
                "workflow_id": "customer-escalation",
                "name": "客户升级处理流程智能体",
                "business_goal": "缩短高优先级客户问题从发现到解决的周期。",
                "start_event": "客户提交高优先级问题或续约风险信号。",
                "end_state": "形成升级处理方案、责任人和客户回访计划。",
                "target_users": ["客服负责人", "销售负责人", "技术支持"],
                "human_roles": ["客服负责人", "技术支持", "业务审批人"],
                "success_metrics": ["首次响应时间下降", "升级处理 SLA 达标", "客户回访闭环"],
                "constraints": ["写入客户系统前必须人工审批"],
                "risks": ["客户上下文缺失", "错误承诺解决时间"],
                "process_nodes": [
                    {
                        "name": "接收升级信号",
                        "node_type": "read_node",
                        "owner_role": "客服负责人",
                        "description": "汇总客户问题、优先级、合同状态和历史沟通。",
                        "done_condition": "升级信号具备客户、问题、优先级和时限。",
                    },
                    {
                        "name": "诊断根因",
                        "node_type": "reasoning_node",
                        "owner_role": "技术支持",
                        "description": "判断问题类型、影响范围和可能根因。",
                        "done_condition": "输出根因假设、证据和下一步处理路径。",
                    },
                    {
                        "name": "审查处理方案",
                        "node_type": "review_node",
                        "owner_role": "销售负责人",
                        "description": "审查承诺、风险和客户沟通计划。",
                        "done_condition": "方案明确通过、打回或补充材料要求。",
                    },
                    {
                        "name": "写入升级计划",
                        "node_type": "write_node",
                        "owner_role": "业务审批人",
                        "description": "审批后生成客户升级处理记录。",
                        "done_condition": "升级计划已审批并可审计。",
                    },
                ],
            },
        )

        assert generated.status_code == 200
        payload = generated.json()
        workflow = payload["workflow_package"]
        assert workflow["workflow_id"] == "customer-escalation"
        assert workflow["name"] == "客户升级处理流程智能体"
        assert workflow["problem_spec"]["business_goal"] == "缩短高优先级客户问题从发现到解决的周期。"
        assert [node["name"] for node in workflow["process_spec"]["nodes"]] == [
            "接收升级信号",
            "诊断根因",
            "审查处理方案",
            "写入升级计划",
        ]
        assert workflow["process_spec"]["entry_node_id"] == "node-1"
        assert workflow["process_spec"]["terminal_node_ids"] == ["node-4"]
        assert workflow["process_spec"]["nodes"][-1]["requires_approval"] is True
        assert workflow["tool_policies"][-1]["permission_level"] == "write_requires_approval"
        assert workflow["eval_specs"][1]["target_node_id"] == "node-3"
        assert payload["validation_report"]["valid"] is True
        audit_events = get_read(client, "/api/governance/audit-events?workflow_id=customer-escalation")
        assert audit_events.status_code == 200
        generation_event = next(event for event in audit_events.json() if event["event_type"] == "workflow_package_generation")
        assert generation_event["details"]["structured_brief_present"] is True
        assert generation_event["details"]["process_node_count"] == 4
    finally:
        app.dependency_overrides.clear()


def test_api_generate_can_save_candidate_version_without_changing_current() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())

        candidate = client.post(
            "/api/workflows/generate",
            headers=admin_headers(client),
            json={
                "user_request": "更新新品上市流程，补充上市复盘节点。",
                "workflow_id": "new-product-launch",
                "version": "0.2.0",
                "save_as_current": False,
                "name": "新品上市流程智能体 v2 candidate",
                "business_goal": "增加上市复盘闭环，但不直接发布当前版本。",
            },
        )
        current = get_read(client, "/api/workflows/new-product-launch")
        candidate_version = get_read(client, "/api/workflows/new-product-launch/versions/0.2.0")
        diff = get_read(client, "/api/workflows/new-product-launch/diff?from_version=0.1.0&to_version=0.2.0")
        promoted = client.post(
            "/api/workflows/new-product-launch/versions/0.2.0/promote",
            headers=admin_headers(client),
            json={
                "reason": "publish candidate after review",
                "change_summary": "补充上市复盘节点与候选版本验证。",
                "risk_acceptance": "质量和 eval gate 通过后接受低风险发布。",
                "reviewed_diff": True,
                "readiness_acknowledged": True,
            },
        )

        assert candidate.status_code == 200
        assert candidate.json()["saved_as_current"] is False
        assert candidate.json()["workflow_package"]["version"] == "0.2.0"
        assert current.status_code == 200
        assert current.json()["version"] == "0.1.0"
        assert candidate_version.status_code == 200
        assert candidate_version.json()["name"] == "新品上市流程智能体 v2 candidate"
        assert diff.status_code == 200
        assert diff.json()["change_count"] > 0
        assert promoted.status_code == 200
        assert promoted.json()["workflow_package"]["version"] == "0.2.0"
        assert promoted.json()["promotion"]["reviewed_diff"] is True
        assert promoted.json()["release_context"]["change_count"] > 0
        assert promoted.json()["release_context"]["change_summary_present"] is True
        assert promoted.json()["release_context"]["risk_acceptance_present"] is True
        assert promoted.json()["release_context"]["reviewed_diff"] is True
        assert promoted.json()["release_context"]["readiness_acknowledged"] is True
        assert repository.get_workflow("new-product-launch").version == "0.2.0"
        audit_events = get_read(client, "/api/governance/audit-events?workflow_id=new-product-launch")
        generation_events = [
            event for event in audit_events.json()
            if event["event_type"] == "workflow_package_generation" and event["workflow_version"] == "0.2.0"
        ]
        assert generation_events
        assert generation_events[0]["details"]["save_as_current"] is False
    finally:
        app.dependency_overrides.clear()


def test_api_candidate_generation_requires_existing_current_workflow() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        candidate = client.post(
            "/api/workflows/generate",
            headers=admin_headers(client),
            json={
                "user_request": "搭建客户升级处理候选版本。",
                "workflow_id": "customer-escalation",
                "version": "0.2.0",
                "save_as_current": False,
            },
        )

        assert candidate.status_code == 409
        assert repository.get_workflow("customer-escalation") is None
        audit_events = get_read(client, "/api/governance/audit-events?workflow_id=customer-escalation")
        assert audit_events.status_code == 200
        assert audit_events.json()[0]["details"]["gate"] == "candidate_base_workflow"
    finally:
        app.dependency_overrides.clear()


def test_api_import_can_save_candidate_version_without_changing_current() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        first_payload = load_example_payload()
        candidate_payload = load_example_payload()
        candidate_payload["version"] = "0.2.0"
        candidate_payload["name"] = "新品上市流程智能体 import candidate"
        candidate_payload["process_spec"]["version"] = "0.2.0"

        imported_current = import_workflow(client, first_payload)
        imported_candidate = client.post(
            "/api/workflows/import?save_as_current=false",
            headers=admin_headers(client),
            json=candidate_payload,
        )
        current = get_read(client, "/api/workflows/new-product-launch")
        candidate_version = get_read(client, "/api/workflows/new-product-launch/versions/0.2.0")

        assert imported_current.status_code == 200
        assert imported_candidate.status_code == 200
        assert imported_candidate.json()["saved_as_current"] is False
        assert current.json()["version"] == "0.1.0"
        assert candidate_version.json()["name"] == "新品上市流程智能体 import candidate"
        audit_events = get_read(client, "/api/governance/audit-events?workflow_id=new-product-launch")
        import_events = [
            event for event in audit_events.json()
            if event["event_type"] == "workflow_package_import" and event["workflow_version"] == "0.2.0"
        ]
        assert import_events
        assert import_events[0]["details"]["save_as_current"] is False
    finally:
        app.dependency_overrides.clear()


def test_api_can_shadow_run_candidate_version_without_changing_current() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        candidate = client.post(
            "/api/workflows/generate",
            headers=admin_headers(client),
            json={
                "user_request": "更新新品上市流程，先做候选版本影子验证。",
                "workflow_id": "new-product-launch",
                "version": "0.2.0",
                "save_as_current": False,
                "name": "新品上市流程智能体 v2 shadow candidate",
            },
        )

        shadow_run = start_workflow_run(
            client,
            {
                "workflow_version": "0.2.0",
                "input_payload": {"product": "AI workflow platform", "market": "global"},
                "shadow_mode": True,
                "idempotency_key": "candidate-shadow-001",
            },
        )
        blocked_live_run = start_workflow_run(
            client,
            {
                "workflow_version": "0.2.0",
                "input_payload": {"product": "AI workflow platform", "market": "global"},
                "shadow_mode": False,
                "enforce_release_readiness": True,
            },
        )
        current = get_read(client, "/api/workflows/new-product-launch")
        run_report = get_read(client, "/api/governance/run-report?workflow_id=new-product-launch")

        assert candidate.status_code == 200
        assert shadow_run.status_code == 200
        assert shadow_run.json()["workflow_version"] == "0.2.0"
        assert shadow_run.json()["shadow_mode"] is True
        assert shadow_run.json()["status"] == "completed"
        assert blocked_live_run.status_code == 409
        assert current.json()["version"] == "0.1.0"
        assert run_report.status_code == 200
        assert run_report.json()["shadow_validation_pending_count"] == 1
    finally:
        app.dependency_overrides.clear()


def test_api_redacts_sensitive_values_from_run_and_trace_responses() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())

        run = start_workflow_run(
            client,
            {
                "input_payload": {
                    "product": "AI workflow platform",
                    "api_key": "raw-api-key-123",
                    "credentials": {
                        "password": "raw-password-123",
                        "nested": {"access_token": "raw-access-token-123"},
                    },
                }
            },
        )
        run_id = run.json()["run_id"]

        run_response = json.dumps(run.json(), ensure_ascii=False)
        fetched_run_response = json.dumps(get_read(client, f"/api/runs/{run_id}").json(), ensure_ascii=False)
        traces_response = json.dumps(get_read(client, f"/api/runs/{run_id}/traces").json(), ensure_ascii=False)
        persisted_run = repository.get_run(run_id)
        assert persisted_run is not None
        persisted_payload = persisted_run.model_dump(mode="json")

        assert "raw-api-key-123" in json.dumps(persisted_payload)
        assert "raw-api-key-123" not in run_response
        assert "raw-password-123" not in run_response
        assert "raw-access-token-123" not in run_response
        assert "raw-api-key-123" not in fetched_run_response
        assert "raw-password-123" not in traces_response
        assert "raw-access-token-123" not in traces_response
        assert "[REDACTED]" in run_response
        assert "[REDACTED]" in traces_response
    finally:
        app.dependency_overrides.clear()


def test_api_exports_run_trace_as_low_sensitive_otlp_payload_and_audits() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        run = start_workflow_run(
            client,
            {
                "input_payload": {
                    "product": "AI workflow platform",
                    "api_key": "raw-api-key-123",
                    "credentials": {"password": "raw-password-123"},
                }
            },
        )
        run_id = run.json()["run_id"]

        exported = client.post(
            "/api/governance/trace-export",
            headers=admin_headers(client),
            json={
                "run_id": run_id,
                "service_name": "agent-workflow-builder-test",
                "deployment_environment": "test",
            },
        )
        audit_events = get_read(
            client,
            f"/api/governance/audit-events?run_id={run_id}&event_type=workflow_trace_export",
        )
        encoded_export = json.dumps(exported.json(), ensure_ascii=False)
        spans = exported.json()["payload"]["resourceSpans"][0]["scopeSpans"][0]["spans"]

        assert exported.status_code == 200
        assert exported.json()["exported"] is False
        assert exported.json()["span_count"] == len(run.json()["traces"]) + 1
        assert spans[0]["name"] == "workflow.run"
        assert spans[1]["parentSpanId"] == spans[0]["spanId"]
        assert "raw-api-key-123" not in encoded_export
        assert "raw-password-123" not in encoded_export
        assert audit_events.status_code == 200
        assert audit_events.json()[0]["details"]["span_count"] == exported.json()["span_count"]
        assert audit_events.json()[0]["details"]["endpoint_configured"] is False
    finally:
        app.dependency_overrides.clear()


def test_api_run_start_uses_durable_checkpoints_while_executing() -> None:
    repository = CountingRunSaveRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())

        run = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "max_steps": 1,
            },
        )

        assert run.status_code == 200
        assert run.json()["status"] == "failed"
        assert repository.save_run_count >= 4
        persisted = repository.get_run(run.json()["run_id"])
        assert persisted is not None
        assert persisted.status == WorkflowRunStatus.FAILED
        assert persisted.output_payload["error"] == "max_steps_exceeded"
    finally:
        app.dependency_overrides.clear()


def test_api_list_endpoints_apply_limit_offset_and_bounds() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        runs = [
            start_workflow_run(client, {"input_payload": {"product": f"AI workflow platform {index}"}})
            for index in range(3)
        ]
        for _ in range(2):
            client.post("/api/workflows/new-product-launch/evals/run", headers=admin_headers(client))

        limited_runs = get_read(client, "/api/runs?workflow_id=new-product-launch&limit=2&offset=1")
        limited_traces = get_read(client, f"/api/runs/{runs[0].json()['run_id']}/traces?limit=2&offset=1")
        limited_eval_results = get_read(client, "/api/governance/eval-results?workflow_id=new-product-launch&limit=1&offset=1")
        limited_audit_events = get_read(client, "/api/governance/audit-events?workflow_id=new-product-launch&limit=2&offset=1")
        oversized_limit = get_read(client, "/api/runs?limit=201")

        assert limited_runs.status_code == 200
        assert len(limited_runs.json()) == 2
        assert limited_traces.status_code == 200
        assert len(limited_traces.json()) == 2
        assert limited_eval_results.status_code == 200
        assert len(limited_eval_results.json()) == 1
        assert limited_audit_events.status_code == 200
        assert len(limited_audit_events.json()) == 2
        assert oversized_limit.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_api_runs_list_filters_by_status() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        failed = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "max_steps": 1,
            },
        )
        paused = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform"}})
        canceled = client.post(
            f"/api/runs/{paused.json()['run_id']}/cancel",
            headers=admin_headers(client),
            json={"reason": "status filter coverage"},
        )
        another_paused = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform 2"}})

        failed_runs = get_read(client, "/api/runs?workflow_id=new-product-launch&status=failed")
        canceled_runs = get_read(client, "/api/runs?workflow_id=new-product-launch&status=canceled")
        paused_runs = get_read(client, "/api/runs?workflow_id=new-product-launch&status=paused")
        invalid_status = get_read(client, "/api/runs?status=waiting")

        assert failed.status_code == 200
        assert canceled.status_code == 200
        assert another_paused.status_code == 200
        assert failed_runs.status_code == 200
        assert [run["run_id"] for run in failed_runs.json()] == [failed.json()["run_id"]]
        assert canceled_runs.status_code == 200
        assert [run["run_id"] for run in canceled_runs.json()] == [paused.json()["run_id"]]
        assert paused_runs.status_code == 200
        assert [run["run_id"] for run in paused_runs.json()] == [another_paused.json()["run_id"]]
        assert invalid_status.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_api_governance_run_report_summarizes_queues_and_recovery_items() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        failed = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "max_steps": 1,
            },
        )
        paused_then_canceled = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform"}})
        canceled = client.post(
            f"/api/runs/{paused_then_canceled.json()['run_id']}/cancel",
            headers=admin_headers(client),
            json={"reason": "run report coverage"},
        )
        pending = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform 2"}})
        shadow = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "shadow_mode": True,
            },
        )

        report = get_read(client, "/api/governance/run-report?workflow_id=new-product-launch")

        assert failed.status_code == 200
        assert canceled.status_code == 200
        assert pending.status_code == 200
        assert shadow.status_code == 200
        assert report.status_code == 200
        payload = report.json()
        assert payload["workflow_count"] == 1
        assert payload["run_count"] == 4
        assert payload["status_counts"]["failed"] == 1
        assert payload["status_counts"]["canceled"] == 1
        assert payload["status_counts"]["paused"] == 1
        assert payload["status_counts"]["completed"] == 1
        assert payload["active_run_count"] == 1
        assert payload["terminal_run_count"] == 3
        assert payload["live_run_count"] == 3
        assert payload["shadow_run_count"] == 1
        assert payload["pending_approval_count"] == 1
        assert payload["pending_node_counts"] == {"go-no-go-decision": 1}
        assert payload["recovery_queue_count"] == 2
        assert payload["recovery_reason_counts"]["max_steps_exceeded"] == 1
        assert payload["recovery_reason_counts"]["workflow_canceled"] == 1
        assert payload["shadow_validation_pending_count"] == 1
        assert payload["sample_limit"] == 20
        assert [item["run_id"] for item in payload["pending_approvals"]] == [pending.json()["run_id"]]
        recovery_items = {item["run_id"]: item for item in payload["recovery_queue"]}
        assert recovery_items[failed.json()["run_id"]]["failure_reason_code"] == "max_steps_exceeded"
        assert (
            recovery_items[failed.json()["run_id"]]["recommended_action_code"]
            == "inspect_graph_and_rerun_with_reviewed_step_budget"
        )
        assert recovery_items[paused_then_canceled.json()["run_id"]]["failure_reason_code"] == "workflow_canceled"
        assert [item["run_id"] for item in payload["shadow_validation_queue"]] == [shadow.json()["run_id"]]
        assert {item["code"] for item in payload["run_items"]} >= {
            "failed_runs_in_recovery_queue",
            "pending_approvals",
            "shadow_validation_pending",
            "canceled_runs_present",
        }
    finally:
        app.dependency_overrides.clear()


def test_api_run_diagnostics_summarizes_failure_and_approval_state() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        failed = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "max_steps": 1,
            },
        )
        paused = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform"}})

        failed_diagnostics = get_read(client, f"/api/runs/{failed.json()['run_id']}/diagnostics")
        paused_diagnostics = get_read(client, f"/api/runs/{paused.json()['run_id']}/diagnostics")

        assert failed.status_code == 200
        assert paused.status_code == 200
        assert failed_diagnostics.status_code == 200
        failed_payload = failed_diagnostics.json()
        assert failed_payload["status"] == "failed"
        assert failed_payload["is_terminal"] is True
        assert failed_payload["failure"]["error"] == "max_steps_exceeded"
        assert failed_payload["failure"]["pending_node_id"] == "collect-market-insights"
        assert failed_payload["failure"]["executed_steps"] == 1
        assert failed_payload["failure"]["max_steps"] == 1
        assert failed_payload["trace_counts"]["success"] == 1
        assert failed_payload["recommended_actions"] == [
            "Inspect the pending node and graph transitions.",
            "Rerun with a larger max_steps budget after confirming the workflow is not looping.",
        ]
        assert paused_diagnostics.status_code == 200
        paused_payload = paused_diagnostics.json()
        assert paused_payload["status"] == "paused"
        assert paused_payload["approval"] == {"required": True, "node_id": "go-no-go-decision"}
        assert paused_payload["failure"] is None
        assert paused_payload["recommended_actions"] == [
            "Review the paused node context, then approve or reject the run."
        ]
    finally:
        app.dependency_overrides.clear()


def test_api_governance_retention_report_is_low_sensitive() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        repository.save_run(
            WorkflowRun(
                run_id="old-terminal-run",
                workflow_id="new-product-launch",
                workflow_version="0.1.0",
                status=WorkflowRunStatus.COMPLETED,
                input_payload={"password": "raw-password-123"},
                output_payload={"token": "raw-token-123"},
                created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        )
        repository.save_eval_results(
            "new-product-launch",
            [
                EvalResult(
                    eval_id="old-eval",
                    workflow_id="new-product-launch",
                    score=1,
                    passed=True,
                    reason="old",
                    created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                )
            ],
        )

        report = get_read(
            client,
            "/api/governance/retention-report?workflow_id=new-product-launch&run_retention_days=30",
        )
        dry_run_apply = client.post(
            "/api/governance/retention-apply",
            headers=admin_headers(client),
            json={
                "workflow_id": "new-product-launch",
                "run_retention_days": 30,
                "eval_retention_days": 30,
                "dry_run": True,
            },
        )
        unconfirmed_apply = client.post(
            "/api/governance/retention-apply",
            headers=admin_headers(client),
            json={
                "workflow_id": "new-product-launch",
                "run_retention_days": 30,
                "eval_retention_days": 30,
                "dry_run": False,
            },
        )
        non_admin_writer = ActorContext(
            actor_id="writer-1",
            role="workflow-operator",
            scopes=["workflow:write"],
        )
        forbidden_apply = client.post(
            "/api/governance/retention-apply",
            headers=actor_token_headers(non_admin_writer),
            json={
                "workflow_id": "new-product-launch",
                "run_retention_days": 30,
                "eval_retention_days": 30,
                "dry_run": False,
                "confirm_apply": True,
                "snapshot_acknowledged": True,
                "reason": "cleanup after reviewed snapshot",
            },
        )
        missing_snapshot_apply = client.post(
            "/api/governance/retention-apply",
            headers=admin_headers(client),
            json={
                "workflow_id": "new-product-launch",
                "run_retention_days": 30,
                "eval_retention_days": 30,
                "dry_run": False,
                "confirm_apply": True,
                "reason": "cleanup after reviewed snapshot",
            },
        )
        missing_reason_apply = client.post(
            "/api/governance/retention-apply",
            headers=admin_headers(client),
            json={
                "workflow_id": "new-product-launch",
                "run_retention_days": 30,
                "eval_retention_days": 30,
                "dry_run": False,
                "confirm_apply": True,
                "snapshot_acknowledged": True,
            },
        )

        assert report.status_code == 200
        payload = report.json()
        assert payload["workflow_id"] == "new-product-launch"
        assert payload["run_account"]["expired_terminal_run_count"] == 1
        terminal_runs = next(item for item in payload["retention_items"] if item["category"] == "terminal_runs")
        assert terminal_runs["sample_ids"] == ["old-terminal-run"]
        assert "raw-password-123" not in report.text
        assert "raw-token-123" not in report.text
        assert dry_run_apply.status_code == 200
        assert dry_run_apply.json()["eligible_counts"] == {"terminal_runs": 1, "eval_results": 1}
        assert dry_run_apply.json()["deleted_counts"] == {"terminal_runs": 0, "eval_results": 0}
        assert repository.get_run("old-terminal-run") is not None
        assert unconfirmed_apply.status_code == 422
        assert forbidden_apply.status_code == 403
        assert missing_snapshot_apply.status_code == 422
        assert missing_reason_apply.status_code == 422
        applied = client.post(
            "/api/governance/retention-apply",
            headers=admin_headers(client),
            json={
                "workflow_id": "new-product-launch",
                "run_retention_days": 30,
                "eval_retention_days": 30,
                "dry_run": False,
                "confirm_apply": True,
                "snapshot_acknowledged": True,
                "reason": "cleanup after reviewed snapshot",
            },
        )
        assert applied.status_code == 200
        assert applied.json()["deleted_counts"] == {"terminal_runs": 1, "eval_results": 1}
        assert repository.get_run("old-terminal-run") is None
        assert repository.list_eval_results("new-product-launch") == []
        retention_events = repository.list_audit_events(
            workflow_id="new-product-launch",
            event_type="workflow_retention_apply",
        )
        assert retention_events
        succeeded_event = next(event for event in retention_events if event.status == "succeeded" and not event.details["dry_run"])
        failed_role_event = next(event for event in retention_events if event.status == "failed" and event.details.get("gate") == "role")
        assert succeeded_event.reason == "cleanup after reviewed snapshot"
        assert succeeded_event.details["eligible_counts"]
        assert succeeded_event.details["snapshot_acknowledged"] is True
        assert failed_role_event.details["actor_role"] == "workflow-operator"
        assert "sample_ids" not in json.dumps(succeeded_event.details)
    finally:
        app.dependency_overrides.clear()


def test_api_shadow_run_completes_without_write_approval_and_is_audited() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())

        run = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "shadow_mode": True,
                "idempotency_key": "shadow-launch-run-001",
            },
        )
        conflict = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "shadow_mode": False,
                "idempotency_key": "shadow-launch-run-001",
            },
        )

        assert run.status_code == 200
        payload = run.json()
        run_id = payload["run_id"]
        assert payload["status"] == "completed"
        assert payload["shadow_mode"] is True
        assert payload["current_node_id"] is None
        assert payload["output_payload"]["go-no-go-decision"]["decision"] == "shadow_draft_created"
        assert payload["output_payload"]["go-no-go-decision"]["tool_results"] == []
        assert payload["traces"][-1]["status"] == "success"
        assert conflict.status_code == 409

        listed = get_read(client, "/api/runs?workflow_id=new-product-launch&status=completed")
        assert listed.status_code == 200
        listed_run = next(candidate for candidate in listed.json() if candidate["run_id"] == run_id)
        assert listed_run["shadow_mode"] is True

        diagnostics = get_read(client, f"/api/runs/{run_id}/diagnostics")
        assert diagnostics.status_code == 200
        assert diagnostics.json()["shadow_mode"] is True
        assert diagnostics.json()["recommended_actions"] == [
            "Compare the shadow output with human handling before enabling live write execution."
        ]

        audit_events = get_read(client, f"/api/governance/audit-events?run_id={run_id}")
        assert audit_events.status_code == 200
        start_event = next(event for event in audit_events.json() if event["event_type"] == "workflow_run_start")
        assert start_event["details"]["shadow_mode"] is True
    finally:
        app.dependency_overrides.clear()


def test_api_shadow_run_comparison_persists_eval_result_and_audit() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        shadow_run = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "shadow_mode": True,
            },
        )
        live_run = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform"}})

        comparison = client.post(
            f"/api/runs/{shadow_run.json()['run_id']}/shadow-comparisons",
            headers=admin_headers(client),
            json={
                "expected_output": {
                    "go-no-go-decision": {
                        "decision": "shadow_draft_created",
                        "shadow_mode": True,
                    }
                },
                "pass_threshold": 1.0,
                "notes": "matched historical human handling",
            },
        )
        live_comparison = client.post(
            f"/api/runs/{live_run.json()['run_id']}/shadow-comparisons",
            headers=admin_headers(client),
            json={"expected_output": {"status": "anything"}},
        )
        listed = get_read(client, f"/api/runs/{shadow_run.json()['run_id']}/shadow-comparisons")
        eval_results = get_read(client, "/api/governance/eval-results?workflow_id=new-product-launch")
        audit_events = get_read(
            client,
            f"/api/governance/audit-events?run_id={shadow_run.json()['run_id']}&event_type=workflow_run_shadow_comparison",
        )

        assert comparison.status_code == 200
        payload = comparison.json()
        assert payload["eval_id"].startswith(f"shadow-comparison-{shadow_run.json()['run_id']}")
        assert payload["passed"] is True
        assert payload["score"] == 1.0
        assert payload["details"]["eval_type"] == "shadow_comparison"
        assert payload["details"]["run_id"] == shadow_run.json()["run_id"]
        assert payload["details"]["compared_path_count"] == 2
        assert payload["details"]["matched_path_count"] == 2
        assert payload["details"]["missing_paths"] == []
        assert payload["details"]["mismatched_paths"] == []
        assert live_comparison.status_code == 409
        assert listed.status_code == 200
        assert [result["eval_id"] for result in listed.json()] == [payload["eval_id"]]
        assert eval_results.status_code == 200
        assert payload["eval_id"] in {result["eval_id"] for result in eval_results.json()}
        assert audit_events.status_code == 200
        audit_payload = audit_events.json()[0]
        assert audit_payload["details"]["eval_id"] == payload["eval_id"]
        assert audit_payload["details"]["passed"] is True
        assert audit_payload["details"]["matched_path_count"] == 2
        assert "expected_output" not in audit_payload["details"]
    finally:
        app.dependency_overrides.clear()


def test_api_release_readiness_gates_enforced_live_run() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())

        initial_readiness = get_read(client, "/api/workflows/new-product-launch/release-readiness")
        blocked_live_run = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "enforce_release_readiness": True,
            },
        )
        shadow_run = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "shadow_mode": True,
            },
        )
        comparison = client.post(
            f"/api/runs/{shadow_run.json()['run_id']}/shadow-comparisons",
            headers=admin_headers(client),
            json={
                "expected_output": {
                    "go-no-go-decision": {
                        "decision": "shadow_draft_created",
                    }
                },
                "pass_threshold": 1.0,
            },
        )
        shadow_ready = get_read(client, "/api/workflows/new-product-launch/release-readiness")
        eval_run = client.post("/api/workflows/new-product-launch/evals/run", headers=admin_headers(client))
        ready = get_read(client, "/api/workflows/new-product-launch/release-readiness")
        live_run = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "enforce_release_readiness": True,
            },
        )
        audit_events = get_read(
            client,
            "/api/governance/audit-events?workflow_id=new-product-launch&event_type=workflow_run_start",
        )

        assert initial_readiness.status_code == 200
        assert initial_readiness.json()["live_ready"] is False
        assert set(initial_readiness.json()["blocking_reasons"]) == {
            "version_eval_results",
            "terminal_shadow_run",
            "passing_shadow_comparison",
        }
        assert blocked_live_run.status_code == 409
        assert blocked_live_run.json()["detail"]["readiness"]["live_ready"] is False
        assert shadow_run.status_code == 200
        assert comparison.status_code == 200
        assert shadow_ready.status_code == 200
        assert shadow_ready.json()["live_ready"] is False
        assert shadow_ready.json()["blocking_reasons"] == ["version_eval_results"]
        assert eval_run.status_code == 200
        assert all(result["details"]["workflow_version"] == "0.1.0" for result in eval_run.json())
        assert ready.status_code == 200
        assert ready.json()["live_ready"] is True
        assert ready.json()["blocking_reasons"] == []
        assert live_run.status_code == 200
        assert live_run.json()["shadow_mode"] is False
        assert live_run.json()["status"] == "paused"
        failed_start_event = next(event for event in audit_events.json() if event["status"] == "failed")
        assert failed_start_event["details"]["gate"] == "release_readiness"
        assert failed_start_event["details"]["enforce_release_readiness"] is True
    finally:
        app.dependency_overrides.clear()


def test_api_governance_risk_report_summarizes_tool_run_and_gate_risks() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        blocked_live_run = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "enforce_release_readiness": True,
            },
        )
        live_run = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform"}})

        report = get_read(client, "/api/governance/risk-report?workflow_id=new-product-launch")

        assert blocked_live_run.status_code == 409
        assert live_run.status_code == 200
        assert report.status_code == 200
        payload = report.json()
        assert payload["workflow_count"] == 1
        assert payload["risk_level"] == "high"
        assert payload["tool_risk"]["high_risk_tool_count"] == 1
        assert payload["tool_risk"]["write_tool_count"] == 1
        assert payload["tool_risk"]["approval_gap_tool_ids"] == []
        assert payload["run_risk"]["live_run_count"] == 1
        assert payload["run_risk"]["shadow_run_count"] == 0
        assert payload["run_risk"]["live_runs_on_unready_version_count"] == 1
        assert payload["quality_risk"]["unready_version_count"] == 1
        assert payload["audit_risk"]["release_gate_block_count"] == 1
        codes = {item["code"] for item in payload["risk_items"]}
        assert "live_runs_without_release_readiness" in codes
        assert "release_gate_block" in codes
        assert "high_risk_tools_present" in codes
    finally:
        app.dependency_overrides.clear()


def test_api_governance_cost_report_summarizes_tokens_duration_and_human_touches() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        live_run = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform"}})
        shadow_run = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "shadow_mode": True,
            },
        )

        report = get_read(client, "/api/governance/cost-report?workflow_id=new-product-launch")

        assert live_run.status_code == 200
        assert shadow_run.status_code == 200
        assert report.status_code == 200
        payload = report.json()
        assert payload["workflow_count"] == 1
        assert payload["run_count"] == 2
        assert payload["live_run_count"] == 1
        assert payload["shadow_run_count"] == 1
        assert payload["trace_count"] == 14
        assert payload["estimated_input_tokens"] > 0
        assert payload["estimated_output_tokens"] > 0
        assert payload["estimated_total_tokens"] == (
            payload["estimated_input_tokens"] + payload["estimated_output_tokens"]
        )
        assert payload["human_touch_count"] == 1
        assert payload["node_costs"]
        decision_node = next(item for item in payload["node_costs"] if item["node_id"] == "go-no-go-decision")
        assert decision_node["trace_count"] == 2
        assert decision_node["human_touch_count"] == 1
        assert {item["code"] for item in payload["cost_items"]} >= {
            "human_touch_cost",
            "highest_token_node",
        }
    finally:
        app.dependency_overrides.clear()


def test_api_governance_quality_report_summarizes_eval_readiness_and_suggestions() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        live_run = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform"}})
        shadow_run = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "shadow_mode": True,
            },
        )
        eval_run = client.post("/api/workflows/new-product-launch/evals/run", headers=admin_headers(client))
        comparison = client.post(
            f"/api/runs/{shadow_run.json()['run_id']}/shadow-comparisons",
            headers=admin_headers(client),
            json={
                "expected_output": {
                    "go-no-go-decision": {
                        "decision": "shadow_draft_created",
                        "shadow_mode": True,
                    }
                },
                "pass_threshold": 1.0,
            },
        )

        report = get_read(client, "/api/governance/quality-report?workflow_id=new-product-launch")

        assert live_run.status_code == 200
        assert shadow_run.status_code == 200
        assert eval_run.status_code == 200
        assert comparison.status_code == 200
        assert report.status_code == 200
        payload = report.json()
        assert payload["workflow_count"] == 1
        assert payload["run_count"] == 2
        assert payload["trace_count"] == 14
        assert payload["quality_level"] == "healthy"
        assert payload["quality_score"] >= 90
        assert payload["node_success_rate"] > 0.9
        assert payload["eval_result_count"] == 3
        assert payload["eval_pass_count"] == 3
        assert payload["eval_fail_count"] == 0
        assert payload["eval_pass_rate"] == 1.0
        assert payload["shadow_comparison_count"] == 1
        assert payload["passing_shadow_comparison_count"] == 1
        assert payload["release_ready_version_count"] == 1
        assert payload["unready_version_count"] == 0
        assert payload["blocking_reason_counts"] == {}
        assert payload["optimization_suggestion_count"] == 1
        assert payload["suggestion_type_counts"]["tool_policy_issue"] == 1
        assert payload["failed_node_counts"] == []
        assert {item["code"] for item in payload["quality_items"]} == {
            "optimization_suggestions_available",
        }
    finally:
        app.dependency_overrides.clear()


def test_api_workflow_repair_plan_returns_low_sensitive_operations() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        failed_trace = TraceRecord(
            run_id="repair-run",
            workflow_id="new-product-launch",
            workflow_version="0.1.0",
            node_id="business-case",
            status=NodeExecutionStatus.FAILED,
            error="missing decision field with token raw-secret-token",
        )
        repository.save_run(
            WorkflowRun(
                run_id="repair-run",
                workflow_id="new-product-launch",
                workflow_version="0.1.0",
                status=WorkflowRunStatus.FAILED,
                traces=[failed_trace],
            )
        )
        repository.save_eval_results(
            "new-product-launch",
            [
                EvalResult(
                    eval_id="eval-fail-1",
                    workflow_id="new-product-launch",
                    score=0.1,
                    passed=False,
                    reason="api_key leaked in output",
                    details={"workflow_version": "0.1.0"},
                )
            ],
        )

        response = get_read(client, "/api/workflows/new-product-launch/repair-plan?version=0.1.0")

        assert response.status_code == 200
        payload = response.json()
        assert payload["workflow_id"] == "new-product-launch"
        assert payload["workflow_version"] == "0.1.0"
        assert {operation["target_type"] for operation in payload["operations"]} == {
            "data_contract",
            "eval_specs",
        }
        contract_operation = next(
            operation for operation in payload["operations"] if operation["target_type"] == "data_contract"
        )
        assert contract_operation["target_id"] == "contract-business-case"
        assert contract_operation["proposed_changes"]["add_regression_eval_for_node"] == "business-case"
        assert "raw-secret-token" not in response.text
        assert "api_key leaked" not in response.text
        assert "[redacted-sensitive-evidence]" in response.text
    finally:
        app.dependency_overrides.clear()


def test_api_apply_repair_plan_creates_candidate_version_without_promoting() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        failed_trace = TraceRecord(
            run_id="repair-run",
            workflow_id="new-product-launch",
            workflow_version="0.1.0",
            node_id="business-case",
            status=NodeExecutionStatus.FAILED,
            error="missing decision field",
        )
        repository.save_run(
            WorkflowRun(
                run_id="repair-run",
                workflow_id="new-product-launch",
                workflow_version="0.1.0",
                status=WorkflowRunStatus.FAILED,
                traces=[failed_trace],
            )
        )
        repository.save_eval_results(
            "new-product-launch",
            [
                EvalResult(
                    eval_id="eval-fail-1",
                    workflow_id="new-product-launch",
                    score=0.1,
                    passed=False,
                    reason="golden eval failed expected output or scoring rules",
                    details={"workflow_version": "0.1.0"},
                )
            ],
        )

        response = client.post(
            "/api/workflows/new-product-launch/repair-plan/apply",
            headers=admin_headers(client),
            json={
                "source_version": "0.1.0",
                "target_version": "0.1.1-repair",
                "reason": "repair failed business-case contract and regression coverage",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        selected_operation_id = next(
            operation["operation_id"]
            for operation in payload["repair_plan"]["operations"]
            if operation["target_type"] == "data_contract"
        )
        preview = client.post(
            "/api/workflows/new-product-launch/repair-plan/preview",
            headers=admin_headers(client),
            json={
                "source_version": "0.1.0",
                "target_version": "0.1.2-contract-only",
                "reason": "preview contract-only repair",
                "selected_operation_ids": [selected_operation_id],
            },
        )
        assert repository.get_workflow_version("new-product-launch", "0.1.2-contract-only") is None
        selected_response = client.post(
            "/api/workflows/new-product-launch/repair-plan/apply",
            headers=admin_headers(client),
            json={
                "source_version": "0.1.0",
                "target_version": "0.1.2-contract-only",
                "reason": "repair only the failed contract",
                "selected_operation_ids": [selected_operation_id],
            },
        )
        invalid_selection = client.post(
            "/api/workflows/new-product-launch/repair-plan/apply",
            headers=admin_headers(client),
            json={
                "source_version": "0.1.0",
                "target_version": "0.1.3-invalid-selection",
                "reason": "invalid repair operation selection",
                "selected_operation_ids": ["not-in-current-plan"],
            },
        )
        duplicate = client.post(
            "/api/workflows/new-product-launch/repair-plan/apply",
            headers=admin_headers(client),
            json={
                "source_version": "0.1.0",
                "target_version": "0.1.1-repair",
                "reason": "duplicate repair candidate",
            },
        )

        assert payload["saved_as_current"] is False
        assert payload["source_version"] == "0.1.0"
        assert payload["target_version"] == "0.1.1-repair"
        assert payload["validation_report"]["valid"] is True
        assert payload["impact_preview"]["release_gate_impacts"] == [
            "quality_gate",
            "version_eval_results",
        ]
        candidate = payload["workflow_package"]
        assert candidate["version"] == "0.1.1-repair"
        business_contract = next(
            contract for contract in candidate["data_contracts"] if contract["contract_id"] == "contract-business-case"
        )
        assert "failed trace reason codes must map to explicit validation rules" in business_contract["validation_rules"]
        assert business_contract["error_policy"] == "pause_or_fail_fast_with_low_sensitive_reason_code_before_downstream_execution"
        assert any(eval_spec["eval_type"] == "regression" for eval_spec in candidate["eval_specs"])
        assert repository.get_workflow("new-product-launch").version == "0.1.0"
        assert repository.get_workflow_version("new-product-launch", "0.1.1-repair") is not None
        assert preview.status_code == 200
        preview_payload = preview.json()
        assert preview_payload["source_version"] == "0.1.0"
        assert preview_payload["target_version"] == "0.1.2-contract-only"
        assert preview_payload["target_version_available"] is True
        assert preview_payload["selected_operation_ids"] == [selected_operation_id]
        assert preview_payload["validation_report"]["valid"] is True
        assert preview_payload["change_count"] > 0
        assert preview_payload["impact_preview"]["change_count"] == preview_payload["change_count"]
        assert preview_payload["impact_preview"]["validation_impact"]["valid"] is True
        assert preview_payload["impact_preview"]["impacted_sections"] == ["data_contracts", "package_metadata"]
        assert preview_payload["impact_preview"]["release_gate_impacts"] == ["quality_gate", "version_eval_results"]
        assert preview_payload["impact_preview"]["risk_counts"]["medium"] > 0
        assert all("from" not in item and "to" not in item for item in preview_payload["impact_preview"]["field_impacts"])
        contract_impact = next(
            item
            for item in preview_payload["impact_preview"]["operation_impacts"]
            if item["target_type"] == "data_contract"
        )
        assert contract_impact["operation_id"] == selected_operation_id
        assert contract_impact["risk_level"] == "medium"
        assert selected_response.status_code == 200
        selected_candidate = selected_response.json()["workflow_package"]
        selected_contract = next(
            contract
            for contract in selected_candidate["data_contracts"]
            if contract["contract_id"] == "contract-business-case"
        )
        assert "failed trace reason codes must map to explicit validation rules" in selected_contract["validation_rules"]
        assert not any(eval_spec["eval_type"] == "regression" for eval_spec in selected_candidate["eval_specs"])
        occupied_preview = client.post(
            "/api/workflows/new-product-launch/repair-plan/preview",
            headers=admin_headers(client),
            json={
                "source_version": "0.1.0",
                "target_version": "0.1.2-contract-only",
                "reason": "preview occupied target",
                "selected_operation_ids": [selected_operation_id],
            },
        )
        assert occupied_preview.status_code == 200
        assert occupied_preview.json()["target_version_available"] is False
        assert invalid_selection.status_code == 422
        assert invalid_selection.json()["detail"]["unknown_operation_ids"] == ["not-in-current-plan"]
        audit_events = repository.list_audit_events(
            workflow_id="new-product-launch",
            event_type="workflow_package_repair_candidate",
        )
        assert any(event.status == "succeeded" for event in audit_events)
        succeeded_event = next(event for event in audit_events if event.status == "succeeded")
        assert succeeded_event.details["impact_summary"]["impacted_sections"]
        assert "field_impacts" not in succeeded_event.details["impact_summary"]
        assert any(
            event.status == "failed" and event.details.get("gate") == "operation_selection"
            for event in audit_events
        )
        assert duplicate.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_api_run_start_is_idempotent_for_retried_requests() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        request_payload = {
            "input_payload": {"product": "AI workflow platform"},
            "idempotency_key": "launch-run-001",
            "max_retries": 1,
        }

        first = start_workflow_run(client, request_payload)
        replay = start_workflow_run(client, request_payload)
        conflict = start_workflow_run(
            client,
            {
                "input_payload": {"product": "Different product"},
                "idempotency_key": "launch-run-001",
                "max_retries": 1,
            },
        )

        assert first.status_code == 200
        assert replay.status_code == 200
        assert conflict.status_code == 409
        assert first.json()["run_id"] == replay.json()["run_id"]
        assert first.json()["idempotency_key"] == "launch-run-001"
        assert len(repository.list_runs("new-product-launch")) == 1
        replay_events = get_read(
            client,
            "/api/governance/audit-events?workflow_id=new-product-launch&event_type=workflow_run_idempotency_replay",
        )
        assert replay_events.status_code == 200
        assert {event["status"] for event in replay_events.json()} == {"succeeded", "failed"}
        failed_event = next(event for event in replay_events.json() if event["status"] == "failed")
        assert failed_event["details"]["gate"] == "idempotency_conflict"
    finally:
        app.dependency_overrides.clear()


def test_api_write_run_and_eval_routes_require_scopes() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        payload = load_example_payload()

        anonymous_import = client.post("/api/workflows/import", json=payload)
        assert anonymous_import.status_code == 401
        assert repository.get_workflow("new-product-launch") is None

        read_only_actor = ActorContext(
            actor_id="read-only-1",
            role="workflow-admin",
            scopes=["workflow:read"],
        )
        forbidden_generate = client.post(
            "/api/workflows/generate",
            headers=actor_token_headers(read_only_actor),
            json={"user_request": "我想搭建一个新品上市流程智能体"},
        )
        assert forbidden_generate.status_code == 403
        assert forbidden_generate.json()["detail"]["required_scope"] == "workflow:write"

        generated = client.post(
            "/api/workflows/generate",
            headers=admin_headers(client),
            json={"user_request": "我想搭建一个新品上市流程智能体"},
        )
        assert generated.status_code == 200
        assert generated.json()["workflow_package"]["workflow_id"] == "new-product-launch"

        assert import_workflow(client, payload).json()["valid"] is True
        forbidden_run = client.post(
            "/api/workflows/new-product-launch/runs",
            headers=actor_token_headers(read_only_actor),
            json={"input_payload": {"product": "AI workflow platform"}},
        )
        assert forbidden_run.status_code == 403
        assert forbidden_run.json()["detail"]["required_scope"] == "workflow:run"

        forbidden_eval = client.post(
            "/api/workflows/new-product-launch/evals/run",
            headers=actor_token_headers(read_only_actor),
        )
        assert forbidden_eval.status_code == 403
        assert forbidden_eval.json()["detail"]["required_scope"] == "workflow:evaluate"
        forbidden_version_eval = client.post(
            "/api/workflows/new-product-launch/versions/0.1.0/evals/run",
            headers=actor_token_headers(read_only_actor),
        )
        assert forbidden_version_eval.status_code == 403
        assert forbidden_version_eval.json()["detail"]["required_scope"] == "workflow:evaluate"
        allowed_eval = client.post("/api/workflows/new-product-launch/evals/run", headers=admin_headers(client))
        assert allowed_eval.status_code == 200
        assert allowed_eval.json()[0]["passed"] is True
        assert allowed_eval.json()[0]["details"]["workflow_version"] == "0.1.0"
        audit_events = get_read(client, "/api/governance/audit-events?workflow_id=new-product-launch")
        events_by_type = {event["event_type"]: event for event in audit_events.json()}
        assert events_by_type["workflow_package_generation"]["actor_id"] == "admin-1"
        assert events_by_type["workflow_package_generation"]["details"]["request_length"] > 0
        assert events_by_type["workflow_package_import"]["actor_id"] == "admin-1"
        assert events_by_type["workflow_eval_run"]["details"]["eval_count"] == len(allowed_eval.json())
        assert events_by_type["workflow_eval_run"]["details"]["failed_count"] == 0
    finally:
        app.dependency_overrides.clear()


def test_api_workflow_run_forwards_actor_role_to_tool_policy_checks() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        assert import_workflow(client, load_example_payload()).status_code == 200

        operator = ActorContext(
            actor_id="operator-1",
            role="workflow-operator",
            display_name="Workflow Operator",
            scopes=["workflow:run"],
        )
        run = client.post(
            "/api/workflows/new-product-launch/runs",
            headers=actor_token_headers(operator),
            json={"input_payload": {"product": "AI workflow platform"}},
        )

        assert run.status_code == 200
        assert run.json()["status"] == "failed"
        assert "actor role is not allowed" in run.json()["traces"][0]["error"]
        persisted_run = repository.get_run(run.json()["run_id"])
        assert persisted_run is not None
        assert "mock-define-launch-goal-tool" in (persisted_run.traces[0].error or "")
    finally:
        app.dependency_overrides.clear()


def test_api_runs_evals_against_candidate_workflow_version() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        current_payload = load_example_payload()
        candidate_payload = load_example_payload()
        candidate_payload["version"] = "0.2.0"
        candidate_payload["name"] = "新品上市流程智能体 candidate"
        candidate_payload["process_spec"]["version"] = "0.2.0"

        import_workflow(client, current_payload)
        candidate_import = client.post(
            "/api/workflows/import?save_as_current=false",
            headers=admin_headers(client),
            json=candidate_payload,
        )
        candidate_eval = client.post(
            "/api/workflows/new-product-launch/versions/0.2.0/evals/run",
            headers=admin_headers(client),
        )

        assert candidate_import.status_code == 200
        assert candidate_import.json()["saved_as_current"] is False
        assert candidate_eval.status_code == 200
        assert all(result["details"]["workflow_version"] == "0.2.0" for result in candidate_eval.json())
        assert repository.get_workflow("new-product-launch").version == "0.1.0"
        assert repository.get_workflow_version("new-product-launch", "0.2.0") is not None
        persisted_results = repository.list_eval_results("new-product-launch")
        assert persisted_results
        assert all(result.details["workflow_version"] == "0.2.0" for result in persisted_results)
        audit_events = repository.list_audit_events(
            workflow_id="new-product-launch",
            event_type="workflow_eval_run",
        )
        assert audit_events
        assert audit_events[0].workflow_version == "0.2.0"
        assert audit_events[0].details["eval_count"] == len(candidate_eval.json())
    finally:
        app.dependency_overrides.clear()


def test_api_approval_requires_actor_headers_and_allowed_role() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        run = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform"}})
        run_id = run.json()["run_id"]

        missing_actor = client.post(f"/api/runs/{run_id}/approval", json={"approved": True})
        assert missing_actor.status_code == 401

        wrong_role = client.post(
            f"/api/runs/{run_id}/approval",
            headers={"X-Actor-Id": "u-2", "X-Actor-Role": "market-analyst"},
            json={"approved": True},
        )
        assert wrong_role.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_api_approval_requires_approve_scope_even_for_allowed_role() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        run = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform"}})
        run_id = run.json()["run_id"]
        actor = ActorContext(
            actor_id="approver-without-scope",
            role="business-approver",
            scopes=["workflow:read"],
        )

        response = client.post(
            f"/api/runs/{run_id}/approval",
            headers=actor_token_headers(actor),
            json={"approved": True},
        )

        assert response.status_code == 403
        assert response.json()["detail"]["required_scope"] == "workflow:approve"
        assert get_read(client, f"/api/runs/{run_id}").json()["status"] == "paused"
    finally:
        app.dependency_overrides.clear()


def test_api_cancel_paused_run_requires_cancel_scope_and_audits() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        run = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform"}})
        run_id = run.json()["run_id"]

        approver_headers = token_headers(client, "approver", "approver")
        forbidden = client.post(
            f"/api/runs/{run_id}/cancel",
            headers=approver_headers,
            json={"reason": "duplicate launch request"},
        )
        canceled = client.post(
            f"/api/runs/{run_id}/cancel",
            headers=admin_headers(client),
            json={"reason": "duplicate launch request"},
        )
        approval_after_cancel = client.post(
            f"/api/runs/{run_id}/approval",
            headers=approver_headers,
            json={"approved": True},
        )
        second_cancel = client.post(
            f"/api/runs/{run_id}/cancel",
            headers=admin_headers(client),
            json={"reason": "already canceled"},
        )

        assert forbidden.status_code == 403
        assert forbidden.json()["detail"]["required_scope"] == "workflow:cancel"
        assert canceled.status_code == 200
        assert canceled.json()["status"] == "canceled"
        assert canceled.json()["output_payload"]["canceled"] is True
        assert canceled.json()["traces"][-1]["status"] == "skipped"
        assert canceled.json()["traces"][-1]["error"] == "workflow run canceled"
        assert approval_after_cancel.status_code == 409
        assert second_cancel.status_code == 409

        audit_events = get_read(client, f"/api/governance/audit-events?run_id={run_id}&event_type=workflow_run_cancel")
        assert audit_events.status_code == 200
        assert len(audit_events.json()) == 1
        assert audit_events.json()[0]["actor_id"] == "admin-1"
        assert audit_events.json()[0]["reason"] == "duplicate launch request"
        assert audit_events.json()[0]["details"]["previous_status"] == "paused"
        assert audit_events.json()[0]["details"]["result_status"] == "canceled"
    finally:
        app.dependency_overrides.clear()


def test_api_reruns_terminal_run_against_original_version_and_audits() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        failed = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "max_steps": 1,
            },
        )
        source_run_id = failed.json()["run_id"]

        rerun = client.post(
            f"/api/runs/{source_run_id}/rerun",
            headers=admin_headers(client),
            json={
                "reason": "retry after increasing step budget",
                "idempotency_key": "rerun-001",
                "max_steps": 50,
            },
        )
        replay = client.post(
            f"/api/runs/{source_run_id}/rerun",
            headers=admin_headers(client),
            json={
                "reason": "retry after increasing step budget",
                "idempotency_key": "rerun-001",
                "max_steps": 50,
            },
        )
        conflict = client.post(
            f"/api/runs/{source_run_id}/rerun",
            headers=admin_headers(client),
            json={
                "reason": "different rerun budget",
                "idempotency_key": "rerun-001",
                "max_steps": 20,
            },
        )

        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        assert rerun.status_code == 200
        assert rerun.json()["status"] == "paused"
        assert rerun.json()["rerun_of_run_id"] == source_run_id
        assert rerun.json()["workflow_version"] == failed.json()["workflow_version"]
        assert replay.status_code == 200
        assert replay.json()["run_id"] == rerun.json()["run_id"]
        assert conflict.status_code == 409
        assert len(repository.list_runs("new-product-launch")) == 2

        audit_events = get_read(
            client,
            f"/api/governance/audit-events?workflow_id=new-product-launch&event_type=workflow_run_rerun",
        )
        assert audit_events.status_code == 200
        assert any(
            event["action"] == "rerun" and event["status"] == "succeeded"
            for event in audit_events.json()
        )
        assert any(
            event["action"] == "replay" and event["status"] == "succeeded"
            for event in audit_events.json()
        )
        assert any(
            event["action"] == "replay" and event["status"] == "failed"
            for event in audit_events.json()
        )
        succeeded = next(event for event in audit_events.json() if event["action"] == "rerun")
        assert succeeded["details"]["source_run_id"] == source_run_id
        assert succeeded["details"]["rerun_run_id"] == rerun.json()["run_id"]
        assert succeeded["details"]["max_steps"] == 50
    finally:
        app.dependency_overrides.clear()


def test_api_rerun_requires_run_scope_and_terminal_source() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())
        paused = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform"}})
        run_id = paused.json()["run_id"]

        forbidden = client.post(
            f"/api/runs/{run_id}/rerun",
            headers=token_headers(client, "approver", "approver"),
            json={"reason": "should not be allowed"},
        )
        non_terminal = client.post(
            f"/api/runs/{run_id}/rerun",
            headers=admin_headers(client),
            json={"reason": "still paused"},
        )

        assert forbidden.status_code == 403
        assert forbidden.json()["detail"]["required_scope"] == "workflow:run"
        assert non_terminal.status_code == 409
        assert non_terminal.json()["detail"]["status"] == "paused"
        audit_events = get_read(
            client,
            f"/api/governance/audit-events?run_id={run_id}&event_type=workflow_run_rerun",
        )
        assert audit_events.status_code == 200
        assert audit_events.json()[0]["status"] == "failed"
        assert audit_events.json()[0]["details"]["gate"] == "source_status"
    finally:
        app.dependency_overrides.clear()


def test_api_lists_versions_and_diffs_workflow_packages() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        first_payload = load_example_payload()
        second_payload = load_example_payload()
        second_payload["version"] = "0.2.0"
        second_payload["name"] = "新品上市流程智能体 v2"
        second_payload["process_spec"]["version"] = "0.2.0"

        assert import_workflow(client, first_payload).json()["valid"] is True
        assert import_workflow(client, second_payload).json()["valid"] is True

        versions = get_read(client, "/api/workflows/new-product-launch/versions")
        assert versions.status_code == 200
        assert {item["version"] for item in versions.json()} == {"0.1.0", "0.2.0"}

        historical = get_read(client, "/api/workflows/new-product-launch/versions/0.1.0")
        assert historical.status_code == 200
        assert historical.json()["name"] == "新品上市流程智能体"

        diff = get_read(client, "/api/workflows/new-product-launch/diff?from_version=0.1.0&to_version=0.2.0")
        assert diff.status_code == 200
        assert diff.json()["change_count"] >= 2
        assert "$.name" in {change["path"] for change in diff.json()["changes"]}
    finally:
        app.dependency_overrides.clear()


def test_api_validate_and_import_reject_quality_gate_errors() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        payload = load_example_payload()
        payload["eval_specs"] = []

        read_only_actor = ActorContext(
            actor_id="read-only-1",
            role="workflow-admin",
            scopes=["workflow:read"],
        )
        anonymous_validation = client.post("/api/workflows/validate", json=payload)
        forbidden_validation = client.post(
            "/api/workflows/validate",
            headers=actor_token_headers(read_only_actor),
            json=payload,
        )
        validation = client.post("/api/workflows/validate", headers=admin_headers(client), json=payload)

        assert anonymous_validation.status_code == 401
        assert forbidden_validation.status_code == 403
        assert forbidden_validation.json()["detail"]["required_scope"] == "workflow:write"
        assert validation.status_code == 200
        assert validation.json()["valid"] is False
        assert validation.json()["errors"][0]["code"] == "missing_eval_specs"

        imported = import_workflow(client, payload)
        assert imported.status_code == 200
        assert imported.json()["valid"] is False
        assert imported.json()["errors"][0]["code"] == "missing_eval_specs"
        assert repository.get_workflow("new-product-launch") is None
        audit_events = get_read(client, "/api/governance/audit-events?workflow_id=new-product-launch")
        assert audit_events.status_code == 200
        assert audit_events.json()[0]["event_type"] == "workflow_package_import"
        assert audit_events.json()[0]["status"] == "failed"
        assert audit_events.json()[0]["details"]["validation_report"]["errors"][0]["code"] == "missing_eval_specs"
    finally:
        app.dependency_overrides.clear()


def test_paused_run_resumes_against_original_workflow_version_after_current_version_changes() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        first_payload = load_example_payload()
        second_payload = load_example_payload()
        second_payload["version"] = "0.2.0"
        second_payload["name"] = "新品上市流程智能体 v2"
        second_payload["process_spec"]["version"] = "0.2.0"
        for policy in second_payload["tool_policies"]:
            if policy["tool_id"] == "mock-go-no-go-decision-tool":
                policy["allowed_roles"] = ["workflow-admin"]

        import_workflow(client, first_payload)
        run = start_workflow_run(
            client,
            {"input_payload": {"product": "AI workflow platform"}, "workflow_version": "0.1.0"},
        )
        assert run.status_code == 200
        assert run.json()["workflow_version"] == "0.1.0"
        run_id = run.json()["run_id"]

        import_workflow(client, second_payload)
        current = get_read(client, "/api/workflows/new-product-launch")
        assert current.json()["version"] == "0.2.0"

        approved = client.post(
            f"/api/runs/{run_id}/approval",
            headers=token_headers(client, "approver", "approver"),
            json={"approved": True},
        )

        assert approved.status_code == 200
        assert approved.json()["status"] == "completed"
        assert approved.json()["workflow_version"] == "0.1.0"
    finally:
        app.dependency_overrides.clear()


def test_api_promotes_workflow_version_with_admin_actor() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        first_payload = load_example_payload()
        second_payload = load_example_payload()
        second_payload["version"] = "0.2.0"
        second_payload["name"] = "新品上市流程智能体 v2"
        second_payload["process_spec"]["version"] = "0.2.0"

        import_workflow(client, first_payload)
        import_workflow(client, second_payload)
        assert get_read(client, "/api/workflows/new-product-launch").json()["version"] == "0.2.0"

        forbidden = client.post(
            "/api/workflows/new-product-launch/versions/0.1.0/promote",
            headers={"X-Actor-Id": "u-1", "X-Actor-Role": "business-approver"},
            json={"reason": "rollback"},
        )
        assert forbidden.status_code == 403

        promoted = client.post(
            "/api/workflows/new-product-launch/versions/0.1.0/promote",
            headers=token_headers(client, "admin", "admin"),
            json={
                "reason": "rollback",
                "change_summary": "回滚到稳定版本。",
                "risk_acceptance": "接受回滚后需重新 shadow 校验的发布风险。",
                "reviewed_diff": True,
                "readiness_acknowledged": True,
            },
        )
        assert promoted.status_code == 200
        assert promoted.json()["workflow_package"]["version"] == "0.1.0"
        assert promoted.json()["promotion"]["actor_id"] == "admin-1"
        assert promoted.json()["release_context"]["current_version"] == "0.2.0"
        assert promoted.json()["release_context"]["target_version"] == "0.1.0"
        assert promoted.json()["release_context"]["change_summary_present"] is True
        assert all(result["passed"] for result in promoted.json()["eval_results"])

        current = get_read(client, "/api/workflows/new-product-launch")
        assert current.json()["version"] == "0.1.0"
        eval_results = get_read(client, "/api/governance/eval-results?workflow_id=new-product-launch")
        assert eval_results.status_code == 200
        assert eval_results.json()
        assert repository.list_eval_results("new-product-launch")
        assert all(result.passed for result in repository.list_eval_results("new-product-launch"))

        audit_events = get_read(client, "/api/governance/audit-events?workflow_id=new-product-launch")
        promotion_events = [
            event for event in audit_events.json()
            if event["event_type"] == "workflow_version_promotion"
        ]
        assert promotion_events
        assert promotion_events[0]["actor_id"] == "admin-1"
        assert promotion_events[0]["workflow_version"] == "0.1.0"
        assert promotion_events[0]["details"]["release_context"]["risk_acceptance_present"] is True

        run = start_workflow_run(client, {"input_payload": {"product": "AI workflow platform"}})
        assert run.json()["workflow_version"] == "0.1.0"
    finally:
        app.dependency_overrides.clear()


def test_api_promote_requires_promote_scope_even_for_admin_role() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        first_payload = load_example_payload()
        second_payload = load_example_payload()
        second_payload["version"] = "0.2.0"
        second_payload["name"] = "新品上市流程智能体 v2"
        second_payload["process_spec"]["version"] = "0.2.0"
        import_workflow(client, first_payload)
        import_workflow(client, second_payload)
        actor = ActorContext(
            actor_id="admin-without-promote-scope",
            role="workflow-admin",
            scopes=["workflow:read", "workflow:approve"],
        )

        response = client.post(
            "/api/workflows/new-product-launch/versions/0.1.0/promote",
            headers=actor_token_headers(actor),
            json={"reason": "rollback"},
        )

        assert response.status_code == 403
        assert response.json()["detail"]["required_scope"] == "workflow:promote"
        assert repository.get_workflow("new-product-launch").version == "0.2.0"
    finally:
        app.dependency_overrides.clear()


def test_api_run_supports_retry_budget() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())

        run = start_workflow_run(
            client,
            {
                "input_payload": {
                    "product": "AI workflow platform",
                    "transient_failures": {"define-launch-goal": 1},
                },
                "max_retries": 1,
            },
        )

        assert run.status_code == 200
        assert run.json()["status"] == "paused"
        assert run.json()["traces"][0]["retryable"] is True
        assert run.json()["traces"][0]["attempt"] == 1
        assert run.json()["traces"][1]["attempt"] == 2
    finally:
        app.dependency_overrides.clear()


def test_api_run_supports_bounded_max_steps_budget() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        import_workflow(client, load_example_payload())

        run = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "max_steps": 1,
            },
        )
        invalid_budget = start_workflow_run(
            client,
            {
                "input_payload": {"product": "AI workflow platform"},
                "max_steps": 201,
            },
        )

        assert run.status_code == 200
        assert run.json()["status"] == "failed"
        assert run.json()["output_payload"]["error"] == "max_steps_exceeded"
        assert len(run.json()["traces"]) == 1
        assert invalid_budget.status_code == 422

        audit_events = get_read(client, "/api/governance/audit-events?workflow_id=new-product-launch")
        start_events = [event for event in audit_events.json() if event["event_type"] == "workflow_run_start"]
        assert start_events[0]["details"]["max_steps"] == 1
        assert start_events[0]["details"]["result_status"] == "failed"
    finally:
        app.dependency_overrides.clear()


def test_api_promote_rejects_invalid_saved_version_before_setting_current() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        valid_payload = load_example_payload()
        invalid_payload = load_example_payload()
        invalid_payload["version"] = "0.2.0"
        invalid_payload["eval_specs"] = []

        import_workflow(client, valid_payload)
        repository._workflow_versions["new-product-launch"]["0.2.0"] = WorkflowPackage.model_validate(invalid_payload)
        assert repository.get_workflow("new-product-launch").version == "0.1.0"

        promoted = client.post(
            "/api/workflows/new-product-launch/versions/0.2.0/promote",
            headers=token_headers(client, "admin", "admin"),
            json={"reason": "try invalid version"},
        )

        assert promoted.status_code == 422
        assert promoted.json()["detail"]["errors"][0]["code"] == "missing_eval_specs"
        assert repository.get_workflow("new-product-launch").version == "0.1.0"
        audit_events = get_read(client, "/api/governance/audit-events?workflow_id=new-product-launch")
        failed_promotion_events = [
            event for event in audit_events.json()
            if event["event_type"] == "workflow_version_promotion" and event["status"] == "failed"
        ]
        assert failed_promotion_events
        assert failed_promotion_events[0]["workflow_version"] == "0.2.0"
        assert failed_promotion_events[0]["reason"] == "try invalid version"
        assert failed_promotion_events[0]["details"]["gate"] == "quality"
        assert (
            failed_promotion_events[0]["details"]["validation_report"]["errors"][0]["code"]
            == "missing_eval_specs"
        )
    finally:
        app.dependency_overrides.clear()


def test_api_promote_rejects_version_when_eval_gate_fails() -> None:
    repository = MemoryWorkflowRepository()
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    try:
        client = TestClient(app)
        valid_payload = load_example_payload()
        failing_payload = load_example_payload()
        failing_payload["version"] = "0.2.0"
        failing_payload["name"] = "新品上市流程智能体 failing eval"
        failing_payload["process_spec"]["version"] = "0.2.0"
        failing_payload["eval_specs"][0]["scoring_rules"] = ["force_fail"]

        import_workflow(client, valid_payload)
        repository._workflow_versions["new-product-launch"]["0.2.0"] = WorkflowPackage.model_validate(failing_payload)
        assert repository.get_workflow("new-product-launch").version == "0.1.0"

        promoted = client.post(
            "/api/workflows/new-product-launch/versions/0.2.0/promote",
            headers=token_headers(client, "admin", "admin"),
            json={"reason": "should fail eval gate"},
        )

        assert promoted.status_code == 422
        assert promoted.json()["detail"]["gate"] == "eval"
        assert promoted.json()["detail"]["failed_eval_results"][0]["passed"] is False
        assert repository.get_workflow("new-product-launch").version == "0.1.0"
        assert repository.list_eval_results("new-product-launch")
        assert repository.list_eval_results("new-product-launch")[0].passed is False
        audit_events = get_read(client, "/api/governance/audit-events?workflow_id=new-product-launch")
        failed_promotion_events = [
            event for event in audit_events.json()
            if event["event_type"] == "workflow_version_promotion" and event["status"] == "failed"
        ]
        assert failed_promotion_events
        assert failed_promotion_events[0]["workflow_version"] == "0.2.0"
        assert failed_promotion_events[0]["reason"] == "should fail eval gate"
        assert failed_promotion_events[0]["details"]["gate"] == "eval"
        assert failed_promotion_events[0]["details"]["failed_count"] == 1
        assert failed_promotion_events[0]["details"]["failed_eval_results"][0]["passed"] is False
    finally:
        app.dependency_overrides.clear()
