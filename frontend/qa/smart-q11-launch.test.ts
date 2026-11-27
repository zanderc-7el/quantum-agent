import test from 'node:test';
import assert from 'node:assert/strict';
import { launchSmartQ11, offersSmartStart } from '../src/lib/smartQ11Launch.ts';
import type { Catalog, CreateWorkflowInput } from '../src/types.ts';

const input = { experiment: 'single', coverage: 'full', mode: 'real', resourceIds: ['Q11'], policy: 'autopilot' } as CreateWorkflowInput;
const catalog = { capabilities: { id: 'arclight-private-f8-q11-v3' } } as unknown as Catalog;
function localStorageForTest() {
  const memory = new Map<string,string>();
  return { getItem: (k: string) => memory.get(k) ?? null, setItem: (k: string,v: string) => memory.set(k,v),
           removeItem: (k: string) => memory.delete(k) } as Storage;
}
test('only the supported fixed full station and smart mode offer an immediate start', () => {
  assert.ok(offersSmartStart(input, catalog));
  for (const change of [{policy:'strict'}, {mode:'replay'}, {coverage:'working_point'}, {resourceIds:['Q18']}, {chipVersionId:'new'}])
    assert.equal(offersSmartStart({...input,...change} as CreateWorkflowInput,catalog),false);
  assert.equal(offersSmartStart(input,catalog,true),false);
  assert.equal(offersSmartStart(input),false);
});
test('a lost response and reload reuse exactly one permitted operation, a subsequent intentional run gets a new identity', async () => {
  const storage = localStorageForTest();
  const calls: any[] = [];
  const api = {write: async <T>(path: string, body: any): Promise<T> => {
    assert.equal(path,'/workflows/smart-start'); calls.push(body);
    if (calls.length === 1) throw new TypeError('connection lost');
    return {id:'wf'} as T;
  }};
  await assert.rejects(launchSmartQ11(api,'Q11',storage));
  await assert.rejects(launchSmartQ11(api,'another',storage)); assert.equal(calls.length,1);
  assert.equal((await launchSmartQ11(api,'Q11',storage)).id,'wf');
  assert.deepEqual(calls[1],calls[0]);
  await launchSmartQ11(api,'Q11',storage);
  assert.notEqual(calls[2].operationId,calls[0].operationId);
});
test('a definite refusal permits correction without retaining a phantom start', async () => {
  const storage = localStorageForTest();
  const refused = {write: async <T>(): Promise<T> => { throw Object.assign(new Error('refused'),{status:409}); }};
  await assert.rejects(launchSmartQ11(refused,'Q11',storage));
  const accepted = {write: async <T>(): Promise<T> => ({id:'wf2'}) as T};
  assert.equal((await launchSmartQ11(accepted,'Q11 next',storage)).id,'wf2');
});
