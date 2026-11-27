import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { assistantReplyPresentation } from '../src/lib/assistantReplyPresentation.ts';

test('recorded Chinese response labels are readable without changing frequencies, SNR, time or counts', () => {
  const original='截至 acquisitionState 快照（北京时间 2026-11-27 03:12:43），首复测均为 7.0096766667 GHz，信噪比 40.60/46.27。\n节点状态为 verified、质量 passed。\n已完成 513/610；该操作回执仍为 pending。';
  const shown=assistantReplyPresentation(original);
  assert.equal(shown.changed,true);
  assert.equal(shown.text,'截至 采集状态快照（北京时间 2026-11-27 03:12:43），首复测均为 7.0096766667 GHz，信噪比 40.60/46.27。\n节点状态为已验证、诊断门槛已通过。\n已完成 513/610；该操作回执仍待完成。');
  assert.ok(!shown.text.includes('尚未启动'));
});

test('English prose, people, identifiers and explicit technical explanations are unchanged', () => {
  for(const original of ['The test passed; verified by Pending.','张 passed 提到 pending；没有状态说明。',
    '状态 verified_user；质量 surpassed；回执 pendingTask。',
    '解释字段 acquisitionState：状态为 verified，质量 passed，回执 pending。',
    'API 枚举说明：状态为 verified。']) assert.deepEqual(assistantReplyPresentation(original),{text:original,changed:false});
});

test('fenced code, inline code and indented code preserve original bytes', () => {
  const original='`状态为 verified`，`质量 passed`。\n```json\n状态为 verified\n```\n~~~text\n操作回执 pending\n~~~\n    状态为 verified';
  assert.deepEqual(assistantReplyPresentation(original),{text:original,changed:false});
});

test('the existing reply renderer keeps the exact original in collapsed technical details', () => {
  const compiled=buildSync({entryPoints:[fileURLToPath(new URL('../src/components/AssistantReply.tsx',import.meta.url))],
    platform:'node',packages:'external',format:'cjs',bundle:true,write:false,jsx:'automatic',loader:{'.css':'empty'}}).outputFiles[0].text;
  const module={exports:{} as {default:(props:{text:string})=>ReturnType<typeof createElement>}};
  new Function('require','module','exports',compiled)(createRequire(import.meta.url),module,module.exports);
  const original='节点状态为 verified、质量 passed。回执仍为 pending。';
  const html=renderToStaticMarkup(createElement(module.exports.default,{text:original}));
  assert.match(html,/<p>节点状态为已验证、诊断门槛已通过。回执仍待完成。<\/p>/);
  assert.ok(html.includes('<details><summary>原始回复与技术详情</summary><pre>'+original+'</pre></details>'));
  assert.doesNotMatch(html,/<details open/);
  const code='字段说明：状态为 verified。';
  assert.doesNotMatch(renderToStaticMarkup(createElement(module.exports.default,{text:code})),/<details/);
});

test('normal Chinese acquisition terms keep the exact numbers and explicit technical questions keep original names', async () => {
  const {technicalReplyRequested}=await import('../src/lib/assistantReplyPresentation.ts');
  const original='首测（selection）和独立复测（verification）均为 7.009676666666667 GHz；对比度信噪比（contrast_snr）为 40.600399 和 46.266314。selection 操作已到 12/84。';
  const display=assistantReplyPresentation(original);
  assert.equal(display.text,'首测和独立复测均为 7.009676666666667 GHz；对比度信噪比为 40.600399 和 46.266314。首测操作已到 12/84。');
  const rows=[{role:'user',content:'把原始字段列出来'},{role:'system',content:''},{role:'assistant',content:original},
    {role:'user',content:'采到哪里了？'},{role:'assistant',content:original}];
  assert.deepEqual(assistantReplyPresentation(original,technicalReplyRequested(rows,2)),{text:original,changed:false});
  assert.equal(technicalReplyRequested(rows,4),false);
  for(const line of ['请解释字段（selection）和（verification）','The selection operation is pending.','`selection 操作`']) {
    assert.equal(assistantReplyPresentation(line).text,line);
  }
});
