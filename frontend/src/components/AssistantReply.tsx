import type { ReactNode } from 'react';
import { assistantReplyPresentation } from '../lib/assistantReplyPresentation';
import './assistant-reply.css';

function codeSpans(text: string): ReactNode[] {
  return text.split(/(`[^`\n]+`)/g).map((part, index) =>
    index % 2 ? <code key={index}>{part.slice(1, -1)}</code> : part);
}

function inline(text: string): ReactNode[] {
  return text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g).map((part, index) => {
    if (index % 2 === 0) return part;
    if (part.startsWith('`')) return <code key={index}>{part.slice(1, -1)}</code>;
    return <strong key={index}>{codeSpans(part.slice(2, -2))}</strong>;
  });
}

type Block =
  | { kind: 'paragraph' | 'heading' | 'code'; text: string }
  | { kind: 'ul' | 'ol'; items: { text: string; number?: number }[] };

function blocks(text: string) {
  const result: Block[] = [];
  let current: Block | undefined;
  let fence: string | undefined;
  for (const line of text.replace(/\r\n?/g, '\n').split('\n')) {
    if (fence && current?.kind === 'code') {
      if (line.trim() === fence) { fence = undefined; current = undefined; }
      else current.text += line + '\n';
      continue;
    }
    const opening = /^ {0,3}(`{3,})[\w.+-]*\s*$/.exec(line);
    if (opening) {
      fence = opening[1];
      current = { kind: 'code', text: '' };
      result.push(current);
      continue;
    }
    if (!line.trim()) { current = undefined; continue; }
    const heading = /^ {0,3}#{1,6}[ \t]+(.+)$/.exec(line);
    if (heading) {
      result.push({ kind: 'heading', text: heading[1] });
      current = undefined;
      continue;
    }
    const item = /^\s*(?:[-+*]|(\d{1,8})[.)])[ \t]+(.+)$/.exec(line);
    if (item) {
      const kind = item[1] === undefined ? 'ul' : 'ol';
      if (!current || current.kind !== kind) {
        current = { kind, items: [] };
        result.push(current);
      }
      current.items.push({ text: item[2], number: kind === 'ol' ? Number(item[1]) : undefined });
    } else if ((current?.kind === 'ol' || current?.kind === 'ul') && /^[ \t]+/.test(line)) {
      current.items[current.items.length - 1].text += '\n' + line.trimStart();
    } else if (current?.kind === 'paragraph') {
      current.text += '\n' + line;
    } else {
      current = { kind: 'paragraph', text: line };
      result.push(current);
    }
  }
  return result;
}

export default function AssistantReply({ text, technical = false }: { text: string; technical?: boolean }) {
  const presentation = assistantReplyPresentation(text, technical);
  return <div className="assistant-reply">{blocks(presentation.text).map((block, index) => {
    if ('items' in block) {
      const List = block.kind;
      return <List key={index}>{block.items.map((item, n) =>
        <li key={n} value={item.number}>{inline(item.text)}</li>)}</List>;
    }
    if (block.kind === 'code') return <pre key={index}><code>{block.text.replace(/\n$/, '')}</code></pre>;
    if (block.kind === 'heading') return <h4 key={index}>{inline(block.text)}</h4>;
    return <p key={index}>{inline(block.text)}</p>;
  })}{presentation.changed && <details><summary>原始回复与技术详情</summary><pre>{text}</pre></details>}</div>;
}
