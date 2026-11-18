import test from "node:test";
import assert from "node:assert/strict";
import { formatWallClock, workflowTiming } from "../src/lib/workflowTiming.ts";
const firstAcquisitionAt = "2026-11-18T01:00:00Z", now = Date.parse("2026-11-18T02:02:03Z");
test("elapsed begins at first actual acquisition attempt and advances with the local clock", () => {
  const source = { status: "running", firstAcquisitionAt, finishedAt: null, elapsed: 0, createdAt: "2020-01-01T00:00:00Z" };
  assert.deepEqual(workflowTiming(source, now), { state: "running", seconds: 3723, ticking: true });
  assert.equal(workflowTiming(source, now + 1000).seconds, 3724);
});
test("paused, queued and model-waiting workflows retain wall-clock elapsed", () => {
  for (const status of ["paused", "queued", "attention"]) assert.deepEqual(workflowTiming({ status, firstAcquisitionAt }, now), { state: "running", seconds: 3723, ticking: true });
});
test("terminal tasks freeze at backend finishedAt instead of reload time", () => {
  for (const status of ["completed", "failed", "aborted", "cancelled"]) {
    const source = { status, firstAcquisitionAt, finishedAt: "2026-11-18T01:15:00Z" };
    assert.deepEqual(workflowTiming(source, now), { state: "finished", seconds: 900, ticking: false });
    assert.equal(workflowTiming(source, now + 86400000).seconds, 900);
  }
});
test("not-started, missing fields and missing terminal endpoint never fabricate zero elapsed", () => {
  assert.deepEqual(workflowTiming({ status: "draft", firstAcquisitionAt: null }, now), { state: "not_started", ticking: false });
  assert.deepEqual(workflowTiming({ status: "running", createdAt: firstAcquisitionAt, elapsed: 0 } as Parameters<typeof workflowTiming>[0], now), { state: "awaiting_start", ticking: false });
  assert.deepEqual(workflowTiming({ status: "failed", firstAcquisitionAt }, now), { state: "awaiting_finish", ticking: false });
});
test("invalid timestamps and clock reversal are visibly unresolved", () => {
  assert.equal(workflowTiming({ status: "running", firstAcquisitionAt: "invalid" }, now).state, "awaiting_start");
  assert.equal(workflowTiming({ status: "running", firstAcquisitionAt }, Date.parse(firstAcquisitionAt) - 1000).state, "clock_skew");
  assert.equal(workflowTiming({ status: "completed", firstAcquisitionAt, finishedAt: "2026-11-18T00:00:00Z" }, now).state, "clock_skew");
});
test("wall-clock label formats hours, minutes and valid first second", () => {
  assert.equal(formatWallClock(3723), "1 小时 02 分 03 秒"); assert.equal(formatWallClock(63), "1 分 03 秒"); assert.equal(formatWallClock(0), "0 秒");
});
