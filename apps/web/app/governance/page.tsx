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
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Failed to load workflows"));
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
      setRepairPreviewError(caught instanceof Error ? caught.message : "Failed to preview repair candidate");
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
      setRepairApplyError(caught instanceof Error ? caught.message : "Failed to create repair candidate");
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
      setRetentionApplyError(caught instanceof Error ? caught.message : "Failed to apply retention");
    } finally {
      setRetentionApplying(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Governance</h1>
          <p className="mt-1 text-sm text-slate-600">Trace, eval, metrics, optimization loop</p>
        </div>
        <select
          className="rounded-md border border-line bg-white px-3 py-2 text-sm"
          value={selectedWorkflowId}
          onChange={(event) => setSelectedWorkflowId(event.target.value)}
        >
          <option value="">All workflows</option>
          {workflows.map((workflow) => (
            <option key={workflow.workflow_id} value={workflow.workflow_id}>
              {workflow.name}
            </option>
          ))}
        </select>
      </div>
      {error ? <p className="text-sm text-red-700">{error}</p> : null}
      <section className="grid gap-4 md:grid-cols-3">
        <MetricCard title="Node Success" value={`${Math.round((metrics.node_success_rate ?? 0) * 100)}%`} />
        <MetricCard title="Approvals" value={`${metrics.approval_count ?? 0}`} />
        <MetricCard title="Avg Runtime" value={`${Math.round(metrics.average_duration_ms ?? 0)} ms`} />
      </section>
      {runReport ? (
        <section className="rounded-md border border-line bg-white p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-ink">Run Account</h2>
            <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
              {runReport.active_run_count} active · {runReport.terminal_run_count} terminal
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            <CostStat label="Runs" value={runReport.run_count} />
            <CostStat label="Pending approvals" value={runReport.pending_approval_count} />
            <CostStat label="Recovery queue" value={runReport.recovery_queue_count} />
            <CostStat label="Shadow validation" value={runReport.shadow_validation_pending_count} />
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-4">
            <CostStat label="Live runs" value={runReport.live_run_count} />
            <CostStat label="Shadow runs" value={runReport.shadow_run_count} />
            <CostStat label="Traces" value={runReport.trace_count} />
            <CostStat label="Avg run" value={`${Math.round(runReport.average_run_duration_ms)} ms`} />
          </div>
          <div className="mt-4 overflow-hidden rounded-sm border border-line">
            <table className="w-full text-left text-sm">
              <thead className="bg-field text-xs uppercase text-slate-600">
                <tr>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Count</th>
                  <th className="px-3 py-2">Queue</th>
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
                    <th className="px-3 py-2">Run</th>
                    <th className="px-3 py-2">Reason</th>
                    <th className="px-3 py-2">Action</th>
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
                    <th className="px-3 py-2">Severity</th>
                    <th className="px-3 py-2">Code</th>
                    <th className="px-3 py-2">Count</th>
                    <th className="px-3 py-2">Signal</th>
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
        <section className="rounded-md border border-line bg-white p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-ink">Quality Account</h2>
            <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
              {qualityReport.quality_level} · {qualityReport.quality_score}
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            <CostStat label="Node success" value={formatRate(qualityReport.node_success_rate)} />
            <CostStat label="Eval pass" value={formatRate(qualityReport.eval_pass_rate)} />
            <CostStat
              label="Ready versions"
              value={`${qualityReport.release_ready_version_count}/${qualityReport.release_ready_version_count + qualityReport.unready_version_count}`}
            />
            <CostStat label="Suggestions" value={qualityReport.optimization_suggestion_count} />
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-4">
            <CostStat label="Eval results" value={qualityReport.eval_result_count} />
            <CostStat
              label="Shadow checks"
              value={`${qualityReport.passing_shadow_comparison_count}/${qualityReport.shadow_comparison_count}`}
            />
            <CostStat label="Failed nodes" value={qualityReport.failed_node_counts.length} />
            <CostStat label="Avg eval" value={formatRate(qualityReport.average_eval_score)} />
          </div>
          {qualityReport.failed_node_counts.length > 0 ? (
            <div className="mt-4 overflow-hidden rounded-sm border border-line">
              <table className="w-full text-left text-sm">
                <thead className="bg-field text-xs uppercase text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Node</th>
                    <th className="px-3 py-2">Failures</th>
                    <th className="px-3 py-2">Reason codes</th>
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
                    <th className="px-3 py-2">Severity</th>
                    <th className="px-3 py-2">Code</th>
                    <th className="px-3 py-2">Count</th>
                    <th className="px-3 py-2">Signal</th>
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
        <section className="rounded-md border border-line bg-white p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-ink">Cost Account</h2>
            <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
              {costReport.estimated_total_tokens.toLocaleString()} est. tokens
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            <CostStat label="Runs" value={costReport.run_count} />
            <CostStat label="Traces" value={costReport.trace_count} />
            <CostStat label="Human touches" value={costReport.human_touch_count} />
            <CostStat label="Avg trace" value={`${Math.round(costReport.average_trace_duration_ms)} ms`} />
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-4">
            <CostStat label="Input tokens" value={costReport.estimated_input_tokens.toLocaleString()} />
            <CostStat label="Output tokens" value={costReport.estimated_output_tokens.toLocaleString()} />
            <CostStat label="Retry traces" value={costReport.retry_trace_count} />
            <CostStat label="Shadow runs" value={`${costReport.shadow_run_count}/${costReport.run_count}`} />
          </div>
          {costReport.node_costs.length > 0 ? (
            <div className="mt-4 overflow-hidden rounded-sm border border-line">
              <table className="w-full text-left text-sm">
                <thead className="bg-field text-xs uppercase text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Node</th>
                    <th className="px-3 py-2">Traces</th>
                    <th className="px-3 py-2">Tokens</th>
                    <th className="px-3 py-2">Avg ms</th>
                    <th className="px-3 py-2">Human</th>
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
                    <th className="px-3 py-2">Code</th>
                    <th className="px-3 py-2">Count</th>
                    <th className="px-3 py-2">Signal</th>
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
        <section className="rounded-md border border-line bg-white p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-ink">Retention Account</h2>
            <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
              generated {formatDateTime(retentionReport.generated_at)}
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            <CostStat label="Expired terminal" value={retentionReport.run_account.expired_terminal_run_count} />
            <CostStat label="Old active" value={retentionReport.run_account.active_run_past_retention_count} />
            <CostStat label="Old evals" value={retentionReport.eval_account.expired_eval_result_count} />
            <CostStat label="Old audit" value={retentionReport.audit_account.expired_audit_event_count} />
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-4">
            <CostStat label="Run policy" value={`${retentionReport.policy.run_retention_days} d`} />
            <CostStat label="Eval policy" value={`${retentionReport.policy.eval_retention_days} d`} />
            <CostStat label="Audit policy" value={`${retentionReport.policy.audit_retention_days} d`} />
            <CostStat label="Sample cap" value={retentionReport.policy.sample_limit} />
          </div>
          <div className="mt-4 overflow-hidden rounded-sm border border-line">
            <table className="w-full text-left text-sm">
              <thead className="bg-field text-xs uppercase text-slate-600">
                <tr>
                  <th className="px-3 py-2">Category</th>
                  <th className="px-3 py-2">Count</th>
                  <th className="px-3 py-2">Recommendation</th>
                  <th className="px-3 py-2">Sample IDs</th>
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
                <span className="mb-1 block text-xs uppercase text-slate-500">Reason</span>
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
                Snapshot reviewed
              </label>
              <div className="flex self-end gap-2">
                <button
                  className="rounded-md border border-line bg-white px-3 py-2 text-sm font-medium text-ink disabled:opacity-50"
                  disabled={retentionApplying}
                  onClick={() => applyRetention(true)}
                  type="button"
                >
                  {retentionApplying ? "Running..." : "Dry Run"}
                </button>
                <button
                  className="rounded-md bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
                  disabled={retentionApplying || !retentionSnapshotAcknowledged || !retentionReason.trim()}
                  onClick={() => applyRetention(false)}
                  type="button"
                >
                  {retentionApplying ? "Applying..." : "Apply"}
                </button>
              </div>
            </div>
            {retentionApplyError ? <p className="mt-3 text-sm text-red-700">{retentionApplyError}</p> : null}
            {retentionApplyResult ? (
              <div className="mt-3 grid gap-3 text-sm md:grid-cols-3">
                <RetentionApplyStat title="Eligible" values={retentionApplyResult.eligible_counts} />
                <RetentionApplyStat title="Deleted" values={retentionApplyResult.deleted_counts} />
                <RetentionApplyStat title="Skipped" values={retentionApplyResult.skipped_counts} />
              </div>
            ) : null}
          </div>
          <div className="mt-3 grid gap-3 text-xs text-slate-600 md:grid-cols-3">
            <p>Runs before {formatDateTime(retentionReport.cutoffs.runs_before)}</p>
            <p>Evals before {formatDateTime(retentionReport.cutoffs.evals_before)}</p>
            <p>Audit before {formatDateTime(retentionReport.cutoffs.audit_events_before)}</p>
          </div>
        </section>
      ) : null}
      {riskReport ? (
        <section className="rounded-md border border-line bg-white p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-ink">Risk Account</h2>
            <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
              {riskReport.risk_level} · {riskReport.risk_score}
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            <RiskStat label="High-risk tools" value={riskReport.tool_risk.high_risk_tool_count} />
            <RiskStat label="Write tools" value={riskReport.tool_risk.write_tool_count} />
            <RiskStat label="Unready live runs" value={riskReport.run_risk.live_runs_on_unready_version_count} />
            <RiskStat label="Gate blocks" value={riskReport.audit_risk.release_gate_block_count} />
          </div>
          {riskReport.risk_items.length > 0 ? (
            <div className="mt-4 overflow-hidden rounded-sm border border-line">
              <table className="w-full text-left text-sm">
                <thead className="bg-field text-xs uppercase text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Severity</th>
                    <th className="px-3 py-2">Code</th>
                    <th className="px-3 py-2">Count</th>
                    <th className="px-3 py-2">Message</th>
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
      <section className="rounded-md border border-line bg-white p-4">
        <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-ink">
          <BarChart3 className="h-4 w-4 text-accent" aria-hidden />
          Quality Signals
        </div>
        <div className="space-y-4">
          <ProgressBar value={Math.round((metrics.node_success_rate ?? 0) * 100)} label="Node success rate" />
          <ProgressBar value={Math.round((metrics.tool_success_rate ?? 0) * 100)} label="Tool success rate" />
        </div>
      </section>
      <section>
        <h2 className="mb-3 text-sm font-semibold text-ink">Audit Events</h2>
        <JsonViewer data={auditEvents} />
      </section>
      <section className="grid gap-4 lg:grid-cols-2">
        <div>
          <h2 className="mb-3 text-sm font-semibold text-ink">Eval Results</h2>
          <JsonViewer data={evalResults} />
        </div>
        <div>
          <h2 className="mb-3 text-sm font-semibold text-ink">Optimization Suggestions</h2>
          <JsonViewer data={suggestions} />
        </div>
      </section>
      {repairPlan ? (
        <section>
          <h2 className="mb-3 text-sm font-semibold text-ink">Package Repair Plan</h2>
          <div className="mb-4 rounded-md border border-line bg-white p-4">
            <div className="grid gap-3 md:grid-cols-[1fr_2fr_auto]">
              <label className="block">
                <span className="mb-1 block text-xs uppercase text-slate-500">Target version</span>
                <input
                  className="w-full rounded-md border border-line px-3 py-2 text-sm"
                  value={repairTargetVersion}
                  onChange={(event) => setRepairTargetVersion(event.target.value)}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs uppercase text-slate-500">Reason</span>
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
                  {repairPreviewing ? "Previewing..." : "Preview"}
                </button>
                <button
                  className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  disabled={repairApplying || !repairTargetVersion.trim() || selectedRepairOperationIds.length === 0}
                  onClick={applyRepairCandidate}
                  type="button"
                >
                  {repairApplying ? "Creating..." : "Create"}
                </button>
              </div>
            </div>
            <div className="mt-4 overflow-hidden rounded-sm border border-line">
              <table className="w-full text-left text-sm">
                <thead className="bg-field text-xs uppercase text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Apply</th>
                    <th className="px-3 py-2">Operation</th>
                    <th className="px-3 py-2">Target</th>
                    <th className="px-3 py-2">Action</th>
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
        <RepairImpactStat label="Changes" value={preview.change_count} />
        <RepairImpactStat label="Fields" value={`${preview.field_impact_count}/${preview.field_impact_limit}`} />
        <RepairImpactStat label="Warnings" value={preview.validation_impact.warning_count} />
        <RepairImpactStat label="Valid" value={preview.validation_impact.valid ? "Yes" : "No"} />
      </div>
      <div className="mt-3 grid gap-3 text-xs text-slate-700 md:grid-cols-3">
        <div>
          <p className="mb-1 font-semibold uppercase text-slate-500">Sections</p>
          <p className="break-words">{preview.impacted_sections.join(", ") || "none"}</p>
        </div>
        <div>
          <p className="mb-1 font-semibold uppercase text-slate-500">Release Gates</p>
          <p className="break-words">{preview.release_gate_impacts.join(", ") || "none"}</p>
        </div>
        <div>
          <p className="mb-1 font-semibold uppercase text-slate-500">Risk Counts</p>
          <p className="break-words">
            {riskEntries.length ? riskEntries.map(([key, value]) => `${key}: ${value}`).join(", ") : "none"}
          </p>
        </div>
      </div>
      <div className="mt-3 overflow-hidden rounded-sm border border-line bg-white">
        <table className="w-full text-left text-xs">
          <thead className="bg-field uppercase text-slate-500">
            <tr>
              <th className="px-2 py-2">Field</th>
              <th className="px-2 py-2">Section</th>
              <th className="px-2 py-2">Risk</th>
              <th className="px-2 py-2">Reason</th>
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
      {preview.truncated ? <p className="mt-2 text-xs text-slate-500">Field list truncated.</p> : null}
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
  if (status === "paused") return "approval";
  if (status === "failed" || status === "rejected" || status === "canceled") return "recovery";
  if (status === "completed") return "terminal";
  return "monitor";
}
