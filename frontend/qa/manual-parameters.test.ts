import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildManualParameterFields, createManualParameterDraft, displayParameterValue,
  parseDisplayParameter, manualParameterChanges, validateManualParameterDraft, splitManualParameterFields,
} from '../src/lib/manualParameters.ts';
import type { ExperimentNode, NodeDetail, ParameterBounds } from '../src/types.ts';

const bounds: ParameterBounds = {
  readout_hz: { min: 6.5e9, max: 7.6e9 }, drive_hz: { min: 4e9, max: 5.5e9 },
  span_hz: { min: 1e5, max: 1e8 }, drive_length_s: { min: 4e-9, max: 2e-6 },
  readout_width_s: { min: 1.2e-6, max: 4e-6 }, readout_amp: { min: 0.01, max: 0.98 },
  points: { min: 9, max: 161, integer: true }, shots: { min: 100, max: 2047 },
  start_s: { min: 0, max: 1e-4 }, stop_s: { min: 1e-8, max: 1e-4 },
  amp_start: { min: 0, max: 0.98 }, amp_stop: { min: 0.01, max: 0.99 },
};
const node = { id: 'n1' } as ExperimentNode;
const detail = { node, attempts: [], decisions: [], rules: [], parameterBounds: bounds,
  parameters: { readout_hz: 6.75e9, drive_hz: 4.83e9, span_hz: 4e6, drive_length_s: 40e-9,
    readout_width_s: 2e-6, points: 61, shots: 2000, start_s: 0, stop_s: 8e-6, amp_start: .05, amp_stop: .98 },
} as NodeDetail;

test('human units round-trip to exact API units and convert bounds consistently', () => {
  const fields = buildManualParameterFields(bounds);
  for (const [key, unit, input, base, min, max] of [
    ['readout_hz', 'GHz', '6.75', 6.75e9, 6.5, 7.6],
    ['drive_hz', 'GHz', '4.83', 4.83e9, 4, 5.5],
    ['span_hz', 'MHz', '4', 4e6, .1, 100],
    ['drive_length_s', 'ns', '40', 40e-9, 4, 2000],
    ['readout_width_s', 'µs', '2', 2e-6, 1.2, 4],
  ] as const) {
    const field = fields.find(item => item.key === key)!;
    assert.equal(field.unit, unit);
    assert.equal(field.min, min); assert.equal(field.max, max);
    assert.equal(displayParameterValue(base, field), input);
    assert.equal(parseDisplayParameter(input, field), base);
  }
});

test('unsupported schema values and disallowed keys never become editable fields', () => {
  const schema = { ...bounds, pulse_compatibility: { enum: ['unsupported-string'] },
    bad: { min: NaN, max: 1 }, reversed: { min: 10, max: 1 }, numeric_enum: { enum: [1, 2] } } as unknown as ParameterBounds;
  assert.deepEqual(buildManualParameterFields(schema, ['points', 'pulse_compatibility']).map(field => field.key), ['points']);
  assert.equal(buildManualParameterFields(schema).some(field => ['bad', 'reversed', 'pulse_compatibility'].includes(field.key)), false);
  assert.deepEqual(buildManualParameterFields(schema).find(field => field.key === 'numeric_enum')!.rule.enum, [1, 2]);
});

test('blank, nonfinite, embedded units, out-of-range and fractional counts are rejected', () => {
  const fields = buildManualParameterFields(bounds);
  const readout = fields.find(field => field.key === 'readout_hz')!;
  for (const input of ['', ' ', 'Infinity', 'NaN', '6.75GHz', '0x19', '[6.75]', '6.4', '7.7']) {
    assert.throws(() => parseDisplayParameter(input, readout));
  }
  for (const key of ['shots', 'points']) {
    assert.throws(() => parseDisplayParameter('100.5', fields.find(field => field.key === key)!));
  }
});

test('schema labels and already-scaled backend units remain authoritative', () => {
  const [field] = buildManualParameterFields({ span_hz: { min: 1, max: 20, unit: 'MHz', label: '频率跨度' } });
  assert.equal(field.label, '频率跨度'); assert.equal(field.unit, 'MHz');
  assert.equal(displayParameterValue(4, field), '4'); assert.equal(parseDisplayParameter('5', field), 5);
});

test('effective values stay missing when absent and only changed fields are submitted', () => {
  const draft = createManualParameterDraft(detail, node, 7);
  assert.equal(draft.values.readout_amp, '');
  assert.deepEqual(manualParameterChanges(draft), {});
  draft.values.readout_hz = '6.8';
  assert.deepEqual(validateManualParameterDraft(draft, 7), { readout_hz: 6.8e9 });
  assert.equal(detail.parameters!.readout_hz, 6.75e9);
});

test('revision changes block dirty drafts and preserve pending input', () => {
  const draft = createManualParameterDraft(detail, node, 7);
  draft.values.readout_hz = '6.8';
  assert.throws(() => validateManualParameterDraft(draft, 8), /已更新/);
  assert.equal(draft.values.readout_hz, '6.8'); assert.equal(draft.revision, 7);
});

test('scan range validation uses edited plus effective endpoints', () => {
  const draft = createManualParameterDraft(detail, node, 7);
  draft.values.start_s = '9';
  assert.throws(() => validateManualParameterDraft(draft, 7), /起点.*终点/);
  draft.values.stop_s = '12';
  assert.deepEqual(validateManualParameterDraft(draft, 7), { start_s: 9e-6, stop_s: 12e-6 });
  draft.values.amp_start = '.98';
  assert.throws(() => validateManualParameterDraft(draft, 7), /起点.*终点/);
});

test('numeric choices and fixed bounds are preserved', () => {
  const fields = buildManualParameterFields({ mode: { min: 1, max: 3, enum: [1, 3] }, xy_filter_mhz: { min: 4700, max: 4700, integer: true } });
  assert.throws(() => parseDisplayParameter('2', fields[0]));
  assert.equal(parseDisplayParameter('3', fields[0]), 3);
  assert.equal(displayParameterValue(4700, fields[1]), '4700');
  assert.equal(fields[1].unit, 'MHz');
});

test('Rabi amplitude exposes its amplitude axis and folds unrelated method options without removing authorization', () => {
  const fields = buildManualParameterFields({ ...bounds,
    pi_pulses: { min: 1, max: 16 }, rotation_hz: { min: -2e6, max: 2e6 },
    spectroscopy_width_s: { min: 1e-7, max: 1.2e-5 },
  });
  const { primary, more } = splitManualParameterFields(fields, 'sq_rabi_amplitude');
  assert.ok(['amp_start', 'amp_stop', 'points', 'readout_hz', 'drive_hz', 'shots'].every(key => primary.some(field => field.key === key)));
  assert.ok(['span_hz', 'start_s', 'stop_s', 'pi_pulses', 'rotation_hz', 'spectroscopy_width_s'].every(key => more.some(field => field.key === key)));
  assert.deepEqual([...primary, ...more].map(field => field.key).sort(), fields.map(field => field.key).sort());
});

test('method-specific controls follow native scan usage and unknown procedures retain every field', () => {
  const fields = buildManualParameterFields({ ...bounds,
    pi_pulses: { min: 1, max: 16 }, rotation_hz: { min: -2e6, max: 2e6 },
    spectroscopy_width_s: { min: 1e-7, max: 1.2e-5 },
  });
  const primary = (procedure: string) => splitManualParameterFields(fields, procedure).primary.map(field => field.key);
  assert.ok(primary('sq_spectroscopy').includes('span_hz'));
  assert.ok(primary('sq_spectroscopy').includes('spectroscopy_width_s'));
  assert.ok(primary('sq_rabi_frequency').includes('stop_s'));
  assert.equal(primary('sq_rabi_frequency').includes('span_hz'), false);
  assert.ok(primary('sq_cpmg').includes('pi_pulses'));
  assert.equal(primary('sq_cpmg').includes('rotation_hz'), false);
  assert.ok(primary('sq_ramsey').includes('rotation_hz'));
  assert.equal(primary('ro_power_shift').includes('amp_start'), false);
  assert.equal(primary('ro_iq01').includes('points'), false);
  assert.deepEqual(splitManualParameterFields(fields, 'future_method'), { primary: fields, more: [] });
  assert.deepEqual(splitManualParameterFields(fields), { primary: fields, more: [] });
});
