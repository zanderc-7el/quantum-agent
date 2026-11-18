import test from "node:test";
import assert from "node:assert/strict";
import { actualPoints, modelProviderName, preferredMembershipModel } from "../src/lib/account.ts";
import type { MembershipStatus } from "../src/lib/account.ts";
import { createRemoteGateway } from "../src/lib/remoteGateway.ts";

const status: MembershipStatus = { provider: "ziyor", configured: true, authenticated: false, modelConnection: { provider: "codex_cli", model: "codex-default", active: true, verified: true }, capabilities: { sendCode: true, login: true, connect: true, test: true } };
const json = (value: unknown, code = 200) => new Response(JSON.stringify(value), { status: code, headers: { "Content-Type": "application/json" } });

test("membership credits distinguish zero from unavailable and never reuse unauthenticated balance", () => {
  assert.equal(actualPoints(status), undefined);
  assert.equal(actualPoints({ ...status, authenticated: true }), undefined);
  assert.equal(actualPoints({ ...status, balance: { remainingCalls: 55 } }), undefined);
  assert.equal(actualPoints({ ...status, authenticated: true, balance: { remainingCalls: 0 } }), "0");
  assert.equal(actualPoints({ ...status, authenticated: true, balance: { remainingCalls: 1234 } }), "1,234");
  assert.equal(modelProviderName("codex_cli"), "Codex CLI");
  assert.equal(modelProviderName("ziyor"), "Ziyor 会员");
});

test("account status and model directory reads do not send code, login or inference", async () => {
  const seen: { url: string; method: string }[] = [];
  const gateway = createRemoteGateway({ fetch: (async (url, init) => { seen.push({ url: String(url), method: init?.method ?? "GET" }); return json(String(url).endsWith("/models") ? { models: [] } : status); }) as typeof fetch });
  const read = await gateway.accountStatus(); await gateway.accountModels();
  assert.equal(read.authenticated, false);
  assert.deepEqual(seen, [{ url: "/api/account/status", method: "GET" }, { url: "/api/account/models", method: "GET" }]);
});

test("membership mutations bind a local session and preserve the same operation ID on 401 retry", async () => {
  let sessions = 0;
  const writes: { url: string; body: string; token: string }[] = [];
  const gateway = createRemoteGateway({ fetch: (async (url, init) => {
    if (String(url).endsWith("/session")) return json({ token: `session-${++sessions}` });
    writes.push({ url: String(url), body: init?.body as string, token: (init?.headers as Record<string, string>)["X-Qcal-Token"] });
    return writes.length === 1 ? json({ error: { message: "expired" } }, 401) : json({ status: "sent", retryAfter: 60 });
  }) as typeof fetch });
  await gateway.accountSendCode("13900000000");
  assert.equal(writes.length, 2); assert.equal(writes[0].body, writes[1].body); assert.deepEqual(writes.map(item => item.token), ["session-1", "session-2"]);
  await gateway.accountLogin("13900000000", "123456", true);
  await gateway.accountConnect("__existing_provider__"); await gateway.accountLogout(); await gateway.accountTest();
  const operations = writes.slice(1).map(item => JSON.parse(item.body));
  assert.equal(new Set(operations.map(item => item.operationId)).size, operations.length);
  assert.equal(operations[1].useForAgent, true);
  assert.equal(operations[2].model, "__existing_provider__");
  assert.deepEqual(writes.slice(2).map(item => item.url), ["/api/account/login", "/api/account/connect", "/api/account/logout", "/api/account/test"]);
});

test("model default follows catalog metadata without substituting a missing or stale ID", () => {
  const models = [{ id: "first", label: "First" }, { id: "preferred", label: "Default", default: true }, { id: "actual", label: "Active" }];
  assert.equal(preferredMembershipModel({ models, defaultModelId: "actual" }), "actual");
  assert.equal(preferredMembershipModel({ models, defaultModelId: "removed" }), "preferred");
  assert.equal(preferredMembershipModel({ models, defaultModelId: "preferred" }, "actual"), "actual");
  assert.equal(preferredMembershipModel({ models: [] }), "");
});
