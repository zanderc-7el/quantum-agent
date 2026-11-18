import http from 'node:http';
import {pathToFileURL} from 'node:url';
import {loadConfig} from './config.mjs';
import {Store} from './store.mjs';
import {Brain} from './brain.mjs';
import {AccountService} from './account/account.mjs';
function accountStatus(result){
  if(result.status!=='error')return 200;
  const code=result.error?.code||'';const upstream=Number(/^ziyor_http_(\d+)$/.exec(code)?.[1]);
  if([400,401,402,403,404,409,429,500,502,503,504].includes(upstream))return upstream;
  if(code==='sms_cooldown')return 429;
  if(code.includes('conflict')||code.includes('unknown')||code==='account_busy')return 409;
  if(code==='ziyor_login_required')return 401;
  if(code.includes('unavailable')||code.includes('storage')||code.includes('locked'))return 503;
  return 400;
}
export function createServer(brain,account=null){return http.createServer(async(req,res)=>{
  const send=(status,data)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
  // Internal service: browsers must use the Python same-origin gateway.
  if(req.headers.origin||!['127.0.0.1','::1','::ffff:127.0.0.1'].includes(req.socket.remoteAddress)){send(403,{error:{code:'internal_only',message:'仅允许本机控制器访问'}});return;}
  try{const url=new URL(req.url,'http://127.0.0.1');
    if(req.method==='GET'&&url.pathname==='/health'){send(200,brain.health());return;}
    if(req.method==='GET'&&url.pathname==='/account/status'){send(200,account?account.status():{provider:'ziyor',configured:false,authenticated:false,error:{code:'account_unavailable',message:'账户服务未加载'}});return;}
    if(req.method==='GET'&&url.pathname==='/account/models'){send(200,account?await account.models():{models:[],error:{code:'account_unavailable',message:'账户服务未加载'}});return;}
    if(req.method==='GET'&&url.pathname==='/events'){const id=url.searchParams.get('sessionId');const after=Number(url.searchParams.get('after')||0);if(!id||!Number.isInteger(after)||after<0)throw new Error('invalid_cursor');send(200,{events:brain.store.events(id,after)});return;}
    const accountAction=/^\/account\/(send-code|login|connect|logout|test)$/.exec(url.pathname)?.[1];
    if(req.method!=='POST'||(!['/chat','/decide'].includes(url.pathname)&&!accountAction)){send(404,{error:{code:'not_found'}});return;}
    if(!String(req.headers['content-type']||'').startsWith('application/json')){send(415,{error:{code:'json_required'}});return;}
    let raw='';for await(const chunk of req){raw+=chunk;if(raw.length>250000){send(413,{error:{code:'body_too_large'}});return;}}
    const input=JSON.parse(raw);
    if(accountAction){if(!account){send(503,{error:{code:'account_unavailable',message:'账户服务未加载'}});return;}const result=await account.operation(accountAction,input);send(accountStatus(result),result);return;}
    send(200,await brain.run(url.pathname.slice(1),input));
  }catch(e){send(400,{error:{code:'invalid_request',message:String(e.message).slice(0,160)}});}
});}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){const config=loadConfig();const store=new Store(config.dbPath);const account=await new AccountService(config).init();const brain=new Brain(config,store);brain.configResolver=()=>account.modelConfig();const server=createServer(brain,account);server.listen(config.port,'127.0.0.1',()=>console.log(JSON.stringify({listening:`127.0.0.1:${config.port}`,...brain.health()})));for(const event of ['SIGINT','SIGTERM'])process.on(event,()=>server.close(()=>{store.close();process.exit(0);}));}
