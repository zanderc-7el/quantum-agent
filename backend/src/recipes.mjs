/** Planning metadata only. No hardware command is issued from this module. */
export const SIM_TOPOLOGY = Object.freeze({
  revision: 1, dataSource: "simulator", qubits: ["Q12", "Q13"],
  adjacentPairs: [["Q12", "Q13"]],
  resources: {
    Q12: ["readout-chain-1", "drive-q12"],
    Q13: ["readout-chain-1", "drive-q13"],
    "Q12-Q13": ["readout-chain-1", "drive-q12", "drive-q13", "coupler-q12-q13"],
  },
});

const node = (id, deps, resources, stage = "single") => ({ id, deps, resources, stage, requiredQuality: "pass" });
const singleBase = [
  node("ReadoutFreqDip", [], ["readout-chain-1"]),
  node("F01Spectroscopy", ["ReadoutFreqDip"], ["drive-q12", "readout-chain-1"]),
  node("XYGateAmpRabiImpl", ["F01Spectroscopy"], ["drive-q12", "readout-chain-1"]),
  node("IqToProbability", ["XYGateAmpRabiImpl"], ["readout-chain-1"]),
  node("ReadoutParaSweepByIQProb", ["IqToProbability"], ["readout-chain-1"]),
  node("IqToProbabilityRetest", ["ReadoutParaSweepByIQProb"], ["readout-chain-1"]),
  node("T1Idle", ["IqToProbabilityRetest"], ["drive-q12", "readout-chain-1"]),
  node("T2CPMG", ["IqToProbabilityRetest"], ["drive-q12", "readout-chain-1"]),
  node("RB1QCal", ["IqToProbabilityRetest"], ["drive-q12", "readout-chain-1"]),
];
const singleFor = qubit => singleBase.map(item => ({
  ...item, id: `${qubit}:${item.id}`, qubit,
  deps: item.deps.map(dep => `${qubit}:${dep}`),
  resources: item.resources.map(resource => resource === "drive-q12" ? `drive-${qubit.toLowerCase()}` : resource),
}));
const single = singleFor("Q12");
const pairPrerequisites = ["Q12", "Q13"].flatMap(qubit =>
  ["T1Idle", "T2CPMG", "RB1QCal"].map(name => `${qubit}:${name}`));
const two = [
  node("F01ZPulseDependency", pairPrerequisites, ["coupler-q12-q13", "drive-q12", "drive-q13"], "two"),
  node("ZeroPointFinderByQQSwap", ["F01ZPulseDependency"], ["coupler-q12-q13", "readout-chain-1"], "two"),
  node("CZSwapSweepZPulse", ["ZeroPointFinderByQQSwap"], ["coupler-q12-q13", "readout-chain-1"], "two"),
  node("TTGAczDelicateG", ["CZSwapSweepZPulse"], ["coupler-q12-q13", "readout-chain-1"], "two"),
  node("TTGAczDelicateDetune", ["TTGAczDelicateG"], ["coupler-q12-q13", "readout-chain-1"], "two"),
  node("CZXEBCal", ["TTGAczDelicateDetune"], ["coupler-q12-q13", "readout-chain-1"], "two"),
  node("CZParaOptimizer", ["CZXEBCal"], ["coupler-q12-q13", "readout-chain-1"], "two"),
  node("CZXEBCalRetest", ["CZParaOptimizer"], ["coupler-q12-q13", "readout-chain-1"], "two"),
];
export const SIM_RECIPES = Object.freeze({
  single: { id: "single-calibration", version: 1, dataSource: "simulator", nodes: single },
  two: { id: "two-cz-calibration", version: 1, dataSource: "simulator", targetPair: "Q12-Q13",
    nodes: [...single, ...singleFor("Q13"), ...two] },
});

export function validateRecipe(recipe) {
  const ids = new Set();
  for (const item of recipe.nodes) {
    if (ids.has(item.id)) throw new Error(`duplicate node: ${item.id}`);
    ids.add(item.id);
  }
  const map = new Map(recipe.nodes.map(item => [item.id, item]));
  const active = new Set();
  const done = new Set();
  function visit(id) {
    if (!map.has(id)) throw new Error(`unknown dependency: ${id}`);
    if (active.has(id)) throw new Error(`cycle: ${id}`);
    if (done.has(id)) return;
    active.add(id);
    for (const dep of map.get(id).deps) visit(dep);
    active.delete(id); done.add(id);
  }
  for (const item of recipe.nodes) visit(item.id);
  return true;
}

/** `results[id] = {status, quality}`. Failed or inconclusive prerequisites block descendants. */
export function readyNodes(recipe, results = {}) {
  validateRecipe(recipe);
  return recipe.nodes.filter(item => !results[item.id] && item.deps.every(dep =>
    results[dep]?.status === "completed" && results[dep]?.quality === item.requiredQuality));
}

/** Deterministic hardware-safe batch selection from the ready set. */
export function selectBatch(recipe, results = {}, occupied = []) {
  const used = new Set(occupied);
  const batch = [];
  for (const item of readyNodes(recipe, results)) {
    if (item.resources.some(resource => used.has(resource))) continue;
    batch.push(item);
    for (const resource of item.resources) used.add(resource);
  }
  return batch;
}
