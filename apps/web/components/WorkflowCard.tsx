import Link from "next/link";
import { GitBranch } from "lucide-react";

type WorkflowCardProps = {
  workflow: {
    workflow_id: string;
    name: string;
    version: string;
    created_at: string;
  };
};

export function WorkflowCard({ workflow }: WorkflowCardProps) {
  return (
    <Link
      href={`/workflows/${workflow.workflow_id}`}
      className="block rounded-md border border-line bg-white p-4 shadow-sm hover:border-accent hover:shadow-md"
    >
      <div className="flex items-start gap-3">
        <GitBranch className="mt-1 h-4 w-4 text-accent" aria-hidden />
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-ink">{workflow.name}</h2>
          <p className="mt-1 text-xs text-slate-600">{workflow.workflow_id}</p>
          <p className="mt-3 text-xs text-slate-500">
            Version / 版本 v{workflow.version} · {new Date(workflow.created_at).toLocaleString()}
          </p>
        </div>
      </div>
    </Link>
  );
}
