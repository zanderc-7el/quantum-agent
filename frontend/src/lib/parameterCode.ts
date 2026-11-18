import type { ExperimentNode, Workflow } from '../types.ts';
import { buildNodeParameters } from './nodeParameters.ts';

export type ParameterCodeTokenKind = 'key' | 'string' | 'number' | 'boolean' | 'null' | 'comment' | 'punctuation' | 'plain';
export interface ParameterCodeToken { kind: ParameterCodeTokenKind; text: string; }
export interface ParameterCodeLine { id: string; text: string; tokens: ParameterCodeToken[]; }
export interface ParameterCodeDocument {
  filename: string;
  text: string;
  lines: ParameterCodeLine[];
  stage: string;
  nodeId: string;
}

type Scalar = string | number | boolean | null;

// JSON permits these separators in strings, but a code viewer needs one physical line per field.
function json(value: Scalar): string {
  return JSON.stringify(value).replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
}

function comment(value: string): string {
  return value.replace(/[\u0000-\u001f\u007f\u2028\u2029]+/g, ' ').trim();
}

/** A read-only JSONC view of the current simulation snapshot; no preview metrics become results. */
export function buildParameterCode(workflow: Workflow, requested?: ExperimentNode): ParameterCodeDocument | null {
  const snapshot = buildNodeParameters(workflow, requested);
  if (!snapshot) return null;
  const { node, runtime } = snapshot;
  const lines: ParameterCodeLine[] = [];
  const push = (id: string, tokens: ParameterCodeToken[]) => {
    lines.push({ id, text: tokens.map(token => token.text).join(''), tokens });
  };
  const punctuation = (text: string): ParameterCodeToken => ({ kind: 'punctuation', text });
  const plain = (text: string): ParameterCodeToken => ({ kind: 'plain', text });
  const note = (id: string, text: string) => push(id, [{ kind: 'comment', text: `// ${comment(text)}` }]);
  const field = (path: string, value: Scalar, explanation: string, last = false, depth = 2) => {
    const key = path.slice(path.lastIndexOf('.') + 1);
    const normalized = typeof value === 'number' && !Number.isFinite(value) ? null : value;
    const kind: ParameterCodeTokenKind = normalized === null ? 'null' : typeof normalized as 'string' | 'number' | 'boolean';
    push(path, [plain('  '.repeat(depth)), { kind: 'key', text: json(key) }, punctuation(': '),
      { kind, text: json(normalized) }, punctuation(last ? '' : ','),
      ...(explanation ? [plain(' '), { kind: 'comment' as const, text: `// ${comment(explanation)}` }] : []),
    ]);
  };
  const open = (name: string) => push(`${name}.$open`, [plain('  '), { kind: 'key', text: json(name) }, punctuation(': {')]);
  const close = (name: string, last = false) => push(`${name}.$close`, [plain('  '), punctuation(last ? '}' : '},')]);

  note('$source', '超导量子自进化测控平台 · 模拟数据 · 只读实时快照');
  note('$telemetry', '尚未连接真实硬件；输入来自演示配置，results 仅在该节点模拟执行完成后产生。');
  push('$open', [punctuation('{')]);
  field('data_source', 'simulated', '数据来源', false, 1);
  field('hardware_telemetry_connected', false, '真实硬件连接', false, 1);
  field('read_only', true, '运行快照，仅供查看', false, 1);

  open('workflow');
  field('workflow.id', workflow.id, '工作流 ID');
  field('workflow.name', workflow.name, '任务名称');
  field('workflow.machine', workflow.machine, '目标机器');
  field('workflow.target', workflow.target, '任务目标比特');
  field('workflow.experiment', workflow.experiment, 'single 单比特 / two 双比特');
  field('workflow.policy', workflow.policy, '审批策略');
  field('workflow.scenario', workflow.scenario, '模拟场景', true);
  close('workflow');

  open('node');
  field('node.id', node.id, '节点 ID');
  field('node.experiment', node.name, '实验方法');
  field('node.label', node.label, '实验名称');
  field('node.target', node.qubits || workflow.target, '当前节点目标比特');
  field('node.stage', snapshot.stage, snapshot.stageLabel, true);
  close('node');

  open('inputs');
  snapshot.inputs.forEach((item, index) => field(`inputs.${item.key}`, item.value,
    [item.unit, item.label, item.source].filter(Boolean).join(' · '), index === snapshot.inputs.length - 1));
  close('inputs');

  open('runtime');
  field('runtime.workflow_status', runtime.workflowStatus, '任务状态');
  field('runtime.node_status', runtime.nodeStatus, '节点状态');
  field('runtime.status', runtime.status, '当前运行情况');
  field('runtime.progress', runtime.progress, '% · 当前节点进度');
  field('runtime.workflow_progress', workflow.progress, '% · 整体任务进度');
  field('runtime.attempt', runtime.attempt, '次 · 当前执行序号，0 表示尚未开始');
  field('runtime.retries', runtime.retries, '次 · 重试次数');
  field('runtime.workflow_elapsed_seconds', runtime.workflowElapsedDemoSeconds, 's · 工作流模拟耗时');
  field('runtime.node_elapsed_seconds', runtime.nodeElapsedDemoSeconds, 's · 当前节点累计模拟耗时');
  field('runtime.observed_samples', runtime.observedSamples, '点 · 已生成模拟采样');
  field('runtime.planned_samples', runtime.plannedSamples, '点 · 计划采样', true);
  close('runtime');

  if (snapshot.stage === 'ready') {
    open('results');
    snapshot.results.forEach((item, index) => field(`results.${item.key}`, item.value,
      [item.unit, item.label, '已执行模拟结果'].filter(Boolean).join(' · '), index === snapshot.results.length - 1));
    close('results');
  } else {
    field('results', null, `${snapshot.stageLabel}；暂无最终结果`, false, 1);
  }

  open('quality');
  field('quality.threshold', snapshot.threshold, '演示阈值');
  field('quality.assessment', snapshot.assessment, '当前判定');
  field('quality.accepted_low_metric', snapshot.acceptedLowMetric, '已接受低指标不等于达标', true);
  close('quality', true);
  push('$close', [punctuation('}')]);

  // Keep file labels safe even when imported experiment names contain path separators or controls.
  const basename = node.name.normalize('NFKC').replace(/[^\p{L}\p{N}_-]+/gu, '_').replace(/^_+|_+$/g, '').slice(0, 80) || 'experiment';
  return { filename: `${basename}.parameters.jsonc`, text: lines.map(line => line.text).join('\n'), lines, stage: snapshot.stage, nodeId: node.id };
}
