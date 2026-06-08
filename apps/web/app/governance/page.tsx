"use client";

import { useEffect, useState } from "react";
import { BarChart3 } from "lucide-react";
import { apiFetch, getLocalAuthHeaders } from "../../lib/api";
import { JsonViewer } from "../../components/JsonViewer";
import { ProgressBar } from "../../components/ProgressBar";

type WorkflowSummary = {
  workflow_id: string;
  name: string;
};

type Metrics = {
  node_success_rate?: number;
  tool_success_rate?: number;
  approval_count?: number;
  total_traces?: number;
  average_duration_ms?: number;
  failure_reason_distribution?: Record<string, number>;
};

type RiskReport = {
  risk_score: number;
  risk_level: string;
  tool_risk: {
    high_risk_tool_count: number;
    write_tool_count: number;
    approval_gap_tool_ids: string[];
  };
  run_risk: {
    live_run_count: number;
    shadow_run_count: number;
    live_runs_on_unready_version_count: number;
  };
  audit_risk: {
    release_gate_block_count: number;
    idempotency_conflict_count: number;
  };
  risk_items: Array<{
    severity: string;
    code: string;
    message: string;
    count: number;
  }>;
};

type RunQueueItem = {
  run_id: string;
  workflow_id: string;
  workflow_version?: string | null;
  status: string;
  current_node_id?: string | null;
  shadow_mode: boolean;
  trace_count: number;
  updated_at: string;
};

type RecoveryQueueItem = RunQueueItem & {
  failure_node_id?: string | null;
  failure_reason_code: string;
  recommended_action_code: string;
};

type RunReport = {
  run_count: number;
  trace_count: number;
  status_counts: Record<string, number>;
  active_run_count: number;
  terminal_run_count: number;
  live_run_count: number;
  shadow_run_count: number;
  pending_approval_count: number;
  pending_node_counts: Record<string, number>;
  recovery_queue_count: number;
  recovery_reason_counts: Record<string, number>;
  shadow_validation_pending_count: number;
  average_run_duration_ms: number;
  pending_approvals: RunQueueItem[];
  recovery_queue: RecoveryQueueItem[];
  shadow_validation_queue: RunQueueItem[];
  run_items: Array<{
    severity: string;
    code: string;
    message: string;
    count: number;
  }>;
};

type QualityReport = {
  quality_score: number;
  quality_level: string;
  node_success_rate: number;
  eval_result_count: number;
  eval_pass_count: number;
  eval_fail_count: number;
  eval_pass_rate: number;
  average_eval_score: number;
  shadow_comparison_count: number;
  passing_shadow_comparison_count: number;
  release_ready_version_count: number;
  unready_version_count: number;
  optimization_suggestion_count: number;
  failed_node_counts: Array<{
    node_id: string;
    failed_trace_count: number;
    failure_reason_codes: Record<string, number>;
  }>;
  quality_items: Array<{
    severity: string;
    code: string;
    message: string;
    count: number;
  }>;
};

type CostReport = {
  estimated_total_tokens: number;
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  total_duration_ms: number;
  average_trace_duration_ms: number;
  human_touch_count: number;
  retry_trace_count: number;
  trace_count: number;
  run_count: number;
  live_run_count: number;
  shadow_run_count: number;
  node_costs: Array<{
    node_id: string;
    trace_count: number;
    estimated_input_tokens: number;
    estimated_output_tokens: number;
    estimated_total_tokens: number;
    total_duration_ms: number;
    average_duration_ms: number;
    human_touch_count: number;
    retry_trace_count: number;
  }>;
  cost_items: Array<{
    code: string;
    message: string;
    count: number;
  }>;
};

type RetentionReport = {
  generated_at: string;
  workflow_id?: string | null;
  policy: {
    run_retention_days: number;
    eval_retention_days: number;
    audit_retention_days: number;
    sample_limit: number;
  };
  cutoffs: {
    runs_before: string;
    evals_before: string;
    audit_events_before: string;
  };
  run_account: {
    run_count: number;
    terminal_run_count: number;
    active_run_count: number;
    expired_terminal_run_count: number;
    active_run_past_retention_count: number;
    status_counts: Record<string, number>;
  };
  eval_account: {
    eval_result_count: number;
    expired_eval_result_count: number;
    passed_count: number;
    failed_count: number;
  };
  audit_account: {
    audit_event_count: number;
    expired_audit_event_count: number;
    event_type_counts: Record<string, number>;
  };
  retention_items: Array<{
    category: string;
    count: number;
    sample_ids: string[];
    sample_truncated: boolean;
    recommendation: string;
  }>;
};

type RetentionApplyResult = {
  dry_run: boolean;
  eligible_counts: Record<string, number>;
  deleted_counts: Record<string, number>;
  skipped_counts: Record<string, number>;
  retention_report: RetentionReport;
};

type RepairOperation = {
  operation_id: string;
  target_type: string;
  target_id: string;
  action: string;
};

type RepairPlan = {
  plan_id: string;
  workflow_id: string;
  workflow_version: string;
  operations: RepairOperation[];
  suggestions: unknown[];
};

type RepairImpactPreview = {
  change_count: number;
  field_impact_count: number;
  field_impact_limit: number;
  truncated: boolean;
  impacted_sections: string[];
  risk_counts: Record<string, number>;
  release_gate_impacts: string[];
  validation_impact: {
    valid: boolean;
    error_count: number;
    warning_count: number;
  };
  operation_impacts: Array<{
    operation_id: string;
    target_type: string;
    target_id: string;
    action: string;
    risk_level: string;
    release_gate_impacts: string[];
    reason_code: string;
  }>;
  field_impacts: Array<{
    op: string;
    path: string;
    section: string;
    risk_level: string;
    reason_code: string;
    operation_ids: string[];
  }>;
};

type RepairPreviewResult = {
  change_count: number;
  target_version_available: boolean;
  impact_preview?: RepairImpactPreview;
  validation_report?: {
    valid: boolean;
  };
  [key: string]: unknown;
};

export default function GovernancePage() {
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [metrics, setMetrics] = useState<Metrics>({});
  const [runReport, setRunReport] = useState<RunReport | null>(null);
  const [qualityReport, setQualityReport] = useState<QualityReport | null>(null);
  const [costReport, setCostReport] = useState<CostReport | null>(null);
  const [retentionReport, setRetentionReport] = useState<RetentionReport | null>(null);
  const [retentionReason, setRetentionReason] = useState("cleanup after reviewed snapshot");
  const [retentionSnapshotAcknowledged, setRetentionSnapshotAcknowledged] = useState(false);
  const [retentionApplying, setRetentionApplying] = useState(false);
  const [retentionApplyResult, setRetentionApplyResult] = useState<RetentionApplyResult | null>(null);
  const [retentionApplyError, setRetentionApplyError] = useState<string | null>(null);
  const [riskReport, setRiskReport] = useState<RiskReport | null>(null);
  const [evalResults, setEvalResults] = useState<unknown[]>([]);
  const [suggestions, setSuggestions] = useState<unknown[]>([]);
  const [repairPlan, setRepairPlan] = useState<RepairPlan | null>(null);
  const [repairTargetVersion, setRepairTargetVersion] = useState("");
  const [repairReason, setRepairReason] = useState("");
  const [selectedRepairOperationIds, setSelectedRepairOperationIds] = useState<string[]>([]);
  const [repairPreviewing, setRepairPreviewing] = useState(false);
  const [repairPreviewResult, setRepairPreviewResult] = useState<RepairPreviewResult | null>(null);
  const [repairPreviewError, setRepairPreviewError] = useState<string | null>(null);
  const [repairApplying, setRepairApplying] = useState(false);
  const [repairApplyResult, setRepairApplyResult] = useState<unknown | null>(null);
  const [repairApplyError, setRepairApplyError] = useState<string | null>(null);
  const [auditEvents, setAuditEvents] = useState<unknown[]>([]);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getLocalAuthHeaders("workflow-admin")
      .then((headers) => apiFetch<WorkflowSummary[]>("/api/workflows", { headers }))
      .then((items) => {
        setWorkflows(items);
        if (items[0]) setSelectedWorkflowId(items[0].workflow_id);
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Failed to load workflows / 加载工作流失败"));
  }, []);

  useEffect(() => {
    const workflowQuery = selectedWorkflowId ? `?workflow_id=${selectedWorkflowId}` : "";
    getLocalAuthHeaders("workflow-admin")
      .then((headers) => {
        apiFetch<Metrics>(`/api/governance/metrics${workflowQuery}`, { headers }).then(setMetrics).catch(() => null);
        apiFetch<RunReport>(`/api/governance/run-report${workflowQuery}`, { headers }).then(setRunReport).catch(() => null);
        apiFetch<QualityReport>(`/api/governance/quality-report${workflowQuery}`, { headers }).then(setQualityReport).catch(() => null);
        apiFetch<CostReport>(`/api/governance/cost-report${workflowQuery}`, { headers }).then(setCostReport).catch(() => null);
        apiFetch<RetentionReport>(`/api/governance/retention-report${workflowQuery}`, { headers })
          .then(setRetentionReport)
          .catch(() => null);
        apiFetch<RiskReport>(`/api/governance/risk-report${workflowQuery}`, { headers }).then(setRiskReport).catch(() => null);
        apiFetch<unknown[]>(`/api/governance/eval-results${workflowQuery}`, { headers }).then(setEvalResults).catch(() => null);
        apiFetch<unknown[]>(`/api/governance/audit-events${workflowQuery}`, { headers }).then(setAuditEvents).catch(() => null);
        if (selectedWorkflowId) {
          apiFetch<unknown[]>(`/api/workflows/${selectedWorkflowId}/optimization-suggestions`, { headers })
            .then(setSuggestions)
            .catch(() => setSuggestions([]));
          apiFetch<RepairPlan>(`/api/workflows/${selectedWorkflowId}/repair-plan`, { headers })
            .then((plan) => {
              setRepairPlan(plan);
              setRepairTargetVersion(`${plan.workflow_version}-repair`);
              setRepairReason("apply package repair plan");
              setSelectedRepairOperationIds(plan.operations.map((operation) => operation.operation_id));
            })
            .catch(() => {
              setRepairPlan(null);
              setSelectedRepairOperationIds([]);
            });
        } else {
          setSuggestions([]);
          setRepairPlan(null);
          setRepairTargetVersion("");
          setRepairReason("");
          setSelectedRepairOperationIds([]);
        }
        setRepairApplyResult(null);
        setRepairApplyError(null);
        setRepairPreviewResult(null);
        setRepairPreviewError(null);
        setRetentionApplyResult(null);
        setRetentionApplyError(null);
      })
      .catch(() => null);
  }, [selectedWorkflowId]);

  async function previewRepairCandidate() {
    if (!selectedWorkflowId || !repairPlan || !repairTargetVersion.trim() || selectedRepairOperationIds.length === 0) {
      return;
    }
    setRepairPreviewing(true);
    setRepairPreviewError(null);
    setRepairPreviewResult(null);
    try {
      const headers = await getLocalAuthHeaders("workflow-admin");
      const result = await apiFetch<RepairPreviewResult>(`/api/workflows/${selectedWorkflowId}/repair-plan/preview`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          source_version: repairPlan.workflow_version,
          target_version: repairTargetVersion.trim(),
          reason: repairReason.trim() || "preview package repair candidate",
          selected_operation_ids: selectedRepairOperationIds
        })
      });
      setRepairPreviewResult(result);
    } catch (caught) {
      setRepairPreviewError(caught instanceof Error ? caught.message : "Failed to preview repair candidate / 预览修复候选失败");
    } finally {
      setRepairPreviewing(false);
    }
  }

  async function applyRepairCandidate() {
    if (!selectedWorkflowId || !repairPlan || !repairTargetVersion.trim() || selectedRepairOperationIds.length === 0) {
      return;
    }
    setRepairApplying(true);
    setRepairApplyError(null);
    setRepairApplyResult(null);
    try {
      const headers = await getLocalAuthHeaders("workflow-admin");
      const result = await apiFetch<unknown>(`/api/workflows/${selectedWorkflowId}/repair-plan/apply`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          source_version: repairPlan.workflow_version,
          target_version: repairTargetVersion.trim(),
          reason: repairReason.trim() || "apply package repair plan",
          selected_operation_ids: selectedRepairOperationIds
        })
      });
      setRepairApplyResult(result);
      setRepairPreviewResult(null);
    } catch (caught) {
      setRepairApplyError(caught instanceof Error ? caught.message : "Failed to create repair candidate / 创建修复候选失败");
    } finally {
      setRepairApplying(false);
    }
  }

  function toggleRepairOperation(operationId: string) {
    setSelectedRepairOperationIds((current) =>
      current.includes(operationId)
        ? current.filter((item) => item !== operationId)
        : [...current, operationId]
    );
  }

  async function applyRetention(dryRun: boolean) {
    if (!selectedWorkflowId) {
      return;
    }
    setRetentionApplying(true);
    setRetentionApplyError(null);
    setRetentionApplyResult(null);
    try {
      const headers = await getLocalAuthHeaders("workflow-admin");
      const result = await apiFetch<RetentionApplyResult>("/api/governance/retention-apply", {
        method: "POST",
        headers,
        body: JSON.stringify({
          workflow_id: selectedWorkflowId,
          dry_run: dryRun,
          confirm_apply: !dryRun,
          snapshot_acknowledged: retentionSnapshotAcknowledged,
          reason: retentionReason.trim() || undefined
        })
      });
      setRetentionApplyResult(result);
      const workflowQuery = `?workflow_id=${selectedWorkflowId}`;
      apiFetch<RetentionReport>(`/api/governance/retention-report${workflowQuery}`, { headers })
        .then(setRetentionReport)
        .catch(() => setRetentionReport(result.retention_report));
      apiFetch<unknown[]>(`/api/governance/audit-events${workflowQuery}`, { headers }).then(setAuditEvents).catch(() => null);
      if (!dryRun) {
        setRetentionSnapshotAcknowledged(false);
      }
    } catch (caught) {
      setRetentionApplyError(caught instanceof Error ? caught.message : "Failed to apply retention / 应用保留策略失败");
    } finally {
      setRetentionApplying(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="surface flex flex-wrap items-center justify-between gap-4 p-5">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">Governance console / 治理控制台</p>
          <h1 className="page-heading mt-1">Governance / 治理</h1>
          <p className="page-subtitle">Trace, eval, metrics, optimization loop. 追踪、评估、指标和优化闭环。</p>
        </div>
        <select
          className="control-input"
          value={selectedWorkflowId}
          onChange={(event) => setSelectedWorkflowId(event.target.value)}
        >
          <option value="">All workflows / 全部工作流</option>
          {workflows.map((workflow) => (
            <option key={workflow.workflow_id} value={workflow.workflow_id}>
              {workflow.name}
            </option>
          ))}
        </select>
      </div>
      {error ? <p className="surface border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p> : null}
      <section className="grid gap-4 md:grid-cols-3">
        <MetricCard title="Node Success / 节点成功率" value={`${Math.round((metrics.node_success_rate ?? 0) * 100)}%`} />
        <MetricCard title="Approvals / 审批数" value={`${metrics.approval_count ?? 0}`} />
        <MetricCard title="Avg Runtime / 平均运行时长" value={`${Math.round(metrics.average_duration_ms ?? 0)} ms`} />
      </section>
      {runReport ? (
        <section className="rounded-md border border-line bg-white p-5 shadow-sm">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-ink">Run Account / 运行账户</h2>
            <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
              {runReport.active_run_count} active / 活跃 · {runReport.terminal_run_count} terminal / 终态
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            <CostStat label="Runs / 运行" value={runReport.run_count} />
            <CostStat label="Pending approvals / 待审批" value={runReport.pending_approval_count} />
            <CostStat label="Recovery queue / 恢复队列" value={runReport.recovery_queue_count} />
            <CostStat label="Shadow validation / 影子校验" value={runReport.shadow_validation_pending_count} />
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-4">
            <CostStat label="Live runs / 正式运行" value={runReport.live_run_count} />
            <CostStat label="Shadow runs / 影子运行" value={runReport.shadow_run_count} />
            <CostStat label="Traces / 追踪" value={runReport.trace_count} />
            <CostStat label="Avg run / 平均运行" value={`${Math.round(runReport.average_run_duration_ms)} ms`} />
          </div>
          <div className="mt-4 overflow-hidden rounded-sm border border-line">
            <table className="w-full text-left text-sm">
              <thead className="bg-field text-xs uppercase text-slate-600">
                <tr>
                  <th className="px-3 py-2">Status / 状态</th>
                  <th className="px-3 py-2">Count / 数量</th>
                  <th className="px-3 py-2">Queue / 队列</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(runReport.status_counts)
                  .filter(([, count]) => count > 0)
                  .map(([status, count]) => (
                    <tr key={status} className="border-t border-line">
                      <td className="px-3 py-2 font-medium text-ink">{status}</td>
                      <td className="px-3 py-2 text-slate-700">{count}</td>
                      <td className="px-3 py-2 text-slate-700">{runQueueLabel(status)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          {runReport.recovery_queue.length > 0 ? (
            <div className="mt-4 overflow-hidden rounded-sm border border-line">
              <table className="w-full text-left text-sm">
                <thead className="bg-field text-xs uppercase text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Run / 运行</th>
                    <th className="px-3 py-2">Reason / 原因</th>
                    <th className="px-3 py-2">Action / 操作</th>
                  </tr>
                </thead>
                <tbody>
                  {runReport.recovery_queue.slice(0, 6).map((item) => (
                    <tr key={item.run_id} className="border-t border-line">
                      <td className="break-all px-3 py-2 font-medium text-ink">{item.run_id}</td>
                      <td className="px-3 py-2 text-slate-700">{item.failure_reason_code}</td>
                      <td className="px-3 py-2 text-slate-700">{item.recommended_action_code}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {runReport.run_items.length > 0 ? (
            <div className="mt-4 overflow-hidden rounded-sm border border-line">
              <table className="w-full text-left text-sm">
                <thead className="bg-field text-xs uppercase text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Severity / 严重度</th>
                    <th className="px-3 py-2">Code / 代码</th>
                    <th className="px-3 py-2">Count / 数量</th>
                    <th className="px-3 py-2">Signal / 信号</th>
                  </tr>
                </thead>
                <tbody>
                  {runReport.run_items.map((item) => (
                    <tr key={item.code} className="border-t border-line">
                      <td className="px-3 py-2 text-slate-700">{item.severity}</td>
                      <td className="px-3 py-2 font-medium text-ink">{item.code}</td>
                      <td className="px-3 py-2 text-slate-700">{item.count}</td>
                      <td className="px-3 py-2 text-slate-700">{item.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      ) : null}
      {qualityReport ? (
        <section className="rounded-md border border-line bg-white p-5 shadow-sm">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-ink">Quality Account / 质量账户</h2>
            <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
              {qualityReport.quality_level} · {qualityReport.quality_score}
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            <CostStat label="Node success / 节点成功率" value={formatRate(qualityReport.node_success_rate)} />
            <CostStat label="Eval pass / 评估通过率" value={formatRate(qualityReport.eval_pass_rate)} />
            <CostStat
              label="Ready versions / 就绪版本"
              value={`${qualityReport.release_ready_version_count}/${qualityReport.release_ready_version_count + qualityReport.unready_version_count}`}
            />
            <CostStat label="Suggestions / 建议" value={qualityReport.optimization_suggestion_count} />
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-4">
            <CostStat label="Eval results / 评估结果" value={qualityReport.eval_result_count} />
            <CostStat
              label="Shadow checks / 影子检查"
              value={`${qualityReport.passing_shadow_comparison_count}/${qualityReport.shadow_comparison_count}`}
            />
            <CostStat label="Failed nodes / 失败节点" value={qualityReport.failed_node_counts.length} />
            <CostStat label="Avg eval / 平均评估" value={formatRate(qualityReport.average_eval_score)} />
          </div>
          {qualityReport.failed_node_counts.length > 0 ? (
            <div className="mt-4 overflow-hidden rounded-sm border border-line">
              <table className="w-full text-left text-sm">
                <thead className="bg-field text-xs uppercase text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Node / 节点</th>
                    <th className="px-3 py-2">Failures / 失败数</th>
                    <th className="px-3 py-2">Reason codes / 原因代码</th>
                  </tr>
                </thead>
                <tbody>
                  {qualityReport.failed_node_counts.slice(0, 6).map((item) => (
                    <tr key={item.node_id} className="border-t border-line">
                      <td className="break-all px-3 py-2 font-medium text-ink">{item.node_id}</td>
                      <td className="px-3 py-2 text-slate-700">{item.failed_trace_count}</td>
                      <td className="px-3 py-2 text-slate-700">
                        {Object.entries(item.failure_reason_codes)
                          .map(([code, count]) => `${code}: ${count}`)
                          .join(", ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {qualityReport.quality_items.length > 0 ? (
            <div className="mt-4 overflow-hidden rounded-sm border border-line">
              <table className="w-full text-left text-sm">
                <thead className="bg-field text-xs uppercase text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Severity / 严重度</th>
                    <th className="px-3 py-2">Code / 代码</th>
                    <th className="px-3 py-2">Count / 数量</th>
                    <th className="px-3 py-2">Signal / 信号</th>
                  </tr>
                </thead>
                <tbody>
                  {qualityReport.quality_items.map((item) => (
                    <tr key={item.code} className="border-t border-line">
                      <td className="px-3 py-2 text-slate-700">{item.severity}</td>
                      <td className="px-3 py-2 font-medium text-ink">{item.code}</td>
                      <td className="px-3 py-2 text-slate-700">{item.count}</td>
                      <td className="px-3 py-2 text-slate-700">{item.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      ) : null}
      {costReport ? (
        <section className="rounded-md border border-line bg-white p-5 shadow-sm">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-ink">Cost Account / 成本账户</h2>
            <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
              {costReport.estimated_total_tokens.toLocaleString()} est. tokens / 预估 token
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            <CostStat label="Runs / 运行" value={costReport.run_count} />
            <CostStat label="Traces / 追踪" value={costReport.trace_count} />
            <CostStat label="Human touches / 人工触点" value={costReport.human_touch_count} />
            <CostStat label="Avg trace / 平均追踪" value={`${Math.round(costReport.average_trace_duration_ms)} ms`} />
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-4">
            <CostStat label="Input tokens / 输入 token" value={costReport.estimated_input_tokens.toLocaleString()} />
            <CostStat label="Output tokens / 输出 token" value={costReport.estimated_output_tokens.toLocaleString()} />
            <CostStat label="Retry traces / 重试追踪" value={costReport.retry_trace_count} />
            <CostStat label="Shadow runs / 影子运行" value={`${costReport.shadow_run_count}/${costReport.run_count}`} />
          </div>
          {costReport.node_costs.length > 0 ? (
            <div className="mt-4 overflow-hidden rounded-sm border border-line">
              <table className="w-full text-left text-sm">
                <thead className="bg-field text-xs uppercase text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Node / 节点</th>
                    <th className="px-3 py-2">Traces / 追踪</th>
                    <th className="px-3 py-2">Tokens / Token</th>
                    <th className="px-3 py-2">Avg ms / 平均毫秒</th>
                    <th className="px-3 py-2">Human / 人工</th>
                  </tr>
                </thead>
                <tbody>
                  {costReport.node_costs.slice(0, 6).map((item) => (
                    <tr key={item.node_id} className="border-t border-line">
                      <td className="break-all px-3 py-2 font-medium text-ink">{item.node_id}</td>
                      <td className="px-3 py-2 text-slate-700">{item.trace_count}</td>
                      <td className="px-3 py-2 text-slate-700">{item.estimated_total_tokens.toLocaleString()}</td>
                      <td className="px-3 py-2 text-slate-700">{Math.round(item.average_duration_ms)}</td>
                      <td className="px-3 py-2 text-slate-700">{item.human_touch_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {costReport.cost_items.length > 0 ? (
            <div className="mt-4 overflow-hidden rounded-sm border border-line">
              <table className="w-full text-left text-sm">
                <thead className="bg-field text-xs uppercase text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Code / 代码</th>
                    <th className="px-3 py-2">Count / 数量</th>
                    <th className="px-3 py-2">Signal / 信号</th>
                  </tr>
                </thead>
                <tbody>
                  {costReport.cost_items.map((item) => (
                    <tr key={item.code} className="border-t border-line">
                      <td className="px-3 py-2 font-medium text-ink">{item.code}</td>
                      <td className="px-3 py-2 text-slate-700">{item.count.toLocaleString()}</td>
                      <td className="px-3 py-2 text-slate-700">{item.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      ) : null}
      {retentionReport ? (
        <section className="rounded-md border border-line bg-white p-5 shadow-sm">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-ink">Retention Account / 保留账户</h2>
            <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
              generated / 生成于 {formatDateTime(retentionReport.generated_at)}
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            <CostStat label="Expired terminal / 过期终态" value={retentionReport.run_account.expired_terminal_run_count} />
            <CostStat label="Old active / 旧活跃项" value={retentionReport.run_account.active_run_past_retention_count} />
            <CostStat label="Old evals / 旧评估" value={retentionReport.eval_account.expired_eval_result_count} />
            <CostStat label="Old audit / 旧审计" value={retentionReport.audit_account.expired_audit_event_count} />
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-4">
            <CostStat label="Run policy / 运行策略" value={`${retentionReport.policy.run_retention_days} d`} />
            <CostStat label="Eval policy / 评估策略" value={`${retentionReport.policy.eval_retention_days} d`} />
            <CostStat label="Audit policy / 审计策略" value={`${retentionReport.policy.audit_retention_days} d`} />
            <CostStat label="Sample cap / 样本上限" value={retentionReport.policy.sample_limit} />
          </div>
          <div className="mt-4 overflow-hidden rounded-sm border border-line">
            <table className="w-full text-left text-sm">
              <thead className="bg-field text-xs uppercase text-slate-600">
                <tr>
                  <th className="px-3 py-2">Category / 类别</th>
                  <th className="px-3 py-2">Count / 数量</th>
                  <th className="px-3 py-2">Recommendation / 建议</th>
                  <th className="px-3 py-2">Sample IDs / 样本 ID</th>
                </tr>
              </thead>
              <tbody>
                {retentionReport.retention_items.map((item) => (
                  <tr key={item.category} className="border-t border-line align-top">
                    <td className="break-all px-3 py-2 font-medium text-ink">{item.category}</td>
                    <td className="px-3 py-2 text-slate-700">{item.count}</td>
                    <td className="px-3 py-2 text-slate-700">{item.recommendation}</td>
                    <td className="break-all px-3 py-2 text-slate-700">
                      {item.sample_ids.length > 0 ? item.sample_ids.join(", ") : "none"}
                      {item.sample_truncated ? " ..." : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-4 rounded-md border border-line bg-field p-3">
            <div className="grid gap-3 md:grid-cols-[2fr_1fr_auto]">
              <label className="block">
                <span className="mb-1 block text-xs uppercase text-slate-500">Reason / 原因</span>
                <input
                  className="w-full rounded-md border border-line px-3 py-2 text-sm"
                  value={retentionReason}
                  onChange={(event) => setRetentionReason(event.target.value)}
                />
              </label>
              <label className="flex items-center gap-2 self-end rounded-md border border-line bg-white px-3 py-2 text-sm text-slate-700">
                <input
                  checked={retentionSnapshotAcknowledged}
                  onChange={(event) => setRetentionSnapshotAcknowledged(event.target.checked)}
                  type="checkbox"
                />
                Snapshot reviewed / 已审查快照
              </label>
              <div className="flex self-end gap-2">
                <button
                  className="rounded-md border border-line bg-white px-3 py-2 text-sm font-medium text-ink disabled:opacity-50"
                  disabled={retentionApplying}
                  onClick={() => applyRetention(true)}
                  type="button"
                >
                  {retentionApplying ? "Running... / 运行中..." : "Dry Run / 试运行"}
                </button>
                <button
                  className="rounded-md bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
                  disabled={retentionApplying || !retentionSnapshotAcknowledged || !retentionReason.trim()}
                  onClick={() => applyRetention(false)}
                  type="button"
                >
                  {retentionApplying ? "Applying... / 应用中..." : "Apply / 应用"}
                </button>
              </div>
            </div>
            {retentionApplyError ? <p className="mt-3 text-sm text-red-700">{retentionApplyError}</p> : null}
            {retentionApplyResult ? (
              <div className="mt-3 grid gap-3 text-sm md:grid-cols-3">
                <RetentionApplyStat title="Eligible / 可处理" values={retentionApplyResult.eligible_counts} />
                <RetentionApplyStat title="Deleted / 已删除" values={retentionApplyResult.deleted_counts} />
                <RetentionApplyStat title="Skipped / 已跳过" values={retentionApplyResult.skipped_counts} />
              </div>
            ) : null}
          </div>
          <div className="mt-3 grid gap-3 text-xs text-slate-600 md:grid-cols-3">
            <p>Runs before / 运行早于 {formatDateTime(retentionReport.cutoffs.runs_before)}</p>
            <p>Evals before / 评估早于 {formatDateTime(retentionReport.cutoffs.evals_before)}</p>
            <p>Audit before / 审计早于 {formatDateTime(retentionReport.cutoffs.audit_events_before)}</p>
          </div>
        </section>
      ) : null}
      {riskReport ? (
        <section className="rounded-md border border-line bg-white p-5 shadow-sm">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-ink">Risk Account / 风险账户</h2>
            <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
              {riskReport.risk_level} · {riskReport.risk_score}
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            <RiskStat label="High-risk tools / 高风险工具" value={riskReport.tool_risk.high_risk_tool_count} />
            <RiskStat label="Write tools / 写入工具" value={riskReport.tool_risk.write_tool_count} />
            <RiskStat label="Unready live runs / 未就绪正式运行" value={riskReport.run_risk.live_runs_on_unready_version_count} />
            <RiskStat label="Gate blocks / 发布门禁阻塞" value={riskReport.audit_risk.release_gate_block_count} />
          </div>
          {riskReport.risk_items.length > 0 ? (
            <div className="mt-4 overflow-hidden rounded-sm border border-line">
              <table className="w-full text-left text-sm">
                <thead className="bg-field text-xs uppercase text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Severity / 严重度</th>
                    <th className="px-3 py-2">Code / 代码</th>
                    <th className="px-3 py-2">Count / 数量</th>
                    <th className="px-3 py-2">Message / 消息</th>
                  </tr>
                </thead>
                <tbody>
                  {riskReport.risk_items.map((item) => (
                    <tr key={item.code} className="border-t border-line">
                      <td className="px-3 py-2 text-slate-700">{item.severity}</td>
                      <td className="px-3 py-2 font-medium text-ink">{item.code}</td>
                      <td className="px-3 py-2 text-slate-700">{item.count}</td>
                      <td className="px-3 py-2 text-slate-700">{item.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      ) : null}
      <section className="rounded-md border border-line bg-white p-5 shadow-sm">
        <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-ink">
          <BarChart3 className="h-4 w-4 text-accent" aria-hidden />
          Quality Signals / 质量信号
        </div>
        <div className="space-y-4">
          <ProgressBar value={Math.round((metrics.node_success_rate ?? 0) * 100)} label="Node success rate / 节点成功率" />
          <ProgressBar value={Math.round((metrics.tool_success_rate ?? 0) * 100)} label="Tool success rate / 工具成功率" />
        </div>
      </section>
      <section>
        <h2 className="mb-3 text-sm font-semibold text-ink">Audit Events / 审计事件</h2>
        <JsonViewer data={auditEvents} />
      </section>
      <section className="grid gap-4 lg:grid-cols-2">
        <div>
          <h2 className="mb-3 text-sm font-semibold text-ink">Eval Results / 评估结果</h2>
          <JsonViewer data={evalResults} />
        </div>
        <div>
          <h2 className="mb-3 text-sm font-semibold text-ink">Optimization Suggestions / 优化建议</h2>
          <JsonViewer data={suggestions} />
        </div>
      </section>
      {repairPlan ? (
        <section>
          <h2 className="mb-3 text-sm font-semibold text-ink">Package Repair Plan / 包修复计划</h2>
          <div className="mb-4 rounded-md border border-line bg-white p-4">
            <div className="grid gap-3 md:grid-cols-[1fr_2fr_auto]">
              <label className="block">
                <span className="mb-1 block text-xs uppercase text-slate-500">Target version / 目标版本</span>
                <input
                  className="w-full rounded-md border border-line px-3 py-2 text-sm"
                  value={repairTargetVersion}
                  onChange={(event) => setRepairTargetVersion(event.target.value)}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs uppercase text-slate-500">Reason / 原因</span>
                <input
                  className="w-full rounded-md border border-line px-3 py-2 text-sm"
                  value={repairReason}
                  onChange={(event) => setRepairReason(event.target.value)}
                />
              </label>
              <div className="flex self-end gap-2">
                <button
                  className="rounded-md border border-line px-4 py-2 text-sm font-medium text-ink disabled:opacity-50"
                  disabled={repairPreviewing || !repairTargetVersion.trim() || selectedRepairOperationIds.length === 0}
                  onClick={previewRepairCandidate}
                  type="button"
                >
                  {repairPreviewing ? "Previewing... / 预览中..." : "Preview / 预览"}
                </button>
                <button
                  className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  disabled={repairApplying || !repairTargetVersion.trim() || selectedRepairOperationIds.length === 0}
                  onClick={applyRepairCandidate}
                  type="button"
                >
                  {repairApplying ? "Creating... / 创建中..." : "Create / 创建"}
                </button>
              </div>
            </div>
            <div className="mt-4 overflow-hidden rounded-sm border border-line">
              <table className="w-full text-left text-sm">
                <thead className="bg-field text-xs uppercase text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Apply / 应用</th>
                    <th className="px-3 py-2">Operation / 操作</th>
                    <th className="px-3 py-2">Target / 目标</th>
                    <th className="px-3 py-2">Action / 动作</th>
                  </tr>
                </thead>
                <tbody>
                  {repairPlan.operations.map((operation) => (
                    <tr key={operation.operation_id} className="border-t border-line align-top">
                      <td className="px-3 py-2">
                        <input
                          checked={selectedRepairOperationIds.includes(operation.operation_id)}
                          onChange={() => toggleRepairOperation(operation.operation_id)}
                          type="checkbox"
                        />
                      </td>
                      <td className="break-all px-3 py-2 font-medium text-ink">{operation.operation_id}</td>
                      <td className="break-all px-3 py-2 text-slate-700">
                        {operation.target_type}: {operation.target_id}
                      </td>
                      <td className="px-3 py-2 text-slate-700">{operation.action}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {repairPreviewError ? <p className="mt-3 text-sm text-red-700">{repairPreviewError}</p> : null}
            {repairPreviewResult ? (
              <div className="mt-4 space-y-3">
                {repairPreviewResult.impact_preview ? (
                  <RepairImpactPanel preview={repairPreviewResult.impact_preview} />
                ) : null}
                <JsonViewer data={repairPreviewResult} />
              </div>
            ) : null}
            {repairApplyError ? <p className="mt-3 text-sm text-red-700">{repairApplyError}</p> : null}
            {repairApplyResult ? (
              <div className="mt-4">
                <JsonViewer data={repairApplyResult} />
              </div>
            ) : null}
          </div>
          <JsonViewer data={repairPlan} />
        </section>
      ) : null}
    </div>
  );
}

function MetricCard({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-white p-4">
      <p className="text-xs uppercase text-slate-500">{title}</p>
      <p className="mt-2 text-2xl font-semibold text-ink">{value}</p>
    </div>
  );
}

function RiskStat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <p className="text-xs uppercase text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold text-ink">{value}</p>
    </div>
  );
}

function CostStat({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <p className="text-xs uppercase text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold text-ink">{value}</p>
    </div>
  );
}

function RetentionApplyStat({ title, values }: { title: string; values: Record<string, number> }) {
  const entries = Object.entries(values);
  return (
    <div className="rounded-sm border border-line bg-white p-3">
      <p className="text-xs uppercase text-slate-500">{title}</p>
      <dl className="mt-2 space-y-1">
        {entries.map(([key, value]) => (
          <div key={key} className="flex items-center justify-between gap-2">
            <dt className="break-words text-xs text-slate-600">{key}</dt>
            <dd className="text-sm font-semibold text-ink">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function RepairImpactPanel({ preview }: { preview: RepairImpactPreview }) {
  const riskEntries = Object.entries(preview.risk_counts);
  return (
    <div className="rounded-md border border-line bg-field p-3">
      <div className="grid gap-3 text-sm md:grid-cols-4">
        <RepairImpactStat label="Changes / 变更" value={preview.change_count} />
        <RepairImpactStat label="Fields / 字段" value={`${preview.field_impact_count}/${preview.field_impact_limit}`} />
        <RepairImpactStat label="Warnings / 警告" value={preview.validation_impact.warning_count} />
        <RepairImpactStat label="Valid / 有效" value={preview.validation_impact.valid ? "Yes / 是" : "No / 否"} />
      </div>
      <div className="mt-3 grid gap-3 text-xs text-slate-700 md:grid-cols-3">
        <div>
          <p className="mb-1 font-semibold uppercase text-slate-500">Sections / 区块</p>
          <p className="break-words">{preview.impacted_sections.join(", ") || "none"}</p>
        </div>
        <div>
          <p className="mb-1 font-semibold uppercase text-slate-500">Release Gates / 发布门禁</p>
          <p className="break-words">{preview.release_gate_impacts.join(", ") || "none"}</p>
        </div>
        <div>
          <p className="mb-1 font-semibold uppercase text-slate-500">Risk Counts / 风险计数</p>
          <p className="break-words">
            {riskEntries.length ? riskEntries.map(([key, value]) => `${key}: ${value}`).join(", ") : "none"}
          </p>
        </div>
      </div>
      <div className="mt-3 overflow-hidden rounded-sm border border-line bg-white">
        <table className="w-full text-left text-xs">
          <thead className="bg-field uppercase text-slate-500">
            <tr>
              <th className="px-2 py-2">Field / 字段</th>
              <th className="px-2 py-2">Section / 区块</th>
              <th className="px-2 py-2">Risk / 风险</th>
              <th className="px-2 py-2">Reason / 原因</th>
            </tr>
          </thead>
          <tbody>
            {preview.field_impacts.slice(0, 8).map((impact) => (
              <tr key={`${impact.path}-${impact.reason_code}`} className="border-t border-line align-top">
                <td className="break-all px-2 py-2 font-mono text-slate-700">{impact.path}</td>
                <td className="px-2 py-2 text-slate-700">{impact.section}</td>
                <td className="px-2 py-2 text-slate-700">{impact.risk_level}</td>
                <td className="break-words px-2 py-2 text-slate-700">{impact.reason_code}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {preview.truncated ? <p className="mt-2 text-xs text-slate-500">Field list truncated. 字段列表已截断。</p> : null}
    </div>
  );
}

function RepairImpactStat({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <p className="text-xs uppercase text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold text-ink">{value}</p>
    </div>
  );
}

function formatRate(value: number) {
  return `${Math.round(value * 100)}%`;
}

function formatDateTime(value: string) {
  return new Date(value).toLocaleString();
}

function runQueueLabel(status: string) {
  if (status === "paused") return "approval / 审批";
  if (status === "failed" || status === "rejected" || status === "canceled") return "recovery / 恢复";
  if (status === "completed") return "terminal / 终态";
  return "monitor / 监控";
}
