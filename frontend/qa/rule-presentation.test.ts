import test from 'node:test';
import assert from 'node:assert/strict';
import { ruleDisplay } from '../src/lib/ruleDisplay.ts';
import type { Rule } from '../src/types.ts';

const rule: Rule = { id: 'field.capture_limit', version: 1, title: '按本机有效采集段数分批', scope: ['s01_readout_frequency', 's02_qubit_frequency'], trigger: '本机采集段数超限', action: 'reject_and_split_capture', verification: '独立重复并检查逐段有效性', evidence: [], policyStatus: 'scoped_candidate_rule' };

test('rule presentation names scope and action while retaining recorded metadata unchanged', () => {
  const before = JSON.stringify(rule), display = ruleDisplay(rule);
  assert.equal(display.fields[0].text, '读取腔定频、比特定频');
  assert.equal(display.fields[2].text, '拒绝当前采集请求，拆分为多次采集');
  assert.deepEqual(display.fields[0].value, rule.scope);
  assert.equal(display.fields[2].value, 'reject_and_split_capture');
  assert.equal(display.statusLabel, '经验建议 · 未发布为自动规则');
  assert.equal(JSON.stringify(rule), before);
  assert.equal(ruleDisplay({ ...rule, action: 'redesign_scan' }).fields[2].text, '重新设计扫描');
});

test('structured scopes use recorded object and owner names without hiding untranslated conditions', () => {
  const display = ruleDisplay({ ...rule, scope: { resourceId: 'Q11', ownerId: 's03_rabi_amplitude', futureCondition: { flag: true } }, action: { kind: 'suggest_parameters', automaticExecution: false } });
  assert.match(display.fields[0].text, /Q11.*Rabi定幅/);
  assert.match(display.fields[0].text, /原始字段/);
  assert.equal(display.fields[2].text, '建议调整参数');
});

test('unknown structured fields remain disclosed in detail instead of emitting JSON or guessed actions', () => {
  const unknown = { future_kind: ['private_owner'] };
  const display = ruleDisplay({ ...rule, scope: ['s06_t1', 'future_owner'], trigger: unknown, action: 'future_action', verification: unknown });
  assert.match(display.fields[0].text, /T1测试/);
  for (const field of display.fields) {
    assert.match(field.text, /原始字段/);
    assert.doesNotMatch(field.text, /[\[\]{}]|future_owner|future_action/);
  }
  assert.deepEqual(display.fields[1].value, unknown);
});
