import test from 'node:test';
import assert from 'node:assert/strict';
import { publishedVersions, resetCreationBinding, creationInput } from '../src/lib/workflowCreationBinding.ts';
import type { Catalog, CreateWorkflowInput } from '../src/types.ts';
import { createRemoteGateway } from '../src/lib/remoteGateway.ts';

const form: CreateWorkflowInput = { name: 'New', mode: 'real', experiment: 'single', resourceIds: ['Q11'], machine: '八号机', target: 'Q11', qubits: 1, type: 'adaptive', policy: 'threshold', scenario: 'normal', coverage: 'full' };
const catalog = { resources: [{ id: 'Q11', label: 'Legacy Q11' }], capabilities: { resources: ['Q11'] }, workingPointCoverage: { available: true, resourceIds: ['Q11'] } } as unknown as Catalog;
const chips: any[] = [{ id: 'chip', name: 'Draft changed', qubits: [{ id: 'WRONG' }], versions: [{ id: 'chipv-a', entityId: 'chip', name: 'Frozen A', digest: 'a'.repeat(64), publishedAt: '2026-11-26', qubits: [{ id: 'Q11', label: 'New Q11', physicalId: 'P55', identityStatus: 'confirmed', parameters: { drive_hz: 4.2e9 } }], topology: { nodes: [{ resourceId: 'Q11', x: 1, y: 2 }], edges: [] } }] }];
const deployments: any[] = [{ id: 'dep', name: 'Route', versions: [{ id: 'depv-a', entityId: 'dep', name: 'Frozen route', machine: '八号机', digest: 'b'.repeat(64), publishedAt: '2026-11-26', wiringConfirmed: true, routes: { Q11: { xyPhysical: 'x' } } }] }];

test('published choices read only immutable version fields, never draft topology or resources', () => {
  const v = publishedVersions(chips)[0]; assert.equal(v.name, 'Frozen A'); assert.equal(v.qubits![0].id, 'Q11');
  assert.equal(publishedVersions([{ ...chips[0], versions: [] }]).length, 0);
});
test('published same-name resource request freezes exact chip/deployment IDs and new initial source', () => {
  const input = creationInput({ ...form, chipVersionId: 'chipv-a', deploymentVersionId: 'depv-a' }, 'published', chips, deployments, catalog);
  assert.equal(input.chipVersionId, 'chipv-a'); assert.equal(input.deploymentVersionId, 'depv-a');
  assert.deepEqual(input.resourceIds, ['Q11']); assert.equal(input.target, 'New Q11');
  assert.equal(input.sourceWorkflowId, undefined); assert.equal(input.manualParameters, undefined);
});
test('changing source/chip/station clears targets and every inherited initialization reference', () => {
  const old = { ...form, coverage: 'working_point' as const, chipVersionId: 'chipv-a', deploymentVersionId: 'depv-a', sourceWorkflowId: 'old', manualWorkingPointId: 'old-point', manualParameters: { drive_hz: 4.8e9 }, manualScienceReferences: [{ role: 'f01', recordId: 'old' }] };
  const cleared = resetCreationBinding(old, { machine: '另一机台', chipVersionId: 'chipv-b' });
  assert.equal(cleared.chipVersionId, 'chipv-b'); assert.equal(cleared.deploymentVersionId, undefined);
  assert.deepEqual(cleared.resourceIds, []); assert.equal(cleared.target, ''); assert.equal(cleared.coverage, 'full');
  for (const key of ['sourceWorkflowId', 'manualWorkingPointId', 'manualParameters', 'manualScienceReferences']) assert.equal((cleared as any)[key], undefined);
});
test('published mode rejects missing/stale/cross-station/unconfirmed bindings and legacy working point', () => {
  for (const change of [{ chipVersionId: 'missing' }, { deploymentVersionId: 'missing' }, { machine: '另一机台' }, { resourceIds: ['Q18'] }, { coverage: 'working_point' as const }]) {
    assert.throws(() => creationInput({ ...form, chipVersionId: 'chipv-a', deploymentVersionId: 'depv-a', ...change }, 'published', chips, deployments, catalog));
  }
  const bad = structuredClone(chips); bad[0].versions[0].qubits[0].identityStatus = 'unresolved';
  assert.throws(() => creationInput({ ...form, chipVersionId: 'chipv-a', deploymentVersionId: 'depv-a' }, 'published', bad, deployments, catalog));
});
test('fixed configuration, replay and existing two-qubit selection keep their original request behavior', () => {
  assert.equal(creationInput({ ...form, coverage: 'working_point' }, 'fixed', [], [], catalog).coverage, 'working_point');
  const replay = creationInput({ ...form, mode: 'replay', coverage: 'main' }, 'fixed', [], [], catalog); assert.equal(replay.mode, 'replay');
  const both = { ...catalog, resources: [...catalog.resources, { id: 'Q18', label: 'Q18' }], capabilities: { resources: ['Q11', 'Q18'] } };
  const two = creationInput({ ...form, experiment: 'two', qubits: 2, resourceIds: ['Q11', 'Q18'] }, 'fixed', [], [], both);
  assert.deepEqual(two.resourceIds, ['Q11', 'Q18']); assert.equal(two.chipVersionId, undefined);
});

test('actual create transport carries the version-bound request and never sends a start action', async () => {
  const input = creationInput({ ...form, chipVersionId: 'chipv-a', deploymentVersionId: 'depv-a' }, 'published', chips, deployments, catalog);
  const sent: { url: string; body: unknown }[] = [];
  const gateway = createRemoteGateway({ fetch: (async (url, init) => {
    if (init?.method) sent.push({ url: String(url), body: JSON.parse(String(init.body)) });
    return new Response(JSON.stringify(String(url).endsWith('/session') ? { token: 'fixture-only' } : { id: 'fixture-workflow', nodes: [] }), { headers: { 'Content-Type': 'application/json' } });
  }) as typeof fetch });
  await gateway.create(input);
  assert.deepEqual(sent, [{ url: '/api/workflows', body: input }]);
  assert.equal((sent[0].body as CreateWorkflowInput).chipVersionId, 'chipv-a');
  assert.equal((sent[0].body as CreateWorkflowInput).deploymentVersionId, 'depv-a');
});
