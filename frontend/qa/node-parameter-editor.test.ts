import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import * as React from 'react';

const bundle = buildSync({ entryPoints: [fileURLToPath(new URL('../src/components/NodeParameterEditor.tsx', import.meta.url))], bundle: true, platform: 'node', format: 'cjs', write: false, external: ['react', 'react/jsx-runtime'] }).outputFiles[0].text;
const realRequire = createRequire(import.meta.url);
function harness() {
  const slots: any[] = []; let cursor = 0;
  const hooks = { ...React,
    useState(initial: any) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial; return [slots[index], (value: any) => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useRef(initial: any) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useEffect() {},
  };
  const module = { exports: {} as Record<string, any> };
  new Function('require', 'module', 'exports', bundle)((id: string) => id === 'react' ? hooks : realRequire(id), module, module.exports);
  return (props: any) => { cursor = 0; return module.exports.default(props); };
}
function elements(tree: any): any[] { return !tree?.props ? [] : [tree, ...[tree.props.children].flat(Infinity).flatMap(elements)]; }
function text(tree: any): string { return typeof tree === 'string' || typeof tree === 'number' ? String(tree) : tree?.props ? [tree.props.children].flat(Infinity).map(text).join('') : ''; }
const button = (tree: any, label: string) => elements(tree).find(item => item.type === 'button' && text(item) === label)?.props;
const field = (tree: any, key: string) => elements(tree).find(item => item.props['aria-label'] === `参数 ${key}`)?.props;
function fixture() {
  const sent: unknown[][] = [];
  return { sent, props: { gateway: { async parameters(...args: unknown[]) { sent.push(args); } }, workflow: { id: 'new-Q18', revision: 17 }, node: { id: 'Q18:frequency', editableParameterKeys: ['readout_hz', 'drive_hz', 'points', 'drive_length_s'] }, parameters: { readout_hz: 7009676666.666666, drive_hz: 4862690000, points: 51, drive_length_s: 320e-9 }, bounds: { readout_hz: { min: 6e9, max: 8e9 }, drive_hz: { min: 4e9, max: 5.5e9 }, points: { min: 9, max: 161, integer: true }, drive_length_s: { min: 20e-9, max: 1e-6 } }, canEdit: true, busy: false, onBusy() {} } };
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
