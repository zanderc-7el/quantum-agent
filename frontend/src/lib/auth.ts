/** Frontend demonstration only. Real authentication must be enforced by a server. */
export type UserRole = "leader" | "operator";
export interface User {
  id: string;
  username: string;
  displayName: string;
  role: UserRole;
}
export interface DemoAccount extends User {
  /** Public sample credential; never use a real password in this demonstration. */
  password: string;
}

export const DEMO_ACCOUNTS: readonly Readonly<DemoAccount>[] = Object.freeze([
  Object.freeze({ id: "leader", username: "leader", displayName: "团队负责人", role: "leader" as const, password: "Leader123!" }),
  Object.freeze({ id: "operator", username: "operator", displayName: "实验工作人员", role: "operator" as const, password: "Operator123!" }),
]);
export const ROLE_LABELS: Readonly<Record<UserRole, string>> = Object.freeze({
  leader: "团队负责人",
  operator: "工作人员",
});
export const SESSION_KEY = "qcal-demo-session-v1";
export const SESSION_TTL_MS = 8 * 60 * 60 * 1000;

/** The injectable subset makes storage denial and invalid sessions testable. */
export interface SessionStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
const invalidatedStores = new WeakSet<SessionStorage>();

function browserStorage(): SessionStorage | undefined {
  try {
    return globalThis.sessionStorage;
  } catch {
    return undefined;
  }
}

export function getUserById(id: string): User | null {
  const account = DEMO_ACCOUNTS.find((candidate) => candidate.id === id);
  if (!account) return null;
  return { id: account.id, username: account.username, displayName: account.displayName, role: account.role };
}

/** Roles come from the account directory, never editable profile preferences. */
export function isKnownUser(user: User | null | undefined): user is User {
  if (!user || typeof user !== "object") return false;
  const known = getUserById(user.id);
  return !!known && known.username === user.username && known.role === user.role;
}

export function authenticate(username: string, password: string): User | null {
  if (typeof username !== "string" || typeof password !== "string") return null;
  const account = DEMO_ACCOUNTS.find(
    (candidate) => candidate.username === username.trim() && candidate.password === password,
  );
  return account ? getUserById(account.id) : null;
}

export function clearSession(storage: SessionStorage | undefined = browserStorage()): boolean {
  if (!storage) return false;
  // If removal is denied, the current page must still treat logout as final.
  invalidatedStores.add(storage);
  try {
    storage.removeItem(SESSION_KEY);
    return true;
  } catch {
    return false;
  }
}

export function saveSession(
  user: User,
  now = Date.now(),
  storage: SessionStorage | undefined = browserStorage(),
): boolean {
  if (!storage || !isKnownUser(user) || !Number.isSafeInteger(now) || now < 0) return false;
  try {
    storage.setItem(SESSION_KEY, JSON.stringify({ userId: user.id, loggedInAt: now }));
    invalidatedStores.delete(storage);
    return true;
  } catch {
    return false;
  }
}

export function readSession(
  now = Date.now(),
  storage: SessionStorage | undefined = browserStorage(),
): User | null {
  if (!storage || invalidatedStores.has(storage)) return null;
  try {
    const raw = storage.getItem(SESSION_KEY);
    if (!raw) return null;
    if (raw.length > 1024) throw new Error("Invalid session");
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid session");
    const session = value as Record<string, unknown>;
    if (
      Object.keys(session).length !== 2 ||
      typeof session.userId !== "string" ||
      typeof session.loggedInAt !== "number" ||
      !Number.isSafeInteger(session.loggedInAt) || session.loggedInAt < 0 ||
      !Number.isSafeInteger(now) || now < session.loggedInAt ||
      now - session.loggedInAt >= SESSION_TTL_MS
    ) throw new Error("Expired or invalid session");
    const user = getUserById(session.userId);
    if (!user) throw new Error("Unknown account");
    return user;
  } catch {
    clearSession(storage);
    return null;
  }
}
