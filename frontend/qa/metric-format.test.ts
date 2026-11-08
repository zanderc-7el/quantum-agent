import test from "node:test";
import assert from "node:assert/strict";
import {
  formatMetricValue,
  formatWireCriterion,
  formatWireTarget,
  formatWireValue,
} from "../src/lib/metricFormat.ts";

test("a ratio reference is shown as a percentage, like the comparison cards", () => {
  assert.equal(
    formatWireTarget({
      metric: "fidelity",
      ideal: 0.995,
      unit: "ratio",
      direction: "higher",
      tolerance: null,
    }),
    "≥ 99.50%",
  );
  assert.equal(
    formatWireCriterion({
      metric: "fidelity",
      comparator: ">=",
      threshold: 0.98,
      unit: "ratio",
    }),
    "≥ 98.00%",
  );
  assert.equal(formatWireValue(0.994, "fidelity", "ratio"), "99.40%");
});

test("time metrics keep the parameter panel's precision", () => {
  assert.equal(
    formatWireTarget({
      metric: "parameters.*.t1Us",
      ideal: 60,
      unit: "μs",
      direction: "higher",
      tolerance: null,
    }),
    "≥ 60.0 μs",
  );
  assert.equal(
    formatWireCriterion({
      metric: "parameters.*.t1Us",
      comparator: ">=",
      threshold: 20,
      unit: "μs",
    }),
    "≥ 20.0 μs",
  );
  assert.equal(formatWireValue(12, "parameters.*.t2Us", "μs"), "12.00 μs");
});

test("band targets keep their tolerance and lower targets their direction", () => {
  assert.equal(
    formatWireTarget({
      metric: "parameters.*.f01Ghz",
      ideal: 4,
      unit: "GHz",
      direction: "band",
      tolerance: 0.05,
    }),
    "≈ 4.00 GHz ± 0.05 GHz",
  );
  assert.equal(
    formatWireTarget({
      metric: "readoutError",
      ideal: 0.02,
      unit: "ratio",
      direction: "lower",
      tolerance: null,
    }),
    "≤ 2.00%",
  );
});

test("an ASCII comparator is shown with its typographic symbol", () => {
  assert.equal(
    formatWireCriterion({
      metric: "parameters.*.f01Ghz",
      comparator: "<=",
      threshold: 0.05,
      unit: "GHz",
    }),
    "≤ 0.05 GHz",
  );
  assert.equal(formatMetricValue(null, "%"), "—");
});
