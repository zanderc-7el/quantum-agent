import {createHash} from 'node:crypto';
import path from 'node:path';
import {ROOT,publicConfig} from '../config.mjs';
import {AccountVault} from './vault.mjs';
export const OFFICIAL_ORIGIN='https://skill.zeelin.cn';
export const EXISTING_PROVIDER='__existing_provider__';
const hash=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
const initial=()=>({version:1,selected:false,credential:null,authenticated:false,user:null,balance:null,model:null,verified:false,models:[],operations:{},cooldownUntil:0});
class AccountError extends Error{constructor(code,message){super(message);this.code=code;}}
const fail=(code,message)=>{throw new AccountError(code,message);};
function phone(value){if(typeof value!=='string'||!/^1[3-9]\d{9}$/.test(value.trim()))fail('invalid_phone','请输入有效手机号');return value.trim();}
function cleanText(v,key=''){const text=typeof v==='string'?v.slice(0,100):'';return key&&text.includes(key)?'Ziyor 用户':text;}
export function normalizeModels(response){
  const raw=response?.data&&typeof response.data==='object'?response.data:response;
  const provider=raw?.providers?.find(p=>p.providerId==='zeelin'&&p.enabled===true);
  if(!provider)return [];
  const channels=provider.billingChannels||[{id:'token',endpointKey:'zeelin_token',defaultModelId:provider.defaultModelId,models:provider.models}];
  const channel=channels.find(c=>c.id==='token'&&c.endpointKey==='zeelin_token');if(!channel)return [];
  const seen=new Set();const models=(channel.models||[]).filter(m=>m.enabled===true&&typeof m.id==='string'&&typeof m.label==='string'&&!seen.has(m.id)&&seen.add(m.id)).map(m=>({id:m.id.slice(0,200),label:m.label.slice(0,200),provider:'ziyor',upstreamName:(m.upstreamName||m.id).slice(0,200),default:m.id===channel.defaultModelId}));
  if(models.length&&!models.some(m=>m.default))models[0].default=true;return models;
}
export class AccountService {
  constructor(baseConfig,{vault=new AccountVault(path.join(ROOT,'runtime','account.sealed')),origin=OFFICIAL_ORIGIN,fetchImpl=fetch}={}){
    const url=new URL(origin);if(origin!==OFFICIAL_ORIGIN&&!(url.protocol==='http:'&&['127.0.0.1','localhost'].includes(url.hostname)))throw new Error('account_origin_forbidden');
    this.baseConfig={...baseConfig};this.vault=vault;this.origin=origin;this.fetch=fetchImpl;this.state=initial();this.busy=false;this.generation=0;this.loadError=null;this.connectionState=structuredClone(this.state);this.switching=false;this.connectionAbort=new AbortController();
  }
  async init(){try{const saved=await this.vault.read();if(saved){if(saved.version!==1||typeof saved.selected!=='boolean'||!saved.operations||typeof saved.operations!=='object'||Array.isArray(saved.operations))throw new Error();this.state={...initial(),...saved};}}catch{this.state.selected=true;this.loadError={code:'account_vault_locked',message:'本机账户密钥无法解密，请重新登录或明确选择其他模型连接'};}this.connectionState=structuredClone(this.state);return this;}
  status(s=this.connectionState,ready=!this.switching){const base=publicConfig(this.baseConfig);return {provider:'ziyor',configured:Boolean(s.credential),authenticated:Boolean(s.credential&&s.authenticated),...(s.user?{user:s.user}:{}),...(s.balance?{balance:s.balance}:{}),modelConnection:{provider:s.selected?'ziyor':base.provider,model:s.selected?s.model?.id||null:base.model,active:ready&&(s.selected?Boolean(s.credential&&s.authenticated&&s.model):Boolean(this.baseConfig.configured)),verified:s.selected?Boolean(s.verified):false,selection:s.selected?'ziyor':'existing',note:s.selected?'Ziyor 官方账户；余额为积分，不推断会员期限':'原有独立模型连接，与 Ziyor 账户无关'},capabilities:{sendCode:true,login:true,connect:true,test:ready&&Boolean(s.credential&&s.authenticated&&s.model&&s.selected)},...(this.loadError?{error:this.loadError}:{})};}
  modelConfig(){const s=this.connectionState,revision={accountGeneration:this.generation,accountSignal:this.connectionAbort.signal};if(!s.selected)return {...this.baseConfig,configured:!this.switching&&this.baseConfig.configured,...revision};return {...this.baseConfig,protocol:'anthropic_messages',providerId:'ziyor',apiKey:!this.switching?s.credential||'':'',baseUrl:this.origin+'/api/v3/anthropic/v1',model:s.model?.upstreamName||'',configured:!this.switching&&Boolean(s.credential&&s.authenticated&&s.model),source:'ziyor-account',...revision};}
  beginConnectionChange(){this.switching=true;this.generation++;this.connectionAbort.abort(new Error('model_configuration_changed'));this.connectionAbort=new AbortController();}
  async save(){await this.vault.write(this.state);this.persisted=structuredClone(this.state);}
  async request(endpoint,body,headers={},timeout=15000){
    let res;try{res=await this.fetch(this.origin+endpoint,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json',...headers},...(body===undefined?{}:{body:JSON.stringify(body)}),redirect:'error',signal:AbortSignal.timeout(timeout)});}catch{fail('ziyor_network_unavailable','无法连接 Ziyor 官方服务，请检查网络后重试');}
    if(!res.ok)fail('ziyor_http_'+res.status, res.status===401||res.status===403?'Ziyor 认证或权限校验失败':res.status===402?'Ziyor 积分不足或套餐限制，请在官方账户查看':'Ziyor 官方服务暂未完成请求');
    let data;try{const text=await res.text();if(text.length>1_000_000)throw new Error();data=JSON.parse(text);}catch{fail('ziyor_invalid_response','Ziyor 返回了无法识别的响应');}
    if(data.code!==undefined&&Number(data.code)!==200)fail('ziyor_request_rejected','Ziyor 未接受请求，请检查手机号、验证码或账户状态');return data;
  }
  async models(){let error;try{const data=await this.request('/api/v3/models_v2');const models=normalizeModels(data);if(!models.length)fail('ziyor_catalog_empty','官方目录未提供可用的 Ziyor 模型');this.state.models=models;}catch(e){error={code:e.code||'ziyor_catalog_unavailable',message:e instanceof AccountError?e.message:'模型目录暂不可用'};}
    return {models:[...this.state.models,...(this.baseConfig.configured?[{id:EXISTING_PROVIDER,label:'原有独立模型连接（'+(publicConfig(this.baseConfig).provider||'未配置')+'）',provider:'existing'}]:[])],source:error?'cached':'official',...(error?{error}:{}),defaultModelId:this.state.models.find(m=>m.default)?.id||null};}
  async userInfo(key,providedPhone){const data=await this.request('/api/zeelin/user_info',{app_key:key});const info=data.data;if(!info||typeof info!=='object')fail('ziyor_profile_unavailable','登录已响应，但账户资料无法确认');const p=String(info.mobile||providedPhone||'');this.state.user={name:cleanText(info.nickname||info.username,key)||'Ziyor 用户',phoneMasked:p.replace(/^(\d{3})\d{4}(\d{4})$/,'$1****$2')};if(!/^\d{3}\*{4}\d{4}$/.test(this.state.user.phoneMasked))this.state.user.phoneMasked='未提供';this.state.balance=Number.isFinite(info.remain_calls)?{remainingCalls:info.remain_calls,label:'积分'}:null;}
  async operation(name,input){
    if(!input||typeof input!=='object'||typeof input.operationId!=='string'||!/^[\w.:-]{1,160}$/.test(input.operationId))return {...this.status(),status:'error',error:{code:'operation_id_required',message:'请求需要唯一 operationId'}};
    const permitted={ 'send-code':['phone','operationId'],login:['phone','code','operationId','useForAgent'],connect:['model','operationId'],logout:['operationId'],test:['operationId']}[name];
    if(!permitted||Object.keys(input).some(k=>!permitted.includes(k)))return {...this.status(),status:'error',error:{code:'invalid_account_request',message:'账户请求包含未知字段'}};
    const fingerprint=hash({name,...input});const prior=this.state.operations[input.operationId];
    if(prior){if(prior.fingerprint!==fingerprint)return {...this.status(),status:'error',error:{code:'idempotency_conflict',message:'同一操作编号不能用于不同请求'}};return prior.response||{...this.status(),status:'error',error:{code:'account_outcome_unknown',message:'该请求已提交但结果未确认，请先检查状态，避免重复操作'}};}
    if(this.busy)return {...this.status(),status:'error',error:{code:'account_busy',message:'另一个账户请求正在处理'}};
    this.busy=true;this.persisted=structuredClone(this.state);
    try{
      if(name==='send-code'||name==='login')phone(input.phone);
      if(name==='login'&&(typeof input.code!=='string'||!/^\d{4,8}$/.test(input.code.trim())||(input.useForAgent!==undefined&&typeof input.useForAgent!=='boolean')))fail('invalid_login','请输入有效验证码');
      if(name==='send-code'&&Date.now()<this.state.cooldownUntil)fail('sms_cooldown','验证码请求过于频繁，请稍后再试');
      this.state.operations[input.operationId]={fingerprint};
      if(name==='send-code')this.state.cooldownUntil=Date.now()+60000;
      if(name==='test')this.state.verified=false;
      await this.save();
      if(name==='test')this.connectionState=structuredClone(this.state);
      if(['login','connect','logout'].includes(name))this.beginConnectionChange();
      let extra={};
      if(name==='send-code'){await this.request('/api/zeelin/send_sms',{mobile:phone(input.phone)});extra={status:'sent',retryAfter:60};}
      if(name==='login'){
        const data=await this.request('/api/zeelin/login',{mobile:phone(input.phone),code:input.code.trim()});
        const key=data?.data?.app_key;if(typeof key!=='string'||key.length<8||key.length>8192)fail('ziyor_login_missing_key','官方登录响应缺少有效账户凭据');
        await this.userInfo(key,input.phone);
        this.state.credential=key;this.state.authenticated=true;this.state.selected=input.useForAgent!==false;this.state.verified=false;this.loadError=null;
        const catalog=await this.models();this.state.model=this.state.models.find(m=>m.default)||this.state.models[0]||null;
        if(catalog.error)extra={error:catalog.error};
      }
      if(name==='connect'){
        if(input.model===EXISTING_PROVIDER){if(!this.baseConfig.configured)fail('existing_provider_unavailable','原有独立模型尚未配置');this.state.selected=false;}
        else {if(!this.state.credential||!this.state.authenticated)fail('ziyor_login_required','请先登录 Ziyor');await this.models();const model=this.state.models.find(m=>m.id===input.model);if(!model)fail('ziyor_model_unavailable','请选择官方目录中已启用的模型');this.state.model=model;this.state.selected=true;this.state.verified=false;}
        this.loadError=null;
      }
      if(name==='logout'){this.state.credential=null;this.state.authenticated=false;this.state.user=null;this.state.balance=null;this.state.verified=false;this.loadError=null;}
      if(name==='test'){
        if(!this.state.credential||!this.state.authenticated||!this.state.selected||!this.state.model)fail('ziyor_login_required','请先登录并选择 Ziyor 模型');
        await this.request('/api/v3/anthropic/v1/messages',{model:this.state.model.upstreamName,max_tokens:1,messages:[{role:'user',content:'hi'}]},{'x-api-key':this.state.credential,'anthropic-version':'2023-06-01'},60000);
        this.state.verified=true;try{await this.userInfo(this.state.credential);}catch{}extra={status:'verified'};
      }
      const response={...this.status(this.state,true),status:'ok',...extra};this.state.operations[input.operationId]={fingerprint,response};
      // Persist only bounded public operation receipts, never phone/code payloads.
      const ids=Object.keys(this.state.operations);for(const id of ids.slice(0,-100))delete this.state.operations[id];
      await this.save();this.connectionState=structuredClone(this.state);this.switching=false;return response;
    }catch(e){this.state=structuredClone(this.persisted);this.connectionState=structuredClone(this.state);this.switching=false;return {...this.status(),status:'error',error:{code:e.code||'account_storage_unavailable',message:e instanceof AccountError?e.message:'本机账户安全存储不可用，未完成操作'}};}
    finally{this.switching=false;this.busy=false;}
  }
}
