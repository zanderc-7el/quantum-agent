import type { SupplementalQuality as Receipt } from "../types";

export default function SupplementalQuality({ receipt }: { receipt?: Receipt }) {
  if (!receipt) return null;
  const labels = { selection: '候选测量', verification: '独立复测', cross: '两次采集的一致性' };
  const statuses = { passed: '通过', failed: '未通过', unverified: '尚未验证' };
  return <details className="rp-provenance"><summary>重复性补充验收 · {receipt.accepted ? '通过' : '尚未全部通过'} · {receipt.policyVersion}</summary>
    {(['selection', 'verification', 'cross'] as const).map(stage => {
      const item = receipt.receipts[stage];
      return <section key={stage}><h4>{labels[stage]} · {item ? statuses[item.status] : '尚未执行'}</h4>
        {!!item?.reasons.length && <ul className="rp-quality-reasons">{item.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>}
        {item && <details><summary>逐项检查与数据依据</summary><pre>{JSON.stringify({ checks: item.checks, evidenceHash: item.evidenceHash, evidence: item.registeredEvidence }, null, 2)}</pre></details>}
      </section>;
    })}
  </details>;
}
