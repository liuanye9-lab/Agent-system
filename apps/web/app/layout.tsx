import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agent Workflow Builder",
  description: "Compile business processes into executable Agent workflows."
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="border-b border-line bg-white">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
            <Link href="/" className="text-base font-semibold text-ink">
              Agent Workflow Builder
            </Link>
            <nav className="flex items-center gap-4 text-sm">
              <Link className="text-slate-700 hover:text-accent" href="/workflows">
                Workflows
              </Link>
              <Link className="text-slate-700 hover:text-accent" href="/runs">
                Runs
              </Link>
              <Link className="text-slate-700 hover:text-accent" href="/workflows/new">
                New
              </Link>
              <Link className="text-slate-700 hover:text-accent" href="/governance">
                Governance
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-6 py-6">{children}</main>
      </body>
    </html>
  );
}
