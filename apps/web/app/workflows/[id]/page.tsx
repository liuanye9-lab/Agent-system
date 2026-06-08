"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Play, Rocket } from "lucide-react";
import { apiFetch, getLocalAuthHeaders } from "../../../lib/api";
import { JsonViewer } from "../../../components/JsonViewer";
import { WorkflowGraph } from "../../../components/WorkflowGraph";

type ProcessNode = {
  node_id: string;
  name: string;
  node_type: string;
  owner_role: string;
  description?: string;
  done_condition?: string;
  requires_approval?: boolean;
  input_contract_id: string;
  output_contract_id: string;
  tool_ids: string[];
};

type ProcessSpec = {
  nodes: ProcessNode[];
  edges: Array<{
    source_node_id: string;
    target_node_id: string;
    condition?: string | null;
    edge_type?: string;
  }>;
  entry_node_id: string;
  terminal_node_ids: string[];
};

type DataContract = {
  contract_id: string;
  name: string;
  [key: string]: unknown;
};

type ToolPolicy = {
  tool_id: string;
  name: string;
  permission_level: string;
  risk_level: string;
  adapter?: string;
  requires_approval?: boolean;
  [key: string]: unknown;
};

type WorkflowPackage = {
  workflow_id: string;
  name: string;
  version: string;
  problem_spec: unknown;
  process_spec: ProcessSpec;
  data_contracts: DataContract[];
  tool_policies: ToolPolicy[];
  agent_specs: unknown;
  eval_specs: unknown;
};

type Run = {
  run_id: string;
  workflow_version?: string | null;
  status: string;
  shadow_mode: boolean;
};

type WorkflowVersion = {
  workflow_id: string;
  name: string;
  version: string;
  created_at: string;
  updated_at: string;
};

type ReleaseReadiness = {
  live_ready: boolean;
  blocking_reasons: string[];
  checks: Array<{
    name: string;
    status: string;
    details: Record<string, unknown>;
  }>;
};

type WorkflowDiff = {
  workflow_id: string;
  from_version: string;
  to_version: string;
  change_count: number;
  changes: Array<{
    op: string;
    path: string;
    from?: unknown;
    to?: unknown;
  }>;
};

export default function WorkflowDetailPage() {
  const params = useParams<{ id: string }>();
  const [workflow, setWorkflow] = useState<WorkflowPackage | null>(null);
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [readiness, setReadiness] = useState<ReleaseReadiness | null>(null);
  const [versionDiff, setVersionDiff] = useState<WorkflowDiff | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [promotingVersion, setPromotingVersion] = useState<string | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  const [evalBusy, setEvalBusy] = useState(false);
  const [evalRunResults, setEvalRunResults] = useState<unknown[] | null>(null);
  const [shadowMode, setShadowMode] = useState(true);
  const [selectedRunVersion, setSelectedRunVersion] = useState("");
  const [inputPayloadText, setInputPayloadText] = useState("{\n  \"product\": \"AI workflow platform\",\n  \"market\": \"global\"\n}");
  const [maxSteps, setMaxSteps] = useState(50);
  const [maxRetries, setMaxRetries] = useState(1);
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [enforceReleaseReadiness, setEnforceReleaseReadiness] = useState(true);
  const [promotionReason, setPromotionReason] = useState("release candidate after review");
  const [promotionChangeSummary, setPromotionChangeSummary] = useState("");
  const [promotionRiskAcceptance, setPromotionRiskAcceptance] = useState("");
  const [promotionReviewedDiff, setPromotionReviewedDiff] = useState(false);
  const [promotionReadinessAcknowledged, setPromotionReadinessAcknowledged] = useState(false);

  const resetVersionReviewState = useCallback(() => {
    setPromotionChangeSummary("");
    setPromotionRiskAcceptance("");
    setPromotionReviewedDiff(false);
    setPromotionReadinessAcknowledged(false);
    setEvalRunResults(null);
  }, []);

  const loadWorkflow = useCallback(async (workflowId: string, preferredVersion?: string) => {
    try {
      const headers = await getLocalAuthHeaders("workflow-admin");
      const [workflowResponse, versionResponse] = await Promise.all([
        apiFetch<WorkflowPackage>(`/api/workflows/${workflowId}`, { headers }),
        apiFetch<WorkflowVersion[]>(`/api/workflows/${workflowId}/versions`, { headers })
      ]);
      setWorkflow(workflowResponse);
      setVersions(versionResponse);
      const versionToSelect =
        preferredVersion && versionResponse.some((version) => version.version === preferredVersion)
          ? preferredVersion
          : workflowResponse.version;
      resetVersionReviewState();
      setSelectedRunVersion(versionToSelect);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load workflow / 加载工作流失败");
    }
  }, [resetVersionReviewState]);

  function selectRunVersion(version: string) {
    resetVersionReviewState();
    setSelectedRunVersion(version);
  }

  useEffect(() => {
    if (!params.id) return;
    loadWorkflow(params.id, getRequestedVersionFromLocation());
  }, [params.id, loadWorkflow]);

  useEffect(() => {
    if (!workflow || !selectedRunVersion) return;
    getLocalAuthHeaders("workflow-admin")
      .then((headers) => {
        const readinessRequest = apiFetch<ReleaseReadiness>(
          `/api/workflows/${workflow.workflow_id}/release-readiness?version=${encodeURIComponent(selectedRunVersion)}`,
          { headers }
        );
        const diffRequest =
          selectedRunVersion === workflow.version
            ? Promise.resolve(null)
            : apiFetch<WorkflowDiff>(
                `/api/workflows/${workflow.workflow_id}/diff?from_version=${encodeURIComponent(workflow.version)}&to_version=${encodeURIComponent(selectedRunVersion)}`,
                { headers }
              );
        return Promise.all([readinessRequest, diffRequest]);
      })
      .then(([readinessResponse, diffResponse]) => {
        setReadiness(readinessResponse);
        setVersionDiff(diffResponse);
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Failed to load release readiness / 加载发布就绪状态失败"));
  }, [workflow, selectedRunVersion]);

  async function runWorkflow() {
    if (!workflow) return;
    let inputPayload: unknown;
    try {
      inputPayload = JSON.parse(inputPayloadText);
    } catch {
      setError("Run input payload must be valid JSON / 运行输入载荷必须是有效 JSON");
      return;
    }
    if (!inputPayload || typeof inputPayload !== "object" || Array.isArray(inputPayload)) {
      setError("Run input payload must be a JSON object / 运行输入载荷必须是 JSON 对象");
      return;
    }
    setRunBusy(true);
    setError(null);
    try {
      const response = await apiFetch<Run>(`/api/workflows/${workflow.workflow_id}/runs`, {
        method: "POST",
        headers: await getLocalAuthHeaders("workflow-admin"),
        body: JSON.stringify({
          input_payload: inputPayload,
          workflow_version: selectedRunVersion || undefined,
          max_steps: maxSteps,
          max_retries: maxRetries,
          shadow_mode: shadowMode,
          enforce_release_readiness: !shadowMode && enforceReleaseReadiness,
          idempotency_key: idempotencyKey.trim() || undefined
        })
      });
      setRun(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to run workflow / 运行工作流失败");
    } finally {
      setRunBusy(false);
    }
  }

  async function promoteVersion(version: string) {
    if (!workflow) return;
    if (!promotionReason.trim()) {
      setError("Promotion reason is required / 提升原因必填");
      return;
    }
    setPromotingVersion(version);
    setError(null);
    try {
      await apiFetch(`/api/workflows/${workflow.workflow_id}/versions/${version}/promote`, {
        method: "POST",
        headers: await getLocalAuthHeaders("workflow-admin"),
        body: JSON.stringify({
          reason: promotionReason.trim(),
          change_summary: promotionChangeSummary.trim() || undefined,
          risk_acceptance: promotionRiskAcceptance.trim() || undefined,
          reviewed_diff: promotionReviewedDiff,
          readiness_acknowledged: promotionReadinessAcknowledged
        })
      });
      await loadWorkflow(workflow.workflow_id);
      setSelectedRunVersion(version);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to promote version / 提升版本失败");
    } finally {
      setPromotingVersion(null);
    }
  }

  async function runVersionEvals() {
    if (!workflow || !selectedRunVersion) return;
    setEvalBusy(true);
    setError(null);
    try {
      const results = await apiFetch<unknown[]>(
        `/api/workflows/${workflow.workflow_id}/versions/${encodeURIComponent(selectedRunVersion)}/evals/run`,
        {
          method: "POST",
          headers: await getLocalAuthHeaders("workflow-admin")
        }
      );
      setEvalRunResults(results);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to run version evals / 运行版本评估失败");
    } finally {
      setEvalBusy(false);
    }
  }

  if (error) return <p className="surface border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p>;
  if (!workflow) return <p className="surface p-4 text-sm text-slate-600">Loading workflow... / 正在加载工作流...</p>;

  return (
    <div className="space-y-6">
      <div className="surface flex flex-wrap items-center justify-between gap-4 p-5">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">Workflow package / 工作流包</p>
          <h1 className="page-heading mt-1">{workflow.name}</h1>
          <p className="page-subtitle">
            {workflow.workflow_id} · v{workflow.version}
          </p>
        </div>
      </div>
      {run ? (
        <div className="surface p-4 text-sm text-slate-700">
          Run / 运行 {run.run_id} · v{run.workflow_version ?? "unknown"} · {run.status} · {run.shadow_mode ? "shadow / 影子" : "live / 正式"} ·{" "}
          <Link className="font-medium text-accent hover:underline" href={`/runs/${run.run_id}`}>
            Open run detail / 打开运行详情
          </Link>
        </div>
      ) : null}
      <WorkflowGraph processSpec={workflow.process_spec} toolPolicies={workflow.tool_policies} dataContracts={workflow.data_contracts} />
      <section className="surface p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-ink">Run Launch / 启动运行</h2>
          <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
            v{selectedRunVersion || workflow.version}
          </span>
        </div>
        <div className="grid gap-4 lg:grid-cols-[0.8fr_1.2fr]">
          <div className="space-y-3">
            <label className="block">
              <span className="mb-2 block text-xs uppercase text-slate-500">Workflow Version / 工作流版本</span>
              <select
                className="control-input w-full"
                value={selectedRunVersion}
                onChange={(event) => selectRunVersion(event.target.value)}
              >
                {versions.map((version) => (
                  <option key={version.version} value={version.version}>
                    {version.version === workflow.version ? `${version.version} (current / 当前)` : version.version}
                  </option>
                ))}
              </select>
            </label>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="block">
                <span className="mb-2 block text-xs uppercase text-slate-500">Max Steps / 最大步数</span>
                <input
                  className="control-input w-full"
                  type="number"
                  min="1"
                  max="200"
                  value={maxSteps}
                  onChange={(event) => setMaxSteps(Number(event.target.value))}
                />
              </label>
              <label className="block">
                <span className="mb-2 block text-xs uppercase text-slate-500">Max Retries / 最大重试</span>
                <input
                  className="control-input w-full"
                  type="number"
                  min="0"
                  max="5"
                  value={maxRetries}
                  onChange={(event) => setMaxRetries(Number(event.target.value))}
                />
              </label>
            </div>
            <label className="block">
              <span className="mb-2 block text-xs uppercase text-slate-500">Idempotency Key / 幂等键</span>
              <input
                className="control-input w-full"
                value={idempotencyKey}
                onChange={(event) => setIdempotencyKey(event.target.value)}
              />
            </label>
            <div className="flex flex-wrap items-center gap-4">
              <label className="inline-flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={shadowMode}
                  onChange={(event) => setShadowMode(event.target.checked)}
                  className="h-4 w-4 rounded border-line"
                />
                Shadow mode / 影子模式
              </label>
              <label className="inline-flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={enforceReleaseReadiness}
                  onChange={(event) => setEnforceReleaseReadiness(event.target.checked)}
                  disabled={shadowMode}
                  className="h-4 w-4 rounded border-line"
                />
                Enforce readiness / 强制就绪检查
              </label>
              <button
                type="button"
                onClick={runWorkflow}
                disabled={runBusy || (!shadowMode && enforceReleaseReadiness && readiness?.live_ready === false)}
                className="control-button-primary"
              >
                <Play className="h-4 w-4" aria-hidden />
                {shadowMode ? "Run Shadow / 影子运行" : "Run Live / 正式运行"}
              </button>
            </div>
          </div>
          <label className="block">
            <span className="mb-2 block text-xs uppercase text-slate-500">Input Payload JSON / 输入载荷 JSON</span>
            <textarea
              className="min-h-64 w-full rounded-md border border-line bg-white p-3 font-mono text-xs text-ink shadow-sm focus:border-accent focus:ring-2 focus:ring-accent/15"
              value={inputPayloadText}
              onChange={(event) => setInputPayloadText(event.target.value)}
            />
          </label>
        </div>
      </section>
      {versionDiff ? <VersionReviewPanel diff={versionDiff} /> : null}
      {readiness ? <ReleaseReadinessPanel readiness={readiness} /> : null}
      {selectedRunVersion ? (
        <VersionEvalPanel
          version={selectedRunVersion}
          busy={evalBusy}
          results={evalRunResults}
          onRun={runVersionEvals}
        />
      ) : null}
      {selectedRunVersion && selectedRunVersion !== workflow.version ? (
        <PromotionPanel
          version={selectedRunVersion}
          readiness={readiness}
          reason={promotionReason}
          changeSummary={promotionChangeSummary}
          riskAcceptance={promotionRiskAcceptance}
          reviewedDiff={promotionReviewedDiff}
          readinessAcknowledged={promotionReadinessAcknowledged}
          busy={promotingVersion === selectedRunVersion}
          onReasonChange={setPromotionReason}
          onChangeSummaryChange={setPromotionChangeSummary}
          onRiskAcceptanceChange={setPromotionRiskAcceptance}
          onReviewedDiffChange={setPromotionReviewedDiff}
          onReadinessAcknowledgedChange={setPromotionReadinessAcknowledged}
          onPromote={() => promoteVersion(selectedRunVersion)}
        />
      ) : null}
      <section>
        <h2 className="mb-3 text-sm font-semibold text-ink">Version History / 版本历史</h2>
        <div className="overflow-hidden rounded-md border border-line bg-white shadow-sm">
          <table className="w-full text-left text-sm">
            <thead className="bg-field text-xs uppercase text-slate-600">
              <tr>
                <th className="px-3 py-2">Version / 版本</th>
                <th className="px-3 py-2">Name / 名称</th>
                <th className="px-3 py-2">Created / 创建时间</th>
                <th className="px-3 py-2">Updated / 更新时间</th>
                <th className="px-3 py-2">Action / 操作</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((version) => (
                <tr key={version.version} className="border-t border-line">
                  <td className="px-3 py-2 font-medium text-ink">{version.version}</td>
                  <td className="px-3 py-2 text-slate-700">{version.name}</td>
                  <td className="px-3 py-2 text-slate-700">{new Date(version.created_at).toLocaleString()}</td>
                  <td className="px-3 py-2 text-slate-700">{new Date(version.updated_at).toLocaleString()}</td>
                  <td className="px-3 py-2">
                    {version.version === workflow.version ? (
                      <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">Current / 当前</span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setSelectedRunVersion(version.version)}
                        className="inline-flex items-center gap-1 rounded-md border border-line bg-white px-2 py-1 text-xs font-medium text-ink hover:border-accent disabled:opacity-60"
                      >
                        <Rocket className="h-3 w-3" aria-hidden />
                        Review / 审查
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="grid gap-4 lg:grid-cols-2">
        <JsonBlock title="Problem Spec / 问题规格" data={workflow.problem_spec} />
        <JsonBlock title="Process Spec / 流程规格" data={workflow.process_spec} />
        <JsonBlock title="Data Contracts / 数据契约" data={workflow.data_contracts} />
        <JsonBlock title="Tool Policies / 工具策略" data={workflow.tool_policies} />
        <JsonBlock title="Agent Specs / 智能体规格" data={workflow.agent_specs} />
        <JsonBlock title="Eval Specs / 评估规格" data={workflow.eval_specs} />
      </section>
    </div>
  );
}

function VersionReviewPanel({ diff }: { diff: WorkflowDiff }) {
  return (
    <section className="rounded-md border border-line bg-white p-5 shadow-sm">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-ink">Version Review / 版本审查</h2>
          <p className="mt-1 text-sm text-slate-600">
            {diff.from_version} to {diff.to_version}
          </p>
        </div>
        <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
          {diff.change_count} changes / 变更
        </span>
      </div>
      {diff.changes.length > 0 ? (
        <div className="overflow-hidden rounded-sm border border-line">
          <table className="w-full text-left text-sm">
            <thead className="bg-field text-xs uppercase text-slate-600">
              <tr>
                <th className="px-3 py-2">Op / 操作</th>
                <th className="px-3 py-2">Path / 路径</th>
                <th className="px-3 py-2">From / 原值</th>
                <th className="px-3 py-2">To / 新值</th>
              </tr>
            </thead>
            <tbody>
              {diff.changes.slice(0, 12).map((change, index) => (
                <tr key={`${change.path}-${index}`} className="border-t border-line">
                  <td className="px-3 py-2 text-slate-700">{change.op}</td>
                  <td className="break-all px-3 py-2 font-medium text-ink">{change.path}</td>
                  <td className="break-all px-3 py-2 text-slate-700">{previewValue(change.from)}</td>
                  <td className="break-all px-3 py-2 text-slate-700">{previewValue(change.to)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="rounded-sm bg-field p-3 text-sm text-slate-600">No package changes detected. 未检测到包变更。</p>
      )}
    </section>
  );
}

function ReleaseReadinessPanel({ readiness }: { readiness: ReleaseReadiness }) {
  return (
    <section className="rounded-md border border-line bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink">Live Readiness / 正式发布就绪</h2>
        <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
          {readiness.live_ready ? "ready / 就绪" : "blocked / 受阻"}
        </span>
      </div>
      {readiness.blocking_reasons.length > 0 ? (
        <p className="mt-2 text-sm text-slate-600">{readiness.blocking_reasons.join(", ")}</p>
      ) : null}
      <div className="mt-3 grid gap-2 md:grid-cols-3">
        {readiness.checks.map((check) => (
          <div key={check.name} className="rounded-sm bg-field p-3 text-sm text-slate-700">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-ink">{check.name}</span>
              <span>{check.status}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function VersionEvalPanel({
  version,
  busy,
  results,
  onRun
}: {
  version: string;
  busy: boolean;
  results: unknown[] | null;
  onRun: () => void;
}) {
  return (
    <section className="rounded-md border border-line bg-white p-5 shadow-sm">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink">Version Evals / 版本评估</h2>
        <button
          type="button"
          onClick={onRun}
          disabled={busy}
          className="rounded-md border border-line bg-white px-3 py-2 text-sm font-medium text-ink hover:border-accent disabled:opacity-60"
        >
          {busy ? "Running... / 运行中..." : `Run / 运行 v${version}`}
        </button>
      </div>
      {results ? <JsonViewer data={results} /> : null}
    </section>
  );
}

function PromotionPanel({
  version,
  readiness,
  reason,
  changeSummary,
  riskAcceptance,
  reviewedDiff,
  readinessAcknowledged,
  busy,
  onReasonChange,
  onChangeSummaryChange,
  onRiskAcceptanceChange,
  onReviewedDiffChange,
  onReadinessAcknowledgedChange,
  onPromote
}: {
  version: string;
  readiness: ReleaseReadiness | null;
  reason: string;
  changeSummary: string;
  riskAcceptance: string;
  reviewedDiff: boolean;
  readinessAcknowledged: boolean;
  busy: boolean;
  onReasonChange: (value: string) => void;
  onChangeSummaryChange: (value: string) => void;
  onRiskAcceptanceChange: (value: string) => void;
  onReviewedDiffChange: (value: boolean) => void;
  onReadinessAcknowledgedChange: (value: boolean) => void;
  onPromote: () => void;
}) {
  return (
    <section className="rounded-md border border-line bg-white p-5 shadow-sm">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink">Promotion / 提升为当前版本</h2>
        <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">v{version}</span>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="block">
          <span className="mb-2 block text-xs uppercase text-slate-500">Reason / 原因</span>
          <textarea
            value={reason}
            onChange={(event) => onReasonChange(event.target.value)}
            className="min-h-20 w-full rounded-md border border-line bg-white p-3 text-sm outline-none focus:border-accent"
          />
        </label>
        <label className="block">
          <span className="mb-2 block text-xs uppercase text-slate-500">Change Summary / 变更摘要</span>
          <textarea
            value={changeSummary}
            onChange={(event) => onChangeSummaryChange(event.target.value)}
            className="min-h-20 w-full rounded-md border border-line bg-white p-3 text-sm outline-none focus:border-accent"
          />
        </label>
        <label className="block md:col-span-2">
          <span className="mb-2 block text-xs uppercase text-slate-500">Risk Acceptance / 风险接受说明</span>
          <textarea
            value={riskAcceptance}
            onChange={(event) => onRiskAcceptanceChange(event.target.value)}
            className="min-h-20 w-full rounded-md border border-line bg-white p-3 text-sm outline-none focus:border-accent"
          />
        </label>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-4">
        <label className="inline-flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={reviewedDiff}
            onChange={(event) => onReviewedDiffChange(event.target.checked)}
            className="h-4 w-4 rounded border-line"
          />
          Diff reviewed / 已审查差异
        </label>
        <label className="inline-flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={readinessAcknowledged}
            onChange={(event) => onReadinessAcknowledgedChange(event.target.checked)}
            className="h-4 w-4 rounded border-line"
          />
          Readiness acknowledged / 已确认就绪状态
        </label>
        <button
          type="button"
          onClick={onPromote}
          disabled={busy}
          className="inline-flex items-center gap-2 rounded-md bg-accent px-3 py-2 text-sm font-medium text-white hover:bg-[#0F5860] disabled:opacity-60"
        >
          <Rocket className="h-4 w-4" aria-hidden />
          Promote / 提升
        </button>
      </div>
      {readiness?.blocking_reasons.length ? (
        <p className="mt-3 text-sm text-slate-600">{readiness.blocking_reasons.join(", ")}</p>
      ) : null}
    </section>
  );
}

function JsonBlock({ title, data }: { title: string; data: unknown }) {
  return (
    <div>
      <h2 className="mb-3 text-sm font-semibold text-ink">{title}</h2>
      <JsonViewer data={data} />
    </div>
  );
}

function previewValue(value: unknown) {
  if (value === undefined) return "-";
  if (value === null) return "null";
  const rendered = typeof value === "string" ? value : JSON.stringify(value);
  if (!rendered) return "-";
  return rendered.length > 120 ? `${rendered.slice(0, 117)}...` : rendered;
}

function getRequestedVersionFromLocation() {
  if (typeof window === "undefined") {
    return "";
  }
  return new URLSearchParams(window.location.search).get("version") ?? "";
}
