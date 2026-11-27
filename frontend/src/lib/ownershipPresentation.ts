export interface OwnershipCheck {
 schema:'qcal-hardware-ownership-check/1'; stage:'confirmation'|'schedule'|'dispatch';
 checkedAt:string; verified:boolean; outcome:'unverified'|'socket_records'|'lock_records'|'mixed_records'|'unknown_records';
 checkStatus:'completed'|'timeout'|'failed'|'unsupported_platform'|'unconfigured_endpoint'|'invalid_response'|'adapter_error'|'unreported';
 sources:('windows_tcp_table'|'chassis_lock_files'|'adapter_ownership')[];
 ownerCount:number; socketCount:number; lockCount:number; unknownCount:number;
 visiblePids:number[]; pidsTruncated:boolean;
}

const sources={windows_tcp_table:'Windows TCP 连接表',chassis_lock_files:'机箱锁记录',adapter_ownership:'执行器占用检查'};
const stages={confirmation:'确认连接',schedule:'任务调度',dispatch:'采集前检查'};
const status={completed:'完成',timeout:'超时',failed:'失败',unsupported_platform:'不支持当前平台',unconfigured_endpoint:'缺少目标地址',
 invalid_response:'返回数据无效',adapter_error:'执行器出错',unreported:'未记录'};

export function ownershipRows(check?:OwnershipCheck):[string,string][] {
 if(check?.schema!=='qcal-hardware-ownership-check/1')return [];
 const rows:[string,string][]=[];
 if(typeof check.checkedAt==='string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(check.checkedAt)){
  const date=new Date(check.checkedAt);
  if(Number.isFinite(date.getTime()))rows.push(['检查时间',date.toLocaleString('zh-CN',{hour12:false})]);
 }
 if(Object.hasOwn(stages,check.stage))rows.push(['检查阶段',stages[check.stage]]);
 if(Object.hasOwn(status,check.checkStatus))rows.push(['检查结果',status[check.checkStatus]]);
 if(typeof check.verified==='boolean')rows.push(['核查状态',check.verified?'核查完成':'未核实']);
 if(Array.isArray(check.sources)){
  const labels=[...new Set(check.sources.filter(k=>Object.hasOwn(sources,k)).map(k=>sources[k]))];
  if(labels.length)rows.push(['来源',labels.join('、')]);
 }
 const counts:[string,number][]=[['TCP 记录',check.socketCount],['锁记录',check.lockCount],['未知类型',check.unknownCount]];
 const values=counts.filter(([,v])=>Number.isSafeInteger(v)&&v>=0);
 if(values.length)rows.push(['记录数',values.map(([k,v])=>`${k} ${v}`).join('，')]);
 if(Array.isArray(check.visiblePids)){
  const pids=[...new Set(check.visiblePids.filter(pid=>Number.isSafeInteger(pid)&&pid>0))].slice(0,8);
  if(pids.length)rows.push(['可见 PID',pids.join('、')+(check.pidsTruncated===true?'（部分）':'')]);
 }
 return rows;
}
