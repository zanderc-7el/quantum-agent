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
export function preferredLiveAxis(axes: string[], procedure = "", adapterId?: string): string {
  if (adapterId === "arclight-private-f8-q11-v2" && procedure === "sq_cpmg" && axes.includes("exp.cir.main.wait_a.params")) return "exp.cir.main.wait_a.params";
  const patterns = procedure === "sq_rabi_amplitude" ? [/\.pi_amp$/] : procedure === "sq_rabi_frequency" ? [/\.drivFreq$/, /\.pi_len$/] : procedure === "sq_rabi_time" ? [/\.pi_len$/] : procedure === "sq_spectroscopy" ? [/\.drivFreq$/] : procedure.startsWith("ro_") ? [/\.probFreq$/, /\.probCarryScale$/] : [/wait[^.]*\.params$/];
  for (const pattern of patterns) { const axis = axes.find(key => pattern.test(key)); if (axis) return axis; }
  return axes[0] ?? POINT_INDEX_AXIS;
}
function stableValue(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableValue).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${JSON.stringify(key)}:${stableValue(item)}`).join(",")}}`;
  return JSON.stringify(value) ?? "null";
}
export interface LivePointGroup { key: string; jobName: string; capture: number; dimensions: [string, unknown][]; points: { x: number; point: LivePoint }[]; cpmgPiPulses?: number; }
export interface LiveGroupingContext { adapterId?: string; procedureId?: string; }
/** Audited CPMG zip: T/(2N), N-1 full T/N gaps, and one final T/(2N).
 * N is fixed per native job and bounded to 1..16 by this exact profile.
 * Keep unknown/gapped/malformed axes on the generic path; never rescale time.
 */
function cpmgWaitAxes(axes: Record<string, unknown>, context: LiveGroupingContext): string[] {
  if (context.adapterId !== "arclight-private-f8-q11-v2" || context.procedureId !== "sq_cpmg") return [];
  const keys = Object.keys(axes).filter(key => key.startsWith("exp.cir.main.wait_"));
  const count = keys.length - 1;
  if (count < 1 || count > 16) return [];
  const expected = ["exp.cir.main.wait_a.params", ...Array.from({ length: count }, (_, index) => `exp.cir.main.wait_${index}.params`)];
  if (keys.some(key => !expected.includes(key))) return [];
  const half = numericAxisValue(axes[expected[0]]);
  if (half === undefined || half < 0) return [];
  for (let index = 0; index < count; index++) {
    const value = numericAxisValue(axes[expected[index + 1]]), target = index === count - 1 ? half : 2 * half;
    if (value === undefined || Math.abs(value - target) > Math.max(1e-15, Math.abs(target) * 1e-9)) return [];
  }
  return expected;
}
/** Only these frozen ArcLight methods declare the named axes as one zipped sweep.
 * Other adapters, methods and extra dimensions retain the generic grouping.
 * This changes presentation only: every point, raw axis and acquisition index survives.
 */
export function linkedLiveAxes(axis: string, context: LiveGroupingContext = {}, axes?: Record<string, unknown>): string[] {
  if (context.adapterId !== "arclight-private-f8-q11-v2") return [];
  if (context.procedureId === "sq_cpmg") {
    const linked = axes ? cpmgWaitAxes(axes, context) : [];
    return linked.includes(axis) ? linked.filter(key => key !== axis) : [];
  }
  const linked = context.procedureId === "sq_ramsey"
    ? ["exp.cir.main.wait.params", "exp.cir.main.phase.params"]
    : context.procedureId === "sq_echo"
      ? ["exp.cir.main.wait_a.params", "exp.cir.main.wait_b.params", "exp.cir.main.phase.params"]
      : [];
  return linked.includes(axis) ? linked.filter(key => key !== axis) : [];
}
export function groupLivePoints(points: LivePoint[], axis: string, context: LiveGroupingContext = {}): LivePointGroup[] {
  const groups = new Map<string, LivePointGroup>();
  for (const point of points) {
    const linked = new Set(linkedLiveAxes(axis, context, point.axes));
    const cpmgPiPulses = context.procedureId === "sq_cpmg" && linked.size ? linked.size : undefined;
    const x = axis === POINT_INDEX_AXIS ? point.pointIndex : numericAxisValue(point.axes[axis]);
    if (x === undefined) continue;
    const dimensions = Object.entries(point.axes).filter(([key]) => key !== axis && !linked.has(key)).sort(([a], [b]) => a.localeCompare(b));
    const key = stableValue([point.jobId, point.capture, dimensions, ...(cpmgPiPulses === undefined ? [] : [cpmgPiPulses])]);
    let group = groups.get(key);
    if (!group) { group = { key, jobName: point.jobName, capture: point.capture, dimensions, points: [], ...(cpmgPiPulses === undefined ? {} : { cpmgPiPulses }) }; groups.set(key, group); }
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
