"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { RefreshCw } from "lucide-react";
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
  { value: "", label: "all" },
  { value: "paused", label: "paused" },
  { value: "failed", label: "failed" },
  { value: "canceled", label: "canceled" },
  { value: "completed", label: "completed" },
  { value: "rejected", label: "rejected" },
  { value: "running", label: "running" },
  { value: "created", label: "created" }
];

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
      setError(caught instanceof Error ? caught.message : "Failed to load runs");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Runs</h1>
          <p className="mt-1 text-sm text-slate-600">Runtime queue by status</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="rounded-md border border-line bg-white px-3 py-2 text-sm"
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
            className="inline-flex items-center gap-2 rounded-md border border-line bg-white px-3 py-2 text-sm font-medium text-ink hover:border-accent disabled:opacity-60"
          >
            <RefreshCw className="h-4 w-4" aria-hidden />
            Refresh
          </button>
        </div>
      </div>
      {error ? <p className="text-sm text-red-700">{error}</p> : null}
      <div className="overflow-hidden rounded-md border border-line bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-field text-xs uppercase text-slate-600">
            <tr>
              <th className="px-3 py-2">Run</th>
              <th className="px-3 py-2">Workflow</th>
              <th className="px-3 py-2">Version</th>
              <th className="px-3 py-2">Mode</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Current Node</th>
              <th className="px-3 py-2">Updated</th>
              <th className="px-3 py-2">Action</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.run_id} className="border-t border-line">
                <td className="px-3 py-2">
                  <Link className="font-medium text-accent hover:underline" href={`/runs/${run.run_id}`}>
                    {run.run_id}
                  </Link>
                  {run.rerun_of_run_id ? (
                    <p className="mt-1 text-xs text-slate-500">
                      rerun of{" "}
                      <Link className="text-accent hover:underline" href={`/runs/${run.rerun_of_run_id}`}>
                        {run.rerun_of_run_id}
                      </Link>
                    </p>
                  ) : null}
                </td>
                <td className="px-3 py-2 text-slate-700">{run.workflow_id}</td>
                <td className="px-3 py-2 text-slate-700">v{run.workflow_version ?? "unknown"}</td>
                <td className="px-3 py-2">
                  <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">
                    {run.shadow_mode ? "shadow" : "live"}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <span className="rounded-sm bg-field px-2 py-1 text-xs text-slate-700">{run.status}</span>
                </td>
                <td className="px-3 py-2 text-slate-700">{run.current_node_id ?? "-"}</td>
                <td className="px-3 py-2 text-slate-700">{new Date(run.updated_at).toLocaleString()}</td>
                <td className="px-3 py-2">
                  <Link className="text-sm font-medium text-accent hover:underline" href={`/runs/${run.run_id}`}>
                    Open
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!error && runs.length === 0 ? (
        <p className="rounded-md border border-line bg-white p-4 text-sm text-slate-600">No runs match this status.</p>
      ) : null}
    </div>
  );
}
