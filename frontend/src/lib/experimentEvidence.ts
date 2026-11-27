import type { ExperimentNode } from '../types.ts';
import { liveAxisLabel, numericAxisValue, POINT_INDEX_AXIS } from './liveSeries.ts';
import type { LivePoint } from './liveSeries.ts';

export function experimentPhases(node: ExperimentNode) {
  const state = node.engineState ?? node.status;
  const result = node.result;
  const verifying = state === 'verifying';
  const failed = ['failed', 'attention', 'unsupported', 'unsupported_method', 'needs_reconciliation'].includes(state);
  const collected = !verifying && !!result && !result.partial && !result.executionSummary;
  const analyzed = collected && !!result?.quality;
  const verified = state === 'verified' && result?.quality?.status === 'passed' && !result.stale;
  type State = 'done' | 'active' | 'pending' | 'failed';
  return [
    { id: 'parameters', label: '参数配置', state: (node.attempt > 0 ? 'done' : 'active') as State },
    { id: 'acquisition', label: verifying ? '数据采集（独立复测）' : '数据采集', state: (verifying ? 'active' : failed && !collected ? 'failed' : collected ? 'done' : ['running', 'acquiring', 'measuring'].includes(state) ? 'active' : 'pending') as State },
    { id: 'analysis', label: '结果分析', state: (analyzed ? 'done' : state === 'analyzing' || state === 'fitting' ? 'active' : 'pending') as State },
    { id: 'result', label: '结果与验收', state: (verified ? 'done' : 'pending') as State },
  ];
}

export function rabiPointEvidence(axis: string, point?: LivePoint) {
  const presentation = liveAxisLabel(axis);
  const value = point ? axis === POINT_INDEX_AXIS ? point.pointIndex : numericAxisValue(point.axes[axis]) : undefined;
  const match = presentation.label.match(/^(.*?) \((.*?)\)$/);
  return { variable: match?.[1] ?? presentation.label, unit: match?.[2] ?? '未记录', value: value === undefined ? undefined : Number((value / presentation.scale).toPrecision(12)),
    observable: '原始读出 I/Q', point,
    explanation: /\.pi_amp$/.test(axis) ? '逐点改变驱动幅度；脉宽及其他设置以本点原始参数为准。'
      : /\.pi_len$/.test(axis) ? '逐点改变驱动脉宽；驱动幅度及其他设置以本点原始参数为准。'
      : /\.driv(?:Carry)?Freq$/.test(axis) ? '逐点改变驱动频率；脉宽及其他设置以本点原始参数为准。' : '按本次记录的扫描变量逐点施加驱动并读出。' };
}
