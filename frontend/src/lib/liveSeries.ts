/** Incremental, unverified summaries of immutable acquired points. Never calibration state. */
export interface LivePoint {
  operationId: string; jobId: string; jobName: string; pointIndex: number;
  capture: number; baseIndex: number; axes: Record<string, unknown>;
  meanI: number; meanQ: number; meanAbs: number; meanMagnitude: number; shots: number;
  pointSha256: string; pointFile: string; cursor: number;
}
export interface LiveSeriesPage {
  operationId: string | null; cursor: number; points: LivePoint[]; hasMore: boolean;
  cachedPoints?: number; availablePoints?: number; complete: boolean; terminal: boolean;
  state: string; pending?: unknown; source: "live_acquired_points"; quality: "unverified";
}
export interface LiveSeriesBuffer { operationId: string | null; cursor: number; points: LivePoint[]; }
export const emptyLiveSeries = (): LiveSeriesBuffer => ({ operationId: null, cursor: 0, points: [] });
const pointKey = (point: LivePoint) => JSON.stringify([point.jobId, point.capture, point.pointIndex]);
export function mergeLiveSeries(current: LiveSeriesBuffer, page: LiveSeriesPage): LiveSeriesBuffer {
  if (page.source !== "live_acquired_points" || page.quality !== "unverified") throw new Error("实时曲线来源标识不匹配");
  if (!Number.isSafeInteger(page.cursor) || page.cursor < 0) throw new Error("实时曲线游标无效");
  const reset = page.operationId !== current.operationId;
  if (!reset && page.cursor < current.cursor) return current;
  const points = new Map((reset ? [] : current.points).map(point => [pointKey(point), point]));
  for (const point of page.points) {
    if (!page.operationId || point.operationId !== page.operationId) throw new Error("实时曲线包含其他执行轮次的数据");
    if (![point.meanI, point.meanQ, point.meanMagnitude].every(Number.isFinite)) throw new Error("实时采集点包含无效 I/Q 值");
    const key = pointKey(point), previous = points.get(key);
    if (previous && previous.pointSha256 !== point.pointSha256) throw new Error("已落盘采集点内容发生变化，请核查数据来源");
    points.set(key, point);
  }
  return { operationId: page.operationId, cursor: page.cursor, points: [...points.values()] };
}
export function numericAxisValue(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value) && value.length === 1 && typeof value[0] === "number" && Number.isFinite(value[0])) return value[0];
  return undefined;
}
export const POINT_INDEX_AXIS = "$pointIndex";
export function numericAxes(points: LivePoint[]): string[] {
  const keys = new Set<string>();
  for (const point of points) for (const [key, value] of Object.entries(point.axes)) if (numericAxisValue(value) !== undefined) keys.add(key);
  return [...keys].sort();
}
export function preferredLiveAxis(axes: string[], procedure = ""): string {
  const patterns = procedure === "sq_rabi_amplitude" ? [/\.pi_amp$/] : procedure === "sq_rabi_frequency" ? [/\.drivFreq$/, /\.pi_len$/] : procedure === "sq_rabi_time" ? [/\.pi_len$/] : procedure === "sq_spectroscopy" ? [/\.drivFreq$/] : procedure.startsWith("ro_") ? [/\.probFreq$/, /\.probCarryScale$/] : [/wait[^.]*\.params$/];
  for (const pattern of patterns) { const axis = axes.find(key => pattern.test(key)); if (axis) return axis; }
  return axes[0] ?? POINT_INDEX_AXIS;
}
function stableValue(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableValue).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${JSON.stringify(key)}:${stableValue(item)}`).join(",")}}`;
  return JSON.stringify(value) ?? "null";
}
export interface LivePointGroup { key: string; jobName: string; capture: number; dimensions: [string, unknown][]; points: { x: number; point: LivePoint }[]; }
export function groupLivePoints(points: LivePoint[], axis: string): LivePointGroup[] {
  const groups = new Map<string, LivePointGroup>();
  for (const point of points) {
    const x = axis === POINT_INDEX_AXIS ? point.pointIndex : numericAxisValue(point.axes[axis]);
    if (x === undefined) continue;
    const dimensions = Object.entries(point.axes).filter(([key]) => key !== axis).sort(([a], [b]) => a.localeCompare(b));
    const key = stableValue([point.jobId, point.capture, dimensions]);
    let group = groups.get(key);
    if (!group) { group = { key, jobName: point.jobName, capture: point.capture, dimensions, points: [] }; groups.set(key, group); }
    group.points.push({ x, point });
  }
  return [...groups.values()].sort((a, b) => a.key.localeCompare(b.key)).map(group => ({ ...group, points: group.points.sort((a, b) => a.x - b.x || a.point.pointIndex - b.point.pointIndex) }));
}
export function liveAxisLabel(axis: string): { label: string; scale: number } {
  if (axis === POINT_INDEX_AXIS) return { label: "采集点序号", scale: 1 };
  if (/\.prob(?:Carry)?Freq$/.test(axis)) return { label: "读出频率 (GHz)", scale: 1e9 };
  if (/\.driv(?:Carry)?Freq$/.test(axis)) return { label: "驱动频率 (GHz)", scale: 1e9 };
  if (/\.pi_amp$/.test(axis)) return { label: "驱动幅度 (a.u.)", scale: 1 };
  if (/CarryScale$/.test(axis)) return { label: "读出幅度 (a.u.)", scale: 1 };
  if (/\.pi_len$/.test(axis)) return { label: "脉冲时长 (μs)", scale: 1e-6 };
  if (/wait[^.]*\.params$/.test(axis)) return { label: "原生等待时长 (μs)", scale: 1e-6 };
  return { label: axis, scale: 1 };
}
