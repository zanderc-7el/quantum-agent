import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { decisionBudgetRows } from '../src/lib/decisionBudget.ts';

// Exact recorded controller lookahead from the CPMG retry decision; no live service reads.
const recorded = () => ({ version: 'selection-verification-budget/1', selectionShots: 2592000,
  verificationShotsUpperBound: 2592000, jointShots: 5184000, remainingShotsAtCheck: 8584000,
  verificationBasis: 'same_sampling_counts_after_calibration_only', reservation: false,
  candidateKnown: false, actualVerificationPlanRechecked: true });

test('displays recorded shots, including captures, with decision-time remaining rather than computed remaining', () => {
  const value = recorded(), before = JSON.stringify(value), rows = decisionBudgetRows(value)!;
  assert.deepEqual(rows, [
    { label: '首测', value: '2,592,000' },
    { label: '独立复测上限', value: '2,592,000' },
    { label: '首测与独立复测合计', value: '5,184,000' },
    { label: '核算时剩余额度', value: '8,584,000' },
  ]);
  assert.equal(JSON.stringify(value), before);
  assert.ok(rows.every(row => row.value !== '3,400,000')); // no subtraction or new budget claim
});

test('known readout upper bound remains distinct and unknown or missing contracts are hidden', () => {
  const value = { ...recorded(), selectionShots: 1000, verificationShotsUpperBound: 100,
    jointShots: 1100, remainingShotsAtCheck: 1100, verificationBasis: 'arclight_single_setting_same_shots_captures_blocks' };
  assert.deepEqual(decisionBudgetRows(value)?.map(row => row.value), ['1,000', '100', '1,100', '1,100']);
  for (const input of [null, undefined, {}, [], '2592000', { ...recorded(), version: 'selection-verification-budget/2' },
    { ...recorded(), verificationBasis: 'unknown' }]) assert.equal(decisionBudgetRows(input), null);
  for (const key of Object.keys(recorded())) {
    const missing: Record<string, unknown> = recorded(); delete missing[key];
    assert.equal(decisionBudgetRows(missing), null, key);
  }
});

test('invalid counts cannot produce displayed numeric budgets', () => {
  for (const key of ['selectionShots', 'verificationShotsUpperBound', 'jointShots', 'remainingShotsAtCheck']) {
    for (const value of [null, undefined, '2592000', true, -1, 0, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])
      assert.equal(decisionBudgetRows({ ...recorded(), [key]: value }), null, `${key}=${value}`);
  }
  assert.equal(decisionBudgetRows({ ...recorded(), jointShots: 2592000 }), null);
  assert.equal(decisionBudgetRows({ ...recorded(), remainingShotsAtCheck: 5183999 }), null);
});

test('reservation, candidate and unsupported verification receipt forms are not mislabeled as a lookahead', () => {
  for (const value of [{ ...recorded(), reservation: true }, { ...recorded(), reservation: 'false' },
    { ...recorded(), candidateKnown: true }, { ...recorded(), actualVerificationPlanRechecked: false },
    { version: 'selection-verification-budget/1', actualVerificationShots: 2592000,
      remainingShotsAtCheck: 8584000, reservation: false, selectionChargedAgain: false }])
    assert.equal(decisionBudgetRows(value), null);
});

const compiled = buildSync({ entryPoints: [fileURLToPath(new URL('../src/components/RemotePanels.tsx', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', loader: { '.css': 'empty' },
}).outputFiles[0].text;
const module = { exports: {} as Record<string, Parameters<typeof createElement>[0]> };
new Function('require', 'module', 'exports', compiled)(createRequire(import.meta.url), module, module.exports);
const render = (value: unknown) => renderToStaticMarkup(createElement(module.exports.DecisionBudget, { value }));

test('controller block clearly labels precheck, verification upper bound and historical remaining', () => {
  const html = render(recorded());
  assert.match(html, /控制器预算核算（shots）/); assert.match(html, /采集前预检 · 未预留/);
  assert.match(html, /独立复测上限/); assert.match(html, /核算时剩余额度/);
  for (const value of ['2,592,000', '5,184,000', '8,584,000']) assert.ok(html.includes(value));
  assert.doesNotMatch(html, /3,400,000|当前剩余|已预留|已消耗|风险|警告/);
});

test('invalid or legacy records add no controller block or fallback guesses', () => {
  for (const input of [undefined, {}, { ...recorded(), jointShots: 0 }, { ...recorded(), reservation: true }])
    assert.equal(render(input), '');
});
