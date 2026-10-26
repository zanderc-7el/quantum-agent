import { useId, useState } from 'react';
import { ChevronDown, Code2, SlidersHorizontal } from 'lucide-react';
import type { ExperimentNode, Workflow } from '../types';
import { buildNodeParameters } from '../lib/nodeParameters';
import type { ParameterValue } from '../lib/nodeParameters';
import './parameter-panel.css';

export interface ParameterPanelProps {
  workflow: Workflow;
  node?: ExperimentNode;
  defaultOpen?: boolean;
  compact?: boolean;
  title?: string;
  /** Show the full contents inside a parent inspector without an accordion. */
  embedded?: boolean;
}

function Values({ values }: { values: ParameterValue[] }) {
  return <dl className="pp-values">{values.map(item => <div key={item.key}><dt>{item.label}<code>{item.key}</code></dt><dd><strong>{item.value}</strong>{item.unit && <span>{item.unit}</span>}{item.source && <small>{item.source}</small>}</dd></div>)}</dl>;
}

export function ParameterPanel({ workflow, node, defaultOpen = false, compact = false, title = '参数与执行状态', embedded = false }: ParameterPanelProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [rawOpen, setRawOpen] = useState(false);
  const id = useId();
  const snapshot = buildNodeParameters(workflow, node);
  if (!snapshot) return <div className="pp-panel pp-empty">当前任务没有可展示的实验节点。</div>;
  const { runtime } = snapshot;
  return <section className={`pp-panel ${compact ? 'pp-compact' : ''} ${embedded ? 'pp-embedded' : ''}`} aria-label={`${snapshot.node.label}参数面板`}>
    {!embedded && <button type="button" className="pp-toggle" aria-expanded={open} aria-controls={`${id}-body`} onClick={() => setOpen(!open)}>
      <SlidersHorizontal size={16} /><span><strong>{title}</strong><small>{snapshot.node.name}</small></span><span className="pp-toggle-status">{open ? '收起' : '展开'} · {Math.round(runtime.progress)}%</span><ChevronDown size={15} className={open ? 'pp-rotated' : ''} />
    </button>}
    {(embedded || open) && <div id={`${id}-body`} className="pp-body">
      <div className={`pp-stage pp-stage-${snapshot.stage}`}><span>{snapshot.stageLabel}</span><small>只读 · 随模拟状态更新</small></div>
      <p className="pp-disclaimer">输入为演示配置，状态来自当前工作流；未连接真实设备遥测，未下发参数。</p>
      <h3>输入配置</h3><Values values={snapshot.inputs} />
      <h3>当前执行</h3>
      <div className="pp-runtime" aria-live="polite" aria-atomic="true">
        <div><span>节点状态</span><strong>{snapshot.status}</strong></div><div><span>执行进度</span><strong>{runtime.progress.toFixed(1)}<small>%</small></strong></div>
        <div><span>执行次数 attempt</span><strong>{runtime.attempt}<small>次</small></strong></div><div><span>重试次数</span><strong>{runtime.retries}<small>次</small></strong></div>
        <div><span>本节点累计演示时间</span><strong>{runtime.nodeElapsedDemoSeconds}<small>s</small></strong></div><div><span>工作流累计演示时间</span><strong>{runtime.workflowElapsedDemoSeconds}<small>s</small></strong></div>
        <div><span>已生成演示采样</span><strong>{runtime.observedSamples}<small>/ {runtime.plannedSamples} 点</small></strong></div>
      </div>
      <div className="pp-progress" role="progressbar" aria-label={`${snapshot.node.label}执行进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={runtime.progress}><span style={{ width: `${runtime.progress}%` }} /></div>
      <h3>{snapshot.stage === 'preview' ? '结果示例预览' : '模拟结果与指标'}</h3>
      {snapshot.results.length > 0 ? <Values values={snapshot.results} /> : snapshot.stage === 'preview' ? <><p className="pp-result-note">此节点尚未执行。以下数值仅展示结果格式，不是已采集结果。</p><Values values={snapshot.resultPreview} /></> : <p className="pp-result-note">{snapshot.stage === 'interrupted' ? '执行已终止，仅保留当前进度与采样数量，不显示最终结果。' : '模拟采样尚未完成，当前进度实时更新；最终参数将在节点完成后显示。'}</p>}
      <div className={`pp-assessment ${snapshot.acceptedLowMetric || snapshot.assessment.includes('低于') ? 'pp-warning' : ''}`}><span>{snapshot.threshold}</span><strong>{snapshot.assessment}</strong></div>
      <button type="button" className="pp-json-toggle" aria-expanded={rawOpen} aria-controls={`${id}-json`} onClick={() => setRawOpen(!rawOpen)}><Code2 size={14} /><span>{rawOpen ? '收起' : '展开'}原始参数 JSON</span><ChevronDown size={14} className={rawOpen ? 'pp-rotated' : ''} /></button>
      {rawOpen && <pre id={`${id}-json`} className="pp-json" tabIndex={0} aria-label="当前模拟参数JSON">{JSON.stringify(snapshot.raw, null, 2)}</pre>}
      <p className="pp-footer">演示秒数不等于真机耗时。拟合误差与 R² 均为预设展示值。</p>
    </div>}
  </section>;
}

export default ParameterPanel;
