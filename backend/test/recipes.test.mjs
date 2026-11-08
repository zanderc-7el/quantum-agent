import test from "node:test";
import assert from "node:assert/strict";
import { SIM_RECIPES, SIM_TOPOLOGY, validateRecipe, readyNodes, selectBatch } from "../src/recipes.mjs";

test("single and CZ recipe graphs include both qubits before CZ", () => {
  assert.equal(SIM_RECIPES.single.nodes.length, 9);
  assert.equal(SIM_RECIPES.two.nodes.length, 26);
  assert.equal(validateRecipe(SIM_RECIPES.single), true);
  assert.equal(validateRecipe(SIM_RECIPES.two), true);
  assert.deepEqual(readyNodes(SIM_RECIPES.single).map(item => item.id), ["Q12:ReadoutFreqDip"]);
  assert.deepEqual(readyNodes(SIM_RECIPES.two).map(item => item.id), ["Q12:ReadoutFreqDip", "Q13:ReadoutFreqDip"]);
  assert.deepEqual(selectBatch(SIM_RECIPES.two).map(item => item.id), ["Q12:ReadoutFreqDip"]);
  assert.deepEqual(SIM_TOPOLOGY.adjacentPairs, [["Q12", "Q13"]]);
});

test("quality gates and occupied resources block downstream work", () => {
  const prefix = Object.fromEntries(SIM_RECIPES.single.nodes.slice(0, 6).map(item =>
    [item.id, { status: "completed", quality: "pass" }]));
  assert.deepEqual(readyNodes(SIM_RECIPES.single, prefix).map(item => item.id), ["Q12:T1Idle", "Q12:T2CPMG", "Q12:RB1QCal"]);
  assert.deepEqual(selectBatch(SIM_RECIPES.single, prefix).map(item => item.id), ["Q12:T1Idle"]);
  assert.deepEqual(selectBatch(SIM_RECIPES.single, prefix, ["readout-chain-1"]), []);
  prefix["Q12:IqToProbabilityRetest"] = { status: "completed", quality: "inconclusive" };
  assert.deepEqual(readyNodes(SIM_RECIPES.single, prefix), []);
});

test("CZ stage waits for all three single-qubit branches", () => {
  const results = Object.fromEntries(SIM_RECIPES.two.nodes.filter(item => item.stage === "single").map(item =>
    [item.id, { status: "completed", quality: "pass" }]));
  assert.deepEqual(readyNodes(SIM_RECIPES.two, results).map(item => item.id), ["F01ZPulseDependency"]);
  results["Q13:T2CPMG"] = { status: "failed", quality: "inconclusive" };
  assert.deepEqual(readyNodes(SIM_RECIPES.two, results), []);
});

test("bad recipe dependency cycles are rejected", () => {
  assert.throws(() => validateRecipe({ nodes: [{ id: "a", deps: ["b"] }, { id: "b", deps: ["a"] }] }), /cycle/);
  assert.throws(() => validateRecipe({ nodes: [{ id: "a", deps: ["missing"] }] }), /unknown dependency/);
});
