import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { groupLivePoints, linkedLiveAxes, numericAxes, preferredLiveAxis, liveAxisLabel, POINT_INDEX_AXIS } from '../src/lib/liveSeries.ts';
import type { LivePoint } from '../src/lib/liveSeries.ts';

// Generated with hardware-free platform_specs.build_job; every sample is OFFLINE.
const fixture = JSON.parse(readFileSync(new URL('./fixtures/cpmg-native-axes.json', import.meta.url), 'utf8'));
const context = { adapterId: 'arclight-private-f8-q11-v2', procedureId: 'sq_cpmg' };
const axis = 'exp.cir.main.wait_a.params';
function points(n: number, capture = 0, extra: Record<string, unknown> = {}): LivePoint[] {
  const example = fixture.examples.find((entry: { pi_pulses: number }) => entry.pi_pulses === n);
  return example.axes.map((axes: Record<string, unknown>, index: number) => ({ operationId: 'OFFLINE', jobId: 'same-job', jobName: 'OFFLINE CPMG',
    pointIndex: capture * 5 + index, capture, baseIndex: index, axes: { ...axes, 'exp.cir.default': 'main', ...extra },
    meanI: index, meanQ: 0, meanAbs: index, meanMagnitude: index, shots: 128,
    pointSha256: `OFFLINE-${n}-${capture}-${index}`, pointFile: 'OFFLINE-not-acquired.npz', cursor: index + 1 }));
}

test('CPMG fixture is bound to exact private native source SHA and profile N bounds', () => {
  for (const source of fixture.sourceChecks) assert.equal(createHash('sha256').update(readFileSync(new URL(`../../${source.path}`, import.meta.url))).digest('hex'), source.sha256);
  const profile = JSON.parse(readFileSync(new URL('../../adapters/arclight_profile_v2.json', import.meta.url), 'utf8'));
  assert.equal(profile.id, context.adapterId); assert.deepEqual(profile.parameterBounds.pi_pulses, [1, 16]);
  for (const n of [1, 2, 4, 16]) {
    const example = fixture.examples.find((entry: { pi_pulses: number }) => entry.pi_pulses === n);
    assert.equal(example.meta.expand_mode, 'zip'); assert.equal(example.totalDelayMultiplier, 2 * n);
    assert.equal(example.axisKey, axis); assert.equal(example.meta.pi_pulses, n);
    example.axes.forEach((values: Record<string, number>, index: number) => assert(Math.abs(values[axis] * 2 * n - example.meta.total_delays_s[index]) < 1e-18));
  }
});

test('audited N1/N2/default N4/N16 zip traces preserve every capture, raw point and half-gap coordinate', () => {
  for (const n of [1, 2, 4, 16]) {
    const raw = [...points(n, 1).reverse(), ...points(n).reverse()];
    const before = JSON.stringify(raw), groups = groupLivePoints(raw, axis, context);
    assert.equal(preferredLiveAxis(numericAxes(raw), 'sq_cpmg', context.adapterId), axis);
    assert.equal(groups.length, 2); assert(groups.every(group => group.cpmgPiPulses === n && group.points.length === 5));
    assert.deepEqual(groups.map(group => group.capture).sort(), [0, 1]);
    assert.equal(new Set(groups.flatMap(group => group.points.map(item => item.point))).size, 10);
    for (const group of groups) for (const item of group.points) assert.equal(item.x, item.point.axes[axis]);
    assert.equal(groups[0].points.at(-1)?.x, 8e-6 / (2 * n));
    assert.equal(JSON.stringify(raw), before); assert.deepEqual(liveAxisLabel(axis), { label: '原生等待时长 (μs)', scale: 1e-6 });
  }
});

test('different N structures, job IDs, captures and independent conditions never merge', () => {
  const mixed = [...points(1), ...points(2), ...points(4), ...points(4, 1),
    ...points(4, 0, { 'cfg.Q1.para.drivFreq': 4.8e9 }), ...points(4, 0, { 'cfg.Q1.para.drivFreq': 4.9e9 }),
    ...points(4).map(point => ({ ...point, jobId: 'another-job' }))];
  const groups = groupLivePoints(mixed, axis, context);
  assert.equal(groups.length, 7); assert(groups.every(group => group.points.length === 5));
  assert.equal(groups.flatMap(group => group.points).length, mixed.length);
  const selectedFullGap = groupLivePoints(points(4), 'exp.cir.main.wait_0.params', context);
  assert.equal(selectedFullGap.length, 1); assert.equal(selectedFullGap[0].points.at(-1)?.x, 8e-6 / 4);
});

test('unknown identity/method, missing or noncontiguous waits, invalid values and N17 retain generic grouping', () => {
  for (const other of [{}, { adapterId: 'arclight-private-f8-q11-v1', procedureId: 'sq_cpmg' }, { ...context, procedureId: 'sq_echo' }]) {
    assert.equal(groupLivePoints(points(4), axis, other).length, 5);
  }
  assert.equal(preferredLiveAxis(numericAxes(points(4)), 'sq_cpmg', 'unknown'), 'exp.cir.main.wait_0.params');
  for (const alter of [
    (axes: Record<string, unknown>) => { delete axes['exp.cir.main.wait_1.params']; },
    (axes: Record<string, unknown>) => { axes['exp.cir.main.wait_5.params'] = axes['exp.cir.main.wait_0.params']; },
    (axes: Record<string, unknown>) => { axes['exp.cir.main.wait_b.params'] = 0; },
    (axes: Record<string, unknown>) => { axes['exp.cir.main.wait_0.params'] = 'unknown'; },
    (axes: Record<string, unknown>) => { axes['exp.cir.main.wait_0.params'] = 123; },
  ]) {
    const raw = points(4); raw.forEach(point => alter(point.axes));
    assert(groupLivePoints(raw, axis, context).every(group => group.cpmgPiPulses === undefined));
    assert.deepEqual(linkedLiveAxes(axis, context, raw[0].axes), []);
  }
  const seventeen = { [axis]: 1e-6, ...Object.fromEntries(Array.from({ length: 17 }, (_, i) => [`exp.cir.main.wait_${i}.params`, i === 16 ? 1e-6 : 2e-6])) };
  assert.deepEqual(linkedLiveAxes(axis, context, seventeen), []);
  assert.deepEqual(linkedLiveAxes(POINT_INDEX_AXIS, context, points(4)[0].axes), []);
});

test('single-item native timing arrays remain untouched and CPMG text never describes virtual phase or total-time conversion', () => {
  const raw = points(2); raw.forEach(point => { for (const key of Object.keys(point.axes).filter(key => key.startsWith('exp.cir.main.wait_'))) point.axes[key] = [point.axes[key]]; });
  const before = JSON.stringify(raw), groups = groupLivePoints(raw, axis, context);
  assert.equal(groups.length, 1); assert.equal(groups[0].points.length, 5); assert.equal(JSON.stringify(raw), before);
  const text = buildSync({ entryPoints: [fileURLToPath(new URL('../src/components/LiveMeasurementSeries.tsx', import.meta.url))], bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', loader: { '.css': 'empty' } }).outputFiles[0].text;
  const module = { exports: {} as Record<string, Parameters<typeof createElement>[0]> };
  new Function('require', 'module', 'exports', text)(createRequire(import.meta.url), module, module.exports);
  const html = renderToStaticMarkup(createElement(module.exports.LiveLinkedAxisNote, { cpmg: true, linked: true }));
  assert.match(html, /横轴保持所选原生等待时长，未换算为总等待时间/); assert.doesNotMatch(html, /相位/);
  assert.match(html, /不同 N、采集轮次及其他扫描条件分别显示/);
});
