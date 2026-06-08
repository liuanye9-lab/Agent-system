import Link from "next/link";
import { Activity, GitBranch, Plus, ShieldCheck, Workflow } from "lucide-react";

export default function HomePage() {
  return (
    <div className="space-y-8">
      <section className="grid gap-6 md:grid-cols-[1.35fr_0.65fr]">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">Project-grade agent operations / 项目级智能体运营</p>
          <h1 className="mt-3 text-4xl font-semibold tracking-normal text-ink">Agent Workflow Builder / 智能体工作流构建器</h1>
          <p className="mt-4 max-w-3xl text-base leading-7 text-slate-700">
            Compile business processes into executable, evaluable, and optimizable agent workflows.
            将业务流程编译成可运行、可评估、可优化的智能体工作流。
          </p>
          <div className="mt-5 flex flex-wrap gap-3">
            <Link
              href="/workflows/new"
              className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-[#0F5860]"
            >
              <Plus className="h-4 w-4" aria-hidden />
              New Workflow / 新建工作流
            </Link>
            <Link
              href="/workflows"
              className="inline-flex items-center gap-2 rounded-md border border-line bg-white px-4 py-2 text-sm font-medium text-ink shadow-sm hover:border-accent hover:bg-field"
            >
              <Workflow className="h-4 w-4" aria-hidden />
              View Workflows / 查看工作流
            </Link>
          </div>
        </div>
        <div className="rounded-md border border-line bg-white p-5 shadow-sm">
          <div className="flex items-center gap-2 text-sm font-semibold text-ink">
            <Activity className="h-4 w-4 text-accent" aria-hidden />
            Control Planes / 控制平面
          </div>
          <dl className="mt-4 space-y-3 text-sm">
            <div>
              <dt className="flex items-center gap-2 font-medium text-ink">
                <GitBranch className="h-4 w-4 text-accent" aria-hidden />
                Builder / 构建
              </dt>
              <dd className="mt-1 text-slate-600">Problem, process, contracts, tools, agents, evals / 问题、流程、契约、工具、智能体、评估</dd>
            </div>
            <div>
              <dt className="font-medium text-ink">Runtime / 运行时</dt>
              <dd className="mt-1 text-slate-600">DAG execution, approval pauses, retries, checkpoints / DAG 执行、审批暂停、重试、检查点</dd>
            </div>
            <div>
              <dt className="flex items-center gap-2 font-medium text-ink">
                <ShieldCheck className="h-4 w-4 text-accent" aria-hidden />
                Governance / 治理
              </dt>
              <dd className="mt-1 text-slate-600">Trace, metrics, eval, optimization loop / 追踪、指标、评估、优化闭环</dd>
            </div>
          </dl>
        </div>
      </section>
    </div>
  );
}
