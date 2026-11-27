import type {ScienceResult} from './scienceGateway';

export interface BlochProtocolStep {
 id:string;kind:'preparation'|'wait'|'drive'|'refocus'|'analysis'|'readout';
 label:string;timing:string;startNs?:number;endNs?:number;
}
export interface BlochProtocol {
 durationNs:number;scan:boolean;zeroWait:boolean;initialState:string;
 refocusCount:number;steps:BlochProtocolStep[];notes:string[];
}
const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
const format=new Intl.NumberFormat('zh-CN',{maximumSignificantDigits:7,useGrouping:false});
export const protocolTime=(t:number)=>`${format.format(t>=1000?t/1000:t)} ${t>=1000?'µs':'ns'}`;
const near=(a:number,b:number)=>Math.abs(a-b)<=8*Number.EPSILON*Math.max(1,Math.abs(a),Math.abs(b));

/** Display-only protocol. Positions come from the returned model, never a hardware plan. */
export function blochProtocol(result:ScienceResult|undefined):BlochProtocol|null {
 if(!result||result.kind!=='bloch'||!['x','t1','ramsey','echo','cpmg'].includes(result.sequence))return null;
 const trajectory=result.trajectory;
 if(!Array.isArray(trajectory)||!trajectory.length)return null;
 const duration=trajectory[trajectory.length-1].t;
 if(!finite(duration)||duration<0||!trajectory.every((p,i)=>finite(p.t)&&p.t>=0&&p.t<=duration&&(i===0||p.t>=trajectory[i-1].t)))return null;
 if(result.replayScan===true&&(!finite(result.selectedDelayNs)||!near(result.selectedDelayNs,duration)))return null;
 const initial=result.initialState;
 if(!['0','1','+x','+y'].includes(initial))return null;
 const count=result.sequence==='echo'?1:result.sequence==='cpmg'?result.metrics?.piPulses:0;
 if(typeof count!=='number'||!Number.isInteger(count)||count<0||count>64||(result.sequence==='cpmg'&&count<1))return null;
 const segments=result.segments??[];
 if(segments.length!==count||segments.some((s,i)=>!finite(s.startNs)||!finite(s.endNs)||!near(s.startNs,s.endNs)||s.startNs<0||s.endNs>duration||(i>0&&s.startNs<segments[i-1].startNs)))return null;
 const transverse=['ramsey','echo','cpmg'].includes(result.sequence);
 const expected=result.sequence==='t1'?'1':transverse?'+x':'0';
 const state=initial==='0'?'|0⟩':initial==='1'?'|1⟩':initial;
 let preparation=`设定模型初态 ${state}`;
 if(initial===expected&&result.sequence==='t1')preparation='制备 |1⟩：π（协议示意）';
 if(initial===expected&&transverse)preparation='制备 π/2（协议示意）';
 const steps:BlochProtocolStep[]=[{id:'preparation',kind:'preparation',label:preparation,timing:'演化前 · 顺序标记'}];
 const notes=[transverse?'坐标为制备后的累计等待时间；制备折入模型初态，末端分析折入读出投影。':result.sequence==='x'?'坐标为从模型初态开始的驱动时间；末端展示 Z 投影。':'坐标为制备后的累计等待时间；制备折入模型初态。',
  '前后步骤仅示顺序，未给出绝对触发时间、实际脉宽或 ADC 窗口；不代表真实执行或片端实测时序。'];
 if(initial!==expected)notes.unshift('当前使用所选模型初态，未推定与标准协议相同的实际制备脉冲。');
 if(transverse)notes.push('横向初态采用模型坐标；π/2 示意不推定真实制备旋转轴。中间 π 使用结果中的模型轴与中心。');
 if(result.sequence==='x'){
  steps.push({id:'drive',kind:'drive',label:'驱动演化（模型）',startNs:0,endNs:duration,timing:`0–${protocolTime(duration)}`});
 }else{
  let previous=0,waitIndex=0;
  const wait=(end:number)=>{if(end>previous||duration===0&&waitIndex===0){
   steps.push({id:`wait-${++waitIndex}`,kind:'wait',label:duration===0?'等待 0 ns（无自由演化）':'自由等待',startNs:previous,endNs:end,timing:`${protocolTime(previous)} → ${protocolTime(end)}`});
  }previous=end;};
  for(const [i,s] of segments.entries()){
   wait(s.startNs);
   steps.push({id:`refocus-${i+1}`,kind:'refocus',label:`理想重聚焦 π ${i+1}${['x','y'].includes(result.refocusAxis)?`（模型 ${result.refocusAxis.toUpperCase()} 轴）`:''}`,
    startNs:s.startNs,endNs:s.endNs,timing:`中心 ${protocolTime(s.startNs)} · 瞬时模型`});
  }
  wait(duration);
 }
 if(transverse)steps.push({id:'analysis',kind:'analysis',label:'分析 π/2（协议示意）',timing:'等待后 · 读出前'});
 steps.push({id:'readout',kind:'readout',label:result.sequence==='x'?'末端 Z 投影（读出示意）':'末端读出（示意）',timing:'演化末端 · 顺序标记'});
 if(duration===0)notes.push('零等待仍按编号显示完整步骤；相同的 0 ns 坐标不表示这些动作同时触发。');
 return {durationNs:duration,scan:result.replayScan===true,zeroWait:duration===0,initialState:initial,refocusCount:count,steps,notes};
}

/** The cursor locates model evolution, not the unknown duration of conceptual preparation/readout. */
export function activeProtocolStep(protocol:BlochProtocol,t:unknown):string|undefined {
 if(!finite(t)||protocol.zeroWait||t<0||t>protocol.durationNs)return undefined;
 if(near(t,protocol.durationNs))return 'readout';
 const pulse=protocol.steps.find(s=>s.kind==='refocus'&&s.startNs!==undefined&&near(t,s.startNs));
 if(pulse)return pulse.id;
 return protocol.steps.find(s=>(s.kind==='wait'||s.kind==='drive')&&s.startNs!==undefined&&s.endNs!==undefined&&t>=s.startNs&&t<s.endNs)?.id;
}
