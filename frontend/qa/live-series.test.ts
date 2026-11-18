import test from "node:test";
import assert from "node:assert/strict";
import { emptyLiveSeries, groupLivePoints, liveAxisLabel, mergeLiveSeries, numericAxes, POINT_INDEX_AXIS, preferredLiveAxis } from "../src/lib/liveSeries.ts";
import type { LivePoint, LiveSeriesPage } from "../src/lib/liveSeries.ts";
import { createRemoteGateway } from "../src/lib/remoteGateway.ts";

const point = (index: number, changes: Partial<LivePoint> = {}): LivePoint => ({ operationId: "op-a", jobId: "scan", jobName: "S21", pointIndex: index, capture: 0, baseIndex: index, axes: { "cfg.Q1.para.probFreq": 7e9 + index * 1e6 }, meanI: .01, meanQ: .02, meanAbs: Math.hypot(.01,.02), meanMagnitude: Math.hypot(.01,.02), shots: 2000, pointSha256: `hash-${index}`, pointFile: `point_${index}.npz`, cursor: index + 1, ...changes });
const page = (points: LivePoint[], changes: Partial<LiveSeriesPage> = {}): LiveSeriesPage => ({ operationId: "op-a", cursor: points.length ? Math.max(...points.map(item => item.cursor)) : 0, points, hasMore: false, complete: false, terminal: false, state: "running", source: "live_acquired_points", quality: "unverified", ...changes });

test("cursor pages reconnect without duplicating randomized acquisition points", () => {
  const first = mergeLiveSeries(emptyLiveSeries(), page([point(1), point(0)]));
  const next = mergeLiveSeries(first, page([point(1), point(2)]));
  assert.equal(next.points.length, 3); assert.equal(next.cursor, 3);
  assert.equal(first.points.length, 2);
  assert.equal(mergeLiveSeries(next, page([point(0)])), next);
  const [group] = groupLivePoints(next.points, "cfg.Q1.para.probFreq");
  assert.deepEqual(group.points.map(item => item.point.pointIndex), [0,1,2]);
});
test("changing operation clears previous points and accepts the new independent cursor", () => {
  const first = mergeLiveSeries(emptyLiveSeries(), page([point(9)]));
  const next = mergeLiveSeries(first, page([point(0, { operationId: "verification-b" })], { operationId: "verification-b" }));
  assert.equal(next.operationId, "verification-b"); assert.equal(next.cursor, 1);
  assert.deepEqual(next.points.map(item => item.pointIndex), [0]);
  assert.deepEqual(mergeLiveSeries(next, page([], { operationId: null, state: "not_started" })), emptyLiveSeries());
});
test("mismatched operation, modified raw hash and nonfinite IQ never enter the buffer", () => {
  const first = mergeLiveSeries(emptyLiveSeries(), page([point(0)]));
  assert.throws(() => mergeLiveSeries(first, page([point(1, { operationId: "other" })])), /其他执行轮次/);
  assert.throws(() => mergeLiveSeries(first, page([point(0, { pointSha256: "changed" })])), /内容发生变化/);
  assert.throws(() => mergeLiveSeries(first, page([point(1, { meanI: NaN })])), /无效 I\/Q/);
  assert.equal(first.points[0].pointSha256, "hash-0");
});
test("capture, job, categorical preparation and every remaining axis remain separate", () => {
  const axis = "cfg.Q1.para.probFreq", common = { [axis]: 7e9, "cfg.Q1.para.probCarryScale": .3, "exp.cir.default": "ground" };
  const points = [point(0, { axes: common }), point(1, { axes: { ...common, [axis]: 7.001e9 } }),
    point(2, { capture: 1, axes: common }), point(3, { jobId: "independent", axes: common }),
    point(4, { axes: { ...common, "exp.cir.default": "excited" } }), point(5, { axes: { ...common, "cfg.Q1.para.probCarryScale": .4 } })];
  const groups = groupLivePoints(points, axis);
  assert.equal(groups.length, 5); assert.deepEqual(groups.map(group => group.points.length).sort(), [1,1,1,1,2]);
});
test("native single-element timing arrays are numeric axes without altering original axes", () => {
  const axis = "exp.cir.main.wait.params";
  const points = [point(0, { axes: { [axis]: [2e-6], "multi": [1,2], state: "ground" } })];
  assert.deepEqual(numericAxes(points), [axis]);
  assert.equal(groupLivePoints(points, axis)[0].points[0].x, 2e-6);
  assert.deepEqual(points[0].axes[axis], [2e-6]);
  assert.deepEqual(liveAxisLabel(axis), { label: "原生等待时长 (μs)", scale: 1e-6 });
});
test("default axes follow physical procedure and categorical-only data uses point index", () => {
  const axes = ["cfg.Q1.para.probFreq", "cfg.Q1.para.probCarryScale", "cfg.Q1.para.drivFreq", "cfg.Q1.gate.U_gates.pi_len", "cfg.Q1.gate.U_gates.pi_amp"];
  assert.equal(preferredLiveAxis(axes, "ro_power_shift"), axes[0]);
  assert.equal(preferredLiveAxis(axes, "sq_spectroscopy"), axes[2]);
  assert.equal(preferredLiveAxis(axes, "sq_rabi_frequency"), axes[2]);
  assert.equal(preferredLiveAxis(axes, "sq_rabi_time"), axes[3]);
  assert.equal(preferredLiveAxis(axes, "sq_rabi_amplitude"), axes[4]);
  assert.equal(preferredLiveAxis([], "ro_iq01"), POINT_INDEX_AXIS);
});
test("live-series gateway uses encoded GET cursor and forwards abort without a command", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const gateway = createRemoteGateway({ fetch: (async (url, init) => { calls.push({ url: String(url), init }); return new Response(JSON.stringify(page([])), { status: 200 }); }) as typeof fetch });
  const controller = new AbortController();
  await gateway.liveSeries("run/a", "node/b", 42, "op x", controller.signal);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/workflows/run%2Fa/nodes/node%2Fb/live-series?after=42&operationId=op+x");
  assert.equal(calls[0].init?.signal, controller.signal);
  assert.equal(calls[0].init?.method, undefined); assert.equal(calls[0].init?.body, undefined);
  controller.abort(); assert.equal((calls[0].init?.signal as AbortSignal).aborted, true);
});
