"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Activity, BadgeCheck, Gauge, GitBranch, LayoutDashboard, Plus, ShieldCheck, Workflow } from "lucide-react";
import { AuthStatus } from "./AuthStatus";

const navItems = [
  { href: "/", label: "Overview", cn: "总览", icon: LayoutDashboard },
  { href: "/workflows", label: "Workflows", cn: "工作流", icon: Workflow },
  { href: "/runs", label: "Runs", cn: "运行", icon: Activity },
  { href: "/workflows/new", label: "New Workflow", cn: "新建", icon: Plus },
  { href: "/governance", label: "Governance", cn: "治理", icon: ShieldCheck },
  { href: "/auth", label: "Access", cn: "访问", icon: BadgeCheck }
];

function isActive(pathname: string, href: string) {
  if (href === "/") {
    return pathname === "/";
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen text-ink lg:grid lg:grid-cols-[268px_1fr]">
      <aside className="border-b border-line/80 bg-white/95 backdrop-blur lg:sticky lg:top-0 lg:h-screen lg:border-b-0 lg:border-r">
        <div className="flex h-full flex-col">
          <div className="border-b border-line/80 px-5 py-5">
            <Link href="/" className="group flex items-center gap-3">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md bg-ink text-white shadow-[0_8px_20px_rgba(31,41,51,0.18)]">
                <GitBranch className="h-5 w-5" aria-hidden />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-base font-semibold leading-5 text-ink group-hover:text-accent">
                  Agent Workflow
                </span>
                <span className="block truncate text-xs font-medium text-muted">智能体工作流控制台</span>
              </span>
            </Link>
          </div>

          <nav className="flex gap-2 overflow-x-auto px-4 py-3 lg:flex-1 lg:flex-col lg:gap-1 lg:overflow-visible lg:py-5">
            {navItems.map((item) => {
              const Icon = item.icon;
              const active = isActive(pathname, item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={[
                    "flex min-w-fit items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium",
                    active
                      ? "bg-[#e6f1f3] text-accent shadow-[inset_3px_0_0_#146C75,0_1px_2px_rgba(20,108,117,0.10)]"
                      : "text-slate-700 hover:bg-[#f3f7f8] hover:text-ink"
                  ].join(" ")}
                >
                  <Icon className="h-4 w-4 shrink-0" aria-hidden />
                  <span className="leading-4">
                    <span className="block">{item.label}</span>
                    <span className="text-xs font-normal opacity-75">{item.cn}</span>
                  </span>
                </Link>
              );
            })}
          </nav>

          <div className="hidden border-t border-line/80 p-4 lg:block">
            <div className="rounded-md border border-line bg-[#f7fafb] p-3">
              <div className="flex items-center gap-2 text-xs font-semibold uppercase text-slate-600">
                <Gauge className="h-4 w-4 text-accent" aria-hidden />
                Control Plane
              </div>
              <p className="mt-2 text-xs leading-5 text-slate-600">Builder, runtime, governance / 构建、运行、治理</p>
            </div>
          </div>
        </div>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-20 border-b border-line/80 bg-white/90 backdrop-blur">
          <div className="flex min-h-16 flex-wrap items-center justify-between gap-3 px-5 py-3 lg:px-8">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">Project-grade operations</p>
              <p className="mt-0.5 text-sm text-slate-600">项目级智能体工作流运营台</p>
            </div>
            <AuthStatus />
          </div>
        </header>
        <main className="mx-auto max-w-[1480px] px-5 py-6 lg:px-8 lg:py-8">{children}</main>
      </div>
    </div>
  );
}
