import { useState } from 'react';
import type { ExperimentNode } from '../types';
import type { LivePoint } from '../lib/liveSeries';
import { rabiPointEvidence } from '../lib/experimentEvidence';
import './configuration-diagrams.css';
export default function RabiPrinciple({workflowId,node,axis,point,operationId}: {workflowId:string;node:ExperimentNode;axis:string;point?:LivePoint;operationId?:string|null}) {
 const [zoom,setZoom]=useState(1);
 const evidence=rabiPointEvidence(axis,point);
 return <section className="cd-panel cd-rabi" aria-label="Rabi 实验原理与本次扫描点"><header><h3>Rabi 实验原理示意</h3><label>放大<input aria-label="Rabi原理图放大" type="range" min="1" max="2" step=".25" value={zoom} onChange={e=>setZoom(Number(e.target.value))}/></label></header>
 <div className="cd-viewport"><svg viewBox="0 0 660 140" style={{width:`${zoom*100}%`,maxWidth:'none'}} role="img" aria-label="驱动脉冲到目标比特，再经读出脉冲与采集形成扫描点"><rect x="10" y="35" width="165" height="65" rx="8"/><rect x="245" y="35" width="150" height="65" rx="8"/><rect x="465" y="35" width="180" height="65" rx="8"/><text x="92" y="61" textAnchor="middle">XY 驱动脉冲</text><text x="92" y="82" textAnchor="middle">{evidence.variable}</text><text x="320" y="72" textAnchor="middle">{node.resourceId||node.qubits||'目标未记录'}</text><text x="555" y="61" textAnchor="middle">读出脉冲 → 采集</text><text x="555" y="82" textAnchor="middle">实际 I / Q 响应</text><text x="208" y="74" textAnchor="middle">→</text><text x="430" y="74" textAnchor="middle">→</text></svg></div>
 <p>{evidence.explanation} 每点施加驱动、读出并按实际设置重复采样；具体机台端口请核对设备配置。</p>
 <dl className="cd-point"><div><dt>运行 / 执行</dt><dd>{workflowId} / {operationId||'未记录'}</dd></div><div><dt>横轴</dt><dd>{evidence.variable} ({evidence.unit}) · 本点 {evidence.value??'未记录'}</dd></div><div><dt>观测量</dt><dd>{evidence.observable}，未换算为激发态概率</dd></div><div><dt>本点采集时间</dt><dd>未记录（逐点摘要未提供时间戳）</dd></div><div><dt>本点采样次数</dt><dd>{point?.shots??'未记录'}</dd></div><div><dt>本点读数</dt><dd>{point?`I=${point.meanI}；Q=${point.meanQ}；|平均 I+iQ|=${point.meanMagnitude}`:'未记录'}</dd></div></dl>
 {point&&<details><summary>扫描点原始设置与来源</summary><pre>{JSON.stringify({operationId:point.operationId,jobId:point.jobId,pointIndex:point.pointIndex,capture:point.capture,axes:point.axes,shots:point.shots,pointFile:point.pointFile,pointSha256:point.pointSha256},null,2)}</pre></details>}
 </section>;
}
