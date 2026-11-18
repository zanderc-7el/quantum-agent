import test from 'node:test';
import assert from 'node:assert/strict';
import { budgetEditError, type BudgetValues } from '../src/lib/budgetEdit.ts';
import { createRemoteGateway } from '../src/lib/remoteGateway.ts';
import type { Workflow } from '../src/types.ts';

const workflow = { id: 'wf', mode: 'real', status: 'attention', revision: 7, maxTotalShots: 30000000, maxAttemptsPerNode: 3, maxWorkflowAttempts: 40 } as Workflow;
const values: BudgetValues = { maxTotalShots: 45000000, maxAttemptsPerNode: 5, maxWorkflowAttempts: 60 };
test('budget editing permits only bounded monotonic increases at a checkpoint', () => {
  assert.equal(budgetEditError(values, workflow, '增加独立复测预算'), undefined);
  for (const status of ['running', 'queued', 'completed', 'aborted']) assert.match(budgetEditError(values, { ...workflow, status } as Workflow, '原因')!, /检查点/);
  assert.ok(budgetEditError(values, { ...workflow, mode: 'replay' }, '原因'));
  assert.ok(budgetEditError(values, workflow, ' '));
  assert.ok(budgetEditError(values, workflow, 'x'.repeat(1001)));
  assert.ok(budgetEditError({ maxTotalShots: 30000000, maxAttemptsPerNode: 3, maxWorkflowAttempts: 40 }, workflow, '原因'));
});
test('invalid budgets cannot be presented as valid client-side proposals', () => {
  for (const maxTotalShots of [29999999, 100000001, NaN, Infinity, 40000000.5]) assert.ok(budgetEditError({ ...values, maxTotalShots }, workflow, '原因'));
  for (const maxAttemptsPerNode of [2, 11, 5.5]) assert.ok(budgetEditError({ ...values, maxAttemptsPerNode }, workflow, '原因'));
  for (const maxWorkflowAttempts of [39, 201, 55.5]) assert.ok(budgetEditError({ ...values, maxWorkflowAttempts }, workflow, '原因'));
  assert.ok(budgetEditError(values, { ...workflow, maxTotalShots: undefined }, '原因'));
});
test('budget gateway preserves revision/reason/token and sends no start command', async () => {
  const writes: { url: string; init: RequestInit }[] = [];
  const gateway = createRemoteGateway({ fetch: (async (url, init) => {
    const value = String(url).endsWith('/session') ? { token: 'test-token' } : { ...workflow, ...values, revision: 8 };
    if (init?.method) writes.push({ url: String(url), init });
    return new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
  }) as typeof fetch });
  await gateway.budget('wf/a', 7, values, '本轮需增加独立复测');
  assert.equal(writes.length, 1);
  assert.equal(writes[0].url, '/api/workflows/wf%2Fa/budget');
  assert.equal(writes[0].init.method, 'PATCH');
  assert.equal((writes[0].init.headers as Record<string, string>)['X-Qcal-Token'], 'test-token');
  const body = JSON.parse(writes[0].init.body as string);
  assert.equal(body.expectedRevision, 7); assert.equal(body.reason, '本轮需增加独立复测');
  assert.deepEqual(body.budget, values); assert.ok(body.operationId);
  assert.equal(Object.hasOwn(body, 'action'), false);
});
