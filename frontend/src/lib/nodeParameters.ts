import type { ExperimentNode, Workflow } from '../types.ts';
import { DEMO_CZ_WIDTH_NS, DEMO_THRESHOLDS, measurementStageLabel, nodeMeasurements } from './measurements.ts';
import { NODE_DURATION_SECONDS } from './simulator.ts';

export interface ParameterValue { key: string; label: string; value: string | number; unit?: string; source?: string; }

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
  const results = stage === 'ready' ? candidate : [];
  const resultPreview = stage === 'preview' ? candidate : [];
  const hasThreshold = isIQ || isXEB || isDecay;
  const threshold = isIQ || isXEB ? `保真度 ≥ ${DEMO_THRESHOLDS.fidelityPercent}%` : isDecay ? `时间 ≥ ${DEMO_THRESHOLDS.coherenceUs} μs` : '此节点未定义数值阈值';
  const acceptedLowMetric = stage === 'ready' && measurement.anomaly && node.status === 'success';
  const assessment = stage !== 'ready' ? '尚无最终判定' : acceptedLowMetric ? '已接受低指标，接受不代表达标' : measurement.anomaly ? '低于演示阈值，等待处理' : hasThreshold ? '符合演示阈值' : '执行完成，未设置数值阈值';
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
  return {
    node,
    stage,
    stageLabel: measurementStageLabel(node),
    status,
    inputs,
    runtime,
    results,
    resultPreview,
    threshold,
    assessment,
    acceptedLowMetric,
    raw: {
      dataSource: 'simulated',
      platform: '超导量子自进化测控平台',
      hardwareTelemetryConnected: false,
      workflowId: workflow.id,
      nodeId: node.id,
      experiment: node.name,
      stage,
      inputs: Object.fromEntries(inputs.map(item => [item.key, { value: item.value, unit: item.unit ?? null, source: item.source }])),
      runtime,
      results: stage === 'ready' ? Object.fromEntries(results.map(item => [item.key, { value: item.value, unit: item.unit ?? null }])) : null,
      previewValues: stage === 'preview' ? Object.fromEntries(resultPreview.map(item => [item.key, { value: item.value, unit: item.unit ?? null }])) : null,
      quality: { threshold, assessment },
    },
  };
}
