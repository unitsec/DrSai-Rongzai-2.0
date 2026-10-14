import { getServerUrl } from "../components/utils";
import { useSettingsStore } from "../components/store";
import { ENABLE_SSO } from "./authConfig";

const TOKEN_KEY = "token";
const USER_EMAIL_KEY = "user_email";
const LEGACY_KEYS = ["username", "user_name"] as const;
const USER_SCOPED_KEYS = [
  "drsai_settings",
  "drsai-mode-config",
  "drsai.recentAgents",
  "current_session_id",
] as const;

function clearUserScopedStorage(): void {
  for (const key of USER_SCOPED_KEYS) {
    window.localStorage.removeItem(key);
  }
  useSettingsStore.getState().resetToDefaults();
}

export function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getUserEmail(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(USER_EMAIL_KEY);
}

let refreshTimer: ReturnType<typeof setTimeout> | null = null;

function cancelAuthRefreshSchedule(): void {
  if (refreshTimer !== null) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
}

function decodeTokenExpiryMs(token: string): number | null {
  try {
    const segment = token.split(".")[1];
    if (!segment) return null;
    const payload = JSON.parse(atob(segment.replace(/-/g, "+").replace(/_/g, "/"))) as { exp?: number };
    return typeof payload.exp === "number" ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}

export function scheduleAuthRefresh(accessToken: string): void {
  if (!ENABLE_SSO || typeof window === "undefined") return;
  cancelAuthRefreshSchedule();
  const expiryMs = decodeTokenExpiryMs(accessToken);
  const delay = expiryMs != null ? Math.max(expiryMs - Date.now() - 2 * 60 * 1000, 30_000) : 28 * 60 * 1000;
  refreshTimer = setTimeout(() => {
    void refreshAccessToken().then((result) => {
      if (result.ok) scheduleAuthRefresh(result.accessToken);
    });
  }, delay);
}

export function saveAuthSession(accessToken: string, userEmail: string): void {
  const previousUserEmail = getUserEmail();
  if (previousUserEmail && previousUserEmail !== userEmail) {
    clearUserScopedStorage();
  }
  window.localStorage.setItem(TOKEN_KEY, accessToken);
  window.localStorage.setItem(USER_EMAIL_KEY, userEmail);
  for (const key of LEGACY_KEYS) {
    window.localStorage.removeItem(key);
  }
  scheduleAuthRefresh(accessToken);
}

export function clearAuthSession(): void {
  cancelAuthRefreshSchedule();
  clearUserScopedStorage();
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_EMAIL_KEY);
  for (const key of LEGACY_KEYS) {
    window.localStorage.removeItem(key);
  }
}

export type AuthVerifyResult =
  | { ok: true; userEmail: string; accessToken: string }
  | { ok: false };

export async function refreshAccessToken(): Promise<AuthVerifyResult> {
  if (!ENABLE_SSO) return { ok: false };
  const response = await fetch(`${getServerUrl()}/auth/refresh`, { method: "POST", credentials: "include" });
  if (!response.ok) return { ok: false };
  const payload = await response.json();
  const accessToken = payload?.data?.access_token as string | undefined;
  const userEmail = payload?.data?.user_id as string | undefined;
  if (!accessToken || !userEmail) return { ok: false };
  saveAuthSession(accessToken, userEmail);
  return { ok: true, userEmail, accessToken };
}

export async function verifyAuthSession(): Promise<AuthVerifyResult> {
  const token = getAuthToken();
  const userEmail = getUserEmail();
  if (!ENABLE_SSO) {
    if (!token || !userEmail) {
      clearAuthSession();
      return { ok: false };
    }
    return { ok: true, userEmail, accessToken: token };
  }
  if (!token) {
    const refreshed = await refreshAccessToken();
    return refreshed.ok ? refreshed : { ok: false };
  }
  const meResponse = await fetch(`${getServerUrl()}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
    credentials: "include",
  });
  if (meResponse.ok) {
    const payload = await meResponse.json();
    const verifiedUserEmail = (payload?.data?.user_id as string | undefined) || userEmail;
    if (verifiedUserEmail) {
      saveAuthSession(token, verifiedUserEmail);
      return { ok: true, userEmail: verifiedUserEmail, accessToken: token };
    }
  } else if (meResponse.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed.ok) return refreshed;
  }

  clearAuthSession();
  return { ok: false };
}

export function startAuthRefreshLoop(): void {
  if (!ENABLE_SSO || typeof window === "undefined") return;
  const token = getAuthToken();
  if (token) scheduleAuthRefresh(token);
}

export function stopAuthRefreshLoop(): void {
  cancelAuthRefreshSchedule();
}
