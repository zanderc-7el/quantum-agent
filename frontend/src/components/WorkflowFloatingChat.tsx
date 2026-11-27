import AssistantReply from './AssistantReply';
import { technicalReplyRequested } from '../lib/assistantReplyPresentation';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ArrowUp, Minus, SquarePen } from "lucide-react";
import type { ExperimentNode, Workflow } from "../types";
import type { RemoteGateway } from "../lib/remoteGateway";
import { remoteChatStore } from "../lib/remoteChatStore";
import { workflowConversationStore } from '../lib/workflowConversationStore';
import { createWorkspaceGateway } from '../lib/workspaceGateway';
import QuantumMark from "./QuantumMark";
import "./workflow-floating-chat.css";

export default function WorkflowFloatingChat({ gateway, workflow, node, projectId }: {
  gateway: RemoteGateway; workflow: Workflow; node?: ExperimentNode; projectId: string;
}) {
  const scope = useMemo(() => ({ projectId, workflowId: workflow.id }), [projectId, workflow.id]);
  const api = useMemo(() => createWorkspaceGateway(() => gateway.session()), [gateway]);
  const binding = useMemo(() => ({ subscribe: (listener: () => void) => workflowConversationStore.subscribe(scope, listener), snapshot: () => workflowConversationStore.get(scope) }), [scope]);
  const legacy = useMemo(() => remoteChatStore.get(scope), [scope]);
  const conversation = useSyncExternalStore(binding.subscribe, binding.snapshot, binding.snapshot);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const messages = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const running = conversation.pending === "running";
  const unresolved = conversation.pending === "unresolved";
  const current = workflow.nodes.find(item => item.id === workflow.currentNodeId);
  const acceptedCount = workflow.mode === "real" ? workflow.nodes.filter(item => item.engineState === "verified" && item.result?.quality.status === "passed" && item.result.dataKind === "real" && !item.result.stale && !item.result.partial && !item.result.verificationPending).length : 0;
  useEffect(() => { setDraft(""); setError(""); }, [scope]);
  useEffect(() => { if (open) { input.current?.focus(); void workflowConversationStore.refresh(scope, api); } }, [open, scope, api]);
  useEffect(() => { if (open) messages.current?.scrollTo({ top: messages.current.scrollHeight, behavior: "smooth" }); }, [open, conversation.conversation?.messages, running]);
  const minimize = () => { setOpen(false); launcher.current?.focus(); };
  const startNew = async () => {
    setError("");
    try { await workflowConversationStore.newConversation(scope, api); setDraft(""); input.current?.focus(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };
  const send = async () => {
    const text = draft.trim(); if (!text || running || unresolved || conversation.loading) return;
    setDraft(""); setError("");
    try { await workflowConversationStore.send(scope, api, text, node?.id); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };
  return <div className="workflow-discussion">
    <button ref={launcher} className="workflow-discussion-orb" aria-label="讨论当前实验" aria-expanded={open} aria-controls="workflow-discussion-window" hidden={open} onClick={() => setOpen(true)}><QuantumMark size={32} /></button>
    <section id="workflow-discussion-window" className="workflow-discussion-window" role="dialog" aria-modal="false" aria-label={`${workflow.target} 实验讨论`} hidden={!open} onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); minimize(); } }}>
      <header className="workflow-discussion-heading"><QuantumMark size={21} /><div><strong>{workflow.target} 实验讨论</strong><span>{workflow.machine}</span></div><button type="button" aria-label="新对话" title="新对话，保留已有记录" disabled={running || unresolved || conversation.loading} onClick={() => void startNew()}><SquarePen size={19} /></button><button type="button" aria-label="最小化讨论窗口" title="最小化" onClick={minimize}><Minus size={21} /></button></header>
      <div className="workflow-discussion-context"><span>{workflow.mode === "replay" ? "历史回放" : "当前实验"} · {current?.label ?? workflow.name}</span>{node && <span>正在查看：{node.label}</span>}<small>{workflow.mode === "replay" ? `${workflow.nodes.length} 项历史记录` : `${acceptedCount} / ${workflow.nodes.length} 项独立复测通过`}{workflow.status === "running" ? " · 采集中" : workflow.status === "paused" ? " · 已暂停" : workflow.status === "attention" ? " · 等待处理" : ""}</small></div>
      <div className="workflow-discussion-messages" ref={messages} aria-live="polite" aria-relevant="additions text">{!conversation.conversation?.messages.length && <div className="workflow-discussion-empty"><QuantumMark size={35} /><h3>当前实验</h3></div>}{(conversation.conversation?.messages ?? []).map((message, index, history) => <article key={message.id} className={`workflow-discussion-message ${message.role}`}><span>{message.role === "user" ? "你" : message.role === "assistant" ? "测控智能体" : "请求状态"}</span>{message.role === "assistant" ? <AssistantReply text={message.content} technical={technicalReplyRequested(history, index)}/> : <div>{message.content}</div>}</article>)}{legacy.messages.length > 0 && <details><summary>历史本地记录</summary>{legacy.messages.map(message => <article key={message.id} className={`workflow-discussion-message ${message.role}`}><span>{message.role === "user" ? "你" : "测控智能体"}</span>{message.role === "assistant" ? <AssistantReply text={message.text}/> : <div>{message.text}</div>}</article>)}</details>}{running && <p className="workflow-discussion-pending" role="status">正在回答…</p>}</div>
      <div className="workflow-discussion-composer">{conversation.error && <p role="alert">{conversation.error}</p>}{conversation.loading && <p role="status">正在读取会话…</p>}{unresolved && <p role="status">等待回执<button type="button" disabled={conversation.loading} onClick={() => void workflowConversationStore.refresh(scope, api)}>查询回执</button></p>}{error && <p role="alert">{error}</p>}<textarea ref={input} aria-label="讨论当前实验的消息" placeholder="询问当前结果或下一步…" value={draft} maxLength={20000} disabled={running || unresolved || conversation.loading} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} /><div><button type="button" aria-label="发送讨论消息" disabled={running || unresolved || conversation.loading || !draft.trim()} onClick={() => void send()}><ArrowUp size={21} /></button></div></div>
    </section>
  </div>;
}
