/** Display-only axes. Original samples and hover units remain unchanged. */
export function plotAxis(min:number,max:number,unit=''){
 const magnitude=Math.max(Math.abs(min),Math.abs(max));
 const scale=unit==='Hz'?(magnitude>=1e9?1e9:magnitude>=1e6?1e6:magnitude>=1e3?1e3:1):1;
 const displayUnit=scale===1e9?'GHz':scale===1e6?'MHz':scale===1e3?'kHz':unit;
 const step=Math.abs(max-min)/4/scale;
 const decimals=step>0?Math.min(12,Math.max(0,Math.ceil(-Math.log10(step))+1)):4;
 return {unit:displayUnit,scale,format(value:number){
  const v=value/scale;
  if(!Number.isFinite(v))return '—';
  if(v!==0&&(Math.abs(v)<.01||Math.abs(v)>=1e5)){
   const digits=step>0?Math.min(12,Math.max(2,Math.ceil(Math.log10(Math.abs(v))-Math.log10(step))+1)):3;
   return v.toExponential(digits).replace(/(\.\d*?[1-9])0+e/,'$1e').replace(/\.0+e/,'e');
  }
  return Number(v.toFixed(decimals)).toString();
 }};
}
export function discreteAxisIndices(length:number){
 if(!Number.isInteger(length)||length<1)return [];
 const count=Math.min(5,length);return Array.from({length:count},(_,i)=>count===1?0:Math.round(i*(length-1)/(count-1)));
}
