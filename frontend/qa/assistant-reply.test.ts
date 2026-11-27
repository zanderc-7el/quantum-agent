import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const components = new URL('../src/components/', import.meta.url);
const compiled = buildSync({
  entryPoints: [fileURLToPath(new URL('AssistantReply.tsx', components))],
  platform: 'node', packages: 'external', format: 'cjs', bundle: true,
  write: false, loader: { '.css': 'empty' },
}).outputFiles[0].text;
const module = { exports: {} as { default: (props: {text: string}) => ReturnType<typeof createElement> } };
new Function('require', 'module', 'exports', compiled)(createRequire(import.meta.url), module, module.exports);
const render = (text: string) => renderToStaticMarkup(createElement(module.exports.default, { text }));

test('formats lab replies into readable paragraphs, headings, lists, emphasis and parameter names', () => {
  const html = render('### **Q11 读出**\r\n\r\n第一段\r\n第二行\r\n\r\n- 设置 `freq_center`\r\n- **核对 `S21`**\r\n\r\n3. 采集\n4. 复测');
  assert.match(html, /<h4><strong>Q11 读出<\/strong><\/h4>/);
  assert.match(html, /<p>第一段\n第二行<\/p>/);
  assert.match(html, /<ul><li>设置 <code>freq_center<\/code><\/li><li><strong>核对 <code>S21<\/code><\/strong><\/li><\/ul>/);
  assert.match(html, /<ol><li value="3">采集<\/li><li value="4">复测<\/li><\/ol>/);
});

test('renders HTML, URLs, images and command links as inert text', () => {
  const html = render('<img src=x onerror=alert(1)>\n[开始测量](qcal://start)\n![图](http://host/image)\n<script>doTask()</script>');
  assert.doesNotMatch(html, /<(?:a|button|img|iframe|script)\b|\s(?:href|src)=["']/i);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /\[开始测量\]\(qcal:\/\/start\)/);
  assert.match(html, /&lt;script&gt;doTask\(\)&lt;\/script&gt;/);
});

test('preserves scientific values, unmatched markers, code and indented list continuation', () => {
  const html = render('-45 dBm 至 +10 dBm\n\n- 本轮范围\n  7.0096 GHz\n\n`**literal**` **未配对\n\n```text\n**原值**\n- 1\n<a>\n```');
  assert.match(html, /<p>-45 dBm 至 \+10 dBm<\/p>/);
  assert.match(html, /<li>本轮范围\n7.0096 GHz<\/li>/);
  assert.match(html, /<code>\*\*literal\*\*<\/code> \*\*未配对/);
  assert.match(html, /<pre><code>\*\*原值\*\*\n- 1\n&lt;a&gt;<\/code><\/pre>/);
});

test('only model text is formatted in the global and workflow chats', () => {
  const read = (name: string) => readFileSync(new URL(name, components), 'utf8');
  assert.match(read('WorkspaceControl.tsx'), /m\.role==='assistant' \? <AssistantReply text=\{m\.content\} technical=\{technicalReplyRequested\(history, index\)\}\/\> : <div>\{m\.content\}<\/div>/);
  assert.match(read('WorkspaceControl.tsx'), /<AssistantReply text=\{m\.analysisMessage\} technical=\{technicalReplyRequested\(history, index\)\}\/\>/);
  for (const name of ['WorkflowFloatingChat.tsx', 'RemotePanels.tsx']) {
    assert.match(read(name), /message\.role === "assistant" \? <AssistantReply text=\{message\.(?:content|text)\} technical=\{technicalReplyRequested\(history, index\)\}\/\> : <(?:div|p)>/);
  }
});
