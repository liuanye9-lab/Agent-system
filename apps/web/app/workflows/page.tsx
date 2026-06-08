"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Plus } from "lucide-react";
import { apiFetch, getLocalAuthHeaders } from "../../lib/api";
import { WorkflowCard } from "../../components/WorkflowCard";

type WorkflowSummary = {
  workflow_id: string;
  name: string;
  version: string;
  created_at: string;
};

export default function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getLocalAuthHeaders("workflow-admin")
      .then((headers) => apiFetch<WorkflowSummary[]>("/api/workflows", { headers }))
      .then(setWorkflows)
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Failed to load workflows / 加载工作流失败"));
  }, []);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Workflows / 工作流</h1>
          <p className="mt-1 text-sm text-slate-600">Generated packages ready to run, review, and govern. 已生成的工作流包，可运行、审查和治理。</p>
        </div>
        <Link
          href="/workflows/new"
          className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-[#0F5860]"
        >
          <Plus className="h-4 w-4" aria-hidden />
          New Workflow / 新建工作流
        </Link>
      </div>
      {error ? <p className="text-sm text-red-700">{error}</p> : null}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {workflows.map((workflow) => (
          <WorkflowCard key={workflow.workflow_id} workflow={workflow} />
        ))}
      </div>
      {!error && workflows.length === 0 ? (
        <p className="rounded-md border border-line bg-white p-4 text-sm text-slate-600 shadow-sm">
          No workflows generated yet. 尚未生成工作流。
        </p>
      ) : null}
    </div>
  );
}
