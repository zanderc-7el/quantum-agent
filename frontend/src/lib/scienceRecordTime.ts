function stamp(value:unknown):string|undefined{
 if(typeof value!=='string'||!value)return undefined;
 const time=new Date(value);return Number.isFinite(time.getTime())?time.toLocaleString('zh-CN'):value;
}
export function recordStoredTime(record:{kind:string;createdAt?:string}):string{
 return `${record.kind==='measurement'?'归档于':'保存于'} ${stamp(record.createdAt)??'时间未记录'}`;
}
export function acquiredTime(result:Record<string,unknown>):string|undefined{
 const time=stamp(result.acquiredAt);return time?`原始采集时间 ${time}`:undefined;
}
