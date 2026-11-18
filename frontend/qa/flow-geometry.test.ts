import test from "node:test";
import assert from "node:assert/strict";
import { buildFlowGeometry, segmentCrossesCard, topologicalFlowNodes } from "../src/lib/flowGeometry.ts";

const nodes = ["找腔", "定频", "Rabi", "IQ判别", "读出优化", "T1", "T2", "RB"].map((label, index) => ({ id: `n${index}`, label }));
const edges = [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [4, 6], [4, 7]].map(([from, to]) => ({ from: `n${from}`, to: `n${to}` }));
function assertNoCardCrossing(geometry: ReturnType<typeof buildFlowGeometry>) {
  for (const route of geometry.routes) {
    for (let segment = 1; segment < route.points.length; segment++) {
      for (const card of geometry.cards) {
        assert.equal(segmentCrossesCard(route.points[segment - 1], route.points[segment], card), false, `${route.edge.from} → ${route.edge.to} segment ${segment} crosses ${card.id}`);
      }
    }
    for (const point of route.points) {
      assert.ok(point.x >= 0 && point.x <= geometry.width);
      assert.ok(point.y >= 0 && point.y <= geometry.height);
    }
  }
}

test("T1/T2/RB branch edges never pass through intervening cards", () => {
  const geometry = buildFlowGeometry(nodes, edges);
  assertNoCardCrossing(geometry);
  const t2 = geometry.routes.find(route => route.edge.to === "n6")!;
  const rb = geometry.routes.find(route => route.edge.to === "n7")!;
  assert.ok(t2.points.length >= 4 && rb.points.length >= 4);
  assert.notEqual(t2.points[1].y, rb.points[1].y, "each long dependency has an independent lane");
  assert.notEqual(t2.points[0].x, rb.points[0].x, "source ports do not obscure each other");
  assert.equal(t2.label, "依赖 → T2");
  assert.equal(rb.label, "依赖 → RB");
});

test("rollback and self-loop paths use outside rails and identify their actual targets", () => {
  const geometry = buildFlowGeometry(nodes, [...edges, { from: "n7", to: "n1", kind: "backtrack" }, { from: "n5", to: "n3", kind: "rollback" }, { from: "n0", to: "n0", kind: "backtrack" }]);
  assertNoCardCrossing(geometry);
  const backward = geometry.routes.filter(route => route.rollback);
  assert.equal(backward.length, 3);
  assert.ok(backward.every(route => route.label?.startsWith("回退 → ")));
  const rightmostCard = Math.max(...geometry.cards.map(card => card.x + card.width));
  assert.ok(backward.every(route => route.points.some(point => point.x > rightmostCard)));
});

test("15-step dependencies across rows and multiple incoming branches remain outside card interiors", () => {
  const all = Array.from({ length: 15 }, (_, index) => ({ id: `n${index}`, label: `步骤${index}` }));
  const more = [...edges, ...[5, 6, 7].map(from => ({ from: `n${from}`, to: "n8" })), ...Array.from({ length: 6 }, (_, index) => ({ from: `n${index + 8}`, to: `n${index + 9}` })), { from: "n14", to: "n11", kind: "backtrack" }];
  assertNoCardCrossing(buildFlowGeometry(all, more));
});

test("dense forward and backward routes allocate enough spacing without crossing any card", () => {
  const all = Array.from({ length: 20 }, (_, index) => ({ id: `n${index}` }));
  const dense = [];
  for (let index = 0; index < 19; index++) dense.push({ from: `n${index}`, to: `n${index + 1}` });
  for (const to of [5, 6, 7, 8, 11, 14, 19]) dense.push({ from: "n4", to: `n${to}` });
  for (const to of [0, 1, 5, 6, 8, 11]) dense.push({ from: "n19", to: `n${to}` });
  assertNoCardCrossing(buildFlowGeometry(all, dense));
});

test("full single-qubit auxiliary dependencies stay forward and appear before their consumers", () => {
  const full = [...nodes, ...["S21Power", "TimeRabi", "AllXY", "Ramsey", "Echo"].map((label, index) => ({ id: `n${index + 8}`, label }))];
  const fullEdges = [...edges, ...[[0,8],[8,1],[2,9],[9,3],[4,10],[5,11],[11,12],[12,6],[10,7],[5,7],[6,7]].map(([from,to])=>({from:`n${from}`,to:`n${to}`}))];
  const before = JSON.stringify({full,fullEdges});
  const geometry = buildFlowGeometry(full,fullEdges);
  const positions = new Map(geometry.cards.map((card,index)=>[card.id,index]));
  for (const edge of fullEdges) assert.ok(positions.get(edge.from)! < positions.get(edge.to)!, `${edge.from} precedes ${edge.to}`);
  assert.equal(geometry.routes.filter(route=>route.rollback).length,0);
  assertNoCardCrossing(geometry);
  assert.equal(JSON.stringify({full,fullEdges}),before,"presentation must not reorder the backend's source snapshot");
  assert.deepEqual(geometry.cards.map(card=>card.id),["n0","n8","n1","n2","n9","n3","n4","n5","n10","n11","n12","n6","n7"]);
});

test("explicit backtrack preserves dependency order while an untyped cycle is never called rollback", () => {
  const shuffled=[nodes[2],nodes[0],nodes[1]];
  const dependencies=[{from:"n0",to:"n1"},{from:"n1",to:"n2"}];
  const withBacktrack=[...dependencies,{from:"n2",to:"n0",kind:"backtrack"}];
  assert.deepEqual(topologicalFlowNodes(shuffled,withBacktrack).map(node=>node.id),["n0","n1","n2"]);
  const geometry=buildFlowGeometry(shuffled,withBacktrack);
  assert.equal(geometry.routes.filter(route=>route.rollback).length,1);
  assertNoCardCrossing(geometry);
  const cycle=buildFlowGeometry(shuffled,[...dependencies,{from:"n2",to:"n0"}]);
  assert.equal(cycle.routes.filter(route=>route.rollback).length,0);
  assert.equal(cycle.cards.length,3);
  assertNoCardCrossing(cycle);
});
