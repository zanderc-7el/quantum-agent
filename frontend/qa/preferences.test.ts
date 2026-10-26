import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_PREFERENCES,
  parsePreferences,
  validateEndpoint,
} from "../src/lib/preferences.ts";

test("preferences tolerate corrupted or unsupported local values", () => {
  for (const input of [null, "", "null", "false", "[", "{}"])
    assert.deepEqual(parsePreferences(input), DEFAULT_PREFERENCES);
  const value = parsePreferences(
    JSON.stringify({
      theme: "bright",
      defaultMachine: "Z1",
      defaultPolicy: "none",
      provider: "unknown",
      autoDemo: false,
    }),
  );
  assert.equal(value.theme, "dark");
  assert.equal(value.defaultMachine, "M7");
  assert.equal(value.defaultPolicy, "threshold");
  assert.equal(value.provider, "local");
  assert.equal(value.autoDemo, false);
});
test("preferences preserve valid choices and bound display fields", () => {
  const p = parsePreferences(
    JSON.stringify({
      ...DEFAULT_PREFERENCES,
      theme: "light",
      displayName: "  张三  ",
      workspaceName: "量".repeat(100),
      defaultMachine: "M3",
      defaultPolicy: "strict",
    }),
  );
  assert.equal(p.theme, "light");
  assert.equal(p.displayName, "张三");
  assert.equal(p.workspaceName.length, 50);
  assert.equal(p.defaultMachine, "M3");
  assert.equal(p.defaultPolicy, "strict");
});
test("connection drafts reject credentials and unsupported schemes", () => {
  for (const value of [
    "",
    "http://localhost:8000/v1",
    "https://example.test/api",
  ])
    assert.equal(validateEndpoint(value), true);
  for (const value of [
    "ftp://test",
    "javascript:alert(1)",
    "https://user:secret@test",
    "https://test?key=secret",
    "https://test#token",
  ])
    assert.equal(validateEndpoint(value), false);
});
