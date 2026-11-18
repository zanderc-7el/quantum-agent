import test from "node:test";
import assert from "node:assert/strict";
import { formatAxisTick } from "../src/lib/axisTicks.ts";

test("narrow cavity and drive scans retain distinct frequency labels", () => {
  for (const [center, span] of [[7.009643333e9, 4e6], [4.86269e9, 1e5], [7e9, 2]]) {
    const values = [center - span / 2, center, center + span / 2];
    const labels = values.map(value => formatAxisTick(value, span));
    assert.equal(new Set(labels).size, 3);
    labels.forEach((label, index) => assert.ok(Math.abs(Number(label) - values[index]) <= span / 100));
  }
});

test("small IQ amplitudes, time axes, zero and invalid values remain readable", () => {
  assert.equal(formatAxisTick(0, 2e-6), "0");
  assert.equal(Number(formatAxisTick(0.0000248, 0.0000006)), 0.0000248);
  assert.equal(Number(formatAxisTick(1.2e-6, 2.4e-6)), 1.2e-6);
  assert.equal(formatAxisTick(NaN, 1), "—");
  assert.equal(Number(formatAxisTick(-2, 0)), -2);
});
