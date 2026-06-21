from __future__ import annotations

from pathlib import Path

from packages.workflow_core.adapters import HttpJSONLLMClient, LLMClient, MockLLMClient
from packages.workflow_core.builder import WorkflowBuilder
from packages.workflow_core.governance import EvalRunner, MetricCollector, OptimizationLoop, TraceStore
from packages.workflow_core.models import WorkflowPackage
from packages.workflow_core.runtime import WorkflowRunner
from packages.workflow_core.storage import SQLiteWorkflowRepository, WorkflowRepository

from apps.api.settings import settings


class UnavailableLLMClient:
    def __init__(self, provider: str, model: str, message: str) -> None:
        self.provider = provider
        self.model = model
        self.message = message

    def complete(self, prompt: str) -> str:
        raise RuntimeError(self.message)


def _build_repository() -> SQLiteWorkflowRepository:
    repo = SQLiteWorkflowRepository(settings.database_url)
    if settings.seed_example_workflow:
        _seed_example_workflow(repo)
    return repo


def _seed_example_workflow(repo: WorkflowRepository) -> None:
    if repo.get_workflow("new-product-launch") is not None:
        return
    example_path = Path(__file__).parents[2] / "examples" / "new_product_launch.workflow.json"
    if not example_path.exists():
        return
    workflow_package = WorkflowPackage.model_validate_json(example_path.read_text(encoding="utf-8"))
    repo.save_workflow(workflow_package)


def _build_llm_client() -> LLMClient:
    if settings.llm_provider == "mock":
        return MockLLMClient()
    if settings.llm_provider in {"http", "openai-compatible", "agnes"}:
        if not settings.llm_endpoint or not settings.llm_model:
            return UnavailableLLMClient(
                provider=settings.llm_provider,
                model=settings.llm_model or "",
                message="real LLM mode requires AGENT_WORKFLOW_LLM_ENDPOINT and AGENT_WORKFLOW_LLM_MODEL",
            )
        return HttpJSONLLMClient(
            endpoint=_llm_endpoint_for_provider(settings.llm_provider, settings.llm_endpoint or ""),
            api_key=settings.llm_api_key,
            model=settings.llm_model or "",
            provider=settings.llm_provider,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return UnavailableLLMClient(
        provider=settings.llm_provider,
        model=settings.llm_model or "",
        message=f"unsupported LLM provider: {settings.llm_provider}",
    )


def _llm_endpoint_for_provider(provider: str, endpoint: str) -> str:
    if provider != "agnes":
        return endpoint
    normalized = endpoint.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


repository = _build_repository()
trace_store = TraceStore()
llm_client = _build_llm_client()
workflow_builder = WorkflowBuilder(llm=llm_client)
workflow_runner = WorkflowRunner()
eval_runner = EvalRunner()
metric_collector = MetricCollector()
optimization_loop = OptimizationLoop()


def get_repository() -> WorkflowRepository:
    return repository


def get_trace_store() -> TraceStore:
    return trace_store


def get_workflow_builder() -> WorkflowBuilder:
    return workflow_builder


def get_workflow_runner() -> WorkflowRunner:
    return workflow_runner


def get_eval_runner() -> EvalRunner:
    return eval_runner


def get_metric_collector() -> MetricCollector:
    return metric_collector


def get_optimization_loop() -> OptimizationLoop:
    return optimization_loop
