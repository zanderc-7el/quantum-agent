import { useEffect, useId, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent, PointerEvent } from 'react';
import { ArrowLeft, ArrowRight, Code2, Crosshair, GripVertical, Pin, SlidersHorizontal, X } from 'lucide-react';
import type { Workflow } from '../types';
import { selectDrawerNode } from '../lib/parameterDrawerState';
import ParameterCodeEditor from './ParameterCodeEditor';
import ParameterPanel from './ParameterPanel';
import './parameter-drawer.css';

export interface ParameterDrawerProps {
  workflow: Workflow;
  /** An explicit node opens pinned; omit to follow the current execution. */
  selectedNodeId?: string;
  onClose: () => void;
  onSelectNode?: (nodeId: string) => void;
}


function bounds() {
  const available = typeof window === 'undefined' ? 680 : Math.max(240, window.innerWidth - 24);
  return { min: Math.min(340, available), max: Math.min(680, available) };
}

function clampWidth(value: number) {
  const { min, max } = bounds();
  return Math.max(min, Math.min(max, value));
}

/** A nonmodal inspector: its values follow store snapshots while the canvas stays usable. */
export function ParameterDrawer({ workflow, selectedNodeId, onClose, onSelectNode }: ParameterDrawerProps) {
  const titleId = useId();
  const selectId = useId();
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  const dragRef = useRef<{ x: number; width: number; pointerId: number } | null>(null);
  const [width, setWidth] = useState(() => clampWidth(560));
  const [resizing, setResizing] = useState(false);
  const [follow, setFollow] = useState(!selectedNodeId);
  const [pinnedNodeId, setPinnedNodeId] = useState(selectedNodeId);
  // The drawer opens on the readable parameter sheet; the JSONC source is a
  // deliberate second step rather than the default landing view.
  const [view, setView] = useState<'panel' | 'code'>('panel');
  onCloseRef.current = onClose;

  useEffect(() => {
    setPinnedNodeId(selectedNodeId);
    setFollow(!selectedNodeId);
  }, [workflow.id, selectedNodeId]);

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const panel = panelRef.current;
    closeRef.current?.focus({ preventScroll: true });
    const handleEscape = (event: globalThis.KeyboardEvent) => {
      // A modal opened above this nonmodal inspector owns Escape until it closes.
      if (event.key !== 'Escape' || event.defaultPrevented || document.querySelector('[aria-modal="true"]')) return;
      event.preventDefault();
      onCloseRef.current();
    };
    const handleResize = () => setWidth(value => clampWidth(value));
    window.addEventListener('keydown', handleEscape);
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('keydown', handleEscape);
      window.removeEventListener('resize', handleResize);
      if (opener?.isConnected && (!document.activeElement || document.activeElement === document.body || panel?.contains(document.activeElement))) {
        opener.focus({ preventScroll: true });
      }
    };
  }, []);

  const node = selectDrawerNode(workflow, follow ? undefined : pinnedNodeId);
  const nodeIndex = node ? workflow.nodes.findIndex(item => item.id === node.id) : -1;
  const { min, max } = bounds();
  const chooseNode = (id: string) => {
    setPinnedNodeId(id);
    setFollow(false);
    onSelectNode?.(id);
  };
  const resizeStart = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.focus();
    dragRef.current = { x: event.clientX, width, pointerId: event.pointerId };
    setResizing(true);
  };
  const resizeMove = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (drag?.pointerId !== event.pointerId) return;
    setWidth(clampWidth(drag.width + drag.x - event.clientX));
  };
  const resizeEnd = (event: PointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    setResizing(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const resizeKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 64 : 24;
    if (event.key === 'ArrowLeft') setWidth(value => clampWidth(value + step));
    else if (event.key === 'ArrowRight') setWidth(value => clampWidth(value - step));
    else if (event.key === 'Home') setWidth(min);
    else if (event.key === 'End') setWidth(max);
    else return;
    event.preventDefault();
  };


  return <aside id="parameter-drawer" ref={panelRef} className={`parameter-drawer ${resizing ? 'pd-resizing' : ''}`} style={{ '--pd-width': `${width}px` } as CSSProperties} aria-labelledby={titleId}>
    <div className="pd-resize-handle" role="separator" aria-orientation="vertical" aria-label="调整参数侧栏宽度，左右方向键调整" aria-valuemin={min} aria-valuemax={max} aria-valuenow={width} aria-valuetext={`${Math.round(width)} 像素`} tabIndex={0} onPointerDown={resizeStart} onPointerMove={resizeMove} onPointerUp={resizeEnd} onPointerCancel={resizeEnd} onLostPointerCapture={() => { dragRef.current = null; setResizing(false); }} onKeyDown={resizeKeys}><GripVertical size={12} /></div>
    <header className="pd-titlebar">
      <SlidersHorizontal size={15} aria-hidden="true" />
      <h2 id={titleId}><span className="pd-heading-label">实时参数</span>{node ? node.name : '参数快照'}</h2>
      <button type="button" ref={closeRef} className="pd-icon-button" onClick={onClose} aria-label="关闭参数侧栏" title="关闭参数侧栏 (Esc)"><X size={16} /></button>
    </header>
    <div className="pd-node-toolbar">
      <span className="pd-file-context" title={workflow.name}>{workflow.machine} / {workflow.target}</span>
      <label className="pd-sr-only" htmlFor={selectId}>实验节点 {nodeIndex + 1} / {workflow.nodes.length}</label>
      <select id={selectId} value={node?.id || ''} onChange={event => chooseNode(event.target.value)} disabled={!workflow.nodes.length}>
        {!workflow.nodes.length && <option value="">暂无实验节点</option>}
        {workflow.nodes.map((item, index) => <option key={item.id} value={item.id}>{String(index + 1).padStart(2, '0')} · {item.name}</option>)}
      </select>
      <button type="button" className="pd-icon-button" aria-label="上一个实验节点" title="上一个节点" disabled={nodeIndex <= 0} onClick={() => chooseNode(workflow.nodes[nodeIndex - 1].id)}><ArrowLeft size={13} /></button>
      <button type="button" className="pd-icon-button" aria-label="下一个实验节点" title="下一个节点" disabled={nodeIndex < 0 || nodeIndex >= workflow.nodes.length - 1} onClick={() => chooseNode(workflow.nodes[nodeIndex + 1].id)}><ArrowRight size={13} /></button>
      <button type="button" className={`pd-follow ${follow ? 'is-active' : ''}`} aria-pressed={follow} onClick={() => { if (follow) setPinnedNodeId(node?.id); setFollow(value => !value); }} title={follow ? '关闭跟随，固定查看当前节点' : '自动跟随执行中的节点'}>{follow ? <Crosshair size={12} /> : <Pin size={12} />}{follow ? '跟随' : '固定'}</button>
      <div className="pd-view-switch" role="group" aria-label="参数显示方式">
        <button type="button" className={view === 'panel' ? 'is-active' : ''} aria-pressed={view === 'panel'} onClick={() => setView('panel')} title="以参数表查看"><SlidersHorizontal size={12} /><span>参数</span></button>
        <button type="button" className={view === 'code' ? 'is-active' : ''} aria-pressed={view === 'code'} onClick={() => setView('code')} title="查看只读 JSONC 源码"><Code2 size={12} /><span>代码</span></button>
      </div>
    </div>
    <div className={`pd-content ${view === 'panel' ? 'pd-content-panel' : ''}`}>
      {view === 'panel'
        ? <ParameterPanel workflow={workflow} node={node} embedded />
        : <ParameterCodeEditor workflow={workflow} node={node} />}
    </div>
  </aside>;
}

export default ParameterDrawer;
