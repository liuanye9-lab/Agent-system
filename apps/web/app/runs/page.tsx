"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, RefreshCw } from "lucide-react";
import { apiFetch, getLocalAuthHeaders } from "../../lib/api";

type RunSummary = {
  run_id: string;
  workflow_id: string;
  workflow_version?: string | null;
  rerun_of_run_id?: string | null;
  shadow_mode: boolean;
  status: string;
  current_node_id?: string | null;
  created_at: string;
  updated_at: string;
};

const statuses = [
  { value: "", label: "all / 全部" },
  { value: "paused", label: "paused / 暂停" },
  { value: "failed", label: "failed / 失败" },
  { value: "canceled", label: "canceled / 已取消" },
  { value: "completed", label: "completed / 已完成" },
  { value: "rejected", label: "rejected / 已拒绝" },
  { value: "running", label: "running / 运行中" },
  { value: "created", label: "created / 已创建" }
];

function statusClass(status: string) {
  switch (status) {
    case "completed":
      return "status-success";
    case "failed":
    case "rejected":
    case "canceled":
      return "status-danger";
    case "paused":
      return "status-warning";
    case "running":
      return "status-accent";
    default:
      return "border-line bg-field text-slate-700";
  }
}

export default function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [status, setStatus] = useState("paused");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadRuns(status);
  }, [status]);

  async function loadRuns(nextStatus: string) {
    setLoading(true);
    setError(null);
    try {
      const headers = await getLocalAuthHeaders("workflow-admin");
      const query = new URLSearchParams({ limit: "50", offset: "0" });
      if (nextStatus) {
        query.set("status", nextStatus);
      }
      const response = await apiFetch<RunSummary[]>(`/api/runs?${query.toString()}`, { headers });
      setRuns(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load runs / 加载运行失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="page-band">
        <div>
          <p className="section-kicker">Runtime queue / 运行队列</p>
          <h1 className="page-heading mt-1">Runs / 运行</h1>
          <p className="page-subtitle">Runtime queue by status, version, mode, and active node. 按状态、版本、模式和当前节点查看运行队列。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="control-input"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            {statuses.map((item) => (
              <option key={item.value || "all"} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => loadRuns(status)}
            disabled={loading}
            className="control-button"
          >
            <RefreshCw className="h-4 w-4" aria-hidden />
            Refresh / 刷新
          </button>
        </div>
      </div>
      {error ? <p className="surface border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p> : null}
      <div className="surface overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table min-w-[980px]">
            <thead>
              <tr>
                <th className="px-4 py-3">Run / 运行</th>
                <th className="px-4 py-3">Workflow / 工作流</th>
                <th className="px-4 py-3">Version / 版本</th>
                <th className="px-4 py-3">Mode / 模式</th>
                <th className="px-4 py-3">Status / 状态</th>
                <th className="px-4 py-3">Current Node / 当前节点</th>
                <th className="px-4 py-3">Updated / 更新时间</th>
                <th className="px-4 py-3">Action / 操作</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id}>
                  <td className="px-4 py-3 align-top">
                    <Link className="font-medium text-accent hover:underline" href={`/runs/${run.run_id}`}>
                      {run.run_id}
                    </Link>
                    {run.rerun_of_run_id ? (
                      <p className="mt-1 text-xs text-slate-500">
                        rerun of / 重跑自{" "}
                        <Link className="text-accent hover:underline" href={`/runs/${run.rerun_of_run_id}`}>
                          {run.rerun_of_run_id}
                        </Link>
                      </p>
                    ) : null}
                  </td>
                  <td className="px-4 py-3 align-top text-slate-700">{run.workflow_id}</td>
                  <td className="px-4 py-3 align-top text-slate-700">v{run.workflow_version ?? "unknown"}</td>
                  <td className="px-4 py-3 align-top">
                    <span className="status-pill">{run.shadow_mode ? "shadow / 影子" : "live / 正式"}</span>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <span className={`status-pill ${statusClass(run.status)}`}>{run.status}</span>
                  </td>
                  <td className="px-4 py-3 align-top text-slate-700">{run.current_node_id ?? "-"}</td>
                  <td className="px-4 py-3 align-top text-slate-700">{new Date(run.updated_at).toLocaleString()}</td>
                  <td className="px-4 py-3 align-top">
                    <Link className="text-sm font-semibold text-accent hover:underline" href={`/runs/${run.run_id}`}>
                      Open / 打开
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {!error && runs.length === 0 ? (
        <div className="surface flex items-center gap-3 p-5 text-sm text-slate-600">
          <Activity className="h-5 w-5 text-accent" aria-hidden />
          <span>No runs match this status. 没有匹配该状态的运行。</span>
        </div>
      ) : null}
    </div>
  );
}
