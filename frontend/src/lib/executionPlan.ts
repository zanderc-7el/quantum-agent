import type { NodeDetail } from '../types.ts';
import { displayParameterValue } from './manualParameters.ts';
import type { ManualParameterField } from './manualParameters.ts';

export function executionDefaultLabel(detail: NodeDetail | undefined, field: ManualParameterField): string {
  if (!detail || detail.resolvedExecutionPlan?.status !== 'ready' || detail.executionDefaultsSource !== 'adapter.build_plan' || Object.hasOwn(detail.parameters ?? {}, field.key)) return '';
  const value = detail.executionDefaults?.[field.key];
  if (typeof value !== 'number' || !Number.isFinite(value)) return '';
  return `执行默认：${displayParameterValue(value, field)}${field.unit ? ` ${field.unit}` : ''}`;
}
