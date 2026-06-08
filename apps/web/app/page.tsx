import Link from "next/link";
import { Activity, ArrowRight, CheckCircle2, GitBranch, Plus, ShieldCheck, Workflow, Wrench } from "lucide-react";

const operatingPanels = [
  {
    title: "Builder Plane",
    cn: "构建平面",
    icon: GitBranch,
    detail: "Briefs, process nodes, data contracts, tool policies, candidate versions / 简报、流程节点、数据契约、工具策略、候选版本",
    href: "/workflows/new"
  },
  {
    title: "Runtime Plane",
    cn: "运行平面",
    icon: Activity,
    detail: "Version-bound runs, approval pauses, retry budgets, checkpoints / 绑定版本的运行、审批暂停、重试预算、检查点",
    href: "/runs"
  },
  {
    title: "Governance Plane",
    cn: "治理平面",
    icon: ShieldCheck,
    detail: "Quality gates, evals, risk reports, release readiness / 质量门、评估、风险报告、发布就绪",
    href: "/governance"
  }
];

const readinessItems = [
  "Pydantic schema validation / Pydantic 模型校验",
  "Approval-gated write tools / 写工具审批门禁",
  "Low-sensitive trace and audit data / 低敏追踪与审计"
];

export default function HomePage() {
  return (
    <div className="space-y-6">
      <section className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <div className="surface p-5 lg:p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">Operator workspace / 操作台</p>
              <h1 className="mt-2 text-3xl font-semibold tracking-normal text-ink">Agent Workflow Builder / 智能体工作流构建器</h1>
              <p className="mt-3 max-w-4xl text-sm leading-6 text-slate-600">
                Compile business know-how into versioned workflow packages, executable agent graphs, traceable runs, evals, and optimization suggestions.
                将业务经验编译为可版本化工作流包、可执行智能体图、可追踪运行、评估与优化建议。
              </p>
            </div>
            <span className="status-pill border-[#b7d7dc] bg-[#e6f1f3] text-accent">MVP control plane / MVP 控制面</span>
          </div>

          <div className="mt-5 grid gap-3 md:grid-cols-3">
            {[
              ["Builder", "Candidate packages", "候选工作流包"],
              ["Runtime", "Approval aware DAGs", "审批感知 DAG"],
              ["Governance", "Quality and risk gates", "质量与风险门禁"]
            ].map(([label, value, cn]) => (
              <div key={label} className="rounded-md border border-line bg-field px-4 py-3">
                <p className="text-xs font-medium uppercase text-slate-500">{label}</p>
                <p className="mt-1 text-sm font-semibold text-ink">{value}</p>
                <p className="mt-1 text-xs text-slate-500">{cn}</p>
              </div>
            ))}
          </div>

          <div className="mt-5 flex flex-wrap gap-3">
            <Link
              href="/workflows/new"
              className="control-button-primary"
            >
              <Plus className="h-4 w-4" aria-hidden />
              New Workflow / 新建工作流
            </Link>
            <Link
              href="/workflows"
              className="control-button"
            >
              <Workflow className="h-4 w-4" aria-hidden />
              View Workflows / 查看工作流
            </Link>
          </div>
        </div>

        <aside className="surface p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Readiness / 就绪项</p>
              <h2 className="mt-1 text-base font-semibold text-ink">Release discipline / 发布纪律</h2>
            </div>
            <Wrench className="h-5 w-5 text-accent" aria-hidden />
          </div>
          <ul className="mt-4 space-y-3">
            {readinessItems.map((item) => (
              <li key={item} className="flex gap-2 text-sm leading-5 text-slate-700">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-accent" aria-hidden />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </aside>
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        {operatingPanels.map((panel) => {
          const Icon = panel.icon;
          return (
            <Link key={panel.title} href={panel.href} className="surface group p-5 hover:border-accent hover:shadow-md">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-3">
                  <span className="flex h-10 w-10 items-center justify-center rounded-md bg-field text-accent">
                    <Icon className="h-5 w-5" aria-hidden />
                  </span>
                  <div>
                    <h2 className="text-sm font-semibold text-ink">{panel.title}</h2>
                    <p className="text-xs text-slate-500">{panel.cn}</p>
                  </div>
                </div>
                <ArrowRight className="h-4 w-4 text-slate-400 group-hover:text-accent" aria-hidden />
              </div>
              <p className="mt-4 text-sm leading-6 text-slate-600">{panel.detail}</p>
            </Link>
          );
        })}
      </section>
    </div>
  );
}
