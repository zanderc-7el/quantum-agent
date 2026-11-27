import type { Catalog, ParameterBounds } from '../types.ts';
import { buildManualParameterFields, parseDisplayParameter, validateScanStep } from './manualParameters.ts';
import { selectableResources } from './workflowResources.ts';
import type { Qubit } from './workspaceGateway.ts';
import type { ScienceManualContext } from './scienceGateway.ts';
export function workingPointBlockReason(catalog?:Catalog,point?:ScienceManualContext):string|undefined {
  if(!point)return undefined;
  const procedures=(catalog?.capabilities as {procedures?:Record<string,boolean|{available?:boolean;supported?:boolean}>})?.procedures;
  const capability=procedures?.ro_s21_flux;
  const supported=capability===true||(typeof capability==='object'&&(capability.available===true||capability.supported===true));
  return supported?'当前手动执行接口尚未声明偏置施加与同点复测契约；点位可查看，暂不能下发。':'偏置通道尚未接入，暂不能施加或复测此点';
}
export function manualResources(catalog?:Catalog,chip?:{qubits?:Qubit[]}) {
  return chip ? (chip.qubits??[]).map(q=>({id:q.id,label:q.label||q.id})) : selectableResources(catalog,'real');
}
export interface ManualMethod { procedureId:string; label:string; capability:string; dependsOn?:string[]; parameterBounds?:ParameterBounds; }
export function catalogManualMethods(catalog?:Catalog):ManualMethod[] {
  // Blueprint owners describe orchestration, not executable methods. Only the
  // server's actual full single-qubit graph can advertise creation candidates.
  return (catalog as (Catalog & {manualMethods?:ManualMethod[]})|undefined)?.manualMethods??[];
}
export function manualScanKeys(procedureId: string): string[] {
  if(procedureId==='sq_rabi_amplitude')return ['amp_start','amp_stop','drive_length_s','shots'];
  if(['sq_rabi_time','sq_rabi_frequency','sq_t1','sq_ramsey','sq_echo','sq_cpmg'].includes(procedureId))return ['start_s','stop_s',...(procedureId.startsWith('sq_rabi_')?['drive_amp']:[]),'shots'];
  return [];
}
export function manualCreationParameters(procedureId:string, raw:Record<string,string>, bounds:ParameterBounds={}) {
 const keys=manualScanKeys(procedureId);if(!keys.length)return {};
 const fields=buildManualParameterFields(bounds,keys);
 if(fields.length!==keys.length)throw new Error('当前目录未提供该方法完整参数范围，请刷新配置后重试');
 const start=fields.find(f=>f.key===keys[0])!,stop=fields.find(f=>f.key===keys[1])!;
 if(start.exponent!==stop.exponent)throw new Error('扫描端点单位不一致');
 const pointField=buildManualParameterFields(bounds,['points'])[0];
 if(!pointField)throw new Error('当前目录未提供扫描点数限制');
 const scan=validateScanStep(raw[start.key]??'',raw[stop.key]??'',raw.scan_step??'',Math.min(pointField.rule.max,10000));
 const scalar=(text:string,field:typeof start)=>{const value=parseDisplayParameter(text,field);if(typeof value!=='number')throw new Error('此方法需要标量扫描参数');return value;};
 const parameters:Record<string,number>={[start.key]:scalar(raw[start.key]??'',start),[stop.key]:scalar(raw[stop.key]??'',stop),points:scalar(String(scan.points),pointField)};
 for(const field of fields.filter(f=>f.key!==start.key&&f.key!==stop.key))parameters[field.key]=scalar(raw[field.key]??'',field);
 return parameters;
}
