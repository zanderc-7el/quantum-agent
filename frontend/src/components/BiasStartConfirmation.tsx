import {useState} from 'react';
import type {BiasStartCommand,BiasStartIntent} from '../types.ts';

export default function BiasStartConfirmation({intent,currentWorkflowId,currentRevision,busy,onCancel,onConfirm}:{intent:BiasStartIntent;currentWorkflowId?:string;currentRevision?:number;busy:boolean;onCancel:()=>void;onConfirm:(value:BiasStartCommand)=>void}) {
  const [checked,setChecked]=useState(false);
  const stale=currentWorkflowId!==intent.workflowId || currentRevision!==intent.expectedRevision;
  const r=intent.requirement;
  const submit=()=>{if(!checked||stale||busy)return;onConfirm({operationId:intent.operationId,expectedRevision:intent.expectedRevision,biasStartConfirmation:{confirmed:true,routeSha256:r.routeSha256,startSettingV:r.startSettingV,endSettingV:r.endSettingV}});};
  return <div className="rp-modal-backdrop"><section className="rp-modal" role="dialog" aria-modal="true" aria-label="核对本次偏置起始设定">
    <h2>核对本次偏置起始设定</h2><p>{r.resourceId} · 线路 {r.lineId}</p>
    <p>起始设定 {r.startSettingV} V → 测量扫描 → 结束设定 {r.endSettingV} V</p>
    <p>请确认当前已按此线路准备静态直流，并核对起始设定。这里记录人工确认，输出电压未独立测压。独立复测将接续首测结束回执；本次确认不授权下一次测量。</p>
    <label><input type="checkbox" checked={checked} onChange={e=>setChecked(e.target.checked)} disabled={stale||busy}/>已核对当前起始设定</label>
    {stale&&<p role="alert">任务已更新，请关闭后重新运行本步并核对。</p>}
    <div className="rp-modal-actions"><button className="secondary" onClick={onCancel} disabled={busy}>取消</button><button className="primary" disabled={!checked||stale||busy} onClick={submit}>确认并运行本步</button></div>
  </section></div>;
}
