"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Ban, Check, RotateCcw, X } from "lucide-react";
import { apiFetch, getLocalAuthHeaders } from "../../../lib/api";
import { JsonViewer } from "../../../components/JsonViewer";
import { TraceTimeline } from "../../../components/TraceTimeline";

type Run = {
  run_id: string;
  workflow_id: string;
  workflow_version?: string | null;
  shadow_mode: boolean;
  status: string;
  current_node_id?: string | null;
  output_payload: unknown;
};

type Trace = {
  node_id: string;
  status: string;
  error?: string | null;
  duration_ms?: number | null;
  input_snapshot?: unknown;
  output_snapshot?: unknown;
};

type RunDiagnostics = {
  run_id: string;
  status: string;
  current_node_id?: string | null;
  shadow_mode: boolean;
  is_terminal: boolean;
  trace_count: number;
  trace_counts: Record<string, number>;
  failure?: {
    node_id?: string | null;
    error?: string | null;
    attempt?: number | null;
    max_attempts?: number | null;
    retryable?: boolean | null;
    retry_budget_exhausted: boolean;
    pending_node_id?: string | null;
    executed_steps?: number | null;
    max_steps?: number | null;
  } | null;
  approval: {
    required: boolean;
    node_id?: string | null;
  };
  recommended_actions: string[];
};

type EvalResult = {
  eval_id: string;
  score: number;
  passed: boolean;
  reason: string;
  details: {
    compared_path_count?: number;
    matched_path_count?: number;
    missing_paths?: string[];
    mismatched_paths?: string[];
  };
};

export default function RunDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [run, setRun] = useState<Run | null>(null);
  const [traces, setTraces] = useState<Trace[]>([]);
  const [diagnostics, setDiagnostics] = useState<RunDiagnostics | null>(null);
  const [comparisons, setComparisons] = useState<EvalResult[]>([]);
  const [approvalPayloadText, setApprovalPayloadText] = useState("{\n  \"reason\": \"business approval from dashboard\"\n}");
  const [approvalMaxSteps, setApprovalMaxSteps] = useState(50);
  const [approvalMaxRetries, setApprovalMaxRetries] = useState(1);
  const [cancelReason, setCancelReason] = useState("dashboard cancellation");
  const [rerunReason, setRerunReason] = useState("dashboard rerun");
  const [rerunMaxSteps, setRerunMaxSteps] = useState(50);
  const [rerunMaxRetries, setRerunMaxRetries] = useState(1);
  const [rerunIdempotencyKey, setRerunIdempotencyKey] = useState("");
  const [rerunShadowMode, setRerunShadowMode] = useState<"preserve" | "shadow" | "live">("preserve");
  const [expectedOutputText, setExpectedOutputText] = useState("{\n  \"go-no-go-decision\": {\n    \"decision\": \"shadow_draft_created\"\n  }\n}");
  const [passThreshold, setPassThreshold] = useState(0.8);
  const [comparisonNotes, setComparisonNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [rerunBusy, setRerunBusy] = useState(false);
  const [comparisonBusy, setComparisonBusy] = useState(false);

  useEffect(() => {
    if (!params.id) return;
    refreshRun(params.id);
  }, [params.id]);

  async function refreshRun(runId: string) {
    try {
      const headers = await getLocalAuthHeaders("workflow-admin");
      const [runResponse, traceResponse, diagnosticsResponse, comparisonResponse] = await Promise.all([
        apiFetch<Run>(`/api/runs/${runId}`, { headers }),
        apiFetch<Trace[]>(`/api/runs/${runId}/traces`, { headers }),
        apiFetch<RunDiagnostics>(`/api/runs/${runId}/diagnostics`, { headers }),
        apiFetch<EvalResult[]>(`/api/runs/${runId}/shadow-comparisons`, { headers })
      ]);
      setRun(runResponse);
      setTraces(traceResponse);
      setDiagnostics(diagnosticsResponse);
      setComparisons(comparisonResponse);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load run / 加载运行失败");
    }
  }

  async function submitApproval(approved: boolean) {
    if (!run) return;
    let approvalPayload: unknown;
    try {
      approvalPayload = approvalPayloadText.trim() ? JSON.parse(approvalPayloadText) : {};
    } catch {
      setError("Approval payload must be valid JSON / 审批载荷必须是有效 JSON");
      return;
    }
    if (!approvalPayload || typeof approvalPayload !== "object" || Array.isArray(approvalPayload)) {
      setError("Approval payload must be a JSON object / 审批载荷必须是 JSON 对象");
      return;
    }
    setApprovalBusy(true);
    setError(null);
    try {
      const response = await apiFetch<Run>(`/api/runs/${run.run_id}/approval`, {
        method: "POST",
        headers: await getLocalAuthHeaders("business-approver"),
        body: JSON.stringify({
          approved,
          approval_payload: approvalPayload,
          max_steps: approvalMaxSteps,
          max_retries: approvalMaxRetries
        })
      });
      setRun(response);
      await refreshRun(run.run_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to submit approval / 提交审批失败");
    } finally {
      setApprovalBusy(false);
    }
  }

  async function cancelRun() {
    if (!run) return;
    setCancelBusy(true);
    setError(null);
    try {
      const response = await apiFetch<Run>(`/api/runs/${run.run_id}/cancel`, {
        method: "POST",
        headers: await getLocalAuthHeaders("workflow-admin"),
        body: JSON.stringify({ reason: cancelReason.trim() || undefined })
      });
      setRun(response);
      await refreshRun(run.run_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to cancel run / 取消运行失败");
    } finally {
      setCancelBusy(false);
    }
  }

  async function rerunRun() {
    if (!run) return;
    setRerunBusy(true);
    setError(null);
    try {
      const shadowMode =
        rerunShadowMode === "preserve"
          ? undefined
          : rerunShadowMode === "shadow";
      const response = await apiFetch<Run>(`/api/runs/${run.run_id}/rerun`, {
        method: "POST",
        headers: await getLocalAuthHeaders("workflow-admin"),
        body: JSON.stringify({
          reason: rerunReason.trim() || undefined,
          max_steps: rerunMaxSteps,
          max_retries: rerunMaxRetries,
          shadow_mode: shadowMode,
          idempotency_key: rerunIdempotencyKey.trim() || undefined
        })
      });
      router.push(`/runs/${response.run_id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to rerun / 重跑失败");
    } finally {
      setRerunBusy(false);
    }
  }

  async function submitShadowComparison() {
    if (!run) return;
    let expectedOutput: unknown;
    try {
      expectedOutput = JSON.parse(expectedOutputText);
    } catch {
      setError("Expected output must be valid JSON / 期望输出必须是有效 JSON");
      return;
    }
    if (!expectedOutput || typeof expectedOutput !== "object" || Array.isArray(expectedOutput)) {
      setError("Expected output must be a JSON object / 期望输出必须是 JSON 对象");
      return;
    }

    setComparisonBusy(true);
    setError(null);
    try {
      const response = await apiFetch<EvalResult>(`/api/runs/${run.run_id}/shadow-comparisons`, {
        method: "POST",
        headers: await getLocalAuthHeaders("workflow-admin"),
        body: JSON.stringify({
          expected_output: expectedOutput,
          pass_threshold: passThreshold,
          notes: comparisonNotes.trim() || undefined
        })
      });
      setComparisons([response, ...comparisons]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to compare shadow run / 对比影子运行失败");
    } finally {
      setComparisonBusy(false);
    }
  }

  if (error && !run) return <p className="surface border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p>;
  if (!run) return <p className="surface p-4 text-sm text-slate-600">Loading run... / 正在加载运行...</p>;
  const canCancel = ["created", "running", "paused"].includes(run.status);
  const canRerun = ["completed", "failed", "rejected", "canceled"].includes(run.status);
  const canCompareShadow = run.shadow_mode && canRerun;

  return (
    <div className="grid gap-6 xl:grid-cols-[420px_1fr]">
      <section className="space-y-4">
        <div className="surface p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">Runtime operation / 运行操作</p>
              <h1 className="page-heading mt-1">Run Detail / 运行详情</h1>
              <p className="page-subtitle break-all">{run.run_id}</p>
            </div>
            <span className={`status-pill ${runStatusClass(run.status)}`}>{run.status}</span>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            <RunFact label="Workflow / 工作流" value={run.workflow_id} />
            <RunFact label="Version / 版本" value={`v${run.workflow_version ?? "unknown"}`} />
            <RunFact label="Mode / 模式" value={run.shadow_mode ? "shadow / 影子" : "live / 正式"} />
            <RunFact label="Current Node / 当前节点" value={run.current_node_id ?? "-"} />
          </div>
        </div>
        {error ? <p className="surface border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p> : null}
        {run.status === "paused" ? (
          <div className="surface p-5">
            <h2 className="text-sm font-semibold text-ink">Approval Required / 需要审批</h2>
            <p className="mt-1 text-sm text-slate-600">
              Current node / 当前节点: {run.current_node_id ?? "unknown"}
            </p>
            <div className="mt-3 space-y-3">
              <label className="block">
                <span className="mb-2 block text-xs uppercase text-slate-500">Approval Payload JSON / 审批载荷 JSON</span>
                <textarea
                  value={approvalPayloadText}
                  onChange={(event) => setApprovalPayloadText(event.target.value)}
                  className="min-h-28 w-full rounded-md border border-line bg-white p-3 font-mono text-xs text-ink shadow-sm focus:border-accent focus:ring-2 focus:ring-accent/15"
                  spellCheck={false}
                />
              </label>
              <div className="grid gap-3 md:grid-cols-2">
                <label className="block">
                  <span className="mb-2 block text-xs uppercase text-slate-500">Max Steps / 最大步数</span>
                  <input
                    type="number"
                    min="1"
                    max="200"
                    value={approvalMaxSteps}
                    onChange={(event) => setApprovalMaxSteps(Number(event.target.value))}
                    className="control-input w-full"
                  />
                </label>
                <label className="block">
                  <span className="mb-2 block text-xs uppercase text-slate-500">Max Retries / 最大重试</span>
                  <input
                    type="number"
                    min="0"
                    max="5"
                    value={approvalMaxRetries}
                    onChange={(event) => setApprovalMaxRetries(Number(event.target.value))}
                    className="control-input w-full"
                  />
                </label>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => submitApproval(true)}
                disabled={approvalBusy}
                className="control-button-primary"
              >
                <Check className="h-4 w-4" aria-hidden />
                Approve / 通过
              </button>
              <button
                type="button"
                onClick={() => submitApproval(false)}
                disabled={approvalBusy}
                className="control-button hover:border-red-700 hover:text-red-700"
              >
                <X className="h-4 w-4" aria-hidden />
                Reject / 拒绝
              </button>
            </div>
          </div>
        ) : null}
        {canCancel ? (
          <div className="surface p-5">
            <h2 className="text-sm font-semibold text-ink">Cancel / 取消</h2>
            <label className="mt-3 block">
              <span className="mb-2 block text-xs uppercase text-slate-500">Reason / 原因</span>
              <textarea
                value={cancelReason}
                onChange={(event) => setCancelReason(event.target.value)}
                className="min-h-20 w-full rounded-md border border-line bg-white p-3 text-sm shadow-sm focus:border-accent focus:ring-2 focus:ring-accent/15"
              />
            </label>
            <button
              type="button"
              onClick={cancelRun}
              disabled={cancelBusy}
              className="mt-3 inline-flex items-center gap-2 rounded-md border border-red-200 bg-white px-3 py-2 text-sm font-medium text-red-700 hover:border-red-700 disabled:opacity-60"
            >
              <Ban className="h-4 w-4" aria-hidden />
              Cancel Run / 取消运行
            </button>
          </div>
        ) : null}
        {canRerun ? (
          <div className="surface p-5">
            <h2 className="text-sm font-semibold text-ink">Rerun / 重跑</h2>
            <label className="mt-3 block">
              <span className="mb-2 block text-xs uppercase text-slate-500">Reason / 原因</span>
              <textarea
                value={rerunReason}
                onChange={(event) => setRerunReason(event.target.value)}
                className="min-h-20 w-full rounded-md border border-line bg-white p-3 text-sm shadow-sm focus:border-accent focus:ring-2 focus:ring-accent/15"
              />
            </label>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <label className="block">
                <span className="mb-2 block text-xs uppercase text-slate-500">Max Steps / 最大步数</span>
                <input
                  type="number"
                  min="1"
                  max="200"
                  value={rerunMaxSteps}
                  onChange={(event) => setRerunMaxSteps(Number(event.target.value))}
                  className="control-input w-full"
                />
              </label>
              <label className="block">
                <span className="mb-2 block text-xs uppercase text-slate-500">Max Retries / 最大重试</span>
                <input
                  type="number"
                  min="0"
                  max="5"
                  value={rerunMaxRetries}
                  onChange={(event) => setRerunMaxRetries(Number(event.target.value))}
                  className="control-input w-full"
                />
              </label>
              <label className="block">
                <span className="mb-2 block text-xs uppercase text-slate-500">Idempotency Key / 幂等键</span>
                <input
                  value={rerunIdempotencyKey}
                  onChange={(event) => setRerunIdempotencyKey(event.target.value)}
                  className="control-input w-full"
                />
              </label>
              <label className="block">
                <span className="mb-2 block text-xs uppercase text-slate-500">Mode / 模式</span>
                <select
                  value={rerunShadowMode}
                  onChange={(event) => setRerunShadowMode(event.target.value as "preserve" | "shadow" | "live")}
                  className="control-input w-full"
                >
                  <option value="preserve">preserve source / 保持来源</option>
                  <option value="shadow">shadow / 影子</option>
                  <option value="live">live / 正式</option>
                </select>
              </label>
            </div>
            <button
              type="button"
              onClick={rerunRun}
              disabled={rerunBusy}
              className="control-button mt-3"
            >
              <RotateCcw className="h-4 w-4" aria-hidden />
              Rerun / 重跑
            </button>
          </div>
        ) : null}
        {diagnostics ? <DiagnosticsPanel diagnostics={diagnostics} /> : null}
        {canCompareShadow ? (
          <ShadowComparisonPanel
            comparisons={comparisons}
            expectedOutputText={expectedOutputText}
            passThreshold={passThreshold}
            comparisonNotes={comparisonNotes}
            busy={comparisonBusy}
            onExpectedOutputChange={setExpectedOutputText}
            onPassThresholdChange={setPassThreshold}
            onComparisonNotesChange={setComparisonNotes}
            onSubmit={submitShadowComparison}
          />
        ) : null}
        <section>
          <h2 className="mb-3 text-sm font-semibold text-ink">Output / 输出</h2>
          <JsonViewer data={run.output_payload} />
        </section>
      </section>
      <section className="space-y-4">
        <div className="surface flex flex-wrap items-center justify-between gap-3 p-5">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">Trace account / 追踪账户</p>
            <h2 className="mt-1 text-base font-semibold text-ink">Trace Timeline / 追踪时间线</h2>
          </div>
          <span className="status-pill">{traces.length} traces / 追踪</span>
        </div>
        <TraceTimeline traces={traces} />
      </section>
    </div>
  );
}

function ShadowComparisonPanel({
  comparisons,
  expectedOutputText,
  passThreshold,
  comparisonNotes,
  busy,
  onExpectedOutputChange,
  onPassThresholdChange,
  onComparisonNotesChange,
  onSubmit
}: {
  comparisons: EvalResult[];
  expectedOutputText: string;
  passThreshold: number;
  comparisonNotes: string;
  busy: boolean;
  onExpectedOutputChange: (value: string) => void;
  onPassThresholdChange: (value: number) => void;
  onComparisonNotesChange: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <div className="surface p-5">
      <h2 className="text-sm font-semibold text-ink">Shadow Comparison / 影子运行对比</h2>
      <div className="mt-3 space-y-3">
        <label className="block text-xs uppercase text-slate-500" htmlFor="expected-output">
          Expected Output / 期望输出
        </label>
        <textarea
          id="expected-output"
          value={expectedOutputText}
          onChange={(event) => onExpectedOutputChange(event.target.value)}
          className="min-h-32 w-full rounded-md border border-line bg-white p-3 font-mono text-xs text-ink shadow-sm focus:border-accent focus:ring-2 focus:ring-accent/15"
        />
        <label className="block">
          <span className="mb-2 block text-xs uppercase text-slate-500">Notes / 备注</span>
          <textarea
            value={comparisonNotes}
            onChange={(event) => onComparisonNotesChange(event.target.value)}
            className="min-h-20 w-full rounded-md border border-line bg-white p-3 text-sm shadow-sm focus:border-accent focus:ring-2 focus:ring-accent/15"
          />
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-xs uppercase text-slate-500" htmlFor="pass-threshold">
            Threshold / 阈值
          </label>
          <input
            id="pass-threshold"
            type="number"
            min="0"
            max="1"
            step="0.05"
            value={passThreshold}
            onChange={(event) => onPassThresholdChange(Number(event.target.value))}
            className="control-input w-24 px-2 py-1"
          />
          <button
            type="button"
            onClick={onSubmit}
            disabled={busy}
            className="control-button"
          >
            <Check className="h-4 w-4" aria-hidden />
            Compare / 对比
          </button>
        </div>
      </div>
      {comparisons.length > 0 ? (
        <div className="mt-4 space-y-2">
          {comparisons.map((comparison) => (
            <div key={comparison.eval_id} className="rounded-sm bg-field p-3 text-sm text-slate-700">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium text-ink">{comparison.passed ? "passed / 通过" : "failed / 失败"}</span>
                <span>{comparison.score.toFixed(2)}</span>
              </div>
              <p className="mt-1">{comparison.reason}</p>
              <p className="mt-1 text-xs text-slate-500">
                {comparison.details.matched_path_count ?? 0} / {comparison.details.compared_path_count ?? 0} paths / 路径
              </p>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function DiagnosticsPanel({ diagnostics }: { diagnostics: RunDiagnostics }) {
  const traceEntries = Object.entries(diagnostics.trace_counts);
  return (
    <div className="surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-ink">Diagnostics / 诊断</h2>
          <p className="mt-1 text-sm text-slate-600">
            {diagnostics.trace_count} traces / 追踪 · {diagnostics.is_terminal ? "terminal / 终态" : "active / 活跃"}
            {" · "}
            {diagnostics.shadow_mode ? "shadow / 影子" : "live / 正式"}
          </p>
        </div>
        <span className={`status-pill ${runStatusClass(diagnostics.status)}`}>{diagnostics.status}</span>
      </div>
      {diagnostics.failure ? (
        <dl className="mt-3 grid gap-2 text-sm">
          <div>
            <dt className="text-xs uppercase text-slate-500">Node / 节点</dt>
            <dd className="text-ink">{diagnostics.failure.node_id ?? diagnostics.current_node_id ?? "-"}</dd>
          </div>
          {diagnostics.failure.error ? (
            <div>
              <dt className="text-xs uppercase text-slate-500">Error / 错误</dt>
              <dd className="break-words text-red-700">{diagnostics.failure.error}</dd>
            </div>
          ) : null}
          {diagnostics.failure.pending_node_id ? (
            <div>
              <dt className="text-xs uppercase text-slate-500">Pending Node / 待处理节点</dt>
              <dd className="text-ink">{diagnostics.failure.pending_node_id}</dd>
            </div>
          ) : null}
          {diagnostics.failure.max_steps ? (
            <div>
              <dt className="text-xs uppercase text-slate-500">Step Budget / 步数预算</dt>
              <dd className="text-ink">
                {diagnostics.failure.executed_steps ?? 0} / {diagnostics.failure.max_steps}
              </dd>
            </div>
          ) : null}
          {diagnostics.failure.max_attempts ? (
            <div>
              <dt className="text-xs uppercase text-slate-500">Retry Budget / 重试预算</dt>
              <dd className="text-ink">
                attempt {diagnostics.failure.attempt ?? 0} / {diagnostics.failure.max_attempts}
                {diagnostics.failure.retry_budget_exhausted ? " · exhausted" : ""}
              </dd>
            </div>
          ) : null}
        </dl>
      ) : null}
      {traceEntries.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {traceEntries.map(([status, count]) => (
            <span key={status} className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
              {status}: {count}
            </span>
          ))}
        </div>
      ) : null}
      {diagnostics.recommended_actions.length > 0 ? (
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-700">
          {diagnostics.recommended_actions.map((action) => (
            <li key={action}>{action}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function RunFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-field px-3 py-2">
      <p className="text-xs font-medium uppercase text-slate-500">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-ink">{value}</p>
    </div>
  );
}

function runStatusClass(status: string) {
  switch (status) {
    case "completed":
      return "border-emerald-200 bg-emerald-50 text-emerald-700";
    case "failed":
    case "rejected":
    case "canceled":
      return "border-red-200 bg-red-50 text-red-700";
    case "paused":
      return "border-amber-200 bg-amber-50 text-amber-700";
    case "running":
      return "border-[#b7d7dc] bg-[#e6f1f3] text-accent";
    default:
      return "border-line bg-field text-slate-700";
  }
}
