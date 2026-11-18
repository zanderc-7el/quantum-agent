// Foreground, one-request diagnosis. This never stops/replaces the live service.
import {DatabaseSync} from 'node:sqlite';
import {pathToFileURL} from 'node:url';
import {loadConfig,resolveOutputBudget} from '../agent/src/config.mjs';
import {AccountService} from '../agent/src/account/account.mjs';
import {Brain} from '../agent/src/brain.mjs';
import {Store} from '../agent/src/store.mjs';
import {complete,safeProviderDiagnostic} from '../agent/src/provider.mjs';
import {validateDecisionInput} from '../agent/src/validation.mjs';

const SCHEMA='qcal-model-response-probe/1';
const safeCodes=new Set(['provider_empty_text','provider_output_limit_without_text','provider_no_final_text',
  'provider_error_envelope','provider_invalid_json','model_output_too_large','model_invalid_action',
  'model_invalid_decision','model_round_limit','model_tool_round_limit','model_configuration_changed',
  'model_total_timeout','model_timeout','model_request_failed','model_probe_single_request_limit','waiting_model',
  'probe_arguments_invalid','probe_session_invalid','probe_decide_input_missing','probe_decide_input_invalid',
  'probe_membership_unavailable','probe_failed']);
export function safeProbeCode(value){return safeCodes.has(value)||/^provider_http_[1-5][0-9]{2}$/.test(value||'')?value:'probe_failed';}

export function readLatestDecisionInput(dbPath,sessionId){
  if(typeof sessionId!=='string'||!/^[a-zA-Z0-9_-]{1,120}$/.test(sessionId))throw new Error('probe_session_invalid');
  const db=new DatabaseSync(dbPath,{readOnly:true});
  try{
    db.exec('PRAGMA query_only=ON');db.exec('BEGIN');
    const started=db.prepare("SELECT seq,payload,created_at FROM model_events WHERE session_id=? AND event_type='runtime_started' ORDER BY seq DESC").all(sessionId)
      .find(row=>{try{return JSON.parse(row.payload).kind==='decide';}catch{return false;}});
    if(!started)throw new Error('probe_decide_input_missing');
    const row=db.prepare("SELECT content FROM messages WHERE session_id=? AND role='user' AND created_at<=? ORDER BY rowid DESC LIMIT 1").get(sessionId,started.created_at);
    if(!row)throw new Error('probe_decide_input_missing');
    let input;try{input=JSON.parse(row.content);validateDecisionInput(input);}catch{throw new Error('probe_decide_input_invalid');}
    return {input,source:{sessionId,eventSeq:started.seq,kind:'decide',inputChars:row.content.length}};
  }finally{db.close();}
}

export async function probeOnce(config,input,provider=complete){
  if(config.providerId!=='ziyor'||config.protocol!=='anthropic_messages'||!config.configured)
    throw new Error('probe_membership_unavailable');
  validateDecisionInput(input);
  const timeoutMs=Math.min(180000,Math.max(1000,Number(config.timeoutMs)||90000));
  const store=new Store(':memory:');
  let requests=0,hasText=false,textChars=0,diagnostic;
  const once=async(c,messages)=>{
    if(requests>=1)throw new Error('model_probe_single_request_limit');
    requests++;
    try{const text=await provider({...c,timeoutMs,outputBudgetRetry:false},messages);hasText=typeof text==='string'&&Boolean(text.trim());textChars=typeof text==='string'?text.length:0;return text;}
    catch(error){diagnostic=safeProviderDiagnostic(error?.providerDiagnostic);throw error;}
  };
  try{
    // Brain builds the same decide prompt and validates final actions. Its tools
    // are read-only context tools; a second provider turn is never sent.
    const result=await new Brain({...config,timeoutMs,outputBudgetRetry:false},store,once).run('decide',input);
    return {schema:SCHEMA,mode:'execute',maxRequests:1,maxHttpRequests:1,requests,timeoutMs,
      maxOutputTokens:resolveOutputBudget({...config,outputBudgetRetry:false}).initialMaxTokens,
      provider:{protocol:'anthropic_messages',membership:true},hasText,textChars,
      status:result.status==='ok'?'ok':'waiting_model',decisionValidated:result.status==='ok'&&Boolean(result.decision),
      ...(result.error?{code:safeProbeCode(result.error.code)}:{}),...(diagnostic?{providerDiagnostic:diagnostic}:{})};
  }finally{store.close();}
}

export function parseProbeArgs(args){
  let sessionId,execute=false;
  for(let i=0;i<args.length;i++){
    if(args[i]==='--session'&&sessionId===undefined){sessionId=args[++i];}
    else if(args[i]==='--execute'&&!execute){execute=true;}
    else throw new Error('probe_arguments_invalid');
  }
  if(typeof sessionId!=='string'||!/^[a-zA-Z0-9_-]{1,120}$/.test(sessionId))throw new Error('probe_session_invalid');
  return {sessionId,execute};
}

export async function main(args=process.argv.slice(2)){
  const options=parseProbeArgs(args),base=loadConfig();
  const {input,source}=readLatestDecisionInput(base.dbPath,options.sessionId);
  if(!options.execute)return {schema:SCHEMA,mode:'preview',maxRequests:1,requests:0,source,
    note:'Add --execute to send at most one foreground membership request; the live service and hardware continue unchanged.'};
  // init only reads the logged-in account using its normal secure-storage path.
  // Do not call models/login/test/save or expose account state/credentials.
  const account=await new AccountService(base).init();
  return {...await probeOnce(account.modelConfig(),input),source};
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  try{console.log(JSON.stringify(await main()));}
  catch(error){console.log(JSON.stringify({schema:SCHEMA,status:'error',code:safeProbeCode(error?.message)}));process.exitCode=1;}
}
