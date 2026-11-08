import type { ExperimentNode, Workflow } from '../types.ts';
import { buildNodeParameters } from './nodeParameters.ts';
import { formatMetricValue, TARGET_SOURCE_LABELS, THRESHOLD_SOURCE_LABELS } from './metricComparison.ts';

/** Current storage has node snapshots and bounded event history, not per-attempt telemetry. */
export const EXPERIMENT_CSV_HISTORY_NOTE = '导出当前浏览器仍保留的全部已运行任务，包含各节点当前状态、配置、已生成的模拟结果及保留的事件。未开始的节点不填最终结果；重试前被覆盖的测量、已删除任务和超过保留上限的旧事件无法还原。数值均为前端模拟数据，非真实设备采集。';

const workflowStatusLabels: Record<Workflow['status'], string> = {
  draft: '草稿', queued: '排队中', running: '运行中', attention: '需要关注', paused: '已暂停', completed: '已完成', failed: '失败', aborted: '已终止',
};
const nodeStatusLabels: Record<ExperimentNode['status'], string> = {
  success: '已完成或确认', running: '运行节点', pending: '等待执行', attention: '等待决策', abandoned: '已终止',
};
const policyLabels: Record<Workflow['policy'], string> = { strict: '逐步审批', threshold: '异常时审批', autopilot: '自动运行' };
const experimentLabels: Record<Workflow['experiment'], string> = { single: '单比特', two: '双比特' };
const workflowTypeLabels: Record<Workflow['type'], string> = { adaptive: '自适应', fixed: '固定' };
const scenarioLabels: Record<Workflow['scenario'], string> = { normal: '正常流程', anomaly: '异常场景' };
const eventKindLabels: Record<Workflow['events'][number]['kind'], string> = { info: '信息', success: '成功', warning: '警告' };

/** Human labels remain paired with stable keys for spreadsheet filtering and integrations. */
function enumLabel<K extends string>(key: K, labels: Record<K, string>): string { return `${labels[key]} (${key})`; }

const columns = [
  ['recordType', '记录类型'], ['dataSource', '数据来源'], ['exportedAt', '导出时间(UTC)'],
  ['workflowId', '任务ID'], ['importSourceId', '导入源任务ID'], ['workflowName', '任务名称'],
  ['experiment', '流程类型'], ['machine', '机器'], ['target', '目标比特'], ['qubits', '比特数'],
  ['workflowType', '编排类型'], ['ownerId', '负责人账号ID'], ['owner', '负责人'],
  ['createdAt', '创建时间'], ['workflowStatus', '任务状态'], ['workflowProgress', '任务进度(%)'],
  ['workflowElapsed', '累计演示运行时间(s)'], ['iterations', '重试迭代数'], ['policy', '审批策略'],
  ['scenario', '模拟场景'], ['approved', '当前审批标记'],
  ['nodeIndex', '节点序号'], ['nodeId', '节点ID'], ['nodeName', '实验名称'], ['nodeLabel', '节点名称'],
  ['nodeTarget', '节点目标比特'], ['nodeStatus', '节点状态'], ['executed', '节点是否已执行'],
  ['stage', '结果阶段'], ['stageLabel', '结果阶段说明'], ['nodeProgress', '节点进度(%)'],
  ['attempt', '执行次数'], ['retries', '重试次数'], ['nodeElapsed', '节点累计估算演示时间(s)'],
  ['observedSamples', '已观察模拟样本数'], ['plannedSamples', '计划模拟样本数'],
  ['detail', '节点详情'], ['threshold', '判定阈值'], ['thresholdSource', '阈值来源'],
  ['designTarget', '设计目标'], ['targetSource', '目标来源'], ['assessment', '判定说明'],
  ['fieldKey', '字段键'], ['fieldLabel', '字段名称'], ['fieldValue', '字段值'], ['unit', '单位'], ['source', '参数来源'],
  ['eventIndex', '事件序号'], ['eventTime', '事件时间'], ['eventKind', '事件级别'], ['eventMessage', '事件内容'],
  ['raw', '原始参数JSON'], ['note', '记录说明'],
] as const;

type ColumnKey = typeof columns[number][0];
type CellValue = string | number | boolean | null | undefined;
type CsvRow = Partial<Record<ColumnKey, CellValue>>;

export interface ExperimentCsvExport {
  filename: string;
  content: string;
  mimeType: 'text/csv;charset=utf-8';
  workflowCount: number;
  nodeCount: number;
  eventCount: number;
  /** Data rows, excluding the one header row. */
  rowCount: number;
  excludedWorkflowCount: number;
  duplicateWorkflowCount: number;
  historyNote: string;
}

/** A canceled queue entry is not an experiment run; a just-started node at 0% is. */
export function isExecutedWorkflow(workflow: Workflow): boolean {
  return workflow.elapsed > 0 || workflow.iterations > 0 || workflow.nodes.some(node => node.attempt > 0 || node.progress > 0);
}

function selectExecuted(workflows: readonly Workflow[]) {
  const seen = new Set<string>();
  const selected: Workflow[] = [];
  let excludedWorkflowCount = 0;
  let duplicateWorkflowCount = 0;
  for (const workflow of workflows) {
    if (seen.has(workflow.id)) { duplicateWorkflowCount += 1; continue; }
    seen.add(workflow.id);
    if (isExecutedWorkflow(workflow)) selected.push(workflow);
    else excludedWorkflowCount += 1;
  }
  return { selected, excludedWorkflowCount, duplicateWorkflowCount };
}

export function countExecutedWorkflows(workflows: readonly Workflow[]): number {
  return selectExecuted(workflows).selected.length;
}

/** Quote every cell, and neutralize text interpreted as formulas by spreadsheet programs. */
function csvCell(value: CellValue): string {
  let text = value == null ? '' : String(value);
  if (typeof value === 'string' && /^(?:[\u0000-\u0020]*[=+\-@]|[\t\r\n])/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

/** Pure export: no storage writes, execution, wall-clock progression, or retained export buffer. */
export function buildExperimentCsv(workflows: readonly Workflow[], exportedAt: Date | string = new Date()): ExperimentCsvExport {
  const date = new Date(exportedAt);
  if (!Number.isFinite(date.getTime())) throw new Error('CSV导出时间无效。');
  const timestamp = date.toISOString();
  const { selected, excludedWorkflowCount, duplicateWorkflowCount } = selectExecuted(workflows);
  const rows: CsvRow[] = [];
  let nodeCount = 0;
  let eventCount = 0;
  for (const workflow of selected) {
    const base: CsvRow = {
      dataSource: 'simulated', exportedAt: timestamp,
      workflowId: workflow.id, importSourceId: workflow.importSourceId, workflowName: workflow.name,
      experiment: enumLabel(workflow.experiment, experimentLabels), machine: workflow.machine, target: workflow.target, qubits: workflow.qubits,
      workflowType: enumLabel(workflow.type, workflowTypeLabels), ownerId: workflow.ownerId, owner: workflow.owner, createdAt: workflow.createdAt,
      workflowStatus: enumLabel(workflow.status, workflowStatusLabels), workflowProgress: workflow.progress, workflowElapsed: workflow.elapsed,
      iterations: workflow.iterations, policy: enumLabel(workflow.policy, policyLabels), scenario: enumLabel(workflow.scenario, scenarioLabels), approved: workflow.approved,
    };
    rows.push({ ...base, recordType: '任务', note: EXPERIMENT_CSV_HISTORY_NOTE });
    workflow.nodes.forEach((node, index) => {
      const snapshot = buildNodeParameters(workflow, node);
      if (!snapshot) return;
      const comparison = snapshot.comparisons[0];
      const nodeBase: CsvRow = {
        ...base, nodeIndex: index + 1, nodeId: node.id, nodeName: node.name, nodeLabel: node.label, nodeTarget: node.qubits,
        nodeStatus: enumLabel(node.status, nodeStatusLabels), executed: node.attempt > 0 || node.progress > 0,
        stage: snapshot.stage, stageLabel: snapshot.stageLabel, nodeProgress: snapshot.runtime.progress,
        attempt: snapshot.runtime.attempt, retries: snapshot.runtime.retries, nodeElapsed: snapshot.runtime.nodeElapsedDemoSeconds,
        observedSamples: snapshot.runtime.observedSamples, plannedSamples: snapshot.runtime.plannedSamples,
        detail: node.detail, threshold: snapshot.threshold, assessment: snapshot.assessment,
        // Structured counterparts to the human-readable threshold text, so a
        // spreadsheet can tell a device criterion from a demo constant.
        thresholdSource: comparison ? THRESHOLD_SOURCE_LABELS[comparison.thresholdSource] : '',
        designTarget: comparison ? formatMetricValue(comparison.ideal, comparison.unit, comparison.decimals) : '',
        targetSource: comparison ? TARGET_SOURCE_LABELS[comparison.targetSource] : '',
      };
      rows.push({
        ...nodeBase, recordType: '节点',
        // Preview chart values are deliberately omitted from experiment data exports.
        raw: JSON.stringify({ ...snapshot.raw, previewValues: null }),
        note: snapshot.stage === 'preview' ? '尚未执行；仅列出配置，无最终结果。' : snapshot.stage === 'ready' ? '当前节点最近一次已生成的模拟结果。' : '保留当前进度，尚无最终结果。',
      });
      nodeCount += 1;
      for (const parameter of snapshot.inputs) rows.push({
        ...nodeBase, recordType: '配置参数', fieldKey: parameter.key, fieldLabel: parameter.label,
        fieldValue: parameter.value, unit: parameter.unit, source: parameter.source,
      });
      for (const result of snapshot.results) rows.push({
        ...nodeBase, recordType: '模拟结果', fieldKey: result.key, fieldLabel: result.label,
        fieldValue: result.value, unit: result.unit, source: '前端确定性模拟规则；非真实设备采集',
      });
    });
    workflow.events.forEach((event, index) => {
      rows.push({ ...base, recordType: '事件', eventIndex: index + 1, eventTime: event.time, eventKind: enumLabel(event.kind, eventKindLabels), eventMessage: event.message });
      eventCount += 1;
    });
  }
  const content = '\uFEFF' + [
    columns.map(([, label]) => csvCell(label)).join(','),
    ...rows.map(row => columns.map(([key]) => csvCell(row[key])).join(',')),
  ].join('\r\n') + '\r\n';
  return {
    filename: `全部已运行实验_${timestamp.replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z')}.csv`,
    content, mimeType: 'text/csv;charset=utf-8', workflowCount: selected.length, nodeCount, eventCount, rowCount: rows.length,
    excludedWorkflowCount, duplicateWorkflowCount, historyNote: EXPERIMENT_CSV_HISTORY_NOTE,
  };
}
