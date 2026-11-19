import test from "node:test";
import assert from "node:assert/strict";
import { groupLivePoints, linkedLiveAxes, POINT_INDEX_AXIS } from "../src/lib/liveSeries.ts";
import type { LivePoint } from "../src/lib/liveSeries.ts";

const wait = "exp.cir.main.wait.params", phase = "exp.cir.main.phase.params";
const context = { adapterId: "arclight-private-f8-q11-v2", procedureId: "sq_ramsey" };
const point = (index: number, changes: Partial<LivePoint> = {}): LivePoint => ({
  operationId: "measurement", jobId: "ramsey", jobName: "Ramsey", pointIndex: index,
  capture: 0, baseIndex: index, axes: { [wait]: index * 1e-7, [phase]: -2 * Math.PI * 8e5 * index * 1e-7 },
  meanI: .001 + index * .00001, meanQ: .002, meanAbs: .0023, meanMagnitude: .0023,
  shots: 16000, pointSha256: `sha${index}`, pointFile: `point_${index}.npz`, cursor: index + 1, ...changes,
});

test("native zipped Ramsey phases no longer split every acquired point into a group", () => {
  const points = [point(2), point(0), point(1)], saved = structuredClone(points);
  const groups = groupLivePoints(points, wait, context);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].points.map(p => p.point.pointIndex), [0, 1, 2]);
  assert.deepEqual(points, saved);
  assert.equal(groups[0].points[2].point, points[0]);
  assert.equal(groups[0].points[2].point.axes[phase], saved[0].axes[phase]);
});

test("linked display never merges captures, jobs or other independent scan dimensions", () => {
  const points = [point(0), point(1), point(2, { capture: 1 }), point(3, { jobId: "other" }),
    point(4, { axes: { ...point(4).axes, "cfg.Q1.para.drivFreq": 4.86e9 } }),
    point(5, { axes: { ...point(5).axes, "cfg.Q1.para.drivFreq": 4.87e9 } })];
  const groups = groupLivePoints(points, wait, context);
  assert.deepEqual(groups.map(g => g.points.length).sort(), [1, 1, 1, 1, 2]);
  assert.equal(groups.flatMap(g => g.points).length, points.length);
});

test("unknown adapters and non-native methods retain every phase as an independent condition", () => {
  const points = [point(0), point(1), point(2)];
  for (const scope of [{}, { ...context, adapterId: "other" }, { ...context, procedureId: "sq_spectroscopy" }]) {
    assert.equal(groupLivePoints(points, wait, scope).length, 3);
    assert.deepEqual(linkedLiveAxes(wait, scope), []);
  }
});

test("phase-axis view remains linked but point-index view keeps explicit original dimensions", () => {
  assert.deepEqual(linkedLiveAxes(phase, context), [wait]);
  assert.equal(groupLivePoints([point(0), point(1)], phase, context).length, 1);
  assert.equal(groupLivePoints([point(0), point(1)], POINT_INDEX_AXIS, context).length, 2);
});

test("native echo half-waits and phase form one zipped sweep without changing raw timing", () => {
  const a = "exp.cir.main.wait_a.params", b = "exp.cir.main.wait_b.params";
  const points = [point(0, { axes: { [a]: [0], [b]: [0], [phase]: 0 } }),
    point(1, { axes: { [a]: [1e-6], [b]: [1e-6], [phase]: -4 * Math.PI * .2 } })];
  const groups = groupLivePoints(points, a, { ...context, procedureId: "sq_echo" });
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].points.map(p => p.x), [0, 1e-6]);
  assert.deepEqual(groups[0].points[1].point.axes[b], [1e-6]);
});
