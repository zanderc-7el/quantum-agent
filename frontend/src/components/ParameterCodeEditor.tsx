import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import type { ExperimentNode, Workflow } from '../types';
import { buildParameterCode } from '../lib/parameterCode';
import './parameter-code-editor.css';

export default function ParameterCodeEditor({ workflow, node }: { workflow: Workflow; node?: ExperimentNode }) {
  const document = useMemo(() => buildParameterCode(workflow, node), [workflow, node]);
  const previous = useRef<{ nodeId: string; lines: Map<string, string> } | null>(null);
  const [changed, setChanged] = useState<Set<string>>(new Set());
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'error'>('idle');
  const copyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const sourceRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!document) { previous.current = null; setChanged(new Set()); return; }
    const last = previous.current;
    const changes = last?.nodeId === document.nodeId
      ? document.lines.filter(line => last.lines.has(line.id) && last.lines.get(line.id) !== line.text).map(line => line.id)
      : [];
    previous.current = { nodeId: document.nodeId, lines: new Map(document.lines.map(line => [line.id, line.text])) };
    setChanged(new Set(changes));
    if (!changes.length) return;
    const timer = setTimeout(() => setChanged(new Set()), 1200);
    return () => clearTimeout(timer);
  }, [document?.text, document?.nodeId]);
  useEffect(() => () => clearTimeout(copyTimer.current), []);
  const copy = async () => {
    if (!document) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(document.text);
      setCopyState('copied');
    } catch {
      setCopyState('error');
      sourceRef.current?.focus();
    }
    clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopyState('idle'), 3000);
  };
  if (!document) return <div className="pc-empty"><code>// 当前任务没有可展示的实验节点</code></div>;
  return <div className="parameter-code-editor">
    <span className="pc-copy-status" role="status">{copyState === 'copied' ? '参数代码已复制，不含行号。' : ''}</span>
    <div className="pc-toolbar"><span><i />{changed.size ? `${changed.size} 行已更新` : '实时快照'}</span><button type="button" onClick={() => void copy()} aria-label="复制参数代码" title="复制完整 JSONC，不含行号">{copyState === 'copied' ? <Check size={13}/> : <Copy size={13}/>} {copyState === 'copied' ? '已复制' : '复制'}</button></div>
    {copyState === 'error' && <div className="pc-copy-error" role="status">复制失败，请选中代码后手动复制。</div>}
    <div ref={sourceRef} className="pc-source" tabIndex={0} role="region" aria-label={`${document.filename} · 只读参数代码，支持选择与复制`}>
      <pre className="pc-lines"><code>{document.lines.map((line, index) => <span className={`pc-line${changed.has(line.id) ? ' pc-line-changed' : ''}`} key={line.id} data-line={index + 1} data-field={line.id}><span className="pc-line-number" aria-hidden="true">{index + 1}</span><span className="pc-line-text">{line.tokens.map((token, tokenIndex) => <span className={`pc-token-${token.kind}`} key={tokenIndex}>{token.text}</span>)}{'\n'}</span></span>)}</code></pre>
    </div>
    <div className="pc-editor-status"><span>JSONC</span><span>UTF-8</span><span>空格: 2</span><span>{document.lines.length} 行</span><strong>只读 · 模拟数据</strong></div>
  </div>;
}
