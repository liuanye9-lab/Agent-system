import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agent Workflow Builder / 智能体工作流构建器",
  description: "Compile business processes into executable Agent workflows. 将业务流程编译为可执行智能体工作流。"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">
        <header className="sticky top-0 z-20 border-b border-line/80 bg-white/90 shadow-sm backdrop-blur">
          <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-4">
            <Link href="/" className="min-w-0 text-sm font-semibold text-ink">
              <span className="block text-base leading-5">Agent Workflow Builder</span>
              <span className="block text-xs font-medium text-muted">智能体工作流构建器</span>
            </Link>
            <nav className="flex flex-wrap items-center justify-end gap-2 text-sm">
              <Link className="rounded-md px-3 py-2 text-slate-700 hover:bg-field hover:text-accent" href="/workflows">
                Workflows / 工作流
              </Link>
              <Link className="rounded-md px-3 py-2 text-slate-700 hover:bg-field hover:text-accent" href="/runs">
                Runs / 运行
              </Link>
              <Link className="rounded-md bg-accent px-3 py-2 font-medium text-white hover:bg-[#0F5860]" href="/workflows/new">
                New / 新建
              </Link>
              <Link className="rounded-md px-3 py-2 text-slate-700 hover:bg-field hover:text-accent" href="/governance">
                Governance / 治理
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
