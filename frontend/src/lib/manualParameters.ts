import type { ExperimentNode, NodeDetail, ParameterBound, ParameterBounds } from '../types.ts';
import { numericParameterBounds } from './parameters.ts';

export type ManualParameterGroup = 'scan' | 'readout' | 'drive' | 'acquisition' | 'advanced';
export const manualParameterGroups: { id: ManualParameterGroup; label: string }[] = [
  { id: 'scan', label: '扫描设置' }, { id: 'readout', label: '读出' },
  { id: 'drive', label: '驱动与脉冲' }, { id: 'acquisition', label: '采集' },
  { id: 'advanced', label: '其他参数' },
];

export interface ManualParameterField {
  key: string; label: string; group: ManualParameterGroup; unit: string;
  /** Decimal exponent from the displayed value to the API value. */
  exponent: number; min: number; max: number; rule: ParameterBound;
}
export interface ManualParameterDraft {
  revision: number; fields: ManualParameterField[]; baseline: Record<string, unknown>;
  initialValues: Record<string, string>; values: Record<string, string>;
}

const labels: Record<string, string> = {
  readout_hz: '读出频率', readout_amp: '读出幅度', readout_width_s: '读出脉冲时长',
  drive_hz: '驱动频率', drive_amp: 'π 脉冲幅度', drive_length_s: 'π 脉冲时长',
  half_pi_amp: 'π/2 脉冲幅度', xy_delay_s: 'XY 延时', xy_attenuation_db: 'XY 衰减',
  xy_filter_mhz: 'XY 滤波频率', spectroscopy_width_s: '频谱脉冲时长',
  span_hz: '频率扫描跨度', points: '扫描点数', start_s: '时间扫描起点', stop_s: '时间扫描终点',
  start_hz: '频率扫描起点', stop_hz: '频率扫描终点', amp_start: '幅度扫描起点', amp_stop: '幅度扫描终点',
  shots: '单次采样次数', capture_repeats: '采集重复次数', acquisitions_per_point: '每点采集块数',
  rotation_hz: '旋转频率', pi_pulses: 'π 脉冲数', rb_sequences: 'RB 序列数',
  rb_master_seed: 'RB 随机种子', rb_bootstrap_replicates: 'RB 重采样次数', order_seed: '扫描顺序种子',
};
const scan = new Set(['span_hz', 'points', 'start_s', 'stop_s', 'start_hz', 'stop_hz', 'amp_start', 'amp_stop']);
const counts = new Set(['shots', 'capture_repeats', 'acquisitions_per_point', 'points', 'pi_pulses', 'rb_sequences', 'rb_master_seed', 'rb_bootstrap_replicates', 'order_seed']);
const acquisition = new Set(['shots', 'capture_repeats', 'acquisitions_per_point']);

// Native scan inputs from platform_specs.build_job. Presentation only: every
// authorized field remains in the full draft and keeps the same save contract.
const methodParameters: Record<string, string[]> = {
  ro_resonator_survey: ['points', 'span_hz'], ro_power_shift: ['points', 'span_hz'],
  sq_spectroscopy: ['points', 'span_hz', 'start_hz', 'stop_hz', 'spectroscopy_width_s'],
  sq_rabi_time: ['points', 'start_s', 'stop_s'], sq_rabi_frequency: ['points', 'start_s', 'stop_s'],
  sq_rabi_amplitude: ['points', 'amp_start', 'amp_stop'], ro_iq01: [],
  ro_optimize: ['points', 'amp_start', 'amp_stop'],
  ro_optimize_frequency: ['points', 'span_hz'], ro_s21_ge: ['points', 'span_hz'],
  sq_t1: ['points', 'start_s', 'stop_s'],
  sq_ramsey: ['points', 'start_s', 'stop_s', 'rotation_hz'], sq_echo: ['points', 'start_s', 'stop_s', 'rotation_hz'],
  sq_cpmg: ['points', 'start_s', 'stop_s', 'pi_pulses'], sq_allxy: [],
  sq_rb: ['rb_lengths', 'rb_sequences', 'rb_master_seed', 'rb_bootstrap_replicates'],
};
const commonParameters = [
  'readout_hz', 'readout_amp', 'readout_width_s', 'drive_hz', 'drive_amp', 'drive_length_s',
  'xy_delay_s', 'xy_attenuation_db', 'xy_filter_mhz', 'shots', 'capture_repeats', 'acquisitions_per_point',
];

export function splitManualParameterFields(fields: ManualParameterField[], procedureId?: string): { primary: ManualParameterField[]; more: ManualParameterField[] } {
  if (!procedureId || !Object.hasOwn(methodParameters, procedureId)) return { primary: fields, more: [] };
  const common = new Set([...commonParameters, ...methodParameters[procedureId]]);
  return { primary: fields.filter(field => common.has(field.key)), more: fields.filter(field => !common.has(field.key)) };
}

/** Decimal shifting avoids visible 39.99999999999999 ns and preserves endpoints. */
function shift(value: number, exponent: number): number {
  if (!exponent || !Number.isFinite(value)) return value;
  const [mantissa, power = '0'] = String(value).split('e');
  return Number(`${mantissa}e${Number(power) + exponent}`);
}

function presentation(key: string, rule: ParameterBound): { unit: string; exponent: number } {
  const baseUnit = rule.unit?.trim();
  // Explicit schema units win over the canonical-key fallback. Values already in
  // MHz or ns must not receive a second conversion.
  if (baseUnit && !['Hz', 's', 'a.u.', 'a.u', 'au', 'dB', ''].includes(baseUnit)) return { unit: baseUnit, exponent: 0 };
  if (baseUnit === 'Hz' || (!baseUnit && key.endsWith('_hz'))) {
    return ['readout_hz', 'drive_hz', 'start_hz', 'stop_hz'].includes(key)
      ? { unit: 'GHz', exponent: 9 } : { unit: 'MHz', exponent: 6 };
  }
  if (baseUnit === 's' || (!baseUnit && key.endsWith('_s'))) {
    return ['drive_length_s', 'xy_delay_s'].includes(key)
      ? { unit: 'ns', exponent: -9 } : { unit: 'µs', exponent: -6 };
  }
  if (key.endsWith('_mhz') && !baseUnit) return { unit: 'MHz', exponent: 0 };
  if (baseUnit === 'dB' || (!baseUnit && key.endsWith('_db'))) return { unit: 'dB', exponent: 0 };
  if (baseUnit || key.endsWith('_amp') || key.startsWith('amp_')) return { unit: baseUnit || 'a.u.', exponent: 0 };
  return { unit: key === 'points' ? '点' : counts.has(key) && !key.endsWith('seed') ? '次' : '', exponent: 0 };
}

export function buildManualParameterFields(bounds: ParameterBounds = {}, allowedKeys?: string[]): ManualParameterField[] {
  const allowed = allowedKeys ? new Set(allowedKeys) : undefined;
  return Object.entries(numericParameterBounds(bounds)).flatMap(([key, sourceRule]) => {
    if ((allowed && !allowed.has(key)) || sourceRule.min > sourceRule.max) return [];
    const rule = { ...sourceRule, integer: sourceRule.integer || counts.has(key) };
    const { unit, exponent } = presentation(key, rule);
    const group: ManualParameterGroup = scan.has(key) ? 'scan' : key.startsWith('readout_') ? 'readout'
      : acquisition.has(key) ? 'acquisition'
      : /^(drive_|half_pi_|xy_|spectroscopy_|rotation_|pi_pulses)/.test(key) ? 'drive' : 'advanced';
    return [{ key, label: rule.label || labels[key] || key, group, unit, exponent,
      min: shift(rule.min, -exponent), max: shift(rule.max, -exponent), rule }];
  });
}

export function displayParameterValue(value: unknown, field: ManualParameterField): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(shift(value, -field.exponent)) : '';
}

export function parseDisplayParameter(raw: string, field: ManualParameterField): number {
  const text = raw.trim();
  if (!text) throw new Error(`${field.label}请输入数值`);
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) throw new Error(`${field.label}请输入${field.unit ? `以 ${field.unit} 为单位的` : ''}有限数值`);
  const value = shift(Number(text), field.exponent);
  if (!Number.isFinite(value)) throw new Error(`${field.label}必须为有限数值`);
  if (value < field.rule.min || value > field.rule.max) throw new Error(`${field.label}需在 ${field.min} 至 ${field.max}${field.unit ? ` ${field.unit}` : ''} 之间`);
  if (field.rule.integer && !Number.isInteger(value)) throw new Error(`${field.label}必须为整数`);
  if (field.rule.enum && !field.rule.enum.includes(value)) throw new Error(`${field.label}请选择允许的数值`);
  return value;
}

export function createManualParameterDraft(detail: NodeDetail, node: ExperimentNode, revision: number): ManualParameterDraft {
  const fields = buildManualParameterFields(detail.parameterBounds, node.editableParameterKeys);
  // Only effective parameters supplied by the detail endpoint are a baseline.
  const baseline = { ...detail.parameters };
  const initialValues = Object.fromEntries(fields.map(field => [field.key, displayParameterValue(baseline[field.key], field)]));
  return { revision, fields, baseline, initialValues, values: { ...initialValues } };
}

export function manualParameterChanges(draft: ManualParameterDraft): Record<string, string> {
  return Object.fromEntries(Object.entries(draft.values).filter(([key, value]) => value !== draft.initialValues[key]));
}

export function validateManualParameterDraft(draft: ManualParameterDraft, currentRevision: number): Record<string, number> {
  if (draft.revision !== currentRevision) throw new Error('配置已更新，请恢复未保存修改后重新编辑。');
  const changes = manualParameterChanges(draft);
  const numeric: Record<string, number> = {};
  for (const [key, raw] of Object.entries(changes)) {
    const field = draft.fields.find(item => item.key === key);
    if (!field) throw new Error(`参数 ${key} 未获准修改`);
    const value = parseDisplayParameter(raw, field);
    if (value !== draft.baseline[key]) numeric[key] = value;
  }
  if (!Object.keys(numeric).length) throw new Error('请至少修改一个参数数值。');
  const effective = { ...draft.baseline, ...numeric };
  for (const [start, stop] of [['start_s', 'stop_s'], ['amp_start', 'amp_stop'], ['start_hz', 'stop_hz']]) {
    if (!(start in numeric) && !(stop in numeric)) continue;
    const first = effective[start], last = effective[stop];
    if (typeof first === 'number' && typeof last === 'number' && first >= last) {
      const label = draft.fields.find(field => field.key === start)?.label ?? start;
      throw new Error(`${label}必须小于扫描终点。`);
    }
  }
  return numeric;
}
