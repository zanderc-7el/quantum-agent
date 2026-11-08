import type { ExperimentNode, Workflow } from '../types.ts';
import { DEMO_CZ_WIDTH_NS, DEMO_THRESHOLDS, measurementStageLabel, nodeMeasurements } from './measurements.ts';
import { buildNodeComparisons, formatMetricValue, THRESHOLD_SOURCE_LABELS } from './metricComparison.ts';
import type { MetricComparison } from './metricComparison.ts';
import { latestNodeParameters } from './remoteMeasurements.ts';
import type { PublicationPayload, QubitParameters } from './remoteMeasurements.ts';
import { NODE_DURATION_SECONDS } from './simulator.ts';

export interface ParameterValue { key: string; label: string; value: string | number; unit?: string; source?: string; }

const SERVER_FIELD_LABELS: Record<string, { label: string; unit: string }> = {
  f01Ghz: { label: 'f01 频率', unit: 'GHz' },
  readoutGhz: { label: '读取频率', unit: 'GHz' },
  t1Us: { label: 'T1 弛豫时间', unit: 'μs' },
  t2Us: { label: 'T2 相干时间', unit: 'μs' },
};

/**
 * Recorded parameters from the server, grouped per qubit. Unknown numeric
 * fields are shown with their wire key instead of being dropped: a real
 * controller may report settings this build does not model yet.
 */
function serverParameterValues(parameters: Record<string, QubitParameters>): ParameterValue[] {
  const values: ParameterValue[] = [];
  for (const [qubit, fields] of Object.entries(parameters)) {
    for (const [key, raw] of Object.entries(fields)) {
      if (typeof raw !== 'number' || !Number.isFinite(raw)) continue;
      const meta = SERVER_FIELD_LABELS[key];
      values.push({
        key: `server_${qubit}_${key}`,
        label: `${meta?.label ?? key}（${qubit}）`,
        value: raw,
        unit: meta?.unit,
        source: '服务端参数',
      });
    }
  }
  return values;
}

export function selectParameterNode(workflow: Workflow, requested?: ExperimentNode): ExperimentNode | undefined {
  return (requested && workflow.nodes.find(node => node.id === requested.id))
    || workflow.nodes.find(node => node.status === 'attention')
    || workflow.nodes.find(node => node.status === 'running')
    || workflow.nodes[0];
}

/** Read-only presentation snapshot derived from current props, never a telemetry stream. */
export function buildNodeParameters(workflow: Workflow, requested?: ExperimentNode) {
  const node = selectParameterNode(workflow, requested);
  if (!node) return null;
  const measurement = nodeMeasurements(workflow, node);
  const { stage } = measurement;
  // Recorded parameters and the publication/readback record come from the
  // server's per-node snapshot. A stale attempt is never shown for a retry.
  const parameterRecord = latestNodeParameters(workflow.id, node.id);
  const serverCurrent = parameterRecord && parameterRecord.attempt === node.attempt && stage !== 'preview' ? parameterRecord : undefined;
  const serverResults = serverCurrent ? serverParameterValues(serverCurrent.parameters) : [];
  const publication: PublicationPayload | null = serverCurrent?.publication ?? null;
  const dataSource = serverCurrent ? 'server' : 'simulated';
  const isIQ = /IqToProbability|ReadoutParaSweep/.test(node.name);
  const isDecay = /T1Idle|T2CPMG/.test(node.name);
  const isXEB = /XEB|RB1QCal|CZParaOptimizer/.test(node.name);
  const isSpectrum = /ReadoutFreqDip|Spectroscopy/.test(node.name);
  const isRabi = /Rabi|Swap/.test(node.name);
  const isCZ = /CZ|TTGA|QQSwap|F01ZPulse/.test(node.name);
  const sampleCount = isIQ ? 88 : isDecay ? 21 : 17;
  const progress = Math.max(0, Math.min(100, node.progress));
  const observedSamples = stage === 'preview' || progress === 0 ? 0 : stage === 'ready' ? sampleCount : isIQ ? Math.max(1, Math.floor(44 * progress / 100)) * 2 : Math.ceil(sampleCount * progress / 100);
  const status = workflow.status === 'paused' && node.status === 'running' ? '已暂停' : ({ pending: '尚未执行', running: '模拟执行中', success: '已完成 / 已确认', attention: '等待决策', abandoned: '已终止' } as const)[node.status];
  const inputs: ParameterValue[] = [
    { key: 'machine', label: '目标机器', value: workflow.machine, source: '工作流配置' },
    { key: 'target', label: '目标比特', value: node.qubits || workflow.target, source: '工作流配置' },
    { key: 'shots', label: '每个设置 shots', value: 200, unit: '次', source: '前端演示配置' },
    { key: 'sample_points', label: isIQ ? '代表性IQ散点' : '计划采样点', value: sampleCount, unit: '点', source: '前端演示配置' },
  ];
  if (isIQ || /Readout/.test(node.name)) inputs.push(
    { key: 'readout_freq', label: '读取频率', value: 6.742, unit: 'GHz', source: '前端演示配置' },
    { key: 'readout_amp', label: '读取幅度', value: 0.18, unit: 'a.u.', source: '前端演示配置' },
    { key: 'readout_duration', label: '读取时长', value: 1000, unit: 'ns', source: '前端演示配置' },
  );
  else if (isDecay) inputs.push(
    { key: 'delay_range', label: '延迟扫描范围', value: '0–160', unit: 'μs', source: '前端演示配置' },
    { key: 'pulse_sequence', label: '脉冲序列', value: node.name === 'T2CPMG' ? 'CPMG · n = 4' : 'Xπ → idle → readout', source: '前端演示配置' },
  );
  else inputs.push(
    { key: 'drive_freq', label: '驱动频率', value: 4.982, unit: 'GHz', source: '前端演示配置' },
    { key: 'drive_amp', label: '驱动幅度', value: 0.217, unit: 'a.u.', source: '前端演示配置' },
  );
  if (isCZ) inputs.push({ key: 'CZ_width', label: 'CZ脉冲宽度', value: DEMO_CZ_WIDTH_NS, unit: 'ns', source: node.name === 'TTGAczDelicateG' ? '参考流程指定值' : '前端演示配置' });

  const candidate: ParameterValue[] = isIQ || isXEB
    ? [{ key: 'fidelity', label: isIQ ? '判别保真度' : '门保真度', value: measurement.fidelity, unit: '%' }]
    : isDecay
      ? [{ key: 'tau', label: node.name === 'T1Idle' ? 'T1 弛豫时间' : 'T2 相干时间', value: measurement.tau, unit: 'μs' }, { key: 'display_error', label: '预设展示误差', value: measurement.error, unit: 'μs' }]
      : isSpectrum
        ? [{ key: 'center_frequency', label: '模拟中心频率', value: node.name === 'ReadoutFreqDip' ? 6.742 : 4.982, unit: 'GHz' }]
        : isRabi
          ? [{ key: 'optimal_amplitude', label: '模拟最优幅度', value: 0.217, unit: 'a.u.' }]
          : [{ key: 'pulse_width', label: '演示脉冲宽度', value: DEMO_CZ_WIDTH_NS, unit: 'ns' }];
  if (!isIQ) candidate.push({ key: 'display_r_squared', label: '预设展示 R²', value: measurement.anomaly ? 0.948 : measurement.r2, unit: '无量纲' });
  // Recorded device parameters win over the demo candidate; the demo values
  // remain only as an explicit preview before the node has run.
  const results = stage === 'ready' ? (serverResults.length > 0 ? serverResults : candidate) : [];
  const resultPreview = stage === 'preview' ? candidate : [];
  const hasThreshold = isIQ || isXEB || isDecay;
  const acceptedLowMetric = stage === 'ready' && measurement.anomaly && node.status === 'success';
  let comparisons: MetricComparison[] = [];
  try {
    comparisons = buildNodeComparisons(workflow, node);
  } catch {
    // A comparison is an aid, never a reason to fail the parameter panel.
    comparisons = [];
  }
  // The bottom line and every export reuse the comparison's resolved
  // references, so a device floor can never sit next to a contradicting
  // demo constant. Only a fully demo-sourced comparison keeps the legacy
  // demo wording.
  const comparison = comparisons[0];
  const demoReferences = comparison?.targetSource === 'demo' && comparison.thresholdSource === 'demo';
  const operator = comparison?.direction === 'higher' ? '≥' : comparison?.direction === 'lower' ? '≤' : '偏差 ≤';
  const threshold = comparison
    ? demoReferences
      ? isIQ || isXEB
        ? `保真度 ≥ ${DEMO_THRESHOLDS.fidelityPercent}%`
        : isDecay
          ? `时间 ≥ ${DEMO_THRESHOLDS.coherenceUs} μs`
          : '此节点未定义数值阈值'
      : comparison.threshold === null
        ? `${comparison.label} 未设验收下限`
        : `${comparison.label} ${operator} ${formatMetricValue(comparison.threshold, comparison.unit, comparison.decimals)}（${THRESHOLD_SOURCE_LABELS[comparison.thresholdSource]}）`
    : '此节点未定义数值阈值';
  const assessment = stage !== 'ready'
    ? '尚无最终判定'
    : acceptedLowMetric
      ? '已接受低指标，接受不代表达标'
      : !comparison
        ? hasThreshold ? '执行完成，未生成对比' : '执行完成，未设置数值阈值'
        : demoReferences
          ? measurement.anomaly
            ? '低于演示阈值，等待处理'
            : hasThreshold ? '符合演示阈值' : '执行完成，未设置数值阈值'
          : comparison.status === 'unavailable'
            ? '无有效实测值'
            : comparison.thresholdSource === 'none'
              ? '已执行 · 未设验收下限'
              : comparison.status === 'failing'
                ? `未达${THRESHOLD_SOURCE_LABELS[comparison.thresholdSource]}，等待处理`
                : comparison.status === 'achieved'
                  ? '达到设计目标'
                  : comparison.status === 'passing'
                    ? '符合验收标准，距理想尚差'
                    : '尚无最终判定';
  const runtime = {
    workflowStatus: workflow.status,
    nodeStatus: node.status,
    status,
    progress,
    attempt: node.attempt,
    retries: Math.max(0, node.attempt - 1),
    workflowElapsedDemoSeconds: Number(workflow.elapsed.toFixed(3)),
    nodeElapsedDemoSeconds: Number(((progress / 100 + Math.max(0, node.attempt - 1)) * NODE_DURATION_SECONDS).toFixed(3)),
    observedSamples,
    plannedSamples: sampleCount,
  };
  const comparisonJson = comparisons.map(item => ({
    metric: item.metric,
    label: item.label,
    ideal: item.ideal,
    threshold: item.threshold,
    actual: item.actual,
    unit: item.unit,
    status: item.status,
    shortfall: item.shortfall,
    attainment: item.attainment,
    source: item.source,
    targetSource: item.targetSource,
    thresholdSource: item.thresholdSource,
  }));
  return {
    node,
    stage,
    stageLabel: measurementStageLabel(node),
    status,
    inputs,
    runtime,
    results,
    resultPreview,
    comparisons,
    threshold,
    assessment,
    acceptedLowMetric,
    dataSource,
    publication,
    publicationVerified: serverCurrent?.publicationVerified ?? null,
    raw: {
      dataSource,
      platform: '超导量子自进化测控平台',
      hardwareTelemetryConnected: publication !== null && publication.adapter !== 'simulator',
      workflowId: workflow.id,
      nodeId: node.id,
      experiment: node.name,
      stage,
      inputs: Object.fromEntries(inputs.map(item => [item.key, { value: item.value, unit: item.unit ?? null, source: item.source }])),
      runtime,
      results: stage === 'ready' ? Object.fromEntries(results.map(item => [item.key, { value: item.value, unit: item.unit ?? null }])) : null,
      previewValues: stage === 'preview' ? Object.fromEntries(resultPreview.map(item => [item.key, { value: item.value, unit: item.unit ?? null }])) : null,
      publication,
      serverParameters: serverCurrent?.parameters ?? null,
      quality: { threshold, assessment, comparisons: comparisonJson },
    },
  };
}
