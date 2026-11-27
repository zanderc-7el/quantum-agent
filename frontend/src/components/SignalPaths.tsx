import { useState } from 'react';
import type { SignalPath } from '../lib/configurationDiagrams';
import { isRasterImage } from '../lib/configurationDiagrams';
import './configuration-diagrams.css';
const kind: Record<string,string> = { instrument:'仪器',attenuator:'衰减器',filter:'滤波器',amplifier:'放大器',mixer:'混频器',cable:'线缆',chip:'芯片',adc:'采集器',other:'其他' };
export default function SignalPaths({scope,paths=[]}: {scope:string;paths?:SignalPath[]}) {
 const [selection,setSelection]=useState<{scope:string;id:string}>();
 const [zoom,setZoom]=useState(1);
 const selected=selection?.scope===scope?paths.find(p=>p.id===selection.id):undefined;
 return <section className="cd-panel" aria-label="机台信号路径"><header><h3>机台信号路径</h3><label>放大<input aria-label="线路图放大" type="range" min="1" max="2" step=".25" value={zoom} onChange={e=>setZoom(Number(e.target.value))}/></label></header>
 {!paths.length?<p className="cd-missing">尚未录入有来源的线路图。物理路由只列设备端口；沿途衰减器、滤波器、温级与信号方向待现场资料核实。</p>:<><div className="cd-path-list">{paths.map(p=><button type="button" key={p.id} aria-pressed={selected?.id===p.id} className={selected?.id===p.id?'primary':'secondary'} onClick={()=>setSelection({scope,id:p.id})}>{p.label} · {p.confirmation==='confirmed'?'已确认':'待核实'}</button>)}</div>
 {selected?<><p>方向：{({output:'从仪器输出至芯片',input:'从芯片读出至采集器',bidirectional:'双向'} as Record<string,string>)[selected.direction]??'未记录'} · 来源：{selected.source||'未记录'} · 资料日期：{selected.sourceDate||'未记录'} · 机台：{selected.station||'见当前接线配置'}</p>{isRasterImage(selected.image)&&<figure><figcaption className="cd-reference-caption">线路原图：{selected.image.name}（使用上方滑块放大核对）</figcaption><div className="cd-viewport"><img className="cd-reference-image" src={selected.image.dataUrl} alt={selected.label+' 原始线路图'} style={{width:`${zoom*100}%`}}/></div></figure>}
 <div className="cd-viewport"><ol data-direction={selected.direction} className="cd-signal-chain" style={{fontSize:`${zoom}em`}} aria-label="沿信号方向排列的部件">{selected.components.map((c,index)=><li key={c.id}><strong>{index+1}. {c.label}</strong><span>{kind[c.type]??c.type}</span><span>入口 → 出口：{c.fromPort||'未记录'} → {c.toPort||'未记录'}</span><span>通道/端口：{[c.channel,c.port].filter(Boolean).join(' / ')||'未记录'}</span>{c.temperatureK!==undefined&&<span>温级 {c.temperatureK} K</span>}{c.attenuationDb!==undefined&&<span>衰减 {c.attenuationDb} dB</span>}{c.delayNs!==undefined&&<span>延时 {c.delayNs} ns</span>}{c.model&&<span>型号 {c.model}</span>}<small>{c.confirmation==='confirmed'?'部件已确认':'部件待核实'} · {c.source||'沿用路径来源'}</small></li>)}</ol></div><p className="cd-note">按资料录入的信号先后顺序绘制；路径确认与各部件确认分别保留。</p></>:<p>选择线路以突出路径并查看沿途端口与部件。</p>}</>}
 </section>;
}
