import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { classifyFlowEdges } from "../src/lib/flowEdgeKinds.ts";
import { buildFlowGeometry } from "../src/lib/flowGeometry.ts";

const catalog = JSON.parse(readFileSync(new URL("../../vendor/experience/config/agent_catalog.json", import.meta.url), "utf8"));
function mainGraph(resources = ["Q11"], twoQubit = false) {
  const owners = catalog.agents.filter((owner: { id: string }) => owner.id.startsWith("s") && owner.id !== "supervisor" || twoQubit && owner.id.startsWith("d"));
  const nodes = resources.flatMap(resourceId => owners.map((owner: { id: string; depends_on: string[] }) => ({
    id: `${resourceId}:${owner.id}`, dependsOn: owner.depends_on.map(id => `${resourceId}:${id}`),
  })));
  return { nodes, edges: nodes.flatMap(node => node.dependsOn.map(from => ({ from, to: node.id }))) };
}

test("catalog main branches remain real primary links without inventing T1 → T2", () => {
  const { nodes, edges } = mainGraph();
  const before = JSON.stringify({ nodes, edges });
  const kinds = classifyFlowEdges(nodes, edges);
  assert.equal(kinds.size, edges.length);
  assert.ok(edges.every(edge => kinds.get(edge) === "main"));
  assert.ok(!edges.some(edge => edge.from.endsWith(":s06_t1") && edge.to.endsWith(":s07_t2")));
  assert.equal(JSON.stringify({ nodes, edges }), before);
});

test("the full-coverage TimeRabi chain distinguishes its existing direct Rabi → IQ dependency", () => {
  const { nodes, edges } = mainGraph();
  const rabi = "Q11:s03_rabi_amplitude", time = `${rabi}:sq_rabi_time`, iq = "Q11:s04_readout_fidelity";
  nodes.push({ id: time, dependsOn: [rabi] });
  edges.push({ from: rabi, to: time }, { from: time, to: iq });
  const kinds = classifyFlowEdges(nodes, edges);
  assert.equal(kinds.get(edges.find(edge => edge.from === rabi && edge.to === iq)!), "dependency");
  assert.equal(kinds.get(edges.find(edge => edge.from === rabi && edge.to === time)!), "main");
  assert.equal(kinds.get(edges.find(edge => edge.from === time && edge.to === iq)!), "main");
  assert.equal(buildFlowGeometry(nodes, edges).routes.length, edges.length, "cross-step dependencies remain drawn");
});

test("classification is independent of node order, resources, and single/two-qubit branches", () => {
  const { nodes, edges } = mainGraph(["Q02", "Q11"], true);
  const kinds = classifyFlowEdges([...nodes].reverse(), [...edges].reverse());
  assert.equal(kinds.size, edges.length);
  assert.ok(edges.every(edge => kinds.get(edge) === "main"));
  assert.equal(edges.filter(edge => edge.to.endsWith(":d01_flux_spectroscopy")).length, 6);
});

test("only explicit rollback kinds are orange; cycles and self-links are dependencies", () => {
  const nodes = ["a", "b", "c"].map(id => ({ id }));
  const edges = [
    { from: "a", to: "b" }, { from: "b", to: "c" }, { from: "c", to: "a" },
    { from: "a", to: "a" }, { from: "c", to: "a", kind: "backtrack" },
    { from: "b", to: "a", kind: "rollback" },
  ];
  assert.deepEqual([...classifyFlowEdges(nodes, edges).values()], ["dependency", "dependency", "dependency", "dependency", "rollback", "rollback"]);
});

test("backtracks, duplicate edges, and missing nodes do not create alternate dependency paths", () => {
  const nodes = ["a", "b", "c"].map(id => ({ id }));
  const edges = [
    { from: "a", to: "b" }, { from: "a", to: "b" }, { from: "b", to: "c" },
    { from: "c", to: "a", kind: "backtrack" }, { from: "a", to: "missing" }, { from: "missing", to: "b" },
  ];
  assert.deepEqual([...classifyFlowEdges(nodes, edges).values()], ["main", "main", "main", "rollback", "dependency", "dependency"]);
});
