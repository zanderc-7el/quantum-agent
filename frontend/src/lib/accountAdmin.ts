/**
 * Account directory management for remote mode.
 *
 * The backend owns the accounts; this module only wraps its endpoints. Every
 * call is authorized server-side with the stored access token, so a leader
 * who loses that role is rejected on the next request even if the tab still
 * shows the management UI.
 */
import {
  apiRequest,
  readRemoteSession,
  saveRemoteSession,
} from "./apiClient.ts";

/** Backend role ids; these are not the UI roles in `auth.ts`. */
export type RemoteAccountRole = "calibration_leader" | "calibration_operator";

export const ACCOUNT_ROLE_LABELS: Readonly<Record<RemoteAccountRole, string>> =
  Object.freeze({
    calibration_leader: "校准负责人",
    calibration_operator: "校准操作员",
  });

export interface AccountRecord {
  userId: string;
  username: string;
  displayName: string;
  role: RemoteAccountRole | string;
  roleLabel: string;
  isActive: boolean;
  tokenVersion: number;
  createdAt: string;
}

export interface AccountPage {
  items: AccountRecord[];
  total: number;
}

export interface CreateAccountInput {
  username: string;
  password: string;
  displayName: string;
  role: RemoteAccountRole;
}

export interface AccountPatch {
  displayName?: string;
  role?: RemoteAccountRole;
  isActive?: boolean;
}

function userIdPath(userId: string): string {
  return `/api/v1/users/${encodeURIComponent(userId)}`;
}

export function fetchAccounts(): Promise<AccountPage> {
  return apiRequest<AccountPage>("/api/v1/users");
}

export function createAccount(input: CreateAccountInput): Promise<AccountRecord> {
  return apiRequest<AccountRecord>("/api/v1/users", {
    method: "POST",
    body: {
      username: input.username.trim(),
      password: input.password,
      displayName: input.displayName.trim(),
      role: input.role,
    },
  });
}

export function updateAccount(
  userId: string,
  patch: AccountPatch,
): Promise<AccountRecord> {
  const body: AccountPatch = {};
  if (patch.displayName !== undefined) body.displayName = patch.displayName.trim();
  if (patch.role !== undefined) body.role = patch.role;
  if (patch.isActive !== undefined) body.isActive = patch.isActive;
  return apiRequest<AccountRecord>(userIdPath(userId), {
    method: "PATCH",
    body,
  });
}

export function resetAccountPassword(
  userId: string,
  password: string,
): Promise<AccountRecord> {
  return apiRequest<AccountRecord>(`${userIdPath(userId)}/password`, {
    method: "POST",
    body: { password },
  });
}

interface PasswordChangePayload {
  access_token: string;
  refresh_token?: string;
}

/**
 * Change the signed-in user's own password.
 *
 * The backend revokes every token on a password change, including the one
 * used for this request, so the fresh pair is written back to the stored
 * session immediately; otherwise the next action would look like an expiry.
 */
export async function changeOwnPassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  const data = await apiRequest<PasswordChangePayload>("/api/v1/auth/password", {
    method: "POST",
    body: { currentPassword, newPassword },
  });
  const session = readRemoteSession();
  if (
    session &&
    data &&
    typeof data.access_token === "string" &&
    data.access_token
  ) {
    saveRemoteSession({
      ...session,
      accessToken: data.access_token,
      refreshToken: data.refresh_token || session.refreshToken,
      savedAt: Date.now(),
    });
  }
}
