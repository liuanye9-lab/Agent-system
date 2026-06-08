export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export type AuthRole = "workflow-admin" | "business-approver" | string;

export type AuthSession = {
  accessToken: string;
  actor: {
    actor_id: string;
    role: AuthRole;
    display_name?: string | null;
    scopes: string[];
  };
  expiresAt: number;
};

const TOKEN_REFRESH_SKEW_SECONDS = 60;
const AUTH_STORAGE_PREFIX = "agent-workflow-builder-session";

export async function getLocalAuthHeaders(role: AuthRole): Promise<Record<string, string>> {
  const session = getStoredAuthSession(role);
  if (!session) {
    throw new Error(`Sign in as ${role} first. 请先以 ${role} 身份登录。`);
  }
  return { Authorization: `Bearer ${session.accessToken}` };
}

export async function signIn(username: string, password: string): Promise<AuthSession> {
  const response = await fetch(`${API_BASE_URL}/api/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });
  if (!response.ok) {
    throw new Error("Sign in failed. 登录失败。");
  }
  const payload = (await response.json()) as {
    access_token: string;
    actor: AuthSession["actor"];
  };
  const tokenPayload = decodeActorTokenPayload(payload.access_token);
  if (!tokenPayload || typeof tokenPayload.exp !== "number") {
    throw new Error("Received an invalid auth token. 收到的登录令牌无效。");
  }
  const session: AuthSession = {
    accessToken: payload.access_token,
    actor: payload.actor,
    expiresAt: tokenPayload.exp
  };
  storeAuthSession(session);
  return session;
}

export function getStoredAuthSession(role?: AuthRole): AuthSession | null {
  if (typeof window === "undefined") {
    return null;
  }
  if (role) {
    return readSession(storageKeyForRole(role));
  }
  for (const key of Object.keys(window.localStorage)) {
    if (!key.startsWith(`${AUTH_STORAGE_PREFIX}:`)) {
      continue;
    }
    const session = readSession(key);
    if (session) {
      return session;
    }
  }
  return null;
}

export function listStoredAuthSessions(): AuthSession[] {
  if (typeof window === "undefined") {
    return [];
  }
  return Object.keys(window.localStorage)
    .filter((key) => key.startsWith(`${AUTH_STORAGE_PREFIX}:`))
    .map(readSession)
    .filter((session): session is AuthSession => session !== null);
}

export function signOut(role?: AuthRole) {
  if (typeof window === "undefined") {
    return;
  }
  if (role) {
    window.localStorage.removeItem(storageKeyForRole(role));
    return;
  }
  for (const key of Object.keys(window.localStorage)) {
    if (key.startsWith(`${AUTH_STORAGE_PREFIX}:`)) {
      window.localStorage.removeItem(key);
    }
  }
}

function storeAuthSession(session: AuthSession) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(storageKeyForRole(session.actor.role), JSON.stringify(session));
}

function readSession(storageKey: string): AuthSession | null {
  const raw = window.localStorage.getItem(storageKey);
  if (!raw) {
    return null;
  }
  try {
    const session = JSON.parse(raw) as AuthSession;
    if (!session.accessToken || !session.actor?.role || !isUsableActorToken(session.accessToken)) {
      window.localStorage.removeItem(storageKey);
      return null;
    }
    return session;
  } catch {
    window.localStorage.removeItem(storageKey);
    return null;
  }
}

function storageKeyForRole(role: AuthRole) {
  return `${AUTH_STORAGE_PREFIX}:${role}`;
}

function isUsableActorToken(token: string): boolean {
  const payload = decodeActorTokenPayload(token);
  if (!payload || typeof payload.exp !== "number") {
    return false;
  }
  const refreshAfter = payload.exp - TOKEN_REFRESH_SKEW_SECONDS;
  return refreshAfter > Math.floor(Date.now() / 1000);
}

function decodeActorTokenPayload(token: string): { exp?: number } | null {
  const [payloadSegment] = token.split(".");
  if (!payloadSegment) {
    return null;
  }
  try {
    const normalized = payloadSegment.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), "=");
    return JSON.parse(globalThis.atob(padded)) as { exp?: number };
  } catch {
    return null;
  }
}
