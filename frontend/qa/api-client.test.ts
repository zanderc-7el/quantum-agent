import test from "node:test";
import assert from "node:assert/strict";
import {
  apiRequest,
  loginWithBackend,
  logoutRemote,
  readRemoteSession,
  saveRemoteSession,
  REMOTE_SESSION_KEY,
} from "../src/lib/apiClient.ts";

class MemoryStorage {
  values = new Map<string, string>();
  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
  removeItem(key: string): void {
    this.values.delete(key);
  }
  clear(): void {
    this.values.clear();
  }
}

function installStorage(): MemoryStorage {
  const storage = new MemoryStorage();
  Object.defineProperty(globalThis, "sessionStorage", {
    value: storage,
    configurable: true,
    writable: true,
  });
  return storage;
}

interface Call {
  url: string;
  method: string;
  authorization: string | null;
  body: string | null;
}

function installFetch(
  responder: (call: Call) => { status: number; payload: unknown },
): Call[] {
  const calls: Call[] = [];
  Object.defineProperty(globalThis, "fetch", {
    configurable: true,
    writable: true,
    value: async (
      url: string,
      init: { method?: string; headers?: Record<string, string>; body?: string },
    ) => {
      const call: Call = {
        url,
        method: init.method ?? "GET",
        authorization: init.headers?.Authorization ?? null,
        body: init.body ?? null,
      };
      calls.push(call);
      const { status, payload } = responder(call);
      return {
        ok: status >= 200 && status < 300,
        status,
        text: async () => JSON.stringify(payload),
      };
    },
  });
  return calls;
}

test("a backend login stores both access and refresh tokens", async () => {
  const storage = installStorage();
  installFetch(() => ({
    status: 200,
    payload: {
      success: true,
      error: null,
      data: {
        access_token: "access-1",
        refresh_token: "refresh-1",
        token_type: "bearer",
        expires_in: 480,
        user: {
          user_id: "user-leader",
          display_name: "团队负责人",
          role_id: "calibration_leader",
        },
      },
    },
  }));

  await loginWithBackend("leader", "Leader123!");
  const stored = JSON.parse(storage.getItem(REMOTE_SESSION_KEY)!);
  assert.equal(stored.accessToken, "access-1");
  assert.equal(stored.refreshToken, "refresh-1");
  assert.equal(stored.user.roleId, "calibration_leader");
});

test("a 401 refreshes once, replays the request and keeps the new token", async () => {
  const storage = installStorage();
  saveRemoteSession({
    accessToken: "expired",
    refreshToken: "refresh-1",
    user: {
      userId: "user-leader",
      username: "leader",
      displayName: "团队负责人",
      roleId: "calibration_leader",
    },
    savedAt: 1,
  });
  const calls = installFetch((call) => {
    if (call.url.endsWith("/api/v1/auth/refresh")) {
      assert.equal(call.authorization, null);
      assert.deepEqual(JSON.parse(call.body!), { refreshToken: "refresh-1" });
      return {
        status: 200,
        payload: {
          success: true,
          error: null,
          data: {
            access_token: "access-2",
            refresh_token: "refresh-2",
            token_type: "bearer",
          },
        },
      };
    }
    if (call.authorization === "Bearer access-2") {
      return {
        status: 200,
        payload: { success: true, error: null, data: { ok: true } },
      };
    }
    return {
      status: 401,
      payload: { success: false, data: null, error: { code: "TOKEN_EXPIRED" } },
    };
  });

  const payload = await apiRequest<{ ok: boolean }>("/api/v1/calibration/x");
  assert.equal(payload.ok, true);
  assert.equal(calls.length, 3);
  const session = readRemoteSession();
  assert.equal(session?.accessToken, "access-2");
  assert.equal(session?.refreshToken, "refresh-2");
  assert.equal(storage.getItem(REMOTE_SESSION_KEY)!.includes("refresh-2"), true);
});

test("a rejected refresh ends the session instead of retrying forever", async () => {
  const storage = installStorage();
  saveRemoteSession({
    accessToken: "expired",
    refreshToken: "stale",
    user: {
      userId: "user-leader",
      username: "leader",
      displayName: "团队负责人",
      roleId: "calibration_leader",
    },
    savedAt: 1,
  });
  let requests = 0;
  installFetch(() => {
    requests += 1;
    return {
      status: 401,
      payload: { success: false, data: null, error: { code: "UNAUTHORIZED" } },
    };
  });
  await assert.rejects(() => apiRequest("/api/v1/calibration/x"));
  assert.equal(requests, 2, "original request + one refresh attempt");
  assert.equal(storage.getItem(REMOTE_SESSION_KEY), null);
});

test("logout calls the server before clearing the local session", async () => {
  const storage = installStorage();
  saveRemoteSession({
    accessToken: "access-1",
    refreshToken: "refresh-1",
    user: {
      userId: "user-leader",
      username: "leader",
      displayName: "团队负责人",
      roleId: "calibration_leader",
    },
    savedAt: 1,
  });
  const calls = installFetch(() => ({
    status: 200,
    payload: { success: true, data: { loggedOut: true }, error: null },
  }));
  await logoutRemote();
  assert.equal(calls[0].url.endsWith("/api/v1/auth/logout"), true);
  assert.equal(calls[0].authorization, "Bearer access-1");
  assert.equal(storage.getItem(REMOTE_SESSION_KEY), null);
});
