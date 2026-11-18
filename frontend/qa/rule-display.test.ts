import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { ruleDisplay } from "../src/lib/ruleDisplay.ts";
import { createRemoteGateway } from "../src/lib/remoteGateway.ts";
import type { Rule } from "../src/types.ts";

const rules = JSON.parse(readFileSync(new URL("../../knowledge/rules.json", import.meta.url), "utf8")).rules as Rule[];
const slowRabi = rules.find(rule => rule.id === "field.slow_rabi")!;

test("actual slow-Rabi candidate remains advice with its recorded scope and validation requirements", () => {
  assert.ok(slowRabi);
  const before = JSON.stringify(slowRabi), display = ruleDisplay(slowRabi);
  assert.equal(display.statusLabel, "经验建议 · 未发布为自动规则");
  assert.equal(display.statusValue, "scoped_candidate_rule");
  assert.deepEqual(display.fields[0].value, ["s02_qubit_frequency", "s03_rabi_amplitude"]);
  assert.equal(display.fields.find(field => field.label === "建议动作")?.value, "redesign_scan");
  assert.equal(display.fields.find(field => field.label === "建议的验证要求")?.value, slowRabi.verification);
  assert.match(String(slowRabi.verification), /随机时间和交错双幅度/);
  assert.equal(JSON.stringify(slowRabi), before);
});

test("missing, approved and unknown statuses or evidence alone never imply automatic execution", () => {
  for (const policyStatus of [undefined, "", "approved", "new_future_status"]) {
    const display = ruleDisplay({ ...slowRabi, policyStatus, evidence: ["independent-result-id"] });
    assert.equal(display.statusLabel, "规则条目 · 自动执行状态未确认");
    assert.equal(display.statusValue, policyStatus || "未声明");
    assert.equal(display.fields.at(-1)?.label, "记录的验证要求");
  }
  const explicitlyDeclared = ruleDisplay({ ...slowRabi, policyStatus: undefined, action: { automaticExecution: true } });
  assert.equal(explicitlyDeclared.statusLabel, "规则条目 · 自动执行状态未确认");
});

test("published suggestions reflect only the explicit nonautomatic action metadata", () => {
  const published = { ...slowRabi, policyStatus: undefined, publishedAt: "2026-11-18T00:00:00Z",
    action: { kind: "suggest_parameters", automaticExecution: false }, scope: { resourceId: "Q11", ownerId: "s03_rabi_amplitude" } };
  const display = ruleDisplay(published);
  assert.equal(display.statusLabel, "已发布经验建议 · 不自动执行");
  assert.deepEqual(display.fields[0].value, published.scope);
  assert.equal(display.fields.at(-1)?.label, "建议的验证要求");
  assert.equal(ruleDisplay({ ...published, action: { kind: "suggest_parameters" } }).statusLabel, "规则条目 · 自动执行状态未确认");
  assert.equal(ruleDisplay({ ...published, policyStatus: "retired" }).statusLabel, "规则条目 · 自动执行状态未确认");
});

test("node gateway retains policy status, scope and source metadata without a write", async () => {
  const requests: RequestInit[] = [];
  const gateway = createRemoteGateway({ fetch: (async (_url, init) => {
    requests.push(init ?? {});
    return new Response(JSON.stringify({ node: { id: "rabi", rules: [slowRabi] }, attempts: [], decisions: [], rules: [slowRabi] }),
      { headers: { "Content-Type": "application/json" } });
  }) as typeof fetch });
  const detail = await gateway.node("wf", "rabi");
  assert.deepEqual(detail.rules[0], slowRabi);
  assert.equal(detail.rules[0].sourceSha256, slowRabi.sourceSha256);
  assert.deepEqual(detail.rules[0].scope, slowRabi.scope);
  assert.equal(requests.length, 1); assert.equal(requests[0].method, undefined);
});
