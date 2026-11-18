import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
export const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
// Ziyor original full-ai-model-runner.ts and model-bridge.ts use 4096 by
// default and cap client requests at 32768. This is not a server-side promise.
export const CLIENT_MAX_OUTPUT_TOKENS=32768;
export function outputBudget(value,fallback){
  const parsed=typeof value==='string'&&/^\d+$/.test(value.trim())?Number(value):value;
  return Number.isSafeInteger(parsed)&&parsed>=1&&parsed<=CLIENT_MAX_OUTPUT_TOKENS?parsed:fallback;
}
export function resolveOutputBudget(config){
  const initial=outputBudget(config.maxOutputTokens,4096),retry=outputBudget(config.retryMaxOutputTokens,8192);
  const retryEnabled=config.protocol==='anthropic_messages'&&config.outputBudgetRetry!==false&&retry>initial;
  return {initialMaxTokens:initial,retryMaxTokens:retryEnabled?retry:null,clientMaxTokens:CLIENT_MAX_OUTPUT_TOKENS,
    retryLimit:retryEnabled?1:0,maxHttpRequests:retryEnabled?2:1,scope:'one_provider_completion',limitSource:'ziyor_original_client_policy'};
}
export function loadConfig(env=process.env, envPath=path.join(ROOT,'local.env')) {
  const values={};
  if(fs.existsSync(envPath)) for(const line of fs.readFileSync(envPath,'utf8').split(/\r?\n/)) {
    const m=line.match(/^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$/); if(m) values[m[1]]=m[2].replace(/^(['"])(.*)\1$/,'$2');
  }
  const e={...values,...env};
  const protocol=e.QCAL_MODEL_PROTOCOL || (e.ANTHROPIC_API_KEY?'anthropic_messages':e.OPENAI_API_KEY?'openai_responses':'');
  const anthropic=protocol==='anthropic_messages';
  const apiKey=e.QCAL_MODEL_API_KEY||(anthropic?e.ANTHROPIC_API_KEY:e.OPENAI_API_KEY)||'';
  const baseUrl=e.QCAL_MODEL_BASE_URL||(anthropic?e.ANTHROPIC_BASE_URL:e.OPENAI_BASE_URL)||(anthropic?'https://api.anthropic.com/v1':'https://api.openai.com/v1');
  const model=e.QCAL_MODEL||(anthropic?e.ANTHROPIC_MODEL:e.OPENAI_MODEL)||'';
  const allowed=['codex_cli','anthropic_messages','openai_chat_completions','openai_responses'];
  const configured=allowed.includes(protocol)&&(protocol==='codex_cli'||Boolean(model&&(apiKey||e.QCAL_MODEL_ALLOW_NO_KEY==='true')));
  return {protocol,apiKey,baseUrl,model,configured,maxOutputTokens:outputBudget(e.QCAL_MODEL_MAX_OUTPUT_TOKENS,4096),
    retryMaxOutputTokens:outputBudget(e.QCAL_MODEL_RETRY_MAX_OUTPUT_TOKENS,8192),source:e.QCAL_MODEL_PROTOCOL?'local.env/environment':protocol?'environment':'none',
    cliPath:e.QCAL_CODEX_PATH||'codex',cliProxy:e.QCAL_CODEX_PROXY||'',port:Number(e.QCAL_AGENT_PORT||8766),timeoutMs:Math.min(180000,Math.max(1000,Number(e.QCAL_MODEL_TIMEOUT_MS||90000))),
    dbPath:e.QCAL_AGENT_DB||path.join(ROOT,'runtime','agent.sqlite')};
}
export function publicConfig(c){return {status:c.configured?'ready':'waiting_model',configured:c.configured,provider:c.providerId||c.protocol||null,model:c.model||(c.protocol==='codex_cli'?'codex-default':null),source:c.source,
  ...(['anthropic_messages','openai_responses'].includes(c.protocol)?{outputBudget:resolveOutputBudget(c)}:{})};}
