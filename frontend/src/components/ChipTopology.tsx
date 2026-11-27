import { useState } from 'react';
import { isRasterImage, topologySelection } from '../lib/configurationDiagrams';
import type { ChipTopologyData } from '../lib/configurationDiagrams';
import type { Qubit } from '../lib/workspaceGateway';
import { diagramSourceLabel } from '../lib/diagramSource';
import { DiagramSourceEvidence } from './DiagramSource';
import './configuration-diagrams.css';

export default function ChipTopology({ scope, topology, qubits = [], selectedResourceId, onSelect, disabled = false }: { scope: string; topology?: ChipTopologyData; qubits?: Qubit[]; selectedResourceId?: string; onSelect?(id: string): void; disabled?: boolean }) {
  const [selection, setSelection] = useState<{ scope: string; resourceId: string }>();
  const [zoom, setZoom] = useState(1);
  const nodes = (topology?.nodes ?? []).filter(n => Number.isFinite(n.x) && Number.isFinite(n.y));
  const selected = topologySelection(topology ? {...topology, nodes} : undefined, scope, selectedResourceId === undefined ? selection : {scope, resourceId:selectedResourceId});
  const image = isRasterImage(topology?.image) ? topology.image : undefined;
  const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
  const minX = Math.min(...xs), minY = Math.min(...ys);
  const rangeX = Math.max(...xs) - minX || 1, rangeY = Math.max(...ys) - minY || 1;
  const scale = Math.min(530 / rangeX, 240 / rangeY);
  const pos = (id: string) => { const n = nodes.find(n => n.resourceId === id); return n ? image ? {x:n.x,y:n.y} : { x: 315 + (n.x - minX - rangeX/2) * scale, y: 165 + (n.y - minY - rangeY/2) * scale } : undefined; };
  const radius = image ? Math.min(image.width,image.height)/22 : 20;
  const choose = (id: string) => { if (disabled) return; if (selectedResourceId === undefined) setSelection({scope, resourceId:id}); onSelect?.(id); };
  const qubit = qubits.find(q => q.id === selected.resourceId);
  const identityConfirmed = (id?: string) => qubits.some(q => q.id === id && q.identityStatus === 'confirmed' && !!q.physicalId?.trim());
  const selectedConfirmed = identityConfirmed(selected.resourceId);
  const neighborsConfirmed = selectedConfirmed && selected.neighbors.every(identityConfirmed);
  const neighborIds = neighborsConfirmed ? selected.neighbors : selected.neighbors.map(id => nodes.find(n => n.resourceId === id)?.designId || id);
  return <section className="cd-panel" aria-label="芯片拓扑">
    <header><h3>芯片拓扑</h3><label>放大<input aria-label="拓扑放大" type="range" min="1" max="3" step=".25" value={zoom} onChange={e => setZoom(Number(e.target.value))}/></label></header>
    {!nodes.length && !image ? <p className="cd-missing">尚未提供有来源的设计布局与连线。请导入或录入已核对资料；共用读出总线不代表比特之间有耦合。</p> : <>
      <div className="cd-viewport"><svg viewBox={image?`0 0 ${image.width} ${image.height}`:'0 0 630 330'} style={{width:`${zoom * 100}%`,maxWidth:'none'}} role="group" aria-label="可选择的芯片布局">
        {image&&<image href={image.dataUrl} width={image.width} height={image.height}/>}
        {(topology?.edges ?? []).map(edge => { const a=pos(edge.from),b=pos(edge.to); if(!a||!b)return null; return <g key={edge.id}><line x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={`cd-edge ${edge.confirmation === 'confirmed' && edge.source?.trim() ? 'confirmed' : 'pending'} ${selected.edges.some(e=>e.id===edge.id)?'selected':''}`}><title>{`${edge.kind} · ${edge.confirmation==='confirmed'?'已确认':'待核实'} · ${diagramSourceLabel(edge.source)||'来源未记录'}`}</title></line></g>; })}
        {nodes.map(n=>{const p=pos(n.resourceId)!;const active=n.resourceId===selected.resourceId;return <g role="button" aria-label={`选择比特 ${n.resourceId}`} aria-pressed={active} aria-disabled={disabled} tabIndex={disabled?-1:0} key={n.resourceId} className={`cd-node ${active?'selected':''} ${selected.neighbors.includes(n.resourceId)?'neighbor':''}`} onClick={()=>choose(n.resourceId)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose(n.resourceId);}}}><title>{identityConfirmed(n.resourceId)?`${n.designId?`设计 ${n.designId} → `:''}实测资源 ${n.resourceId}`:`${n.designId?`设计 ${n.designId} · `:''}草稿编号 ${n.resourceId} · 实测身份待核实`}</title><circle cx={p.x} cy={p.y} r={radius}/><text x={p.x} y={p.y+radius/5} style={{fontSize:radius*.6}} textAnchor="middle">{n.resourceId}</text></g>;})}
      </svg></div>
      <p className="cd-note">实线：资料已确认；虚线：待核实。图形按已录入坐标排列。</p>
      {(image||topology?.source)&&<><p className="cd-reference-caption">原图：{image?.name||'未上传'} · 来源：{diagramSourceLabel(topology?.source||image?.source)||'未记录'} · 资料日期：{topology?.date||image?.date||'未记录'}</p><DiagramSourceEvidence source={topology?.source||image?.source}/>{topology?.source&&image?.source&&topology.source!==image.source&&<DiagramSourceEvidence source={image.source} label="原图来源证据"/>}</>}
      {!nodes.length&&<p className="cd-missing">原图已导入，尚未对应实测资源。请在版图表单中逐点录入。</p>}
      {selected.resourceId ? <div className="cd-details"><strong>{selectedConfirmed?`目标 ${selected.resourceId}`:`草稿编号 ${selected.resourceId} · 实测身份待核实`}</strong><p>设计编号：{nodes.find(n=>n.resourceId===selected.resourceId)?.designId||'未记录'} · {selectedConfirmed?'物理编号':'物理编号（待核实）'}：{qubit?.physicalId?.trim()||'未记录'} · {neighborsConfirmed?'已确认直接耦合比特':'设计图已记录直接耦合编号'}：{neighborIds.join('、')||'邻接资料待核实'}</p><p>T₁ / T₂：暂无测量数据（本配置未关联带时间的测量记录）</p>{selected.edges.map(e=><div key={e.id}><p>{e.from} ↔ {e.to} · {e.kind} · {e.confirmation==='confirmed'?'已确认':'待核实'} · 来源：{diagramSourceLabel(e.source)||'未记录'}</p><DiagramSourceEvidence source={e.source}/></div>)}</div> : <p>点选比特查看编号与已记录连接。</p>}
    </>}
  </section>;
}
