import test from "node:test";
import assert from "node:assert/strict";
import { REMOTE_SESSION_KEY, readRemoteSession } from "../src/lib/apiClient.ts";
import {
  changeOwnPassword,
  createAccount,
  fetchAccounts,
  resetAccountPassword,
  updateAccount,
} from "../src/lib/accountAdmin.ts";

const storageValues = new Map<string, string>();
Object.defineProperty(globalThis, "sessionStorage", {
  configurable: true,
  value: {
    getItem: (key: string) => storageValues.get(key) ?? null,
    setItem: (key: string, value: string) => {
      storageValues.set(key, value);
    },
    removeItem: (key: string) => {
      storageValues.delete(key);
    },
  },
});

function installSession(): void {
  storageValues.clear();
  storageValues.set(
    REMOTE_SESSION_KEY,
    JSON.stringify({
      accessToken: "old-access",
      refreshToken: "old-refresh",
      user: {
        userId: "user-leader",
        username: "leader",
        displayName: "团队负责人",
        roleId: "calibration_leader",
      },
      savedAt: 1,
    }),
  );
}

function ok(data: unknown): Response {
  return new Response(JSON.stringify({ success: true, data, error: null }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

interface Call {
  url: string;
  init: RequestInit;
}

function installFetch(payload: unknown): Call[] {
  const calls: Call[] = [];
  globalThis.fetch = (async (input: unknown, init: RequestInit = {}) => {
    calls.push({ url: String(input), init });
    return ok(payload);
  }) as typeof fetch;
  return calls;
}

const ACCOUNT = {
  userId: "user-abc",
  username: "alice",
  displayName: "Alice",
  role: "calibration_operator",
  roleLabel: "校准操作员",
  isActive: true,
  tokenVersion: 0,
  createdAt: "2026-01-01T00:00:00Z",
};

test("account list is read with the leader's bearer token", async () => {
  installSession();
  const calls = installFetch({ items: [ACCOUNT], total: 1 });
  const page = await fetchAccounts();
  assert.equal(calls[0].url, "http://127.0.0.1:18789/api/v1/users");
  assert.equal(
    (calls[0].init.headers as Record<string, string>).Authorization,
    "Bearer old-access",
  );
  assert.deepEqual(page.items.map((account) => account.username), ["alice"]);
});

test("creating an account trims identity fields and sends the backend role", async () => {
  installSession();
  const calls = installFetch(ACCOUNT);
  const created = await createAccount({
    username: " alice ",
    displayName: " Alice ",
    password: "Password123!",
    role: "calibration_operator",
  });
  assert.equal(calls[0].init.method, "POST");
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), {
    username: "alice",
    displayName: "Alice",
    password: "Password123!",
    role: "calibration_operator",
  });
  assert.equal(created.userId, "user-abc");
});

test("updates patch only the provided fields and encode the user id", async () => {
  installSession();
  const calls = installFetch(ACCOUNT);
  await updateAccount("user/a b", { isActive: false });
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:18789/api/v1/users/user%2Fa%20b",
  );
  assert.equal(calls[0].init.method, "PATCH");
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), { isActive: false });

  await updateAccount("user-abc", { role: "calibration_leader" });
  assert.deepEqual(JSON.parse(String(calls[1].init.body)), {
    role: "calibration_leader",
  });

  await updateAccount("user-abc", { displayName: " Alice B " });
  assert.deepEqual(JSON.parse(String(calls[2].init.body)), {
    displayName: "Alice B",
  });
});

test("leader password reset posts to the account's password endpoint", async () => {
  installSession();
  const calls = installFetch(ACCOUNT);
  await resetAccountPassword("user-abc", "NewPassword123!");
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:18789/api/v1/users/user-abc/password",
  );
  assert.equal(calls[0].init.method, "POST");
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), {
    password: "NewPassword123!",
  });
});

test("changing my own password keeps this tab signed in with the replacement tokens", async () => {
  installSession();
  const calls = installFetch({
    access_token: "new-access",
    refresh_token: "new-refresh",
    token_type: "bearer",
  });
  await changeOwnPassword("OldPassword123!", "NewPassword123!");
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:18789/api/v1/auth/password",
  );
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), {
    currentPassword: "OldPassword123!",
    newPassword: "NewPassword123!",
  });
  const session = readRemoteSession();
  assert.equal(session?.accessToken, "new-access");
  assert.equal(session?.refreshToken, "new-refresh");
  assert.equal(session?.user.userId, "user-leader");
});

test("a refresh token is kept when the backend rotates only the access token", async () => {
  installSession();
  installFetch({ access_token: "new-access" });
  await changeOwnPassword("OldPassword123!", "NewPassword123!");
  const session = readRemoteSession();
  assert.equal(session?.accessToken, "new-access");
  assert.equal(session?.refreshToken, "old-refresh");
});
