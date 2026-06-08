import Link from "next/link";
import { Plus, Workflow, Activity } from "lucide-react";

export default function HomePage() {
  return (
    <div className="space-y-6">
      <section className="grid gap-6 md:grid-cols-[1.4fr_0.6fr]">
        <div>
          <h1 className="text-3xl font-semibold tracking-normal text-ink">Agent Workflow Builder</h1>
          <p className="mt-3 max-w-2xl text-base leading-7 text-slate-700">
            把业务流程编译成可运行、可评估、可优化的 Agent 工作流。
          </p>
          <div className="mt-5 flex flex-wrap gap-3">
            <Link
              href="/workflows/new"
              className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-[#0F5860]"
            >
              <Plus className="h-4 w-4" aria-hidden />
              New Workflow
            </Link>
            <Link
              href="/workflows"
              className="inline-flex items-center gap-2 rounded-md border border-line bg-white px-4 py-2 text-sm font-medium text-ink hover:border-accent"
            >
              <Workflow className="h-4 w-4" aria-hidden />
              View Workflows
            </Link>
          </div>
        </div>
        <div className="rounded-md border border-line bg-white p-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-ink">
            <Activity className="h-4 w-4 text-accent" aria-hidden />
            MVP Planes
          </div>
          <dl className="mt-4 space-y-3 text-sm">
            <div>
              <dt className="font-medium text-ink">Builder</dt>
              <dd className="text-slate-600">problem, process, contracts, tools, agents, evals</dd>
            </div>
            <div>
              <dt className="font-medium text-ink">Runtime</dt>
              <dd className="text-slate-600">lightweight DAG runner with approval pauses</dd>
            </div>
            <div>
              <dt className="font-medium text-ink">Governance</dt>
              <dd className="text-slate-600">trace, metrics, eval, optimization loop</dd>
            </div>
          </dl>
        </div>
      </section>
    </div>
  );
}
