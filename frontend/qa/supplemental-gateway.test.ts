import test from 'node:test';
import assert from 'node:assert/strict';
import { createRemoteGateway } from '../src/lib/remoteGateway.ts';

test('supplemental acceptance is an explicit versioned write, never a measurement action', async () => {
  const writes: { url: string; init: RequestInit }[] = [];
  const gateway = createRemoteGateway({ fetch: (async (url, init) => {
    if (init?.method) writes.push({ url: String(url), init });
    return new Response(JSON.stringify(String(url).endsWith('/session') ? { token: 'test-token' } : { id: 'wf', revision: 8 }), { headers: { 'Content-Type': 'application/json' } });
  }) as typeof fetch });
  await gateway.supplementalAcceptance('wf/a', 7, '补齐重复采集一致性检查');
  assert.equal(writes.length, 1);
  assert.equal(writes[0].url, '/api/workflows/wf%2Fa/supplemental-acceptance');
  assert.equal(writes[0].init.method, 'POST');
  const body = JSON.parse(writes[0].init.body as string);
  assert.equal(body.policyVersion, 'capture-consistency/1');
  assert.equal(body.expectedRevision, 7);
  assert.equal(body.reason, '补齐重复采集一致性检查');
  assert.ok(body.operationId);
  assert.equal(Object.hasOwn(body, 'action'), false);
  assert.equal(Object.hasOwn(body, 'gates'), false);
});
