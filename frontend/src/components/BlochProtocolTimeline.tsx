import type {ScienceResult} from '../lib/scienceGateway';
import {activeProtocolStep,blochProtocol,protocolTime} from '../lib/blochProtocol';

export default function BlochProtocolTimeline({result,cursorNs}:{result:ScienceResult;cursorNs?:number}){
 const protocol=blochProtocol(result);
 if(!protocol)return <p className="science-note">该模型结果缺少完整步骤信息；未推定制备或读出时序。</p>;
 const active=activeProtocolStep(protocol,cursorNs);
 return <section aria-label="所选实验步骤时序">
  <h4>步骤时序（模型与协议示意）</h4>
  <p className="science-note">{protocol.scan?'所选扫描等待':'本次模型演化'}：{protocolTime(protocol.durationNs)}。{protocol.refocusCount>0&&`中间理想重聚焦 ${protocol.refocusCount} 次。`}{typeof cursorNs==='number'?`内部游标：${protocolTime(cursorNs)}。`:''}</p>
  <p className="science-note">{result.sequence==='x'?'时间从模型初态算起。读出只标末端顺序。':'时间从制备后算起。制备和读出只标顺序。'}</p>
  <div className="science-sequence" role="list" aria-label="协议步骤顺序">
   {protocol.steps.map((step,index)=><span key={step.id} role="listitem" data-step-kind={step.kind} data-step-id={step.id} className={active===step.id?'active':''} aria-current={active===step.id?'step':undefined}>
    <strong>{index+1}. {step.label}</strong><small>{step.timing}</small>
   </span>)}
  </div>
  <details className="science-details"><summary>坐标与协议说明</summary>{protocol.notes.map((note,i)=><p key={i}>{note}</p>)}</details>
 </section>;
}
