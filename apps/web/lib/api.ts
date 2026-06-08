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

type AuthRole = "workflow-admin" | "business-approver";
const TOKEN_REFRESH_SKEW_SECONDS = 60;

const localCredentials: Record<AuthRole, { username: string; password: string }> = {
  "workflow-admin": { username: "admin", password: "admin" },
  "business-approver": { username: "approver", password: "approver" }
};

export async function getLocalAuthHeaders(role: AuthRole): Promise<Record<string, string>> {
  const storageKey = `agent-workflow-builder-token-${role}`;
  if (typeof window !== "undefined") {
    const cachedToken = window.localStorage.getItem(storageKey);
    if (cachedToken && isUsableActorToken(cachedToken)) {
      return { Authorization: `Bearer ${cachedToken}` };
    }
    if (cachedToken) window.localStorage.removeItem(storageKey);
  }

  const credentials = localCredentials[role];
  const response = await fetch(`${API_BASE_URL}/api/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(credentials)
  });
  if (!response.ok) {
    throw new Error("Failed to create local auth token");
  }
  const payload = (await response.json()) as { access_token: string };
  if (typeof window !== "undefined") {
    window.localStorage.setItem(storageKey, payload.access_token);
  }
  return { Authorization: `Bearer ${payload.access_token}` };
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
  const [, payloadSegment] = token.split(".");
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
