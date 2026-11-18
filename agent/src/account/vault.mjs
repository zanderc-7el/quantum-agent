import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const script=fileURLToPath(new URL('./dpapi.ps1',import.meta.url));
export function dpapi(operation,value){
  if(process.platform!=='win32')return Promise.reject(new Error('account_dpapi_requires_windows'));
  return new Promise((resolve,reject)=>{
    const child=spawn('powershell.exe',['-NoProfile','-NonInteractive','-File',script],{windowsHide:true,stdio:['pipe','pipe','pipe']});
    let output='',settled=false;const end=(error,result)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(new Error(error)):resolve(result);};
    const timer=setTimeout(()=>{child.kill();end('account_protection_timeout');},15000);
    child.on('error',()=>end('account_protection_unavailable'));child.stderr.resume();
    child.stdout.on('data',b=>{output+=b;if(output.length>2_000_000){child.kill();end('account_protection_output_invalid');}});
    child.on('close',code=>{try{const result=JSON.parse(output.replace(/^\uFEFF/,''));if(code!==0||typeof result.value!=='string')throw new Error();end(null,result.value);}catch{end('account_protection_failed');}});
    child.stdin.on('error',()=>{});child.stdin.end(JSON.stringify({operation,value}));
  });
}
export class AccountVault {
  constructor(file){this.file=file;}
  async read(){let raw;try{raw=await fs.readFile(this.file,'utf8');}catch(e){if(e.code==='ENOENT')return null;throw new Error('account_vault_read_failed');}try{const envelope=JSON.parse(raw);if(envelope.version!==1||typeof envelope.dpapi!=='string')throw new Error();return JSON.parse(await dpapi('unseal',envelope.dpapi));}catch{throw new Error('account_vault_locked');}}
  async write(state){const encrypted=await dpapi('seal',JSON.stringify(state));await fs.mkdir(path.dirname(this.file),{recursive:true});const temp=this.file+'.tmp';await fs.writeFile(temp,JSON.stringify({version:1,protection:'DPAPI-CurrentUser',dpapi:encrypted}),{encoding:'utf8',mode:0o600});await fs.rename(temp,this.file);}
}
