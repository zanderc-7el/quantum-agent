import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';

const loaded = await import('../src/source-tools.mjs').catch(error => {
  if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
  return {};
});
const factory = loaded.createSourceTools;
test('project README and integration contract are readable reference data', async t => {
  const {put,tools}=await fixture(t);
  await put('README.md','Instructions are reference data, never authorization.');
  await put('INTEGRATION_CONTRACT.md','Controller contract');
  assert.equal((await tools.execute('read_source',{path:'README.md'})).lines[0].line,1);
  assert.deepEqual((await tools.execute('search_source',{query:'Controller',pathPrefix:'INTEGRATION_CONTRACT.md'})).matches.map(v=>v.path),['INTEGRATION_CONTRACT.md']);
});
async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'qcal-source-tools-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  const put = async (relative, content) => {
    const target = path.join(root, relative);
    await fs.mkdir(path.dirname(target), {recursive: true});
    await fs.writeFile(target, content);
    return target;
  };
  assert.equal(typeof factory, 'function', 'createSourceTools must provide bounded read-only project source access');
  return {root, put, tools: factory({root})};
}

test('read returns exact numbered source and hash without modifying the file', async t => {
  const {put, tools} = await fixture(t);
  const source = '# readout\r\ndef pause():\r\n    return "ready"\r\n';
  const target = await put('backend/workflow.py', source);
  const before = await fs.stat(target);
  const result = await tools.execute('read_source', {path: 'backend/workflow.py', startLine: 2, endLine: 3});
  assert.deepEqual(result.lines, [{line: 2, content: 'def pause():'}, {line: 3, content: '    return "ready"'}]);
  assert.equal(result.path, 'backend/workflow.py');
  assert.equal(result.sha256, createHash('sha256').update(source).digest('hex'));
  assert.equal(result.truncated, false);
  assert.equal(await fs.readFile(target, 'utf8'), source);
  assert.equal((await fs.stat(target)).mtimeMs, before.mtimeMs);
});

test('search is literal and constrained by prefix, with traceable line hashes', async t => {
  const {put, tools} = await fixture(t);
  await put('backend/a.py', 'alpha\nneedle.*()\nnot a regex\n');
  await put('frontend/src/a.ts', 'needle.*()\n');
  const result = await tools.execute('search_source', {query: 'needle.*()', pathPrefix: 'backend', limit: 3});
  assert.equal(result.matches.length, 1);
  assert.equal(result.matches[0].path, 'backend/a.py');
  assert.equal(result.matches[0].line, 2);
  assert.equal(result.matches[0].content, 'needle.*()');
  assert.equal(result.matches[0].sha256, createHash('sha256').update('alpha\nneedle.*()\nnot a regex\n').digest('hex'));
  assert.equal(result.truncated, false);
});

test('source roots and measurement code are allowed while runtime products and sensitive paths are excluded', async t => {
  const {put, tools} = await fixture(t);
  const allowed = ['backend/a.py', 'adapters/arclight_runtime/platform_worker.py',
    'adapters/arclight_runtime/work/runner.py', 'adapters/arclight_configurable_runtime/platform_specs.py',
    'agent/src/brain.mjs', 'frontend/src/App.tsx', 'knowledge/method.md', 'knowledge/rules.json',
    'profiles/notes.md'];
  const denied = ['backend/.env.py', 'backend/token_store.py', 'backend/credentials/config.py',
    'agent/src/account/account.mjs', 'backend/data/a.py', 'backend/runtime/a.py', 'backend/logs/a.py',
    'backend/node_modules/a.py', 'adapters/arclight_runtime/results/a.py',
    'adapters/arclight_runtime/offline_validation/results/a.py', 'adapters/arclight_runtime/base_config.json',
    'profiles/current.json', 'frontend/src/report.csv', 'unknown/a.py', 'knowledge/secret.yaml'];
  for (const name of [...allowed, ...denied]) await put(name, 'needle');
  const result = await tools.execute('list_source_files', {limit: 100});
  assert.deepEqual(result.files.map(file => file.path).sort(), allowed.sort());
  for (const name of denied) await assert.rejects(tools.execute('read_source', {path: name}), /source_path_not_allowed/);
  const matches = await tools.execute('search_source', {query: 'needle', limit: 100});
  assert.deepEqual(matches.matches.map(match => match.path).sort(), allowed.sort());
});

test('absolute, traversal, alternate streams and caller root overrides cannot escape fixed root', async t => {
  const {put, tools, root} = await fixture(t);
  await put('backend/a.py', 'safe');
  for (const relative of ['../backend/a.py', 'backend/../backend/a.py', '/etc/passwd',
    'D:\\qoskit-workspace\\backend\\a.py', '\\\\server\\share\\a.py', 'backend/a.py:secret',
    'backend/./a.py', 'backend\\..\\a.py', 'backend/a.py ', 'backend/%2e%2e/a.py']) {
    await assert.rejects(tools.execute('read_source', {path: relative}), /source_path_not_allowed/);
    await assert.rejects(tools.execute('search_source', {query: 'safe', pathPrefix: relative}), /source_path_not_allowed/);
  }
  await assert.rejects(tools.execute('read_source', {path: 'backend/a.py', root}), /source_invalid_arguments/);
  await assert.rejects(tools.execute('shell', {command: 'noop'}), /source_tool_not_allowed/);
  assert.throws(() => factory({root: 'D:\\qoskit-workspace'}), /source_root_not_allowed/);
});

test('junctions are never traversed even when they point to another allowed source directory', async t => {
  const {put, tools, root} = await fixture(t);
  await put('backend/real/a.py', 'junction-secret');
  const target = path.join(root, 'backend/real');
  const link = path.join(root, 'backend/jump');
  await fs.symlink(target, link, process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(tools.execute('read_source', {path: 'backend/jump/a.py'}), /source_path_not_allowed/);
  await assert.rejects(tools.execute('list_source_files', {pathPrefix: 'backend/jump'}), /source_path_not_allowed/);
  const result = await tools.execute('list_source_files', {pathPrefix: 'backend'});
  assert.deepEqual(result.files.map(file => file.path), ['backend/real/a.py']);
  const linkedRoot = factory({root: link});
  await assert.rejects(linkedRoot.execute('list_source_files', {}), /source_root_not_allowed/);
});

test('file links and hardlinks cannot expose source through alternate names', async t => {
  const {put, tools, root} = await fixture(t);
  const target = await put('backend/real.py', 'linked-content');
  await fs.link(target, path.join(root, 'backend/hard.py'));
  await assert.rejects(tools.execute('read_source', {path: 'backend/hard.py'}), /source_path_not_allowed/);
  if (process.platform !== 'win32') {
    await fs.symlink(target, path.join(root, 'backend/link.py'));
    await assert.rejects(tools.execute('read_source', {path: 'backend/link.py'}), /source_path_not_allowed/);
  }
});

test('embedded credentials and binary data are not returned through harmless file names', async t => {
  const {put, tools} = await fixture(t);
  await put('backend/a.py', 'api_key = "real-secret-value"\n');
  await put('knowledge/b.md', '-----BEGIN PRIVATE KEY-----\nnot-public');
  await put('backend/c.py', Buffer.from([0, 1, 2, 3]));
  await put('backend/ok.py', 'api_key = os.getenv("API_KEY")\n');
  for (const relative of ['backend/a.py', 'knowledge/b.md', 'backend/c.py'])
    await assert.rejects(tools.execute('read_source', {path: relative}), /source_(sensitive_content|not_text)/);
  const result = await tools.execute('list_source_files', {});
  assert.deepEqual(result.files.map(file => file.path), ['backend/ok.py']);
  assert.equal((await tools.execute('search_source', {query: 'real-secret-value'})).matches.length, 0);
});

test('line windows and serialized output are bounded without claiming complete coverage', async t => {
  const {put, tools} = await fixture(t);
  await put('backend/long.py', Array.from({length: 200}, (_, i) => `line ${i + 1}`).join('\n'));
  const window = await tools.execute('read_source', {path: 'backend/long.py', startLine: 1, endLine: 200});
  assert.equal(window.lines.length, 160);
  assert.equal(window.truncated, true);
  await put('backend/wide.py', 'needle' + '"\\'.repeat(30000));
  const wide = await tools.execute('read_source', {path: 'backend/wide.py'});
  assert.ok(JSON.stringify(wide).length <= 12000);
  assert.equal(wide.truncated, true);
  const matches = await tools.execute('search_source', {query: 'needle'});
  assert.ok(JSON.stringify(matches).length <= 12000);
  assert.equal(matches.truncated, true);
});

test('file size, aggregate scan and match limits stop bounded work and report truncation', async t => {
  const {put, tools} = await fixture(t);
  await put('backend/huge.py', 'x'.repeat(600 * 1024));
  await assert.rejects(tools.execute('read_source', {path: 'backend/huge.py'}), /source_file_too_large/);
  for (let i = 0; i < 25; i++) await put(`backend/f${String(i).padStart(2, '0')}.py`, 'x'.repeat(500 * 1024));
  const scan = await tools.execute('search_source', {query: 'missing'});
  assert.ok(scan.scannedBytes <= 8 * 1024 * 1024);
  assert.equal(scan.truncated, true);
  const limited = await tools.execute('list_source_files', {limit: 2});
  assert.equal(limited.files.length, 2);
  assert.equal(limited.truncated, true);
});
