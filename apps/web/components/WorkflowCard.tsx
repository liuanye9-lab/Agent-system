import Link from "next/link";
import { ArrowRight, CalendarClock, GitBranch } from "lucide-react";

type WorkflowCardProps = {
  workflow: {
    workflow_id: string;
    name: string;
    version: string;
    created_at: string;
  };
};

export function WorkflowCard({ workflow }: WorkflowCardProps) {
  const createdAt = new Date(workflow.created_at).toLocaleString();

  return (
    <Link
      href={`/workflows/${workflow.workflow_id}`}
      className="surface group block p-4 hover:border-accent hover:shadow-md"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-field text-accent">
            <GitBranch className="h-4 w-4" aria-hidden />
          </span>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-ink">{workflow.name}</h2>
            <p className="mt-1 truncate text-xs text-slate-600">{workflow.workflow_id}</p>
          </div>
        </div>
        <ArrowRight className="mt-2 h-4 w-4 shrink-0 text-slate-400 group-hover:text-accent" aria-hidden />
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="status-pill">v{workflow.version}</span>
        <span className="inline-flex min-w-0 items-center gap-1 text-xs text-slate-500">
          <CalendarClock className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="truncate">{createdAt}</span>
        </span>
      </div>
    </Link>
  );
}
