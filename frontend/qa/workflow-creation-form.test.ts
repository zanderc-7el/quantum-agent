import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import * as React from 'react';

const realRequire = createRequire(import.meta.url);
let code: string;
function harness() {
  code ??= buildSync({ entryPoints: [fileURLToPath(new URL('../src/components/WorkflowCreationForm.tsx', import.meta.url))], bundle: true, platform: 'node', format: 'cjs', write: false, external: ['react', 'react/jsx-runtime'], loader: { '.css': 'empty' } }).outputFiles[0].text;
  const slots: any[] = [], effects: (() => void)[] = []; let cursor = 0;
  const hooks = { ...React,
    useState(initial: any) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial; return [slots[index], (value: any) => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useRef(initial: any) { return slots[cursor++] ??= { current: initial }; },
    useEffect(fn: any, deps: any[]) { const index = cursor++; if (!slots[index] || deps.some((value, i) => value !== slots[index].deps[i])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = fn(); }); } },
  };
  const module = { exports: {} as any };
  new Function('require', 'module', 'exports', code)((id: string) => id === 'react' ? hooks : realRequire(id), module, module.exports);
  return { render(props: any) { cursor = 0; const tree = module.exports.default(props); while (effects.length) effects.shift()!(); return tree; }, close() { slots.forEach(slot => slot?.cleanup?.()); } };
}
function elements(tree: any): any[] { return tree?.props ? [tree, ...[tree.props.children].flat(Infinity).flatMap(elements)] : []; }
function text(tree: any): string { return typeof tree === 'string' || typeof tree === 'number' ? String(tree) : tree?.props ? [tree.props.children].flat(Infinity).map(text).join('') : ''; }
const field = (tree: any, label: string) => elements(tree).find(e => e.props['aria-label'] === label)?.props;
const topology = (tree: any) => elements(tree).find(e => e.type?.name === 'ChipTopology')?.props;
const submit = (tree: any) => elements(tree).find(e => e.type === 'form')!.props.onSubmit({ preventDefault() {} });
const tick = () => new Promise<void>(resolve => setImmediate(resolve));
function fixture() {
  const q = (id: string, physicalId: string, hz: number) => ({ id, label: id, physicalId, identityStatus: 'confirmed', ready: true, parameters: { drive_hz: hz } });
  const chip = (id: string, physical: string, hz: number) => ({ id, name: id + ' current draft', versions: [{ id: id + 'v', entityId: id, name: id + ' frozen', digest: 'a'.repeat(64), publishedAt: '2026-11-26', qubits: [q('Q11', physical, hz), { ...q('DRAFT', 'unverified', 4e9), identityStatus: 'unresolved' }], topology: { nodes: [{ resourceId: 'Q11', x: 10, y: 10 }, { resourceId: 'DRAFT', x: 20, y: 20 }], edges: [] } }] });
  const dep = (id: string, machine: string) => ({ id, name: id + ' draft', versions: [{ id: id + 'v', entityId: id, name: id + ' frozen', digest: 'b'.repeat(64), publishedAt: '2026-11-26', machine, wiringConfirmed: true, routes: { Q11: { xyPhysical: 'xy' } } }] });
  const chips = [chip('A', 'P11', 4.2e9), chip('B', 'P88', 5.1e9)], deployments = [dep('R8', '八号机'), dep('R9', '九号机')];
  const reads: string[] = [], sent: any[] = [];
  const props: any = { api: { async get(path: string) { reads.push(path); return path === '/chips' ? chips : deployments; }, write() { throw new Error('No config write'); } }, catalog: { adapter: { id: 'legacy' }, resources: [{ id: 'Q11', label: 'Legacy Q11' }, { id: 'Q18', label: 'Legacy Q18' }], capabilities: { resources: ['Q11', 'Q18'] }, workingPointCoverage: { available: true, resourceIds: ['Q11'] } }, form: { name: 'Task', mode: 'real', experiment: 'single', resourceIds: ['Q11'], machine: '八号机', target: 'Legacy Q11', qubits: 1, type: 'adaptive', policy: 'threshold', scenario: 'normal', coverage: 'working_point' }, busy: false, onChange(next: any) { props.form = next; }, async onCreate(input: any) { sent.push(input); }, onCancel() {} };
  return { props, chips, deployments, reads, sent };
}
function choose(h: ReturnType<typeof harness>, f: ReturnType<typeof fixture>, label: string, value: string) { field(h.render(f.props), label).onChange({ target: { value } }); return h.render(f.props); }

test('published target selection is bidirectional and submits exact immutable IDs without old Q11 initialization', async () => {
  const h = harness(), f = fixture(); h.render(f.props); await tick();
  choose(h, f, '配置来源', 'published');
  assert.equal(f.props.form.coverage, 'full'); assert.deepEqual(f.props.form.resourceIds, []);
  choose(h, f, '新建工作流芯片版本', 'Av'); choose(h, f, '新建工作流机台', '八号机'); choose(h, f, '新建工作流接线版本', 'R8v');
  let tree = choose(h, f, '目标资源', 'Q11');
  assert.equal(topology(tree).selectedResourceId, 'Q11'); assert.equal(topology(tree).qubits[0].physicalId, 'P11');
  topology(tree).onSelect('DRAFT'); assert.deepEqual(f.props.form.resourceIds, ['Q11']);
  assert.match(text(h.render(f.props)), /待|确认|身份/);
  tree = choose(h, f, '新建工作流芯片版本', 'Bv');
  assert.equal(topology(tree).selectedResourceId, ''); assert.equal(f.props.form.deploymentVersionId, undefined); assert.equal(f.props.form.machine, '');
  choose(h, f, '新建工作流机台', '九号机'); choose(h, f, '新建工作流接线版本', 'R9v');
  tree = h.render(f.props); topology(tree).onSelect('Q11'); tree = h.render(f.props);
  assert.equal(field(tree, '目标资源').value, 'Q11'); assert.equal(topology(tree).qubits[0].physicalId, 'P88');
  submit(tree); submit(tree); await tick(); assert.equal(f.sent.length, 1, 'single flight create');
  assert.deepEqual({ chip: f.sent[0].chipVersionId, dep: f.sent[0].deploymentVersionId, resources: f.sent[0].resourceIds, machine: f.sent[0].machine, coverage: f.sent[0].coverage }, { chip: 'Bv', dep: 'R9v', resources: ['Q11'], machine: '九号机', coverage: 'full' });
  assert.equal(f.sent[0].manualParameters, undefined); assert.equal(f.sent[0].sourceWorkflowId, undefined);
  assert.deepEqual(f.reads, ['/chips', '/deployments']); h.close();
});

test('station changes clear endpoint, selected point and initialization; stale submit cannot bypass validation', async () => {
  const h = harness(), f = fixture(); h.render(f.props); await tick(); choose(h, f, '配置来源', 'published');
  choose(h, f, '新建工作流芯片版本', 'Av'); choose(h, f, '新建工作流机台', '八号机'); choose(h, f, '新建工作流接线版本', 'R8v'); choose(h, f, '目标资源', 'Q11');
  const tree = choose(h, f, '新建工作流机台', '九号机');
  assert.equal(f.props.form.chipVersionId, 'Av'); assert.equal(f.props.form.deploymentVersionId, undefined); assert.equal(topology(tree).selectedResourceId, '');
  submit(tree); await tick(); assert.equal(f.sent.length, 0); h.close();
});

test('no published versions preserves fixed flow; switching replay/two removes every published binding', async () => {
  const h = harness(), f = fixture(); f.chips.length = 0; f.deployments.length = 0;
  h.render(f.props); await tick(); let tree = h.render(f.props);
  assert.match(text(tree), /尚无已发布芯片/); submit(tree); await tick(); assert.equal(f.sent[0].coverage, 'working_point');
  choose(h, f, '配置来源', 'published'); f.props.form.chipVersionId = 'old'; f.props.form.deploymentVersionId = 'old-route';
  choose(h, f, '运行方式', 'replay'); tree = h.render(f.props); submit(tree); await tick();
  assert.equal(f.sent[1].chipVersionId, undefined); assert.equal(f.sent[1].deploymentVersionId, undefined); assert.equal(f.sent[1].mode, 'replay');
  choose(h, f, '运行方式', 'real'); await tick(); choose(h, f, '实验流程', 'two'); choose(h, f, '目标资源', 'Q11'); tree = choose(h, f, '第二个资源', 'Q18');
  assert.equal(field(tree, '配置来源'), undefined); submit(tree); await tick(); assert.deepEqual(f.sent[2].resourceIds, ['Q11', 'Q18']); h.close();
});

test('late configuration response after switching to replay cannot expose previous chip topology', async () => {
  const h = harness(), f = fixture(); const pending: ((value: any) => void)[] = [];
  f.props.api.get = () => new Promise(resolve => pending.push(resolve)); h.render(f.props); choose(h, f, '配置来源', 'published');
  choose(h, f, '运行方式', 'replay'); pending[0](f.chips); pending[1](f.deployments); await tick();
  const tree = h.render(f.props); assert.equal(topology(tree), undefined); assert.equal(field(tree, '新建工作流芯片版本'), undefined); h.close();
});

test('legacy mode and two-qubit transitions preserve the originally chosen first resource', async () => {
  const h = harness(), f = fixture(); h.render(f.props); await tick();
  choose(h, f, '目标资源', 'Q18'); choose(h, f, '运行方式', 'replay');
  assert.deepEqual(f.props.form.resourceIds, ['Q18']);
  choose(h, f, '实验流程', 'two'); assert.deepEqual(f.props.form.resourceIds, ['Q18']);
  const tree = choose(h, f, '第二个资源', 'Q11'); submit(tree); await tick();
  assert.deepEqual(f.sent[0].resourceIds, ['Q18', 'Q11']); h.close();
});
