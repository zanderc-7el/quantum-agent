import {useState} from 'react';
import {addPhysicalRoute,canConfirmXYPassband,passbandGHz,physicalDraftNumber,updatePhysicalPort,updateXYPassband} from '../lib/physicalRoutes';
import type {PhysicalRoute,PhysicalRoutes} from '../lib/physicalRoutes';

const portLabels={xyPhysical:'XY 驱动',readoutPhysical:'读出输出',adcPhysical:'ADC 输入'};
export default function PhysicalRoutesEditor({routes={},onChange}:{routes?:PhysicalRoutes;onChange(routes:PhysicalRoutes):void}) {
 const [routeId,setRouteId]=useState('');
 const change=(id:string,route:PhysicalRoute)=>onChange({...routes,[id]:route});
 const duplicate=Object.hasOwn(routes,routeId.trim());
 return <>
  {Object.entries(routes).map(([id,route])=><fieldset className="wc-qubit" key={id}><legend>{id} 物理路由</legend>
   <div className="wc-grid">{(Object.keys(portLabels) as (keyof typeof portLabels)[]).map(key=><div key={key}><strong>{portLabels[key]}</strong><div className="wc-grid">{[0,1].map(index=><label key={index}>{index===0?'槽位':'端口'}<input aria-label={`${id} ${portLabels[key]}${index===0?'槽位':'端口'}`} type="number" min="0" value={Array.isArray(route[key])?String((route[key] as unknown[])[index]??''):''} onChange={e=>change(id,updatePhysicalPort(route,key,index,e.target.value))}/></label>)}</div></div>)}
    <label>ADC 采样率 (Hz)<input aria-label={`${id} ADC 采样率 (Hz)`} type="number" min="1" value={String(route.adcSampleRateHz??'')} onChange={e=>change(id,{...route,adcSampleRateHz:physicalDraftNumber(e.target.value)})}/></label>
   </div>
   <details><summary>XY 驱动通带</summary><div className="wc-grid">
    {([0,1] as const).map(index=><label key={index}>{index===0?'下限':'上限'} (GHz)<input aria-label={`${id} XY 通带${index===0?'下限':'上限'} (GHz)`} type="number" min="0" step="any" value={passbandGHz(route,index)} onChange={e=>change(id,updateXYPassband(route,index,e.target.value))}/></label>)}
    <label>来源<textarea aria-label={`${id} XY 通带来源`} rows={2} value={typeof route.xyPassbandSource==='string'?route.xyPassbandSource:''} onChange={e=>change(id,{...route,xyPassbandSource:e.target.value,xyPassbandConfirmed:false})}/></label>
   </div><label className="wc-check"><input aria-label={`${id} 确认 XY 通带`} type="checkbox" checked={route.xyPassbandConfirmed===true&&canConfirmXYPassband(route)} disabled={!canConfirmXYPassband(route)} onChange={e=>change(id,{...route,xyPassbandConfirmed:e.target.checked===true&&canConfirmXYPassband(route)})}/>已核实通带及来源</label></details>
   <button type="button" className="secondary" onClick={()=>{const next={...routes};delete next[id];onChange(next);}}>移除路由</button>
  </fieldset>)}
  <div className="wc-toolbar"><input aria-label="接线逻辑比特 ID" placeholder="逻辑比特 ID，例如 Q12" value={routeId} onChange={e=>setRouteId(e.target.value)}/><button type="button" className="secondary" disabled={!routeId.trim()||duplicate} onClick={()=>{const next=addPhysicalRoute(routes,routeId);if(next===routes)return;onChange(next);setRouteId('');}}>添加路由</button></div>
 </>;
}
