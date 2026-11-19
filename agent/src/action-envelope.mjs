// Compatibility for one observed model-generated TEXT envelope. This is not
// a provider tool-use API and grants no tools beyond the existing allowlist.
import {createHash} from 'node:crypto';
import {parseLocalAgentAction} from '../../vendor/ziyor/upstream/src/feature/local-model/domain/local-agent-protocol.ts';
import {validateInput,record} from './validation.mjs';

export const TEXT_TOOL_ENVELOPE_VERSION='single-closed-tool-call-text/1';
const allowedTools=new Set(['list_rules','inspect_observation','validate_proposal','audit_reader_text']);
const failure=()=>({ok:false,error:'Unsupported or malformed tool_call text envelope',
  repairPrompt:'仅输出规定的 final 或 tool_call JSON。'});

function wholeStandardJson(raw){
  const value=raw.trim();
  const fence=value.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  try{JSON.parse((fence?.[1]||value).trim());return true;}catch{return false;}
}

export function parseAgentAction(raw){
  if(typeof raw!=='string')return failure();
  // Preserve the original standard JSON/fence parser, including literal tag
  // strings inside JSON. A bare {name,arguments} object remains unsupported.
  if(wholeStandardJson(raw)||!/<\/?tool_call(?:\s|>|$)/i.test(raw))return parseLocalAgentAction(raw);
  if(raw.length>200000)return failure();
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
