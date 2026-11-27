import type {Attention} from './workspaceGateway.ts';

/** Display partition only. Preserve server order within each state. */
export function attentionQueueItems(items:Attention[]):Attention[] {
 return [...items.filter(item=>item.state==='open'),...items.filter(item=>item.state==='deferred')];
}
export function attentionQueueState(state:Attention['state']):string {
 return state==='open'?'待处理':state==='deferred'?'稍后处理':'已处理';
}
export function attentionWorkflowLabel(item:Attention,items:Attention[]):string {
 const displayName=(value:Attention)=>value.workflowName?.trim()||'任务名称未记录';
 const identity=(value:Attention)=>value.workflowId||value.id;
 const name=displayName(item);
 const same=items.filter(other=>displayName(other)===name);
 if(new Set(same.map(other=>other.workflowId??other.id)).size<2)return name;
 const withTime=(value:Attention)=>{
  const time=typeof value.workflowCreatedAt==='string'?new Date(value.workflowCreatedAt):undefined;
  if(!time||!Number.isFinite(time.getTime()))return name;
  const label=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(time);
  return `${name} · ${label} 创建`;
 };
 const label=withTime(item);
 const collisions=[...new Set(same.filter(other=>withTime(other)===label).map(identity))];
 if(collisions.length<2)return label;
 const id=identity(item);
 let length=Math.min(6,id.length);
 while(length<id.length && collisions.some(other=>other!==id&&other.slice(-length)===id.slice(-length)))length++;
 return `${label} · 任务号 ${id.slice(-length)}`;
}
export function attentionRowLabel(item:Attention,items:Attention[]):string {
 return `${attentionWorkflowLabel(item,items)} · ${item.title} · ${attentionQueueState(item.state)}`;
}
