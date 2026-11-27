import assert from 'node:assert/strict';
import test from 'node:test';
import {createRemoteGateway} from '../src/lib/remoteGateway.ts';

test('restoring spectroscopy drive inheritance only submits the current node, revision and explicit recovery intent', async () => {
  const writes: {url: string; method?: string; body: Record<string, unknown>}[] = [];
  const gateway = createRemoteGateway({fetch: async (url, init) => {
    if (String(url).endsWith('/session')) return Response.json({token:'offline'});
    writes.push({url:String(url),method:init?.method,body:JSON.parse(String(init?.body))});
    return Response.json({id:'w',revision:10,nodes:[]});
  }});
  await gateway.parameters('w','Q11:s02',9,{points:21});
  await gateway.parameters('w','Q11:s02',10,{},true);
  assert.equal(writes.length,2);
  const [edit,restore] = writes;
  assert.equal(edit.url, '/api/workflows/w/nodes/Q11%3As02/parameters');
  assert.equal(edit.url,restore.url);
  assert.equal(restore.method,'PATCH');
  assert.deepEqual(edit.body.parameters,{points:21});
  assert.ok(!Object.hasOwn(edit.body,'clearSpectrumDriveOverrides'));
  assert.deepEqual(restore.body.parameters,{});
  assert.equal(restore.body.expectedRevision,10);
  assert.equal(restore.body.clearSpectrumDriveOverrides,true);
  assert.ok(restore.body.operationId && restore.body.operationId!==edit.body.operationId);
});
