import { useId, useState } from 'react';
import { ChevronDown, Code2, SlidersHorizontal } from 'lucide-react';
import type { ExperimentNode, Workflow } from '../types';
import { buildNodeParameters } from '../lib/nodeParameters';
import type { ParameterValue } from '../lib/nodeParameters';
import {
  COMPARISON_STATUS_LABELS,
  TARGET_SOURCE_LABELS,
  THRESHOLD_SOURCE_LABELS,
  attainmentBar,
  comparisonStatusClass,
  describeComparisonGap,
  formatMetricValue,
} from '../lib/metricComparison';
import type { MetricComparison } from '../lib/metricComparison';
import type { PublicationPayload } from '../lib/remoteMeasurements';
import './parameter-panel.css';

const MISMATCH_REASONS: Record<string, string> = {
  value_mismatch: '回读值与下发值不一致',
  qubit_missing: '回读缺少该量子比特',
  readback_missing: '控制器未返回回读',
  readback_unavailable: '控制器不可达或响应无效',
};

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

function AttainmentBar({ comparison }: { comparison: MetricComparison }) {
  const bar = attainmentBar(comparison);
  if (!bar) return null;
  if (bar.kind === 'band')
    return <div className="pp-compare-bar pp-compare-bar--band" aria-hidden="true">
      <span className="pp-compare-band" style={{ left: `${bar.bandLeft}%`, width: `${bar.bandWidth}%` }} />
      {bar.floorLeft !== null && <i className="pp-compare-floor" style={{ left: `${bar.floorLeft}%` }} />}
      {bar.floorRight !== null && <i className="pp-compare-floor" style={{ left: `${bar.floorRight}%` }} />}
      <b className="pp-compare-ideal" style={{ left: `${bar.ideal}%` }} />
      <span className="pp-compare-actual" style={{ left: `${bar.actual}%` }} />
    </div>;
  return <div className="pp-compare-bar" aria-hidden="true">
    <span className="pp-compare-fill" style={{ width: `${bar.actual}%` }} />
    {bar.floor !== null && <i className="pp-compare-floor" style={{ left: `${bar.floor}%` }} />}
    <b className="pp-compare-ideal" style={{ left: `${bar.ideal}%` }} />
  </div>;
}

function ComparisonCard({ comparison }: { comparison: MetricComparison }) {
  return <div className={`pp-compare ${comparisonStatusClass(comparison.status)}`}>
    <div className="pp-compare-head">
      <span>{comparison.label}</span>
      <strong>{COMPARISON_STATUS_LABELS[comparison.status]}</strong>
    </div>
    <div className="pp-compare-values">
      <div><span>理想目标</span><strong>{formatMetricValue(comparison.ideal, comparison.unit, comparison.decimals)}</strong></div>
      <div><span>验收下限</span><strong>{formatMetricValue(comparison.threshold, comparison.unit, comparison.decimals)}</strong></div>
      <div><span>实测</span><strong>{formatMetricValue(comparison.actual, comparison.unit, comparison.decimals)}</strong></div>
    </div>
    <p className="pp-compare-gap">{describeComparisonGap(comparison)}</p>
    <AttainmentBar comparison={comparison} />
  </div>;
}

function PublicationCard({ publication, verified }: { publication: PublicationPayload; verified: boolean | null }) {
  const verifiedOk = verified === true;
  const fieldCount = Object.values(publication.requested).reduce((total, fields) => total + Object.keys(fields).length, 0);
  return <section className={`pp-publication ${verifiedOk ? 'pp-publication-ok' : 'pp-publication-bad'}`} aria-label="参数下发与回读校验">
    <div className="pp-publication-head">
      <span>下发与回读</span>
      <strong>{verifiedOk ? '回读一致' : verified === false ? '回读不一致' : '未确认'}</strong>
    </div>
    <dl className="pp-publication-meta">
      <div><dt>适配器</dt><dd>{publication.adapter}</dd></div>
      <div><dt>控制器</dt><dd>{publication.controller}</dd></div>
      <div><dt>下发字段</dt><dd>{fieldCount} 个</dd></div>
      <div><dt>回读时间</dt><dd>{publication.publishedAt || '—'}</dd></div>
    </dl>
    {publication.mismatches.length > 0 && <ul className="pp-publication-mismatches">
      {publication.mismatches.map((item, index) => <li key={`${item.qubit ?? 'global'}-${item.field ?? 'qubit'}-${index}`}>
        <code>{item.qubit ?? '全局'}{item.field ? ` · ${item.field}` : ''}</code>
        <span>
          {MISMATCH_REASONS[item.reason] ?? item.reason}
          {item.requested !== null ? `：下发 ${item.requested}` : ''}
          {item.readback !== null ? `，回读 ${item.readback}` : ''}
        </span>
      </li>)}
    </ul>}
    {publication.readback && <details className="pp-publication-readback">
      <summary>查看回读值</summary>
      <pre>{JSON.stringify(publication.readback, null, 2)}</pre>
    </details>}
    <p className="pp-publication-note">回读不一致或无法回读时，即使质量指标达标也不会被视为已接受。</p>
  </section>;
}

function comparisonReferences(comparisons: MetricComparison[]): string {
  const first = comparisons[0];
  if (!first) return "";
  return `${TARGET_SOURCE_LABELS[first.targetSource]} · ${THRESHOLD_SOURCE_LABELS[first.thresholdSource]}`;
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
      <div className={`pp-stage pp-stage-${snapshot.stage}`}><span>{snapshot.stageLabel}</span><small>只读 · {snapshot.dataSource === 'server' ? '服务端记录' : '随模拟状态更新'}</small></div>
      <p className="pp-disclaimer">{snapshot.dataSource === 'server'
        ? (snapshot.publication ? '参数来自服务端下发记录；回读校验决定该节点结果是否可接受。' : '参数来自服务端测量记录。')
        : '输入为演示配置，状态来自当前工作流；未连接真实设备遥测，未下发参数。'}</p>
      {snapshot.comparisons.length > 0 ? <section className="pp-compare-section" aria-label="理想值与实测值对比">
        <div className="pp-compare-title"><h3>理想值与实测值</h3><span>{comparisonReferences(snapshot.comparisons)}</span></div>
        <div className="pp-compare-list">{snapshot.comparisons.map(item => <ComparisonCard key={item.key} comparison={item} />)}</div>
        <p className="pp-compare-note">「理想目标」是设备设计期望值；「验收下限」是触发人工决策的门槛，二者不可混用。实测值只在节点完成后显示。</p>
      </section> : <p className="pp-compare-note">此节点没有可对比的质量指标：控制参数步骤或未映射的指标不显示理想值对比。</p>}
      <h3>输入配置</h3><Values values={snapshot.inputs} />
      <h3>当前执行</h3>
      <div className="pp-runtime" aria-live="polite" aria-atomic="true">
        <div><span>节点状态</span><strong>{snapshot.status}</strong></div><div><span>执行进度</span><strong>{runtime.progress.toFixed(1)}<small>%</small></strong></div>
        <div><span>执行次数 attempt</span><strong>{runtime.attempt}<small>次</small></strong></div><div><span>重试次数</span><strong>{runtime.retries}<small>次</small></strong></div>
        <div><span>本节点累计演示时间</span><strong>{runtime.nodeElapsedDemoSeconds}<small>s</small></strong></div><div><span>工作流累计演示时间</span><strong>{runtime.workflowElapsedDemoSeconds}<small>s</small></strong></div>
        <div><span>已生成演示采样</span><strong>{runtime.observedSamples}<small>/ {runtime.plannedSamples} 点</small></strong></div>
      </div>
      <div className="pp-progress" role="progressbar" aria-label={`${snapshot.node.label}执行进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={runtime.progress}><span style={{ width: `${runtime.progress}%` }} /></div>
      {snapshot.publication && <PublicationCard publication={snapshot.publication} verified={snapshot.publicationVerified} />}
      <h3>{snapshot.stage === 'preview' ? '结果示例预览' : snapshot.dataSource === 'server' ? '服务端参数与指标' : '模拟结果与指标'}</h3>
      {snapshot.results.length > 0 ? <Values values={snapshot.results} /> : snapshot.stage === 'preview' ? <><p className="pp-result-note">此节点尚未执行。以下数值仅展示结果格式，不是已采集结果。</p><Values values={snapshot.resultPreview} /></> : <p className="pp-result-note">{snapshot.stage === 'interrupted' ? '执行已终止，仅保留当前进度与采样数量，不显示最终结果。' : '模拟采样尚未完成，当前进度实时更新；最终参数将在节点完成后显示。'}</p>}
      <div className={`pp-assessment ${snapshot.acceptedLowMetric || snapshot.comparisons[0]?.status === 'failing' || snapshot.assessment.includes('低于') ? 'pp-warning' : ''}`}><span>{snapshot.threshold}</span><strong>{snapshot.assessment}</strong></div>
      <button type="button" className="pp-json-toggle" aria-expanded={rawOpen} aria-controls={`${id}-json`} onClick={() => setRawOpen(!rawOpen)}><Code2 size={14} /><span>{rawOpen ? '收起' : '展开'}原始参数 JSON</span><ChevronDown size={14} className={rawOpen ? 'pp-rotated' : ''} /></button>
      {rawOpen && <pre id={`${id}-json`} className="pp-json" tabIndex={0} aria-label="当前模拟参数JSON">{JSON.stringify(snapshot.raw, null, 2)}</pre>}
      <p className="pp-footer">演示秒数不等于真机耗时。拟合误差与 R² 均为预设展示值。</p>
    </div>}
  </section>;
}

export default ParameterPanel;
