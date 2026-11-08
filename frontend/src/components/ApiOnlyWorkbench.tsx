import { useState } from "react";
import { Activity, ArrowRight, Check, FlaskConical, RotateCcw, ShieldAlert, WifiOff } from "lucide-react";
import { runValidation, type ApiOnlyReport } from "../lib/apiOnly/validation-core.mjs";
import BackendSimulatorWorkbench from "./BackendSimulatorWorkbench";
import { remoteEnabled } from "../lib/apiClient";
import "./api-only-workbench.css";

const callLabels: Record<string, string> = {
  "/calibration/insert": "创建校准记录",
  "/task/insert": "提交独立实验任务",
  "/task/findByCondition": "查询任务",
  "/experimentalData/findByCondition": "查询实验数据",
  "/calibration/cancelCalibration": "请求取消校准",
};

function Curve({ report }: { report: ApiOnlyReport }) {
  const { frequenciesHz, amplitudes } = report.sampleCurve;
  const width = 680;
  const height = 210;
  const left = 28;
  const right = 20;
  const top = 18;
  const bottom = 30;
  const min = Math.min(...amplitudes);
  const max = Math.max(...amplitudes);
  const span = max - min || 1;
  const points = amplitudes.map((value, index) => {
    const x = left + (index * (width - left - right)) / (amplitudes.length - 1);
    const y = top + ((max - value) * (height - top - bottom)) / span;
    return [x, y] as const;
  });
  const selected = frequenciesHz.indexOf(report.generatedProposal.proposedCenterHz);
  return (
    <div className="api-curve">
      <div className="api-section-head"><div><Activity size={18} /><strong>合成 S21 响应</strong></div><small>数据仅由本地模拟器生成</small></div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="模拟 S21 幅度随频率变化曲线">
        {[0, 1, 2, 3].map((line) => <line key={line} x1={left} x2={width - right} y1={top + line * 54} y2={top + line * 54} className="api-grid" />)}
        <polyline points={points.map(([x, y]) => `${x},${y}`).join(" ")} className="api-line" />
        {points.map(([x, y], index) => <circle key={index} cx={x} cy={y} r={index === selected ? 6 : 3.5} className={index === selected ? "api-point selected" : "api-point"} />)}
        <text x={left} y={height - 5}>{(frequenciesHz[0] / 1e9).toFixed(2)} GHz</text>
        <text x={width - right} y={height - 5} textAnchor="end">{(frequenciesHz.at(-1)! / 1e9).toFixed(2)} GHz</text>
      </svg>
      <div className="api-proposal"><span>建议中心频率</span><strong>{(report.generatedProposal.proposedCenterHz / 1e9).toFixed(3)} GHz</strong><small>基于最小幅度点；未下发设备</small></div>
    </div>
  );
}

export default function ApiOnlyWorkbench() {
  const [report, setReport] = useState<ApiOnlyReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      setReport(await runValidation());
    } catch (reason) {
      setReport(null);
      setError(reason instanceof Error ? reason.message : "模拟验证失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="api-workbench page-enter">
      <div className="api-hero">
        <div><div className="api-kicker"><FlaskConical size={15} /> API-ONLY / OFFLINE MOCK</div>
          <h1>接口验证台</h1>
          <p>沿用当前工作台的操作方式，先验证文档中可描述的调用链和数据处理。真机及 32 所服务未连接。</p>
        </div>
        <button className="primary" onClick={run} disabled={busy}>{busy ? <RotateCcw size={16} className="api-spin" /> : <ArrowRight size={16} />}{busy ? "正在验证" : report ? "重新运行验证" : "运行模拟验证"}</button>
      </div>
      <div className="api-overview">
        <div><small>运行环境</small><strong><WifiOff size={18} /> 离线进程内模拟</strong><span>无 SDK · 无网络 · 无真机</span></div>
        <div><small>覆盖路径</small><strong>{report ? report.mockCallsPassed.length : "—"} <em>/ 6</em></strong><span>按交付文档的请求路径演练</span></div>
        <div><small>自动校准闭环</small><strong className="api-pending">尚未验证</strong><span>参数发布、回读与硬件确认待定</span></div>
      </div>
      {error && <div className="api-error" role="alert"><ShieldAlert size={18} />{error}</div>}
      <div className="api-grid-layout">
        <section className="api-card">
          <div className="api-section-head"><div><Check size={18} /><strong>接口调用链</strong></div><small>{report ? "本次模拟已完成" : "等待运行"}</small></div>
          <ol className="api-call-list">
            {(report?.mockCallsPassed ?? [
              "/calibration/insert", "/calibration/findByKey/…", "/task/insert",
              "/task/findByCondition", "/experimentalData/findByCondition",
              "/calibration/cancelCalibration",
            ]).map((path, index) =>
              <li key={index} className={report ? "done" : ""}>
                <span className="api-call-number">{String(index + 1).padStart(2, "0")}</span>
                <div><strong>{path.startsWith("/calibration/findByKey/") ? "查询校准记录" : callLabels[path] ?? path}</strong><code>{path}</code></div>
                {report && <Check size={16} />}
              </li>)}
          </ol>
          <p className="api-note">任务提交与校准记录在文档中未给出可确认的执行关联；曲线为合成数据。</p>
        </section>
        <section className="api-card api-evidence">
          {report ? <Curve report={report} /> : <div className="api-empty"><Activity size={34} /><strong>等待数据</strong><span>运行验证后展示合成曲线与下一步建议。</span></div>}
        </section>
      </div>
      {remoteEnabled()
        ? <section className="api-card"><div className="api-section-head"><strong>权威校准服务已启用</strong></div>
            <p className="api-note">当前工作流、审批与运行记录由 QuantaMind 校准 API 管理。此页的离线接口验证仍可运行；独立 5290 模拟服务不参与远程工作流。</p>
          </section>
        : <BackendSimulatorWorkbench />}
      <section className="api-card api-gaps">
        <div className="api-section-head"><div><ShieldAlert size={18} /><strong>接入真实系统前的接口合同</strong></div><small>{report ? report.unverifiedForFullAutomation.length : 7} 项待确认</small></div>
        <div className="api-gap-list">{(report?.unverifiedForFullAutomation ?? [
          "创建校准是否启动真实执行", "任务状态与终态语义", "逐次扫描参数覆盖",
          "原始数据与运行尝试的关联", "取消后的硬件停止确认",
          "参数发布、回读与回滚", "幂等与设备互斥",
        ]).map((gap, index) => <span key={index}>{gap}</span>)}</div>
      </section>
    </main>
  );
}
