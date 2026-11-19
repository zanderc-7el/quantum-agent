import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { MeasurementResult, Workflow } from '../src/types.ts';

const require = createRequire(import.meta.url);
function compile(path: string) {
  const text = buildSync({ entryPoints: [fileURLToPath(new URL(path, import.meta.url))], bundle: true,
    write: false, platform: 'node', format: 'cjs', packages: 'external', loader: { '.css': 'empty' } }).outputFiles[0].text;
  const module = { exports: {} as Record<string, Parameters<typeof createElement>[0]> };
  new Function('require', 'module', 'exports', text)(require, module, module.exports);
  return module.exports;
}
const panels = compile('../src/components/RemotePanels.tsx');
const withdrawal = compile('../src/components/ReadoutWithdrawal.tsx');
const render = (component: Parameters<typeof createElement>[0], props: Record<string, unknown>) => renderToStaticMarkup(createElement(component, props));

test('actual result labels native fit coordinates separately, draws dashed fits without measured dots', () => {
  const result: MeasurementResult = { dataKind: 'real', metrics: {}, artifacts: [], quality: { status: 'unverified', reasons: [] }, series: [
    { name: 'raw trace 1', fit: false, x: [0, 1, 2], y: [1, 2, 1] },
    { name: 'native fit 1', fit: true, x: Array.from({ length: 500 }, (_, i) => i / 100), y: Array.from({ length: 500 }, (_, i) => Math.cos(i / 100)) },
    { name: 'legacy curve', x: [0, 1], y: [2, 3] },
    { name: 'short fit', fit: true, x: [0, 1, 2], y: [1, 2, 1] },
  ] };
  const before = JSON.stringify(result);
  const html = render(panels.RealResult, { result });
  const figures = [...html.matchAll(/<figure class="rp-series">[\s\S]*?<\/figure>/g)].map(match => match[0]);
  assert.equal(figures.length, 4);
  assert.match(figures[0], /3 个实测数据点/);
  assert.equal((figures[0].match(/<circle/g) ?? []).length, 3);
  assert.match(figures[1], /native fit 1，拟合曲线，500 个绘制坐标/);
  for (const fit of [figures[1], figures[3]]) {
    assert.match(fit, /stroke-dasharray="6 4"/); assert.doesNotMatch(fit, /实测数据点|<circle/);
  }
  assert.match(figures[2], /2 个曲线坐标/); assert.doesNotMatch(figures[2], /实测数据点|拟合曲线/);
  assert.equal(JSON.stringify(result), before);
});

function restoredFixture(): Workflow {
  const values = { readout_hz: 7009510000, readout_amp: .8 }, versions = { readout_hz: 1, readout_amp: 2 };
  return { id: 'offline-wf', mode: 'real', status: 'running', nodes: [{ id: 'optimizer', resourceId: 'Q11', procedureId: 'ro_optimize',
    status: 'success', engineState: 'verified', latestAttemptId: 'fresh-fixed-verify', parameters: { ...values },
    parameterVersions: { ...versions }, result: { dataKind: 'real', quality: { status: 'passed', reasons: [] },
      metrics: { assignment_fidelity: .55 }, parameters: { ...values }, artifacts: [] } }],
    readoutRevalidations: { Q11: { id: 'restored', resourceId: 'Q11', state: 'completed', origin: 'operator_withdrawal',
      validationStatus: 'verified', withdrawalOperationId: 'withdraw-op', optimizerNodeId: 'optimizer', rabiNodeId: 'rabi',
      sourceAttemptId: 'old-optimizer', lastFixedAttemptId: 'fresh-fixed-verify', endedAt: '2026-11-18T12:28:05Z',
      values: { ...values }, parameterVersions: { ...versions } } },
    lastReadoutWithdrawal: { ruleVersion: 'readout-withdrawal/1', source: 'operator_withdrawal', operationId: 'withdraw-op',
      revalidationId: 'restored', validationStatus: 'unverified', reason: 'offline fixture', createdAt: '2026-11-18T11:07:54Z',
      before: { values: { ...values, readout_amp: .63125 }, parameterVersions: { readout_hz: 0, readout_amp: 1 } },
      after: { values: { ...values }, parameterVersions: { ...versions } }, invalidatedNodes: ['rabi', 'optimizer'],
      sourceEvidence: { optimizerAttemptId: 'old-optimizer', baselineIqAttemptId: 'old-iq', operations: [] } } } as unknown as Workflow;
}

test('matching fresh optimizer and readout versions show later validation while immutable withdrawal stays unverified', () => {
  const workflow = restoredFixture(), before = JSON.stringify(workflow);
  const html = render(withdrawal.default, { workflow, gateway: {} });
  assert.match(html, /撤回当时状态：未重新验证（unverified）/);
  assert.match(html, /撤回当时标记待复测节点：2/);
  assert.match(html, /恢复基准后的读出独立复验：已通过诊断验收/);
  assert.match(html, /fresh-fixed-verify/);
  assert.doesNotMatch(html, /高保真|单门保真|全部流程完成/);
  assert.equal(JSON.stringify(workflow), before);
});

test('missing, stale, mismatched or unaccepted current evidence cannot upgrade historical receipt display', () => {
  const changes: ((wf: Workflow) => void)[] = [
    wf => { wf.readoutRevalidations = undefined; },
    wf => { wf.readoutRevalidations!.Q11.withdrawalOperationId = 'other'; },
    wf => { wf.readoutRevalidations!.Q11.id = 'other'; },
    wf => { wf.readoutRevalidations!.Q11.validationStatus = 'unverified'; },
    wf => { wf.readoutRevalidations!.Q11.state = 'active'; },
    wf => { wf.readoutRevalidations!.Q11.origin = 'other'; },
    wf => { wf.readoutRevalidations!.Q11.endedAt = undefined; },
    wf => { wf.nodes[0].engineState = 'stale'; },
    wf => { wf.nodes[0].result!.stale = true; },
    wf => { wf.nodes[0].result!.quality.status = 'failed'; },
    wf => { wf.nodes[0].result!.dataKind = 'historical'; },
    wf => { wf.nodes[0].latestAttemptId = 'different'; },
    wf => { wf.nodes[0].resourceId = 'Q18'; },
    wf => { wf.nodes[0].parameters!.readout_amp = .63125; },
    wf => { wf.nodes[0].parameterVersions!.readout_amp = 3; },
    wf => { wf.nodes[0].result!.parameters!.readout_amp = .63125; },
    wf => { wf.readoutRevalidations!.Q11.parameterVersions.readout_amp = 3; },
    wf => { wf.lastReadoutWithdrawal!.after.parameterVersions.readout_amp = 3; },
  ];
  for (const change of changes) {
    const workflow = restoredFixture(); change(workflow);
    const html = render(withdrawal.default, { workflow, gateway: {} });
    assert.match(html, /这是保留的历史回执/);
    assert.doesNotMatch(html, /恢复基准后的读出独立复验：已通过/);
  }
});
