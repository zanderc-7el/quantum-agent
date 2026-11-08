/**
 * HTTP client for the QuantaMind calibration API.
 *
 * The frontend stays usable without a backend: every function here is only
 * reached when `VITE_GATEWAY_MODE=remote`. Tokens live in sessionStorage next
 * to (but separate from) the demo session, so either can be removed without
 * leaving a half-authenticated tab behind.
 */
import type { User, UserRole } from "./auth.ts";

export const REMOTE_SESSION_KEY = "qcal-remote-session-v1";
export const DEFAULT_API_BASE_URL = "http://127.0.0.1:18789";

export interface RemoteUser {
  userId: string;
  username: string;
  displayName: string;
  roleId: string;
}

export interface RemoteSession {
  accessToken: string;
  /** Present after a backend login/refresh; old sessions may not have one. */
  refreshToken?: string;
  user: RemoteUser;
  savedAt: number;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(
    message: string,
    code = "NETWORK_ERROR",
    status = 0,
    details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

function env(): Partial<ImportMetaEnv> {
  try {
    return (import.meta as { env?: ImportMetaEnv }).env ?? {};
  } catch {
    return {};
  }
}

export function remoteEnabled(): boolean {
  return env().VITE_GATEWAY_MODE === "remote";
}

export function apiBaseUrl(): string {
  return (env().VITE_API_BASE_URL || DEFAULT_API_BASE_URL).replace(/\/+$/, "");
}

function sessionStore(): Storage | undefined {
  try {
    return globalThis.sessionStorage;
  } catch {
    return undefined;
  }
}

export function readRemoteSession(): RemoteSession | null {
  const storage = sessionStore();
  if (!storage) return null;
  try {
    const raw = storage.getItem(REMOTE_SESSION_KEY);
    if (!raw || raw.length > 4096) throw new Error("invalid session");
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value))
      throw new Error("invalid session");
    const session = value as Partial<RemoteSession>;
    const user = session.user as Partial<RemoteUser> | undefined;
    if (
      typeof session.accessToken !== "string" ||
      !session.accessToken ||
      (session.refreshToken !== undefined &&
        typeof session.refreshToken !== "string") ||
      typeof session.savedAt !== "number" ||
      !Number.isSafeInteger(session.savedAt) ||
      !user ||
      typeof user.userId !== "string" ||
      typeof user.username !== "string" ||
      typeof user.displayName !== "string" ||
      typeof user.roleId !== "string"
    )
      throw new Error("invalid session");
    return session as RemoteSession;
  } catch {
    clearRemoteSession();
    return null;
  }
}

export function saveRemoteSession(session: RemoteSession): boolean {
  const storage = sessionStore();
  if (!storage) return false;
  try {
    storage.setItem(REMOTE_SESSION_KEY, JSON.stringify(session));
    return true;
  } catch {
    return false;
  }
}

export function clearRemoteSession(): void {
  const storage = sessionStore();
  if (!storage) return;
  try {
    storage.removeItem(REMOTE_SESSION_KEY);
  } catch {
    /* A denied removal still ends this page's remote session. */
  }
}

const unauthorizedListeners = new Set<() => void>();

/** Called once per 401 so the app can return to the login screen. */
export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

function notifyUnauthorized(): void {
  clearRemoteSession();
  for (const listener of unauthorizedListeners) listener();
}

interface Envelope<T> {
  success: boolean;
  data: T | null;
  error: { code?: string; message?: string; details?: unknown } | null;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  token?: string | null;
  signal?: AbortSignal;
  /** Do not clear the stored token on a 401 (used by the login call itself). */
  anonymous?: boolean;
  /** Retry once through `/auth/refresh` on a 401. Defaults to true. */
  refresh?: boolean;
}

function friendlyMessage(status: number, code: string, fallback: string): string {
  if (status === 401) return "登录已过期，请重新登录。";
  if (status === 429) return "操作过于频繁，请稍后再试。";
  if (status === 0) return fallback;
  if (code === "CONFLICT") return "任务已被其他操作更新，请刷新后重试。";
  return fallback;
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const session = readRemoteSession();
  const token = options.token === undefined ? session?.accessToken : options.token;
  const headers: Record<string, string> = { Accept: "application/json" };
  const body =
    options.body === undefined
      ? undefined
      : typeof options.body === "string"
        ? options.body
        : JSON.stringify(options.body);
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      method: options.method ?? "GET",
      headers,
      body,
      signal: options.signal,
    });
  } catch (cause) {
    throw new ApiError(
      `无法连接后端服务（${apiBaseUrl()}），请确认服务已启动。`,
      "NETWORK_ERROR",
      0,
      { cause: cause instanceof Error ? cause.message : String(cause) },
    );
  }

  const raw = await response.text();
  let payload: unknown = null;
  try {
    payload = raw ? JSON.parse(raw) : null;
  } catch {
    payload = null;
  }
  const envelope =
    payload && typeof payload === "object" && "success" in payload
      ? (payload as Envelope<T>)
      : null;

  if (!response.ok || (envelope && envelope.success === false) || !envelope) {
    const error = envelope?.error ?? {};
    const code = typeof error.code === "string" ? error.code : `HTTP_${response.status}`;
    const backendMessage =
      typeof error.message === "string" && error.message ? error.message : "";
    const fallback =
      backendMessage ||
      (response.status === 401
        ? "登录已过期，请重新登录。"
        : `请求失败（HTTP ${response.status}）。`);
    const details =
      error.details && typeof error.details === "object"
        ? (error.details as Record<string, unknown>)
        : {};
    if (
      response.status === 401 &&
      !options.anonymous &&
      options.refresh !== false &&
      (await refreshRemoteSession())
    ) {
      // The account is still valid; replay the request once with the new
      // access token. A failure here falls through to a normal 401.
      return apiRequest<T>(path, { ...options, refresh: false });
    }
    if (response.status === 401 && !options.anonymous) notifyUnauthorized();
    throw new ApiError(
      // A 401 from the login call means bad credentials, not an expired session.
      options.anonymous
        ? fallback
        : friendlyMessage(response.status, code, fallback),
      code,
      response.status,
      details,
    );
  }

  return envelope.data as T;
}

interface TokenPayload {
  access_token: string;
  refresh_token?: string;
  token_type: string;
  expires_in?: number;
  user?: {
    user_id: string;
    display_name: string;
    role_id: string;
    role_label?: string;
  };
}

interface LoginPayload extends TokenPayload {
  user: { user_id: string; display_name: string; role_id: string; role_label?: string };
}

/**
 * Exchange the stored refresh token for a new pair.
 *
 * Returns false when there is nothing to refresh or the backend rejects it;
 * the caller then treats the session as expired. Never notifies listeners:
 * whichever request triggered the refresh decides whether to sign the user
 * out when the replay also fails.
 */
export async function refreshRemoteSession(): Promise<boolean> {
  const session = readRemoteSession();
  if (!session?.refreshToken) return false;
  try {
    const data = await apiRequest<TokenPayload>("/api/v1/auth/refresh", {
      method: "POST",
      body: { refreshToken: session.refreshToken },
      token: null,
      anonymous: true,
      refresh: false,
    });
    if (!data || typeof data.access_token !== "string" || !data.access_token)
      return false;
    saveRemoteSession({
      ...session,
      accessToken: data.access_token,
      refreshToken: data.refresh_token || session.refreshToken,
      savedAt: Date.now(),
    });
    return true;
  } catch {
    return false;
  }
}

/** Server-side logout so the old token cannot be replayed; best effort. */
export async function logoutRemote(): Promise<void> {
  try {
    await apiRequest("/api/v1/auth/logout", {
      method: "POST",
      refresh: false,
    });
  } catch {
    /* Offline or already expired: local cleanup is still required. */
  } finally {
    clearRemoteSession();
  }
}

const LOCAL_ROLE_FOR_REMOTE = {
  calibration_leader: "leader",
  calibration_operator: "operator",
} as const satisfies Record<string, UserRole>;

/**
 * Exchange credentials for a token and store the remote session.
 *
 * The account comes from the backend directory, not a front-end list: any
 * username the backend accepts can enter, and its role is taken from the
 * server response. Roles this workspace has no UI for are rejected rather
 * than mapped onto a more privileged one.
 */
export async function loginWithBackend(
  username: string,
  password: string,
): Promise<User> {
  const name = typeof username === "string" ? username.trim() : "";
  if (!name)
    throw new ApiError("请输入账号后再登录。", "INVALID_ACCOUNT", 400);

  const data = await apiRequest<LoginPayload>("/api/v1/auth/login", {
    method: "POST",
    body: { username: name, password },
    token: null,
    anonymous: true,
  });

  if (
    !data?.user ||
    typeof data.access_token !== "string" ||
    !data.access_token ||
    typeof data.user.user_id !== "string" ||
    !data.user.user_id
  )
    throw new ApiError(
      "后端返回的登录信息不完整，请联系管理员。",
      "INVALID_RESPONSE",
      0,
    );

  const role = LOCAL_ROLE_FOR_REMOTE[
    data.user.role_id as keyof typeof LOCAL_ROLE_FOR_REMOTE
  ];
  if (!role)
    throw new ApiError(
      `账号角色「${data.user.role_id}」暂不受此工作空间支持，请联系管理员调整角色。`,
      "UNSUPPORTED_ROLE",
      403,
    );

  const displayName =
    typeof data.user.display_name === "string" && data.user.display_name
      ? data.user.display_name
      : name;
  const session: RemoteSession = {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    user: {
      userId: data.user.user_id,
      username: name,
      displayName,
      roleId: data.user.role_id,
    },
    savedAt: Date.now(),
  };
  if (!saveRemoteSession(session))
    throw new ApiError(
      "浏览器无法保存本次登录会话，请允许此站点使用会话存储后重试。",
      "STORAGE_DENIED",
      0,
    );
  return {
    id: data.user.user_id,
    username: name,
    displayName,
    role,
    source: "remote",
  };
}
