import { useEffect, useState } from "react";

const BASE = ""; // Same-origin Docker proxy; no separate host port needed.
type Point = { frequencyHz: number; magnitudeDb?: number; response?: number };
type Analysis = { quality: string; candidateHz: number | null; reason: string | null; note?: string };
type Decision = { action: string; reason: string; publishAllowed?: boolean };
type Run = { runId: string; attemptId: string; recipeId: string; sourceReadoutRevision: number | null; sourceReadoutHash: string | null; status: string; expectedPoints: number; followupDepth: number; parentRunId: string | null; points: Point[]; analysis: Analysis | null; decision: Decision | null; dataSource: string; eventSeq: number; artifactSha256: string | null; artifactVerified: boolean | null };
type Event = { eventSeq: number; kind: string; payload: { status?: string; points?: Point[]; sha256?: string; [key: string]: unknown } };
type Summary = { runId: string; planId: string; attemptId: string; dataSource: string };
type Plan = { planId: string; input: { recipeId: string; parameters: { points: number } }; upstream: { revision: number; artifactSha256: string } | null; followupDepth: number; parentRunId: string | null };
type Candidate = { candidateId: string; sourceRunId: string; status: string; valueHz: number; baselineRevision: number; publishedRevision?: number | null };
type ParameterState = { revisions: { revision: number; valueHz: number; source: string }[]; candidates: Record<string, Candidate> };
type AgentPreview = { accepted: boolean; reason?: string; recipeId?: string; nodeCount?: number; target?: string; firstBatch?: string[]; executable?: boolean; modelUsed?: boolean };
type DeviceAdapter = { adapterId: string; connected: boolean; hardwareConnected: boolean; fault: string; capabilities: string[] };

async function request<T>(path: string, method = "GET", body?: unknown, idempotencyKey?: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: method === "POST" ? { "content-type": "application/json", "idempotency-key": idempotencyKey ?? crypto.randomUUID() } : undefined,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data?.error?.message ?? `HTTP ${response.status}`);
  return data as T;
}

function applyEvent(previous: Run, event: Event): Run {
  if (event.eventSeq <= previous.eventSeq) return previous;
  if (event.eventSeq !== previous.eventSeq + 1) throw new Error("事件序号出现缺口，请重新加载运行");
  return {
    ...previous,
    eventSeq: event.eventSeq,
    status: event.kind === "run.status" && typeof event.payload.status === "string" ? event.payload.status : previous.status,
    points: event.kind === "measurement.chunk" && Array.isArray(event.payload.points) ? [...previous.points, ...event.payload.points] : previous.points,
    analysis: event.kind === "analysis.completed" ? event.payload as unknown as Analysis : previous.analysis,
    decision: event.kind === "skill.decision" ? event.payload as unknown as Decision : previous.decision,
    artifactSha256: event.kind === "measurement.finalized" && typeof event.payload.sha256 === "string" ? event.payload.sha256 : previous.artifactSha256,
  };
}

export default function BackendSimulatorWorkbench() {
  const [runId, setRunId] = useState(() => {
    try { return sessionStorage.getItem("qcal-simulator-run-id") ?? ""; }
    catch { return ""; }
  });
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [parameters, setParameters] = useState<ParameterState | null>(null);
  const [goal, setGoal] = useState("对 Q12 和 Q13 做 CZ 校准");
  const [agentPreview, setAgentPreview] = useState<AgentPreview | null>(null);
  const [deviceAdapter, setDeviceAdapter] = useState<DeviceAdapter | null>(null);
  useEffect(() => { void request<DeviceAdapter>("/api/v1/device-adapter").then(setDeviceAdapter).catch(() => {}); }, []);
  async function refreshParameters() {
    setParameters(await request<ParameterState>("/api/v1/parameters/readout-frequency"));
  }
  useEffect(() => {
    if (!runId) return;
    let active = true;
    let loading = false;
    let parameterLoaded = false;
    let cursor = 0;
    let currentRun: Run | null = null;
    const refresh = async () => {
      if (loading) return;
      loading = true;
      try {
        if (!currentRun) {
          const summary = await request<Summary>(`/api/v1/runs/${encodeURIComponent(runId)}/summary`);
          const plan = await request<Plan>(`/api/v1/plans/${encodeURIComponent(summary.planId)}`);
          currentRun = { runId, attemptId: summary.attemptId, recipeId: plan.input.recipeId,
            sourceReadoutRevision: plan.upstream?.revision ?? null, sourceReadoutHash: plan.upstream?.artifactSha256 ?? null,
            status: "queued", expectedPoints: plan.input.parameters.points,
            followupDepth: plan.followupDepth ?? 0, parentRunId: plan.parentRunId ?? null, points: [], analysis: null, decision: null,
            dataSource: summary.dataSource, eventSeq: 0, artifactSha256: null, artifactVerified: null };
          cursor = 0;
        }
        const result = await request<{ events: Event[] }>(`/api/v1/runs/${encodeURIComponent(runId)}/events?after=${cursor}`);
        for (const event of result.events) currentRun = applyEvent(currentRun, event);
        cursor = currentRun.eventSeq;
        if (currentRun.artifactSha256 && currentRun.artifactVerified === null) {
          const measured = await request<{ points: Point[]; artifact: { sha256: string } | null }>(`/api/v1/attempts/${encodeURIComponent(currentRun.attemptId)}/measurements`);
          const bytes = new TextEncoder().encode(JSON.stringify(measured.points));
          const digest = await crypto.subtle.digest("SHA-256", bytes);
          const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
          currentRun = { ...currentRun, artifactVerified: hash === currentRun.artifactSha256 &&
            hash === measured.artifact?.sha256 && measured.points.length === currentRun.points.length };
        }
        if (active) setRun(currentRun);
        if (active && !parameterLoaded && ["completed", "failed", "stopped"].includes(currentRun.status)) {
          await refreshParameters(); parameterLoaded = true;
        }
      } catch (cause) { if (active) setError(cause instanceof Error ? cause.message : "读取运行失败"); }
      finally { loading = false; }
    };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 500);
    return () => { active = false; window.clearInterval(timer); };
  }, [runId]);
  function selectRun(id: string) {
    setRunId(id);
    try { sessionStorage.setItem("qcal-simulator-run-id", id); } catch { /* Keep the active run in memory. */ }
  }
  async function start(spanHz: number, points: number) {
    setBusy(true); setError(""); setRun(null);
    try {
      const profile = await request<{ id: string; centerHz: number }>("/api/v1/device-profile");
      const plan = await request<{ planId: string }>("/api/v1/plans", "POST", {
        blueprintId: "single", blueprintVersion: 1, targetQubitId: "Q12", deviceId: profile.id, recipeId: "readout_resonator_scan", recipeVersion: 1,
        motivation: spanHz < 2_000_000 ? "模拟边界极小值并验证 Skill 复扫决策" : "验证 Q12 读出谐振频率并生成模拟候选值",
        parameters: { centerHz: profile.centerHz, spanHz, points },
      });
      const started = await request<{ runId: string }>(`/api/v1/plans/${plan.planId}/runs`, "POST");
      selectRun(started.runId);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "运行失败；请先启动本地实验服务"); }
    finally { setBusy(false); }
  }
  async function stop() {
    if (!runId) return;
    try {
      const result = await request<{ stopConfirmed: boolean }>(`/api/v1/runs/${encodeURIComponent(runId)}/stop`, "POST");
      if (!result.stopConfirmed) setError("模拟设备尚未确认停止；资源保持占用，请重试停止确认。");
      else setError("");
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : "停止失败"); }
  }
  async function startSpectrum() {
    const sourceRevision = currentRevision?.revision;
    if (!sourceRevision) return;
    setBusy(true); setError("");
    try {
      const plan = await request<Plan>("/api/v1/plans", "POST", {
        blueprintId: "single", blueprintVersion: 1, targetQubitId: "Q12", deviceId: "sim-readout-v1", recipeId: "f01_spectroscopy", recipeVersion: 1,
        motivation: `使用读出参数版本 ${sourceRevision} 探索 Q12 的 F01 频谱`, sourceReadoutRevision: sourceRevision,
        parameters: { centerHz: 5_100_000_000, spanHz: 100_000_000, points: 101 },
      });
      const started = await request<{ runId: string }>(`/api/v1/plans/${encodeURIComponent(plan.planId)}/runs`, "POST");
      selectRun(started.runId);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "频谱计划创建失败"); }
    finally { setBusy(false); }
  }
  async function followup() {
    if (!runId) return;
    setBusy(true); setError("");
    try {
      const plan = await request<Plan>(`/api/v1/runs/${encodeURIComponent(runId)}/follow-up-plan`, "POST", undefined, `followup-${runId}`);
      const started = await request<{ runId: string }>(`/api/v1/plans/${encodeURIComponent(plan.planId)}/runs`, "POST", undefined, `start-${plan.planId}`);
      selectRun(started.runId);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "复扫计划创建失败"); }
    finally { setBusy(false); }
  }
  async function parameterAction(action: "propose" | "approve" | "publish" | "rollback") {
    if (!runId || !parameters) return;
    const candidate = Object.values(parameters.candidates).find(item => item.sourceRunId === runId);
    const revision = parameters.revisions.at(-1)?.revision ?? 1;
    setBusy(true); setError("");
    try {
      if (action === "propose") await request(`/api/v1/runs/${encodeURIComponent(runId)}/parameter-candidate`, "POST", undefined, `candidate-${runId}`);
      if (action === "approve" && candidate) await request(`/api/v1/parameter-candidates/${encodeURIComponent(candidate.candidateId)}/approve`, "POST", { actor: "demo-reviewer" }, `approve-${candidate.candidateId}`);
      if (action === "publish" && candidate) await request(`/api/v1/parameter-candidates/${encodeURIComponent(candidate.candidateId)}/publish`, "POST", { expectedRevision: revision }, `publish-${candidate.candidateId}`);
      if (action === "rollback") await request("/api/v1/parameters/readout-frequency/rollback", "POST", { targetRevision: 1, expectedRevision: revision, actor: "demo-reviewer" }, `rollback-${revision}`);
      await refreshParameters();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "模拟参数操作失败"); }
    finally { setBusy(false); }
  }
  const currentCandidate = parameters && Object.values(parameters.candidates).find(item => item.sourceRunId === runId);
  const currentRevision = parameters?.revisions.at(-1);
  async function previewGoal() {
    try { setAgentPreview(await request<AgentPreview>("/api/v1/agent/plan-preview", "POST", { goal, blueprintId: "single", blueprintVersion: 1 })); setError(""); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "计划预览失败"); }
  }
  return <section className="api-card backend-simulator">
    <div className="api-section-head"><div><strong>服务端实验切片 · 确定性模拟</strong></div><small>独立于离线接口验证台</small></div>
    <p className="api-note">通过同源代理连接 Docker 模拟后端，演练 Q12 读出 S21 扫频、模拟参数发布和依赖该版本的 F01 频谱。服务不连接 32 所接口或真机；S21 使用离散最小值、F01 使用离散峰值示例，均非真实物理拟合。</p>
    {deviceAdapter && <p className="api-note">设备适配器：{deviceAdapter.adapterId} · {deviceAdapter.connected ? "模拟控制器已连接" : "未连接"} · 真机连接：{deviceAdapter.hardwareConnected ? "是" : "否"} · 故障场景：{deviceAdapter.fault}</p>}
    <div className="backend-actions"><button className="primary" disabled={busy || Boolean(run && ["queued", "running", "stopping"].includes(run.status))} onClick={() => { void start(40_000_000, 41); }}>{busy ? "正在创建" : "运行正常扫描"}</button>
      <button disabled={busy || Boolean(run && ["queued", "running", "stopping"].includes(run.status))} onClick={() => { void start(1_000_000, 11); }}>模拟边界异常</button>
      {run && ["queued", "running", "stopping"].includes(run.status) && <button onClick={() => { void stop(); }}>{run.status === "stopping" ? "重试停止确认" : "停止并确认"}</button>}</div>
    {run?.decision?.action === "retry_wider_scan" && <div className="backend-actions"><button disabled={busy} onClick={() => { void followup(); }}>按 Skill 建议扩大扫描并复测</button></div>}
    {error && <div className="api-error" role="alert">{error}</div>}
    {run && <div className="backend-run">
      <div><span>运行 ID</span><code>{run.runId}</code></div><div><span>实验</span><strong>{run.recipeId}</strong></div><div><span>状态</span><strong>{run.status}</strong></div>
      <div><span>采样点</span><strong>{run.points.length} / {run.expectedPoints}</strong></div><div><span>事件序号</span><strong>{run.eventSeq}</strong></div>
      <div><span>复测层级</span><strong>{run.followupDepth}</strong></div><div><span>来源运行</span><code>{run.parentRunId ?? "首次扫描"}</code></div>
      <div><span>数据来源</span><strong>{run.dataSource}</strong></div>
      {run.sourceReadoutRevision !== null && <div><span>上游读出版本</span><strong>{run.sourceReadoutRevision}</strong></div>}
      {run.sourceReadoutHash && <div><span>上游产物 SHA-256</span><code title={run.sourceReadoutHash}>{run.sourceReadoutHash.slice(0, 16)}…</code></div>}
      <div><span>候选频率</span><strong>{run.analysis?.candidateHz ? `${(run.analysis.candidateHz / 1e9).toFixed(6)} GHz` : "—"}</strong></div>
      <div><span>产物校验</span><strong>{run.artifactVerified === null ? "等待归档" : run.artifactVerified ? "SHA-256 一致" : "校验失败"}</strong></div>
      <div><span>Skill 决策</span><strong>{run.decision?.action ?? "—"}</strong></div>
      {run.analysis && <p>分析：{run.analysis.quality} · {run.analysis.note ?? run.analysis.reason}</p>}
      {run.recipeId === "f01_spectroscopy" && <p>F01 频率仅是模拟候选，尚未进行 0→1 跃迁验证或参数发布。</p>}
    </div>}
    {run && parameters && <div className="backend-parameter">
      <h3>模拟参数版本</h3>
      <p>当前版本 {currentRevision?.revision} · {currentRevision ? `${(currentRevision.valueHz / 1e9).toFixed(6)} GHz` : "—"}。以下审批人与设备回读均为本机模拟，不具备真实权限或真机写回。</p>
      {currentCandidate && <p>本次候选 {currentCandidate.candidateId} · {currentCandidate.status} · {(currentCandidate.valueHz / 1e9).toFixed(6)} GHz</p>}
      <div className="backend-actions">
        {run.recipeId === "readout_resonator_scan" && run.decision?.action === "accept_candidate" && !currentCandidate && <button disabled={busy} onClick={() => { void parameterAction("propose"); }}>生成读出候选</button>}
        {currentCandidate?.status === "proposed" && <button disabled={busy} onClick={() => { void parameterAction("approve"); }}>模拟审批</button>}
        {currentCandidate?.status === "approved" && <button disabled={busy} onClick={() => { void parameterAction("publish"); }}>发布到模拟参数库</button>}
        {currentRevision && currentRevision.revision > 1 && <button disabled={busy} onClick={() => { void parameterAction("rollback"); }}>回滚到初始版本</button>}
        {run.recipeId === "readout_resonator_scan" && currentCandidate?.status === "published" && currentCandidate.publishedRevision === currentRevision?.revision && <button disabled={busy} onClick={() => { void startSpectrum(); }}>用已发布读出参数运行 F01 频谱</button>}
      </div>
    </div>}
    <div className="backend-parameter">
      <h3>校准目标预览 · 规则规划器</h3>
      <p>根据目标匹配已定义的模拟 DAG。当前没有模型服务，也不会调用设备。</p>
      <div className="backend-actions"><input aria-label="校准目标" value={goal} onChange={event => setGoal(event.target.value)} />
        <button onClick={() => { void previewGoal(); }}>预览计划</button></div>
      {agentPreview && <p>{agentPreview.accepted
        ? `目标 ${agentPreview.target} · ${agentPreview.recipeId} · ${agentPreview.nodeCount} 个规划节点 · 首批 ${agentPreview.firstBatch?.join("、")} · 不可执行`
        : `无法生成计划：${agentPreview.reason}`}</p>}
    </div>
  </section>;
}
