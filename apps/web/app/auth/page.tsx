"use client";

import { type FormEvent, useState } from "react";
import { LogIn, LogOut } from "lucide-react";
import { type AuthSession, listStoredAuthSessions, signIn, signOut } from "../../lib/api";

export default function AuthPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [sessions, setSessions] = useState<AuthSession[]>(() => listStoredAuthSessions());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(username.trim(), password);
      setPassword("");
      setSessions(listStoredAuthSessions());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign in failed. 登录失败。");
    } finally {
      setBusy(false);
    }
  }

  function onSignOut(role?: string) {
    signOut(role);
    setSessions(listStoredAuthSessions());
  }

  return (
    <div className="mx-auto grid max-w-5xl gap-5 lg:grid-cols-[1fr_0.9fr]">
      <section className="surface p-5">
        <div className="mb-5">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">Access control / 访问控制</p>
          <h1 className="page-heading mt-1">Account Sign In / 账号登录</h1>
          <p className="page-subtitle">
            Use configured API users to unlock workflow operations. 使用已配置的 API 用户执行工作流操作。
          </p>
        </div>
        <form className="space-y-4" onSubmit={onSubmit}>
          <label className="block text-sm font-medium text-ink">
            Username / 用户名
            <input
              className="control-input mt-1 w-full"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label className="block text-sm font-medium text-ink">
            Password / 密码
            <input
              className="control-input mt-1 w-full"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          {error ? <p className="text-sm text-red-700">{error}</p> : null}
          <button
            type="submit"
            disabled={busy}
            className="control-button-primary"
          >
            <LogIn className="h-4 w-4" aria-hidden />
            {busy ? "Signing in / 登录中" : "Sign in / 登录"}
          </button>
        </form>
      </section>

      <section className="surface p-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-ink">Active Sessions / 当前会话</h2>
            <p className="mt-1 text-sm text-slate-600">Stored in this browser only. 仅保存在当前浏览器。</p>
          </div>
          {sessions.length > 0 ? (
            <button
              type="button"
              onClick={() => onSignOut()}
              className="control-button"
            >
              <LogOut className="h-4 w-4" aria-hidden />
              Sign out all / 全部退出
            </button>
          ) : null}
        </div>
        <div className="space-y-3">
          {sessions.map((session) => (
            <div key={session.actor.role} className="rounded-md border border-line bg-field p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="font-medium text-ink">{session.actor.display_name || session.actor.actor_id}</p>
                  <p className="text-xs text-slate-600">{session.actor.role}</p>
                </div>
                <button
                  type="button"
                  onClick={() => onSignOut(session.actor.role)}
                  className="control-button"
                >
                  Sign out / 退出
                </button>
              </div>
            </div>
          ))}
          {sessions.length === 0 ? (
            <p className="rounded-md border border-line bg-field p-3 text-sm text-slate-600">
              No active browser session. 当前没有浏览器会话。
            </p>
          ) : null}
        </div>
      </section>
    </div>
  );
}
