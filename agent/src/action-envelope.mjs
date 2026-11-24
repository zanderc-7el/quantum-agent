// Compatibility for one observed model-generated TEXT envelope. This is not
// a provider tool-use API and grants no tools beyond the existing allowlist.
import {createHash} from 'node:crypto';
import {parseLocalAgentAction} from '../../vendor/ziyor/upstream/src/feature/local-model/domain/local-agent-protocol.ts';
import {validateInput,record} from './validation.mjs';
import {isDeepStrictEqual} from 'node:util';
import {workspaceReadDefinitions} from './read-tool-definitions.mjs';

export const TEXT_TOOL_ENVELOPE_VERSION='single-closed-tool-call-text/1';
export const READONLY_MIRROR_VERSION='identical-readonly-json-dsml-mirror/1';
const readDefinitions=new Map(workspaceReadDefinitions.map(d=>[d.name,d]));
const allowedTools=new Set(['list_rules','inspect_observation','validate_proposal','audit_reader_text','list_workspace','list_documents','read_document','create_campaign','campaign_action','analyze_import','start_measurement','workflow_action','search_source','read_source','list_source_files','inspect_runtime','inspect_workflow','inspect_configuration','inspect_report','inspect_report_export','export_report','save_chip_draft','save_deployment_draft','publish_configuration']);
const failure=()=>({ok:false,error:'Exactly one complete action envelope is required',
  repairPrompt:'每轮仅输出一个完整的 final 或 tool_call JSON；不能串联多个动作。'});

function wholeStandardJson(raw){
  const value=raw.trim();
  const fence=value.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  try{JSON.parse((fence?.[1]||value).trim());return true;}catch{return false;}
}

function uniqueKeyJson(text){
  const value=JSON.parse(text),stack=[];
  // JSON.parse validates grammar first. Scan its string tokens only to reject
  // duplicate decoded object keys, including escaped-key aliases.
  for(let i=0;i<text.length;i++){
    const c=text[i];
    if(c==='{'||c==='['){stack.push(c==='{'?new Set():null);continue;}
    if(c==='}'||c===']'){stack.pop();continue;}
    if(c!=='"')continue;
    const start=i++;
    while(i<text.length){if(text[i]==='\\'){i+=2;continue;}if(text[i]==='"')break;i++;}
    let next=i+1;while(/[ \t\r\n]/.test(text[next]??''))next++;
    if(text[next]===':'){
      const key=JSON.parse(text.slice(start,i+1)),keys=stack.at(-1);
      if(!keys||keys.has(key))throw new Error('duplicate_json_key');keys.add(key);
    }
  }
  return value;
}

function readAction(value){
  validateInput(value);
  const parsed=parseLocalAgentAction(JSON.stringify(value));
  if(!parsed.ok||parsed.action.type!=='tool_call')throw new Error('not_read_action');
  const action=parsed.action,definition=readDefinitions.get(action.tool);
  if(!definition||!record(action.arguments))throw new Error('not_read_tool');
  const schema=definition.inputSchema;
  if((schema.required??[]).some(k=>!Object.hasOwn(action.arguments,k)))throw new Error('missing_read_argument');
  for(const [key,item] of Object.entries(action.arguments)){
    const rule=schema.properties[key];
    if(!Object.hasOwn(schema.properties,key)||typeof item!==rule.type||
       (rule.type==='number'&&!Number.isFinite(item))||(rule.enum&&!rule.enum.includes(item)))throw new Error('invalid_read_argument');
  }
  return action;
}

function identicalReadonlyMirror(raw){
  // No XML parser, entity decoding, prefix extraction, or receipt handling.
  // Both complete representations must validate independently and agree.
  const marker='<｜｜DSML｜｜ calls>',offset=raw.indexOf(marker);
  if(offset<0)return null;
  const primary=readAction(uniqueKeyJson(raw.slice(0,offset).trim()));
  const tail=raw.slice(offset).trim();
  const block=tail.match(/^<｜｜DSML｜｜ calls>[ \t\r\n]*<｜｜DSML｜｜ invoke name="([a-z_]+)">([\s\S]*?)<\/｜｜DSML｜｜ invoke>[ \t\r\n]*<\/｜｜DSML｜｜ calls>$/);
  if(!block)throw new Error('invalid_dsml_mirror');
  const values=Object.create(null);let rest=block[2];
  while(rest.length){
    rest=rest.replace(/^[ \t\r\n]+/,'');
    if(!rest)break;
    const item=rest.match(/^<｜｜DSML｜｜ parameter name="([a-zA-Z_][a-zA-Z0-9_]*)" string="(true|false)">([^<]*)<\/｜｜DSML｜｜ parameter>/);
    if(!item||Object.hasOwn(values,item[1])||['__proto__','prototype','constructor'].includes(item[1]))throw new Error('invalid_dsml_parameter');
    values[item[1]]=item[2]==='true'?item[3]:uniqueKeyJson(item[3]);
    rest=rest.slice(item[0].length);
  }
  const args=Object.hasOwn(values,'arguments')?(Object.keys(values).length===1?values.arguments:null):{...values};
  const duplicate=readAction({type:'tool_call',tool:block[1],arguments:args});
  if(!isDeepStrictEqual(primary,duplicate))throw new Error('different_dsml_mirror');
  const canonicalText=JSON.stringify(primary);
  return {ok:true,action:primary,canonicalText,normalization:{formatVersion:READONLY_MIRROR_VERSION,
    originalPublicTextSha256:createHash('sha256').update(raw).digest('hex'),tool:primary.tool}};
}

export function parseAgentAction(raw){
  if(typeof raw!=='string')return failure();
  // Require a complete document before invoking the upstream schema parser.
  // Its recovery parser extracts the first JSON object and would otherwise
  // silently discard additional actions. Literal JSON/tags inside strings are
  // already handled correctly by JSON.parse and do not represent extra actions.
  if(wholeStandardJson(raw))return parseLocalAgentAction(raw);
  if(raw.length>200000)return failure();
  try{const mirrored=identicalReadonlyMirror(raw);if(mirrored)return mirrored;}catch{return failure();}
  const match=raw.trim().match(/^<tool_call>\s*([\s\S]*?)\s*<\/tool_call>$/);
  if(!match)return failure();
  let value;
  try{
    value=JSON.parse(match[1]);
    if(!record(value)||Object.keys(value).sort().join(',')!=='arguments,name'
        ||!allowedTools.has(value.name)||!record(value.arguments))return failure();
    validateInput(value.arguments); // Same depth/size/unsafe-key boundary.
  }catch{return failure();}
  const canonicalText=JSON.stringify({type:'tool_call',tool:value.name,arguments:value.arguments});
  const parsed=parseLocalAgentAction(canonicalText);
  if(!parsed.ok)return parsed;
  return {...parsed,canonicalText,normalization:{formatVersion:TEXT_TOOL_ENVELOPE_VERSION,
    originalPublicTextSha256:createHash('sha256').update(raw).digest('hex'),tool:value.name}};
}
