import type { ConversationInspection } from '../lib/workspaceGateway';

const toolLabels: Record<string, string> = {
  search_source: '源码检索', source_search: '源码检索', read_source: '读取源码', source_read: '读取源码',
  inspect_workflow: '任务检查', inspect_runtime: '运行状态', inspect_configuration: '配置检查',
};
const runtimeLabels: Record<string, string> = {health: '设备与服务', catalog: '测量方法', resources: '比特资源', workflows: '实验任务', attention: '待处理事项'};

function sourceLabel(item: ConversationInspection) {
  const source = item.source && typeof item.source === 'object' ? item.source as Record<string, unknown> : {};
  const args = item.arguments ?? {};
  const file = typeof item.source === 'string' ? item.source : source.path ?? source.file ?? source.relativePath ?? args.path;
  if (typeof file === 'string' && !file.startsWith('/api/')) {
    const start = source.startLine ?? source.line ?? source.lineStart ?? args.startLine;
    const end = source.endLine ?? source.lineEnd ?? args.endLine;
    return `${file}${typeof start === 'number' ? `:${start}${typeof end === 'number' && end !== start ? `–${end}` : ''}` : ''}`;
  }
  if (item.tool === 'inspect_runtime') return runtimeLabels[String(args.kind)] ?? '运行状态';
  if (item.tool === 'inspect_configuration') return args.kind === 'deployment' ? '接线配置' : '芯片配置';
  if (item.tool === 'inspect_workflow') return args.nodeId ? '任务节点' : '实验任务';
  return typeof args.query === 'string' ? `检索：${args.query}` : undefined;
}

export default function ConversationEvidence({items}: {items?: ConversationInspection[]}) {
  if (!items?.length) return null;
  return <details className="wc-evidence"><summary>查看依据</summary>{items.map((item, index) => {
    const source = sourceLabel(item);
    const stamp = item.checkedAt ? new Date(item.checkedAt) : undefined;
    return <section key={index} className="wc-evidence-item">
      <div><strong>{toolLabels[item.tool] ?? '检查记录'}</strong><span>{item.ok ? '已检查' : '未完成'}</span></div>
      {source && <p>{source}</p>}
      {typeof item.summary === 'string' && item.summary && item.summary !== source && <p>{item.summary}</p>}
      {stamp && Number.isFinite(stamp.getTime()) && <time dateTime={item.checkedAt}>{stamp.toLocaleString('zh-CN')}</time>}
      <details><summary>技术详情</summary><pre>{JSON.stringify(item, null, 2)}</pre></details>
    </section>;
  })}</details>;
}
