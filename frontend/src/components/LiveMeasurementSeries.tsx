import { formatAxisTick } from "../lib/axisTicks";
import { useEffect, useMemo, useState } from "react";
import { Activity, ChevronLeft, ChevronRight } from "lucide-react";
import type { ExperimentNode } from "../types";
import type { RemoteGateway } from "../lib/remoteGateway";
import { emptyLiveSeries, groupLivePoints, liveAxisLabel, mergeLiveSeries, numericAxes, POINT_INDEX_AXIS, preferredLiveAxis } from "../lib/liveSeries";
import type { LivePoint, LivePointGroup, LiveSeriesBuffer, LiveSeriesPage } from "../lib/liveSeries";
import "./live-measurement.css";

const COLORS = ["#47c9c0", "#ffb35c", "#b79cff", "#70b9ff", "#ed88b8", "#bddb70", "#f07b78", "#97b8c9"];
const PAGE_SIZE = COLORS.length;
const format = (value: number) => Math.abs(value) >= 1e7 || (value !== 0 && Math.abs(value) < .0001) ? value.toExponential(3) : Number(value.toPrecision(7)).toString();
type Measure = "meanMagnitude" | "meanI" | "meanQ";
const measures: Record<Measure, string> = { meanMagnitude: "|平均 I + iQ|", meanI: "平均 I", meanQ: "平均 Q" };
function dimensionText(dimensions: LivePointGroup["dimensions"]) {
  return dimensions.map(([key, value]) => `${key.split(".").slice(-2).join(".")}=${typeof value === "number" ? format(value) : JSON.stringify(value)}`).join(" · ");
}
function LiveScatter({ groups, axis, measure, archived = false }: { groups: LivePointGroup[]; axis: string; measure: Measure; archived?: boolean }) {
  const presentation = liveAxisLabel(axis);
  const values = groups.flatMap(group => group.points.map(({ x, point }) => ({ x: x / presentation.scale, y: point[measure] })));
  if (!values.length) return null;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const value of values) { minX = Math.min(minX, value.x); maxX = Math.max(maxX, value.x); minY = Math.min(minY, value.y); maxY = Math.max(maxY, value.y); }
  if (maxX === minX) { const pad = Math.max(Math.abs(minX) * 1e-6, 1e-6); minX -= pad; maxX += pad; }
  if (maxY === minY) { const pad = Math.max(Math.abs(minY) * .05, 1e-8); minY -= pad; maxY += pad; }
  const sx = (x: number) => 82 + (x / presentation.scale - minX) / (maxX - minX) * 680;
  const sy = (y: number) => 282 - (y - minY) / (maxY - minY) * 232;
  const tooltip = (point: LivePoint) => `采集轮次 ${point.capture + 1} · 原始点索引 ${point.pointIndex}\n${JSON.stringify(point.axes)}\nI=${point.meanI}; Q=${point.meanQ}\n文件：${point.pointFile}\nSHA256：${point.pointSha256}`;
  return <><svg className="rp-live-scatter" viewBox="0 0 800 330" role="img" aria-label={`${archived ? "已归档" : "实时"}原始采集散点，${values.length} 点，横轴${presentation.label}，${measures[measure]}，质量结论见正式结果`}>
    <text x="82" y="24">{measures[measure]} (原始 IQ)</text>
    <line x1="82" x2="82" y1="50" y2="282" /><line x1="82" x2="762" y1="282" y2="282" />
    {[0, .5, 1].map(fraction => <g key={fraction}><line className="grid" x1="82" x2="762" y1={282 - fraction * 232} y2={282 - fraction * 232} /><text x="74" y={286 - fraction * 232} textAnchor="end">{formatAxisTick(minY + fraction * (maxY - minY), maxY - minY)}</text><text x={82 + fraction * 680} y="303" textAnchor="middle">{formatAxisTick(minX + fraction * (maxX - minX), maxX - minX)}</text></g>)}
    {groups.map((group, index) => <g key={group.key} fill={COLORS[index]}>{group.points.map(({ x, point }) => <circle key={`${point.capture}-${point.pointIndex}`} cx={sx(x)} cy={sy(point[measure])} r="2.4" opacity=".85"><title>{tooltip(point)}</title></circle>)}</g>)}
    <text x="422" y="325" textAnchor="middle">{presentation.label}</text>
  </svg><div className="rp-live-legend">{groups.map((group, index) => <span key={group.key} title={`${group.jobName} · ${dimensionText(group.dimensions)}`}><i style={{ background: COLORS[index] }} /><span>{group.jobName} · 采集轮次 {group.capture + 1}{group.dimensions.length ? ` · ${dimensionText(group.dimensions)}` : ""} · {group.points.length} 点</span></span>)}</div></>;
}

export default function LiveMeasurementSeries({ gateway, workflowId, node, archived = false }: { gateway: RemoteGateway; workflowId: string; node: ExperimentNode; archived?: boolean }) {
  const [buffer, setBuffer] = useState<LiveSeriesBuffer>(emptyLiveSeries);
  const [page, setPage] = useState<LiveSeriesPage>();
  const [error, setError] = useState("");
  const [selectedAxis, setSelectedAxis] = useState("");
  const [measure, setMeasure] = useState<Measure>("meanMagnitude");
  const [groupPage, setGroupPage] = useState(0);
  useEffect(() => {
    let active = true, current = emptyLiveSeries();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | undefined;
    setBuffer(current); setPage(undefined); setError(""); setSelectedAxis(""); setGroupPage(0);
    const poll = async () => {
      request = new AbortController();
      let timedOut = false;
      const timeout = setTimeout(() => { timedOut = true; request?.abort(); }, 10000);
      try {
        const response = await gateway.liveSeries(workflowId, node.id, current.cursor, current.operationId, request.signal);
        if (!active) return;
        const changed = response.operationId !== current.operationId;
        if (changed) { current = emptyLiveSeries(); setBuffer(current); setPage(undefined); setSelectedAxis(""); setGroupPage(0); }
        current = mergeLiveSeries(current, response);
        setBuffer(current); setPage(response); setError("");
      } catch (cause) {
        if (active && (timedOut || !request.signal.aborted)) setError(timedOut ? "实时采集摘要请求超时" : cause instanceof Error ? cause.message : "实时采集摘要暂时不可用");
      } finally {
        clearTimeout(timeout);
        if (active) timer = setTimeout(() => void poll(), 2500);
      }
    };
    void poll();
    return () => { active = false; if (timer) clearTimeout(timer); request?.abort(); };
  }, [gateway, workflowId, node.id]);
  const axes = useMemo(() => numericAxes(buffer.points), [buffer.points]);
  const axis = selectedAxis && (selectedAxis === POINT_INDEX_AXIS || axes.includes(selectedAxis)) ? selectedAxis : preferredLiveAxis(axes, node.procedureId);
  const groups = useMemo(() => groupLivePoints(buffer.points, axis), [buffer.points, axis]);
  const lastPage = Math.max(0, Math.ceil(groups.length / PAGE_SIZE) - 1), visiblePage = Math.min(groupPage, lastPage);
  const visibleGroups = groups.slice(visiblePage * PAGE_SIZE, (visiblePage + 1) * PAGE_SIZE);
  const terminal = archived || page?.terminal;
  return <section className="rp-live-measurement" aria-label={archived ? "已归档采集曲线" : "实时采集曲线"}>
    <header><strong><Activity size={15} />{terminal ? "原始采集点 · 结论见正式结果" : "实时采集 · 尚未验收"}</strong><span>{buffer.points.length} 个已落盘点{page?.availablePoints !== undefined ? ` / 当前可读 ${page.availablePoints}` : ""}</span></header>
    <p className="rp-live-note">逐点显示实际采集的 I/Q 摘要；不同采集轮次及扫描条件分别分组。这里保留原始散点，不作拟合或平滑。</p>
    {error && <p className="rp-live-error" role="status">{error}。保留已收到的采集点，正在按游标重连。</p>}
    {!buffer.points.length ? <p className="rp-live-empty">{terminal ? "本次执行暂无可显示的已落盘采集点。" : "等待首个已落盘采集点…"}</p> : <>
      <div className="rp-live-options"><label>横轴<select aria-label="实时曲线横轴" value={axis} onChange={event => { setSelectedAxis(event.target.value); setGroupPage(0); }}>{axes.map(key => <option value={key} key={key}>{liveAxisLabel(key).label}</option>)}<option value={POINT_INDEX_AXIS}>采集点序号</option></select></label><label>测量值<select aria-label="实时曲线测量值" value={measure} onChange={event => setMeasure(event.target.value as Measure)}>{Object.entries(measures).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
      <LiveScatter groups={visibleGroups} axis={axis} measure={measure} archived={archived} />
      {groups.length > PAGE_SIZE && <div className="rp-live-pagination"><span>显示分组 {visiblePage * PAGE_SIZE + 1}–{Math.min((visiblePage + 1) * PAGE_SIZE, groups.length)} / {groups.length}</span><button disabled={visiblePage === 0} onClick={() => setGroupPage(visiblePage - 1)} aria-label="上一页采集分组"><ChevronLeft size={14} /></button><button disabled={visiblePage === lastPage} onClick={() => setGroupPage(visiblePage + 1)} aria-label="下一页采集分组"><ChevronRight size={14} /></button></div>}
    </>}
    <footer><span>{page?.hasMore ? "正在补齐已落盘点…" : page?.complete ? "本次执行采集点已同步；质量结论见正式结果" : terminal ? "本次执行已结束，正在同步剩余采集点" : "每 2.5 秒同步已落盘采集点"}</span>{buffer.operationId && <details><summary>本次执行与数据游标</summary><code>{buffer.operationId} · cursor {buffer.cursor}</code></details>}</footer>
  </section>;
}
