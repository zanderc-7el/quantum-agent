import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import * as React from 'react';
import type { Workflow } from '../src/types.ts';

// Render the real workspace's pure tree and handlers with in-memory hook state.
// No DOM, network, browser, effects, or hardware is involved.
const bundle = buildSync({ entryPoints: [fileURLToPath(new URL('../src/components/ManualExperimentWorkspace.tsx', import.meta.url))], bundle: true, platform: 'node', format: 'cjs', write: false, external: ['react', 'react/jsx-runtime'], loader: { '.css': 'empty' } }).outputFiles[0].text;
const realRequire = createRequire(import.meta.url);
function harness() {
  const slots: any[] = []; let cursor = 0;
  const hooks = { ...React,
    useState(initial: any) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial; return [slots[index], (value: any) => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useRef(initial: any) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useMemo(factory: () => any) { const index = cursor++; return slots[index] ??= factory(); },
    useCallback(callback: any) { return callback; },
    useEffect() {},
  };
  const module = { exports: {} as Record<string, any> };
  new Function('require', 'module', 'exports', bundle)((id: string) => id === 'react' ? hooks : realRequire(id), module, module.exports);
  return (props: any) => { cursor = 0; return module.exports.default(props); };
}
function elements(tree: any): any[] {
  if (!tree || typeof tree !== 'object' || !tree.props) return [];
  return [tree, ...[tree.props.children].flat(Infinity).flatMap(elements)];
}
function text(tree: any): string {
  if (typeof tree === 'string' || typeof tree === 'number') return String(tree);
  return tree?.props ? [tree.props.children].flat(Infinity).map(text).join('') : '';
}
const button = (tree: any, label: string) => elements(tree).find(item => item.type === 'button' && text(item) === label)?.props;
const workflow = (id: string): Workflow => ({ id, name: id, machine: '八号机', target: 'Q11', qubits: 1, type: 'adaptive', policy: 'strict', scenario: 'normal', mode: 'real', experiment: 'single', status: 'running', engineState: 'acquiring', owner: '', createdAt: '', progress: 0, elapsed: 0, iterations: 0, events: [], approved: false, currentNodeId: 'n1', nodes: [{ id: 'n1', name: 'S21', label: '找腔', qubits: 'Q11', detail: '', status: 'running', engineState: 'acquiring', progress: 0, attempt: 1, dependsOn: [], capability: 'available' }] });

test('workspace stop requests stay task scoped while another task receipt is pending', async () => {
  const render = harness(); const a = workflow('A'), b = workflow('B');
  const sent: unknown[][] = []; const finish: Array<() => void> = [];
  const base = { gateway: {}, workflows: [a, b], busy: false, onBusy() {}, onCreate() {}, onSelectWorkflow() {}, onSelectNode() {}, onBlueprint() {}, onParameterDirty() {} };
  const props = (value: Workflow) => ({ ...base, workflow: value, node: value.nodes[0], onAction: (...args: unknown[]) => { sent.push([value.id, ...args]); return new Promise<void>(resolve => finish.push(resolve)); } });
  button(render(props(a)), '终止').onClick();
  assert.equal(button(render(props(a)), '正在请求终止…').disabled, true);
  let tree = render(props(b));
  assert.equal(button(tree, '暂停').disabled, false);
  assert.equal(button(tree, '终止').disabled, false);
  button(tree, '暂停').onClick();
  tree = render(props(b));
  assert.equal(button(tree, '正在请求暂停…').disabled, true);
  button(tree, '终止').onClick();
  button(render(props(b)), '正在请求终止…').onClick();
  assert.deepEqual(sent, [['A', 'abort', undefined], ['B', 'pause', undefined], ['B', 'abort', undefined]]);
  finish[0](); finish[1](); await Promise.resolve(); await Promise.resolve();
  assert.equal(button(render(props(b)), '正在请求终止…').disabled, true);
  finish[2](); await Promise.resolve(); await Promise.resolve();
  assert.equal(button(render(props(b)), '终止').disabled, false);
});
