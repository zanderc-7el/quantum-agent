import { useState } from 'react';
import type { Configuration } from '../lib/workspaceGateway';
import { biasConfirmation, biasGroups, biasSheetComplete, biasSheetConfirmed, updateBiasSheet } from '../lib/biasRoutes';

const textKeys = new Set(['lineId','stationIdentity']);
export default function BiasRoutesEditor({deployment, onChange}: {deployment: Configuration; onChange(value: Configuration): void}) {
  const [resource, setResource] = useState('');
  const sheets = deployment.biasRouteDrafts ?? {};
  const registered = deployment.biasRoutes ?? {};
  const id = resource.trim();
  const allowedId = Boolean(id && id.length<=128 && !Object.hasOwn(sheets,id) && !Object.hasOwn(registered,id));
  return <details className="wc-qubit"><summary>磁通偏置接线（LF）</summary>
    {Object.entries(registered).filter(([key])=>!Object.hasOwn(sheets,key)).map(([key,route])=>
      <p key={key}>{key} · {String(route.lineId ?? '已配置线路')} · 槽位 {String(route.board)} / 通道 {String(route.channel)} · {route.schema==='qcal-bias-route/1'?'独立测压':'设定电压'}</p>)}
    {Object.entries(sheets).map(([key, sheet]) => {
      const confirmed = biasSheetConfirmed(deployment,key,sheet);
      const change = (field: string, value: string) => onChange(updateBiasSheet(deployment,key,{fields:{...sheet.fields,[field]:value}}));
      return <fieldset key={key} className="wc-qubit"><legend>{key} 磁通线路 · {confirmed?'已核实':'待核实'}</legend>
        {biasGroups.map(group=><details key={group.title} open={group.title==='物理连接' || undefined}><summary>{group.title}</summary>
          {group.title==='成功回执' && <p>按仪器协议或已核实的记录填写，每行一种成功返回值。</p>}
          <div className="wc-grid">{group.fields.map(([field,label])=><label key={field}>{label}
            {field==='polarity' ? <select aria-label={key+' '+label} value={sheet.fields[field]??''} onChange={e=>change(field,e.target.value)}>
              <option value="">待核实</option><option value="1">正向（+1）</option><option value="-1">反向（−1）</option></select>
              : field.endsWith('Source') || field.endsWith('Replies') ?
              <textarea aria-label={key+' '+label} rows={2} maxLength={4096} value={sheet.fields[field]??''} onChange={e=>change(field,e.target.value)}/>
              : <input aria-label={key+' '+label} type={textKeys.has(field)?'text':'number'} step={field==='board'||field==='channel'?'1':'any'} value={sheet.fields[field]??''} onChange={e=>change(field,e.target.value)}/>}
          </label>)}</div></details>)}
        <label className="wc-check"><input type="checkbox" aria-label={key+' 已核实磁通线路'} checked={confirmed}
          disabled={!biasSheetComplete(sheet.fields) || !deployment.instrument?.host || !deployment.instrument?.port}
          onChange={e=>onChange(updateBiasSheet(deployment,key,{fields:sheet.fields,...(e.target.checked ?
            {confirmation:biasConfirmation(deployment,key,sheet.fields)}:{})}))}/>已核实线路、设定范围、结束值与回执</label>
        <button type="button" className="secondary" onClick={()=>onChange(updateBiasSheet(deployment,key))}>移除此偏置草稿</button>
      </fieldset>;
    })}
    <div className="wc-toolbar"><label>比特编号<input aria-label="磁通线路比特编号" value={resource} maxLength={128} onChange={e=>setResource(e.target.value)}/></label>
      <button type="button" className="secondary" disabled={!allowedId} onClick={()=>{if (allowedId) {
        onChange(updateBiasSheet(deployment,id,{fields:{}})); setResource('');
      }}}>添加偏置线路</button></div>
  </details>;
}
