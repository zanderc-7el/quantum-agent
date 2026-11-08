import { useId, useRef, useState } from "react";
import { Activity, ArrowRight, Atom, Check, Eye, EyeOff, FlaskConical, GitBranch, LockKeyhole, Moon, ShieldCheck, Sun, UserRound, UsersRound } from "lucide-react";
import { authenticate, clearSession, DEMO_ACCOUNTS, ROLE_LABELS, saveSession } from "../lib/auth";
import type { User } from "../lib/auth";
import { clearRemoteSession, loginWithBackend, remoteEnabled } from "../lib/apiClient";
import { PLATFORM_NAME } from "../lib/branding";
import "./auth.css";

export interface LoginScreenProps {
  onLogin: (user: User) => void;
  theme?: "dark" | "light";
  onToggleTheme?: () => void;
}

export default function LoginScreen({ onLogin, theme = "dark", onToggleTheme }: LoginScreenProps) {
  const remote = remoteEnabled();
  // Remote accounts come from the backend directory, so there is no sample
  // name to prefill; the mock environment keeps the demo shortcut.
  const [username, setUsername] = useState(remote ? "" : "leader");
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const passwordInput = useRef<HTMLInputElement>(null);
  const id = useId();
  const errorId = `${id}-error`;

  const login = async () => {
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      // Remote mode verifies credentials against the backend account directory
      // and uses the identity and role it returns; mock mode checks the local
      // demo directory.
      const user = remote
        ? await loginWithBackend(username, password)
        : authenticate(username, password);
      if (!user) {
        setError("账号或密码不正确，请重试。");
        setPassword("");
        passwordInput.current?.focus();
        return;
      }
      if (!saveSession(user)) {
        clearSession();
        clearRemoteSession();
        setError("浏览器无法保存本次会话，请允许此站点使用会话存储后重试。");
        return;
      }
      setPassword("");
      try {
        onLogin(user);
      } catch {
        clearSession();
        clearRemoteSession();
        setError("工作空间暂时无法打开，请重新登录。");
      }
    } catch (cause) {
      setError(
        cause instanceof Error && cause.message
          ? cause.message
          : "登录失败，请稍后重试。",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth-page">
      <header className="auth-topbar">
        <div className="auth-brand"><span className="auth-brand-icon"><Atom size={25} /></span><span>{PLATFORM_NAME}<small>SUPERCONDUCTING QUANTUM CONTROL</small></span></div>
        {onToggleTheme && <button type="button" className="auth-theme" onClick={onToggleTheme} aria-label={theme === "dark" ? "切换到明亮主题" : "切换到深色主题"} title={theme === "dark" ? "切换到明亮主题" : "切换到深色主题"}>{theme === "dark" ? <Sun size={19} /> : <Moon size={19} />}</button>}
      </header>
      <div className="auth-layout">
        <section className="auth-intro" aria-labelledby={`${id}-intro`}>
          <span className="auth-eyebrow"><span />协同实验 · 有序探索</span>
          <h1 id={`${id}-intro`}>从一次校准，<br />到更可靠的量子实验。</h1>
          <p className="auth-lead">连接实验计划、工作流与分析报告。让每一次执行都有记录，让每一次决策都有依据。</p>
          <div className="auth-visual" aria-hidden="true">
            <div className="auth-visual-top"><span><Activity size={14} /> CALIBRATION WORKSPACE</span><span><i /> DEMO</span></div>
            <svg className="auth-wave" viewBox="0 0 480 120"><defs><linearGradient id={`${id}-wave`}><stop stopColor="currentColor" stopOpacity="0.16" /><stop offset="0.5" stopColor="currentColor" /><stop offset="1" stopColor="currentColor" stopOpacity="0.2" /></linearGradient></defs><path className="auth-wave-grid" d="M0 30H480M0 60H480M0 90H480M60 0V120M120 0V120M180 0V120M240 0V120M300 0V120M360 0V120M420 0V120" /><path d="M0 60H30C48 60 50 23 66 23S88 97 104 97S126 30 144 30S166 88 184 88S206 39 224 39S246 78 264 78S286 47 304 47S326 70 344 70S366 55 384 55S406 63 424 63L480 60" fill="none" stroke={`url(#${id}-wave)`} strokeWidth="2.5" /></svg>
            <div className="auth-flow"><span><FlaskConical size={16} /> 实验计划</span><ArrowRight size={14} /><span><GitBranch size={16} /> 工作流</span><ArrowRight size={14} /><span><Check size={16} /> 分析报告</span></div>
          </div>
          <div className="auth-role-summary"><div><ShieldCheck size={19} /><span><strong>负责人统筹</strong><small>团队任务、质量审批与策略配置</small></span></div><div><UserRound size={19} /><span><strong>工作人员执行</strong><small>创建并操作本人任务，共享团队结果</small></span></div></div>
        </section>
        <section className="auth-card" aria-labelledby={`${id}-login`}>
          <div className="auth-card-symbol"><LockKeyhole size={22} /></div>
          <span className="auth-card-kicker">WORKSPACE ACCESS</span>
          <h2 id={`${id}-login`}>登录实验工作空间</h2>
          <p className="auth-card-description">{remote ? "账号由 QuantaMind 后端目录校验，任务与结果保存在服务器。新账号由团队负责人创建。" : "使用演示账号，体验不同角色的协作流程。"}</p>
          <form onSubmit={(event) => { event.preventDefault(); void login(); }}>
            <label className="auth-field" htmlFor={`${id}-username`}><span>账号</span><div className="auth-input-wrap"><UserRound size={17} /><input id={`${id}-username`} name="username" value={username} onChange={(event) => { setUsername(event.target.value); setError(""); }} placeholder={remote ? "输入账号" : "输入演示账号"} autoComplete="username" autoCapitalize="none" spellCheck={false} maxLength={remote ? 64 : 80} required aria-describedby={error ? errorId : undefined} /></div></label>
            <label className="auth-field" htmlFor={`${id}-password`}><span>密码</span><div className="auth-input-wrap"><LockKeyhole size={17} /><input ref={passwordInput} id={`${id}-password`} name="password" type={visible ? "text" : "password"} value={password} onChange={(event) => { setPassword(event.target.value); setError(""); }} placeholder={remote ? "输入密码" : "输入演示密码"} autoComplete="current-password" maxLength={128} required aria-describedby={error ? errorId : undefined} /><button type="button" className="auth-password-toggle" aria-label={visible ? "隐藏密码" : "显示密码"} aria-pressed={visible} onClick={() => setVisible((value) => !value)}>{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></label>
            {error && <p className="auth-error" id={errorId} role="alert">{error}</p>}
            <button type="submit" className="auth-submit" disabled={busy}>{busy ? "正在登录…" : "进入工作空间"}<ArrowRight size={17} /></button>
            <p className="auth-session-note">会话仅在当前标签页保存，最长 8 小时。</p>
          </form>
          {!remote && (
            <>
              <div className="auth-demo-heading"><UsersRound size={15} /><span>公开演示账号</span><small>点击选择账号，再输入密码</small></div>
              <div className="auth-accounts">{DEMO_ACCOUNTS.map((account) => <button key={account.id} type="button" className={`auth-account ${username === account.username ? "is-selected" : ""}`} aria-pressed={username === account.username} onClick={() => { setUsername(account.username); setPassword(""); setError(""); passwordInput.current?.focus(); }}><span className="auth-account-role">{account.role === "leader" ? <ShieldCheck size={15} /> : <UserRound size={15} />}{ROLE_LABELS[account.role]}</span><span className="auth-account-credentials"><code>{account.username}</code><small>密码 <code>{account.password}</code></small></span></button>)}</div>
            </>
          )}
          <p className="auth-disclaimer">{remote ? "远程模式：账号由 QuantaMind 后端目录校验，前端不再内置账号。忘记密码请联系团队负责人在「设置 → 账号与权限」中重置。" : "这是前端模拟环境。账号和密码为公开演示数据，请勿输入真实密码。正式认证与权限须由服务端校验。"}</p>
        </section>
      </div>
      <footer className="auth-footer"><span><span />{remote ? "远程模式 · 已连接 QuantaMind 后端" : "本地模拟 · 未连接真实设备或后端"}</span><span>单比特 / 双比特校准工作空间</span></footer>
    </main>
  );
}
