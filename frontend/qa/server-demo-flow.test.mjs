import test from 'node:test';
import assert from 'node:assert/strict';
import { createServerDemoGateway } from '../src/lib/serverDemoGateway.ts';
import { makeDemoWorkflows, demoMetadata } from '../../backend/src/demo-workflows.mjs';

test('frontend adapter drives server-owned single/two workflow lifecycle without browser ticks', async()=>{
 const service=makeDemoWorkflows();
 const gateway=createServerDemoGateway({fetch:async(url,options)=>{
  const segments=String(url).replace('/api/demo/workflows','').split('/').filter(Boolean);
  const body=options?.body?JSON.parse(options.body):null;
  const value=!segments.length?(body?{workflow:service.create(body)}:{workflows:service.list()}):segments[1]==='actions'?{workflow:service.act(segments[0],body.action)}:segments[1]==='policy'?{workflow:service.policy(segments[0],body.policy)}:{workflow:service.get(segments[0])};
  return {ok:true,json:async()=>({...demoMetadata,...value})};
 }});
 for(const experiment of ['single','two']){
  const w=await gateway.create({name:experiment+'演示',machine:'M7',experiment,qubits:experiment==='single'?1:2,target:experiment==='single'?'Q12':'Q12–Q13',type:'adaptive',policy:'threshold',scenario:'normal'});
  assert.equal(w.nodes.length,experiment==='single'?9:17);
  assert.equal((await gateway.act(w.id,'start')).status,'running');
  service.advance(1);
  await gateway.act(w.id,'pause');const paused=await gateway.get(w.id);service.advance(10);assert.deepEqual(await gateway.get(w.id),paused);
  await gateway.act(w.id,'resume');for(let i=0;i<150;i++)service.advance(1);
  const completed=await gateway.get(w.id);assert.equal(completed.status,'completed');assert.equal(completed.progress,100);
  assert.equal(completed.nodes.every(n=>n.status==='success'),true);
 }
});
