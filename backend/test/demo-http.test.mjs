import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm, readFile, writeFile, unlink } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';

test('HTTP demo lifecycle, server clock, validation and original API remain available', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'qcal-demo-http-'));
  const probe = net.createServer();
  await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  const child = spawn(process.execPath, ['--experimental-strip-types', fileURLToPath(new URL('../src/server.mjs', import.meta.url))], {
    env: { ...process.env, QCAL_PORT: String(port), QCAL_HOST: '127.0.0.1', QCAL_DATA_FILE: path.join(directory, 'state.json'), QCAL_DEMO_DATA_FILE: path.join(directory, 'workflows.json') },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) {
      const exited = new Promise(resolve => child.once('exit', resolve));
      child.kill(); await exited;
    }
    await rm(directory, { recursive: true, force: true });
  });
  let logs = ''; child.stderr.on('data', chunk => { logs += chunk; });
  const base = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(`${base}/health`)).ok) { ready = true; break; } } catch {}
    await new Promise(resolve => setTimeout(resolve, 30));
  }
  assert.ok(ready, logs);
  const request = async (url, method = 'GET', body) => {
    const response = await fetch(base + url, { method, headers: { 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: response.status, data: await response.json() };
  };
  assert.equal((await request('/api/v1/device-profile')).status, 200);
  assert.equal((await request('/health')).data.hardwareConnected, false);
  assert.equal((await request('/api/demo/workflows')).data.workflows.length, 2);
  const created = await request('/api/demo/workflows', 'POST', { name: 'HTTP demo', machine: 'Simulator', qubits: 1, target: 'Q12', type: 'adaptive', policy: 'autopilot', scenario: 'normal' });
  assert.equal(created.status, 201);
  assert.equal(created.data.dataSource, 'simulated');
  const url = `/api/demo/workflows/${created.data.workflow.id}`;
  assert.equal((await request(url + '/actions', 'POST', { action: 'start' })).data.workflow.status, 'running');
  await new Promise(resolve => setTimeout(resolve, 1200));
  const paused = await request(url + '/actions', 'POST', { action: 'pause' });
  assert.equal(paused.data.workflow.status, 'paused');
  assert.ok(paused.data.workflow.elapsed >= 1);
  assert.equal((await request(url + '/actions', 'POST', { action: 'resume' })).data.workflow.status, 'running');
  assert.equal((await request(url + '/policy', 'POST', { policy: 'strict' })).data.workflow.policy, 'strict');
  assert.equal((await request(url + '/actions', 'POST', { action: 'invalid' })).status, 400);
  assert.equal((await request('/api/demo/workflows/missing')).status, 404);
  assert.equal((await request('/api/demo/workflows', 'POST', null)).status, 400);
  const forbidden = await fetch(base + '/api/demo/workflows', { headers: { Origin: 'https://untrusted.example' } });
  assert.equal(forbidden.status, 403);

  // Verify persisted data rather than treating a live process as durable storage.
  await request(url + '/actions', 'POST', { action: 'abort' });
  const workflowFile = path.join(directory, 'workflows.json');
  const experimentFile = path.join(directory, 'state.json');
  const savedWorkflows = await readFile(workflowFile, 'utf8');
  const savedExperiments = await readFile(experimentFile, 'utf8');
  const storage = await request('/api/demo/storage-status');
  assert.equal(storage.status, 200);
  assert.equal(storage.data.status, 'ok');
  assert.equal(storage.data.storageType, 'json-files');
  assert.equal(storage.data.databaseConfigured, false);
  assert.equal(storage.data.stores.workflows.matchesMemory, true);
  assert.equal(storage.data.stores.workflows.counts.total, 3);
  assert.equal(storage.data.stores.experiments.counts.plans, 0);
  assert.ok(Number.isFinite(Date.parse(storage.data.stores.workflows.lastSavedAt)));
  assert.equal(storage.data.stores.workflows.bytes, Buffer.byteLength(savedWorkflows));
  assert.equal((await readFile(workflowFile, 'utf8')), savedWorkflows);
  assert.ok(!JSON.stringify(storage.data).includes(directory));
  assert.ok(!JSON.stringify(storage.data).includes(created.data.workflow.id));

  await writeFile(workflowFile, JSON.stringify({ version: 1, workflows: [] }));
  const mismatch = await request('/api/demo/storage-status');
  assert.equal(mismatch.status, 503);
  assert.equal(mismatch.data.stores.workflows.errorCode, 'STATE_MISMATCH');
  assert.equal((await request('/api/demo/workflows')).data.workflows.length, 3);

  await writeFile(workflowFile, '{invalid');
  const corrupt = await request('/api/demo/storage-status');
  assert.equal(corrupt.status, 503);
  assert.equal(corrupt.data.stores.workflows.errorCode, 'INVALID_JSON');
  assert.equal(await readFile(workflowFile, 'utf8'), '{invalid');
  await writeFile(workflowFile, savedWorkflows);

  await unlink(experimentFile);
  const missing = await request('/api/demo/storage-status');
  assert.equal(missing.status, 503);
  assert.equal(missing.data.stores.experiments.errorCode, 'FILE_MISSING');
  assert.equal((await request('/health')).status, 200);
  await writeFile(experimentFile, savedExperiments);
  assert.equal((await request('/api/demo/storage-status')).status, 200);
  assert.equal((await fetch(base + '/api/demo/storage-status', { headers: { Origin: 'https://untrusted.example' } })).status, 403);
});
