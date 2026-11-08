import test from "node:test";
import assert from "node:assert/strict";
import { createServerDemoGateway } from "../src/lib/serverDemoGateway.ts";
const metadata={dataSource:"simulated",hardwareConnected:false};
test("server demo uses same-origin API and never supplies browser ticks",async()=>{
 const calls: {path:string,method?:string,body?:unknown}[]=[];
 const gateway=createServerDemoGateway({fetch:async(path,options)=>{
  calls.push({path:String(path),method:options?.method,body:options?.body});
  return {ok:true,json:async()=>({...metadata,workflows:[],workflow:{id:"demo-id"}})} as Response;
 }});
 assert.deepEqual(await gateway.list(),[]);
 await gateway.act("demo-id","start");await gateway.policy("demo-id","strict");
 assert.deepEqual(calls.map(c=>c.path),['/api/demo/workflows','/api/demo/workflows/demo-id/actions','/api/demo/workflows/demo-id/policy']);
 assert.equal(calls[1].body,JSON.stringify({action:'start'}));
 assert.equal('advance' in gateway,false);assert.equal('snapshot' in gateway,false);
});
test("backend failure remains visible and never falls back to fabricated state",async()=>{
 const statuses:boolean[]=[];
 const gateway=createServerDemoGateway({fetch:async()=>{throw new Error('offline');},onStatus:(_,ok)=>statuses.push(ok)});
 await assert.rejects(gateway.list(),/offline/);assert.equal(statuses.at(-1),false);
});
test("product refuses a real-equipment or ambiguous backend envelope",async()=>{
 const gateway=createServerDemoGateway({fetch:async()=>({ok:true,json:async()=>({workflows:[],hardwareConnected:true})} as Response)});
 await assert.rejects(gateway.list(),/模拟后端/);
});
