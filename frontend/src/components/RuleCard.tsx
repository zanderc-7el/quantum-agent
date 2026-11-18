import { BookOpen } from "lucide-react";
import type { Rule } from "../types";
import { ruleDisplay } from "../lib/ruleDisplay";

const text = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "未提供";

export default function RuleCard({ rule }: { rule: Rule }) {
  const display = ruleDisplay(rule);
  return <article className="rp-rule">
    <div><BookOpen size={15} /><strong>{rule.title}</strong><small>v{rule.version}</small></div>
    <p className="rp-rule-status">{display.statusLabel}</p>
    <dl>{display.fields.map(field => <div key={field.label}><dt>{field.label}</dt><dd>{text(field.value)}</dd></div>)}</dl>
    <details>
      <summary>状态、依据与来源</summary>
      <dl><dt>规则 ID</dt><dd>{rule.id}</dd><dt>原始策略状态</dt><dd>{display.statusValue}</dd>
        {rule.publishedAt && <><dt>发布记录时间</dt><dd>{rule.publishedAt}</dd></>}
        {rule.sourceSha256 && <><dt>来源 SHA256</dt><dd>{rule.sourceSha256}</dd></>}
      </dl>
      {!!rule.evidence?.length && <ul>{rule.evidence.map((item, index) => <li key={index}>{text(item)}</li>)}</ul>}
    </details>
  </article>;
}
