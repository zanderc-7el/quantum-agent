import { readableParameter } from '../lib/manualParameters';

type Row = { label: string; value: string };
const namedScope = (name: string) => /^(?:Q|B|R)\d+$/.test(name) || /^[\p{Script=Han}\s\d（）()/-]+$/u.test(name);
const archiveScopes: Record<string,string> = { parameters: '参数', setting: '设置', ParaSpace: '扫描配置', effectiveParameters: '生效参数' };

export default function ReadableParameterSummary({ value, procedure }: { value: unknown; procedure?: string }) {
  const rows: Row[] = [];
  function walk(source: unknown, scopes: string[]) {
    if (!source || typeof source !== 'object' || Array.isArray(source)) return;
    const blocks = (source as Record<string, unknown>).acquisitions_per_point;
    const count = blocks && typeof blocks === 'object' && 'value' in blocks ? blocks.value : blocks;
    const hasBlocks = typeof count === 'number' && Number.isSafeInteger(count) && count > 0;
    for (const [key, entry] of Object.entries(source)) {
      const wrapped = entry !== null && typeof entry === 'object' && !Array.isArray(entry);
      if (wrapped && !Object.hasOwn(entry, 'value')) {
        if (namedScope(key)) walk(entry, [...scopes, key]);
        else if (Object.hasOwn(archiveScopes, key)) walk(entry, [...scopes, archiveScopes[key]]);
        continue;
      }
      const row = readableParameter(key, wrapped ? entry.value : entry,
        wrapped && typeof entry.unit === 'string' ? entry.unit : undefined, procedure, hasBlocks);
      if (row) rows.push({ ...row, label: [...scopes, row.label].join(' · ') });
    }
  }
  walk(value, []);
  return <>{rows.length > 0 && <dl className="rp-readable-parameters">{rows.map(row =>
    <div key={row.label}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>}
    <details><summary>参数原始结构</summary><pre>{JSON.stringify(value, null, 2) ?? '—'}</pre></details></>;
}

