import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import * as React from 'react';

const bundles = new Map<string, string>();
function bundleFor(component: string) {
  if (!bundles.has(component)) bundles.set(component, buildSync({ entryPoints: [fileURLToPath(new URL(`../src/components/${component}.tsx`, import.meta.url))], bundle: true, platform: 'node', format: 'cjs', write: false, external: ['react', 'react/jsx-runtime'], loader: { '.css': 'empty' } }).outputFiles[0].text);
  return bundles.get(component)!;
}
const realRequire = createRequire(import.meta.url);
function harness(component = 'NodeParameterEditor') {
  const slots: any[] = []; const effects: (() => void)[] = []; let cursor = 0;
  const hooks = { ...React,
    useId: () => 'node-preview-form',
    useState(initial: any) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial; return [slots[index], (value: any) => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useRef(initial: any) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useEffect(fn: any, deps: any[]) { const index = cursor++; if (!slots[index] || deps.some((value, i) => value !== slots[index].deps[i])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = fn(); }); } },
  };
  const module = { exports: {} as Record<string, any> };
  new Function('require', 'module', 'exports', bundleFor(component))((id: string) => id === 'react' ? hooks : realRequire(id), module, module.exports);
  return Object.assign((props: any) => { cursor = 0; const tree = module.exports.default(props); while (effects.length) effects.shift()!(); return tree; }, { close() { for (const slot of slots) slot?.cleanup?.(); } });
}
function elements(tree: any): any[] { return !tree?.props ? [] : [tree, ...[tree.props.children].flat(Infinity).flatMap(elements)]; }
function text(tree: any): string { return typeof tree === 'string' || typeof tree === 'number' ? String(tree) : tree?.props ? [tree.props.children].flat(Infinity).map(text).join('') : ''; }
const button = (tree: any, label: string) => elements(tree).find(item => item.type === 'button' && text(item) === label)?.props;
const field = (tree: any, key: string) => elements(tree).find(item => item.props['aria-label'] === `参数 ${key}`)?.props;
const preview = (tree: any) => elements(tree).find(item => item.type?.name === 'ManualPulsePreview')?.props;
function fixture() {
  const sent: unknown[][] = [];
  return { sent, props: { gateway: { async parameters(...args: unknown[]) { sent.push(args); } }, workflow: { id: 'new-Q18', revision: 17 }, node: { id: 'Q18:frequency', resourceId: 'Q18', procedureId: 'sq_rabi_amplitude', editableParameterKeys: ['readout_hz', 'drive_hz', 'points', 'drive_length_s'] }, detailRevision: 17, parameters: { readout_hz: 7009676666.666666, drive_hz: 4862690000, points: 51, drive_length_s: 320e-9 }, bounds: { readout_hz: { min: 6e9, max: 8e9 }, drive_hz: { min: 4e9, max: 5.5e9 }, points: { min: 9, max: 161, integer: true }, drive_length_s: { min: 20e-9, max: 1e-6 } }, canEdit: true, busy: false, onBusy() {} } };
}

test('blueprint editor shows human units and sends only changed values in native units', async () => {
  const { props, sent } = fixture(); const render = harness();
  button(render(props), '编辑本节点参数').onClick();
  let tree = render(props);
  assert.match(text(tree), /驱动频率 \(GHz\)/);
  assert.match(text(tree), /π 脉冲时长 \(ns\)/);
  assert.equal(field(tree, 'drive_hz').value, '4.86269');
  assert.equal(field(tree, 'drive_length_s').value, '320');
  assert.equal(button(tree, '保存参数').disabled, true);
  field(tree, 'drive_hz').onChange({ target: { value: '4.863' } });
  tree = render(props);
  await button(tree, '保存参数').onClick();
  await Promise.resolve();
  assert.deepEqual(sent, [['new-Q18', 'Q18:frequency', 17, { drive_hz: 4863000000 }]]);
  // High-precision readout must not be silently rounded and resubmitted.
});

test('correcting an invalid value removes its obsolete error before saving', async () => {
  const { props, sent } = fixture(); const render = harness();
  button(render(props), '编辑本节点参数').onClick();
  field(render(props), 'points').onChange({ target: { value: '8' } });
  button(render(props), '保存参数').onClick();
  await Promise.resolve();
  assert.match(text(render(props)), /扫描点数需在 9 至 161/);
  assert.equal(sent.length, 0);
  field(render(props), 'points').onChange({ target: { value: '9' } });
  assert.equal(elements(render(props)).some(item => item.props.role === 'alert'), false);
  button(render(props), '保存参数').onClick(); await Promise.resolve();
  assert.deepEqual(sent, [['new-Q18', 'Q18:frequency', 17, { points: 9 }]]);
});

test('changed revisions cannot save the displayed draft', async () => {
  const { props, sent } = fixture(); const render = harness();
  button(render(props), '编辑本节点参数').onClick();
  field(render(props), 'points').onChange({ target: { value: '61' } });
  const tree = render({ ...props, workflow: { ...props.workflow, revision: 18 } });
  assert.equal(button(tree, '保存参数').disabled, true);
  button(tree, '保存参数').onClick(); await Promise.resolve();
  assert.equal(sent.length, 0);
});

test('blueprint preview is opt-in and read-only viewing freezes its requested revision across progress', () => {
  const { props } = fixture(); const render = harness(); const running = { ...props, canEdit: false };
  assert.equal(preview(render(running)), undefined);
  button(render(running), '展开参数时序').onClick();
  let tree = render(running);
  assert.equal(preview(tree).revision, 17); assert.deepEqual(preview(tree).changes, {});
  assert.equal(preview(tree).workflowId, props.workflow.id); assert.equal(preview(tree).nodeId, props.node.id);
  for (let revision = 18; revision < 100; revision++) {
    tree = render({ ...running, workflow: { ...props.workflow, revision } });
    assert.equal(preview(tree).revision, 17, 'progress must not schedule another preview request');
  }
  assert.match(text(tree), /快照.*17/);
  button(tree, '刷新参数时序').onClick();
  tree = render({ ...running, workflow: { ...props.workflow, revision: 99 } });
  assert.equal(preview(tree).revision, 99);
});

test('blueprint draft preview uses the save parser and cancel removes the unsaved timing', async () => {
  const { props, sent } = fixture(); const render = harness();
  button(render(props), '展开参数时序').onClick();
  button(render(props), '编辑本节点参数').onClick();
  field(render(props), 'drive_length_s').onChange({ target: { value: '70' } });
  let tree = render(props); const expected = preview(tree).changes;
  assert.deepEqual(expected, { drive_length_s: 70e-9 }); assert.equal(preview(tree).dirty, true);
  field(tree, 'points').onChange({ target: { value: 'NaN' } });
  assert.match(preview(render(props)).problem, /有限/);
  field(render(props), 'points').onChange({ target: { value: '51' } });
  button(render(props), '保存参数').onClick(); await Promise.resolve();
  assert.deepEqual(sent, [['new-Q18', 'Q18:frequency', 17, expected]]);
  assert.equal(preview(render(props)), undefined, 'successful save closes obsolete preview');
  button(render(props), '编辑本节点参数').onClick();
  field(render(props), 'drive_length_s').onChange({ target: { value: '90' } });
  button(render(props), '展开参数时序').onClick();
  assert.deepEqual(preview(render(props)).changes, { drive_length_s: 90e-9 });
  button(render(props), '取消').onClick();
  assert.equal(preview(render(props)), undefined);
  button(render(props), '展开参数时序').onClick();
  assert.deepEqual(preview(render(props)).changes, {});
});

test('stale detail cannot seed an edit and stale drafts cannot request or save a preview', () => {
  const { props } = fixture(); const render = harness();
  const old = { ...props, detailRevision: 16 };
  assert.equal(button(render(old), '编辑本节点参数').disabled, true);
  button(render(old), '编辑本节点参数').onClick();
  assert.equal(field(render(old), 'points'), undefined);
  button(render(props), '编辑本节点参数').onClick();
  button(render(props), '展开参数时序').onClick();
  field(render(props), 'points').onChange({ target: { value: '61' } });
  const tree = render({ ...props, workflow: { ...props.workflow, revision: 18 } });
  assert.match(preview(tree).problem, /版本/);
  assert.equal(button(tree, '保存参数').disabled, true);
});

test('switching workflow or node never exposes the previous draft or preview even before effects', () => {
  for (const target of ['workflow', 'node']) {
    const { props } = fixture(); const render = harness();
    button(render(props), '编辑本节点参数').onClick();
    field(render(props), 'points').onChange({ target: { value: '61' } });
    button(render(props), '展开参数时序').onClick();
    const next = { ...props, [target]: { ...props[target], id: 'other' } };
    const first = render(next);
    assert.equal(preview(first), undefined); assert.equal(field(first, 'points'), undefined);
    assert.equal(text(first).includes('61'), false);
  }
});

test('late save completion from another node cannot show success on the selected node', async () => {
  const { props } = fixture(); const render = harness(); let resolve!: () => void;
  const saving = { ...props, gateway: { parameters: () => new Promise<void>(done => { resolve = done; }) } };
  button(render(saving), '编辑本节点参数').onClick();
  field(render(saving), 'points').onChange({ target: { value: '61' } });
  button(render(saving), '保存参数').onClick();
  const next = { ...saving, node: { ...props.node, id: 'other' } }; render(next);
  resolve(); await Promise.resolve();
  assert.ok(!text(render(next)).includes('参数已保存'));
});

test('running blueprint issues one real preview request until an explicit refresh, and never a command', async () => {
  const { props } = fixture(); const render = harness(); const renderPreview = harness('ManualPulsePreview');
  const calls: unknown[][] = [];
  const running = { ...props, canEdit: false, gateway: {
    async pulsePreview(workflowId: string, nodeId: string, workflowRevision: number, ...args: unknown[]) {
      calls.push([workflowId, nodeId, workflowRevision, ...args]);
      return { workflowId, nodeId, workflowRevision, status: 'unavailable', segments: [], requestedChanges: {}, normalizedChanges: {}, reason: '该方法的实际时序尚未覆盖' };
    },
    parameters() { assert.fail('viewing must not save'); }, command() { assert.fail('viewing must not execute'); },
  } };
  assert.equal(preview(render(running)), undefined);
  button(render(running), '展开参数时序').onClick();
  renderPreview(preview(render(running)));
  await new Promise(resolve => setTimeout(resolve, 210));
  let tree: any;
  for (let revision = 18; revision <= 80; revision++) {
    tree = render({ ...running, workflow: { ...props.workflow, revision } });
    assert.match(text(renderPreview(preview(tree))), /实际时序尚未覆盖/);
  }
  await new Promise(resolve => setTimeout(resolve, 210));
  assert.equal(calls.length, 1); assert.equal(calls[0][2], 17);
  button(tree, '刷新参数时序').onClick();
  renderPreview(preview(render({ ...running, workflow: { ...props.workflow, revision: 80 } })));
  await new Promise(resolve => setTimeout(resolve, 210));
  assert.equal(calls.length, 2); assert.equal(calls[1][2], 80);
  render.close(); renderPreview.close();
});
