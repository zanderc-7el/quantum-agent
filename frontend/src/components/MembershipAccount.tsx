import { useEffect, useRef, useState } from "react";
import { ArrowRight, CheckCheck, ChevronRight, Crown, LogOut, RefreshCw, ShieldCheck, X } from "lucide-react";
import type { RemoteGateway } from "../lib/remoteGateway";
import { actualPoints, membershipError, modelProviderName, preferredMembershipModel } from "../lib/account";
import type { MembershipModelCatalog, MembershipStatus } from "../lib/account";

export function MembershipEntry({ status, onOpen }: { status?: MembershipStatus; onOpen: () => void }) {
  const points = actualPoints(status);
  return <button className="rp-membership-entry" onClick={onOpen} aria-label="打开 Ziyor 会员账户">
    <span className="rp-membership-icon"><Crown size={19} /></span>
    <span><strong>Ziyor 会员 <em>{status?.authenticated ? "已登录" : "登录"}</em></strong>
      <small>{status?.authenticated ? `${status.user?.phoneMasked || status.user?.name || "会员账户"}${points !== undefined ? ` · ${points} 积分` : ""}` : "登录后使用会员额度"}</small></span>
    <ChevronRight size={14} />
  </button>;
}

type Pending = "send" | "login" | "connect" | "logout" | "test" | "refresh" | null;
export default function MembershipAccount({ gateway, initialStatus, onStatus, onClose }: {
  gateway: RemoteGateway; initialStatus?: MembershipStatus; onStatus: (status: MembershipStatus) => void; onClose: () => void;
}) {
  const [status, setStatus] = useState(initialStatus);
  const [modelCatalog, setModelCatalog] = useState<MembershipModelCatalog>({ models: [] });
  const models = modelCatalog.models;
  const [selectedModel, setSelectedModel] = useState("");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [useForAgent, setUseForAgent] = useState(true);
  const [pending, setPending] = useState<Pending>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [modelError, setModelError] = useState("");
  const [notice, setNotice] = useState("");
  const [retryAt, setRetryAt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const pendingRef = useRef<Pending>(null);
  const dialog = useRef<HTMLElement>(null);
  const mounted = useRef(true);
  const statusCallback = useRef(onStatus);
  statusCallback.current = onStatus;
  const validPhone = /^1[3-9]\d{9}$/.test(phone);
  const remaining = Math.max(0, Math.ceil((retryAt - now) / 1000));
  const connection = status?.modelConnection;
  const points = actualPoints(status);
  const selected = models.find(item => item.id === selectedModel);
  const selectedExisting = selected?.provider === "existing" || selectedModel === "__existing_provider__";
  const readStatus = async () => {
    const next = await gateway.accountStatus();
    if (mounted.current) { setStatus(next); statusCallback.current(next); }
    return next;
  };
  const readModels = async () => {
    try { const next = await gateway.accountModels(); if (mounted.current) { setModelCatalog(next); setModelError(""); } }
    catch (cause) { if (mounted.current) setModelError(cause instanceof Error ? cause.message : "模型目录暂时不可用"); }
  };
  useEffect(() => {
    mounted.current = true;
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.focus();
    void Promise.allSettled([readStatus(), readModels()]).then(results => {
      if (!mounted.current) return;
      if (results[0].status === "rejected") setError(results[0].reason instanceof Error ? results[0].reason.message : "账户状态暂时不可用");
      setLoading(false);
    });
    return () => { mounted.current = false; previous?.focus(); };
  }, [gateway]);
  useEffect(() => {
    if (!retryAt) return;
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, [retryAt]);
  useEffect(() => {
    if (models.some(item => item.id === selectedModel)) return;
    setSelectedModel(preferredMembershipModel(modelCatalog, connection?.model));
  }, [modelCatalog, models, connection?.model, selectedModel]);
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pendingRef.current) onClose();
      if (event.key !== "Tab") return;
      const nodes = [...(dialog.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, [tabindex='0']") ?? [])].filter(item => item.getClientRects().length);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [onClose]);
  const run = async (action: Pending, operation: () => Promise<void>) => {
    if (pendingRef.current) return;
    pendingRef.current = action; setPending(action); setError(""); setNotice("");
    try { await operation(); }
    catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : "账户请求未完成，请重试"); }
    finally { pendingRef.current = null; if (mounted.current) setPending(null); }
  };
  const sendCode = () => {
    if (!validPhone || remaining || !status?.capabilities.sendCode) return;
    void run("send", async () => {
      const result = await gateway.accountSendCode(phone);
      if (result.status !== "sent") throw new Error("验证码尚未发送，请重试");
      const seconds = Number.isFinite(result.retryAfter) ? Math.max(0, result.retryAfter) : 60;
      setNow(Date.now()); setRetryAt(Date.now() + seconds * 1000); setNotice("验证码已发送，请查看手机短信。");
    });
  };
  const login = () => {
    if (!validPhone || !code.trim() || !status?.capabilities.login) return;
    void run("login", async () => {
      await gateway.accountLogin(phone, code.trim(), useForAgent);
      const next = await readStatus();
      if (!next.authenticated) throw new Error(membershipError(next.error) || "尚未完成会员登录");
      setPhone(""); setCode("");
      const usesMembership = ["ziyor", "zeelin"].includes(next.modelConnection.provider);
      setNotice(useForAgent ? usesMembership ? "会员已登录，智能体已选择 Ziyor 会员额度。" : "会员已登录，请在模型设置中连接 Ziyor 会员模型。" : "会员已登录，保留当前独立模型连接。");
      await readModels();
    });
  };
  const connect = () => {
    if (!selectedModel || !status?.capabilities.connect || (!selectedExisting && !status.authenticated)) return;
    void run("connect", async () => { await gateway.accountConnect(selectedModel); await readStatus(); setNotice(selectedExisting ? "已选择原有独立模型连接。" : "已选择 Ziyor 会员模型。"); });
  };
  const logout = () => void run("logout", async () => {
    await gateway.accountLogout(); const next = await readStatus();
    if (next.authenticated) throw new Error("会员尚未退出，请重试");
    setPhone(""); setCode(""); setNotice("已退出会员。使用会员模型的任务将等待模型连接。");
  });
  const testConnection = () => void run("test", async () => {
    await gateway.accountTest(); const next = await readStatus();
    setNotice(next.modelConnection.verified ? "模型连接验证通过。" : "验证请求已返回，请查看当前连接状态。");
  });

  return <div className="rp-modal-backdrop" onClick={event => { if (event.target === event.currentTarget && !pending) onClose(); }}>
    <section ref={dialog} className="rp-modal rp-account-modal" role="dialog" aria-modal="true" aria-labelledby="membership-title" tabIndex={-1}>
      <button className="icon-button rp-modal-close" aria-label="关闭会员账户" onClick={onClose} disabled={!!pending}><X size={19} /></button>
      <header className="rp-account-heading"><span className="rp-account-emblem"><Crown size={26} /></span><div><div className="rp-eyebrow">ZIYOR MEMBERSHIP</div><h2 id="membership-title">Ziyor 会员账户</h2></div></header>

      {loading && <p className="rp-account-hint" role="status">正在读取账户状态…</p>}
      {(error || membershipError(status?.error)) && <div className="rp-error" role="alert">{error || membershipError(status?.error)}</div>}
      {notice && <div className="rp-account-notice" role="status"><CheckCheck size={16} />{notice}</div>}
      {status?.authenticated ? <div className="rp-member-identity"><div><span className="rp-member-avatar"><Crown size={22} /></span><div><strong>{status.user?.name || "Ziyor 会员"}</strong><small>{status.user?.phoneMasked || "会员已登录"}</small></div><span className="rp-member-connected">已登录</span></div><section><span>账户积分</span><strong data-testid="membership-points">{points ?? "暂未获取"}</strong></section></div> : <form onSubmit={event => { event.preventDefault(); login(); }}>

        {status && (!status.capabilities.sendCode || !status.capabilities.login) && <p className="rp-account-hint">{!status.capabilities.login ? "会员登录暂不可用，请刷新状态后重试。" : "验证码发送暂不可用；已有验证码可继续登录。"}</p>}
        <label>手机号<input type="tel" autoComplete="tel" inputMode="numeric" aria-label="会员手机号" placeholder="请输入手机号" value={phone} maxLength={11} onChange={event => setPhone(event.target.value.replace(/\D/g, ""))} disabled={!!pending} /></label>
        <label>短信验证码<div className="rp-code-row"><input type="text" autoComplete="one-time-code" inputMode="numeric" aria-label="短信验证码" placeholder="请输入验证码" value={code} maxLength={12} onChange={event => setCode(event.target.value.replace(/\D/g, ""))} disabled={!!pending} /><button type="button" className="secondary" onClick={sendCode} disabled={!!pending || loading || !validPhone || remaining > 0 || !status?.capabilities.sendCode}>{pending === "send" ? "发送中…" : remaining ? `${remaining}s 后重发` : "获取验证码"}</button></div></label>
        <button type="submit" className="primary rp-account-login" disabled={!!pending || loading || !validPhone || !code.trim() || !status?.capabilities.login}>{pending === "login" ? "正在登录…" : useForAgent ? "登录并使用 Ziyor 额度" : "登录会员"}<ArrowRight size={16} /></button>
        <details className="rp-account-advanced"><summary>高级登录选项</summary><label className="rp-account-checkbox"><input type="checkbox" checked={useForAgent} onChange={event => setUseForAgent(event.target.checked)} disabled={!!pending} />登录后使用 Ziyor 会员额度驱动智能体</label></details>
      </form>}
      <section className="rp-account-connection"><h3>智能体当前连接</h3><div className="rp-account-current"><span className={connection?.active ? "rp-model-dot active" : "rp-model-dot"} /><div><strong>{modelProviderName(connection?.provider)}</strong><small>{connection?.model || "尚未选择模型"}</small></div><span>{connection?.active ? connection.verified ? "已验证" : "已连接 · 待验证" : "等待模型连接"}</span></div>
        {connection?.note && <p className="rp-account-hint">{connection.note}</p>}
        <details className="rp-account-advanced"><summary>模型与高级连接设置</summary>
          {modelError && <p className="rp-account-hint" role="alert">{modelError}</p>}
          <label className="rp-account-model-label">模型目录<select aria-label="会员模型目录" value={selectedModel} onChange={event => setSelectedModel(event.target.value)} disabled={!!pending || !models.length}>{!models.length && <option value="">暂无可用模型</option>}{models.map(model => <option key={model.id} value={model.id}>{model.label}{model.default || model.id === modelCatalog.defaultModelId ? " · 默认" : ""}</option>)}</select></label>
          {modelCatalog.source && <p className="rp-account-hint">目录来源：{modelCatalog.source}</p>}
          <button className="secondary rp-account-connect" onClick={connect} disabled={!!pending || !selectedModel || !status?.capabilities.connect || (!selectedExisting && !status.authenticated)}>{pending === "connect" ? "正在切换…" : selectedExisting ? "切换到原有独立连接" : "使用此 Ziyor 模型"}<ArrowRight size={14} /></button>

        </details>
        <button className="secondary rp-account-test" onClick={testConnection} disabled={!!pending || !connection?.active || !status?.capabilities.test}>{pending === "test" ? "正在验证…" : "验证连接"}<ShieldCheck size={14} /></button>
        <p className="rp-account-hint">验证会发送一次请求，可能消耗额度。</p>
      </section>
      <footer className="rp-account-footer"><button className="secondary" onClick={() => void run("refresh", async () => { await readStatus(); await readModels(); })} disabled={!!pending}><RefreshCw size={14} />刷新状态</button>{status?.authenticated && <button className="rp-account-logout" onClick={logout} disabled={!!pending}><LogOut size={14} />{pending === "logout" ? "正在退出…" : "退出会员"}</button>}</footer>
    </section>
  </div>;
}
