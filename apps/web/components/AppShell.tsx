"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Bot, History, MessageSquare, Settings, ShieldCheck } from "lucide-react";
import { AuthStatus } from "./AuthStatus";

const recentItems = [
  { title: "客户跟进助手", detail: "沟通记录、提醒、周报" },
  { title: "订单处理自动化", detail: "状态识别、发货通知" },
  { title: "投研观察 Agent", detail: "新闻、财报、风险清单" }
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const inSimpleBuilder = pathname === "/" || pathname.startsWith("/agent-systems");

  return (
    <div className="min-h-screen text-ink lg:grid lg:grid-cols-[272px_1fr]">
      <aside className="border-b border-line/80 bg-white/95 backdrop-blur lg:sticky lg:top-0 lg:h-screen lg:border-b-0 lg:border-r">
        <div className="flex h-full flex-col">
          <div className="px-5 py-5">
            <Link href="/agent-systems" className="group flex items-center gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-accent text-white shadow-[0_8px_20px_rgba(20,108,117,0.18)]">
                <Bot className="h-5 w-5" aria-hidden />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-base font-semibold leading-5 text-ink group-hover:text-accent">Agent Builder</span>
                <span className="block truncate text-xs font-medium text-muted">对话式智能体搭建</span>
              </span>
            </Link>
          </div>

          <div className="hidden gap-2 overflow-x-auto px-4 pb-4 lg:flex lg:flex-1 lg:flex-col lg:gap-2 lg:overflow-visible">
            <div className="mb-1 hidden items-center gap-2 px-2 text-xs font-semibold text-slate-500 lg:flex">
              <History className="h-4 w-4" aria-hidden />
              最近对话
            </div>
            {recentItems.map((item, index) => (
              <Link
                key={item.title}
                href="/agent-systems"
                className={[
                  "flex min-w-[220px] items-start gap-3 rounded-md border px-3 py-3 text-left lg:min-w-0",
                  index === 0 && inSimpleBuilder
                    ? "border-[#b7d7dc] bg-[#eaf5f6] text-ink"
                    : "border-transparent bg-white text-slate-700 hover:border-line hover:bg-[#f6fafb]"
                ].join(" ")}
              >
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-white text-accent">
                  <MessageSquare className="h-4 w-4" aria-hidden />
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-sm font-semibold">{item.title}</span>
                  <span className="mt-1 block truncate text-xs text-slate-500">{item.detail}</span>
                </span>
              </Link>
            ))}
          </div>

          <div className="hidden border-t border-line/80 p-4 lg:block">
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-1">
              <Link className="sidebar-utility" href="/auth">
                <Settings className="h-4 w-4" aria-hidden />
                账号设置
              </Link>
              <Link className="sidebar-utility" href="/governance">
                <ShieldCheck className="h-4 w-4" aria-hidden />
                高级治理
              </Link>
            </div>
          </div>
        </div>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-20 border-b border-line/80 bg-white/90 backdrop-blur">
          <div className="flex min-h-16 flex-wrap items-center justify-between gap-3 px-5 py-3 lg:px-8">
            <div>
              <p className="text-sm font-semibold text-ink">新建 Agent</p>
              <p className="mt-0.5 text-xs text-slate-500">描述目标，系统自动整理方案</p>
            </div>
            <AuthStatus />
          </div>
        </header>
        <main className="mx-auto max-w-[1180px] px-5 py-5 lg:px-8 lg:py-6">{children}</main>
      </div>
    </div>
  );
}
