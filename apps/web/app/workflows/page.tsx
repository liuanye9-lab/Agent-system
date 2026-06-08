"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { PackageCheck, Plus } from "lucide-react";
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
      <div className="page-band">
        <div>
          <p className="section-kicker">Package registry / 包注册表</p>
          <h1 className="page-heading mt-1">Workflows / 工作流</h1>
          <p className="page-subtitle">Generated packages ready to run, review, and govern. 已生成的工作流包，可运行、审查和治理。</p>
        </div>
        <Link
          href="/workflows/new"
          className="control-button-primary"
        >
          <Plus className="h-4 w-4" aria-hidden />
          New Workflow / 新建工作流
        </Link>
      </div>
      {error ? <p className="surface border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p> : null}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {workflows.map((workflow) => (
          <WorkflowCard key={workflow.workflow_id} workflow={workflow} />
        ))}
      </div>
      {!error && workflows.length === 0 ? (
        <div className="surface flex items-center gap-3 p-5 text-sm text-slate-600">
          <PackageCheck className="h-5 w-5 text-accent" aria-hidden />
          <span>No workflows generated yet. 尚未生成工作流。</span>
        </div>
      ) : null}
    </div>
  );
}
