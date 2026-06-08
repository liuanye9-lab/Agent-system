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
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Failed to load workflows"));
  }, []);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold text-ink">Workflows</h1>
        <Link
          href="/workflows/new"
          className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-[#0F5860]"
        >
          <Plus className="h-4 w-4" aria-hidden />
          New Workflow
        </Link>
      </div>
      {error ? <p className="text-sm text-red-700">{error}</p> : null}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {workflows.map((workflow) => (
          <WorkflowCard key={workflow.workflow_id} workflow={workflow} />
        ))}
      </div>
      {!error && workflows.length === 0 ? (
        <p className="rounded-md border border-line bg-white p-4 text-sm text-slate-600">No workflows generated yet.</p>
      ) : null}
    </div>
  );
}
