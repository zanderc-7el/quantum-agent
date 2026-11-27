export type PhysicalRoute = Record<string, unknown>;
export type PhysicalRoutes = Record<string, PhysicalRoute>;

// Keep aligned with backend XY-passband provenance validation. Only complete
// placeholder values are rejected: real report titles may contain these words.
const placeholderSources = new Set(['unknown','unconfirmed','pending','tbd','todo','n/a','na','none','null','test','example','placeholder','未提供','暂无','未知','待确认','待核实','待填写','待补充','待定','不详','无','无资料','无来源','已确认','确认','占位','示例','测试']);
export function validXYPassbandSource(source:unknown):source is string {
 return typeof source==='string' && [...source].length<=2000 && !!source.trim()
  && /[\p{L}\p{N}]/u.test(source)
  && !placeholderSources.has(source.normalize('NFKC').toLowerCase().replace(/\s+/g,''));
}
export function validXYPassband(range:unknown):range is [number,number] {
 return Array.isArray(range) && range.length===2
  && range.every(n=>typeof n==='number'&&Number.isFinite(n)&&n>0) && range[0]<range[1];
}
export function canConfirmXYPassband(route:PhysicalRoute):boolean {
 return validXYPassband(route.xyPassbandHz) && validXYPassbandSource(route.xyPassbandSource);
}
export function passbandGHz(route:PhysicalRoute,index:0|1):string {
 const hz=Array.isArray(route.xyPassbandHz)?route.xyPassbandHz[index]:undefined;
 return typeof hz==='number'&&Number.isFinite(hz)?String(hz/1e9):'';
}
export function updateXYPassband(route:PhysicalRoute,index:0|1,textGHz:string):PhysicalRoute {
 const range:[number|null,number|null]=[0,1].map(n=>{
  const value=Array.isArray(route.xyPassbandHz)?route.xyPassbandHz[n]:null;
  return typeof value==='number'&&Number.isFinite(value)?value:null;
 }) as [number|null,number|null];
 const hz=textGHz.trim()?Number(textGHz)*1e9:NaN;
 range[index]=Number.isFinite(hz)?hz:null;
 const next:PhysicalRoute={...route,xyPassbandConfirmed:false};
 if(range.every(value=>value===null))delete next.xyPassbandHz;
 else next.xyPassbandHz=range;
 return next;
}
export function physicalDraftNumber(text:string):number|'' {
 const value=text.trim()?Number(text):NaN;
 return Number.isFinite(value)?value:'';
}
export function updatePhysicalPort(route:PhysicalRoute,key:string,index:number,text:string):PhysicalRoute {
 const tuple=Array.isArray(route[key])?[...route[key] as unknown[]]:['',''];
 tuple[index]=physicalDraftNumber(text);
 return {...route,[key]:tuple,...(key==='xyPhysical'?{xyPassbandConfirmed:false}:{})};
}
export function addPhysicalRoute(routes:PhysicalRoutes,rawId:string):PhysicalRoutes {
 const id=rawId.trim();
 if(!id||Object.hasOwn(routes,id))return routes;
 return {...routes,[id]:{xyPhysical:['',''],readoutPhysical:['',''],adcPhysical:['',''],xyPassbandConfirmed:false}};
}
