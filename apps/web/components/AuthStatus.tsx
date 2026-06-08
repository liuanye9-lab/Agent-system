"use client";

import Link from "next/link";
import { LogIn, LogOut, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { type AuthSession, listStoredAuthSessions, signOut } from "../lib/api";

export function AuthStatus() {
  const [sessions, setSessions] = useState<AuthSession[]>([]);

  const refreshSessions = useCallback(() => {
    setSessions(listStoredAuthSessions());
  }, []);

  useEffect(() => {
    refreshSessions();
    window.addEventListener("storage", refreshSessions);
    return () => window.removeEventListener("storage", refreshSessions);
  }, [refreshSessions]);

  function onSignOut(role?: string) {
    signOut(role);
    refreshSessions();
  }

  if (sessions.length === 0) {
    return (
      <Link
        href="/auth"
        className="control-button"
      >
        <LogIn className="h-4 w-4" aria-hidden />
        Sign in / 登录
      </Link>
    );
  }

  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {sessions.map((session) => (
        <span
          key={session.actor.role}
          className="inline-flex items-center gap-2 rounded-md border border-[#b7d7dc] bg-[#e6f1f3] px-3 py-2 text-xs font-medium text-accent"
          title={session.actor.actor_id}
        >
          <ShieldCheck className="h-4 w-4 text-accent" aria-hidden />
          {session.actor.display_name || session.actor.role}
          <button
            type="button"
            className="rounded-sm p-0.5 text-slate-500 hover:bg-white hover:text-ink"
            onClick={() => onSignOut(session.actor.role)}
            aria-label={`Sign out ${session.actor.role}`}
          >
            <LogOut className="h-3.5 w-3.5" aria-hidden />
          </button>
        </span>
      ))}
      <Link className="rounded-md px-2 py-2 text-sm font-medium text-slate-700 hover:bg-field hover:text-accent" href="/auth">
        Account / 账号
      </Link>
    </div>
  );
}
