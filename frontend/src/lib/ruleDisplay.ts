import type { Rule } from "../types.ts";

// Original owner names from vendor/experience/config/agent_catalog.json.
const scopeLabels: Record<string, string> = {
  s01_readout_frequency: '读取腔定频', s02_qubit_frequency: '比特定频', s03_rabi_amplitude: 'Rabi定幅',
  s04_readout_fidelity: '读取保真度测试', s05_readout_optimization: '读取参数优化', s06_t1: 'T1测试',
  s07_t2: 'T2测试', s08_single_qubit_rb: '单比特RB测试', d01_flux_spectroscopy: '二维比特能谱',
  d02_idle_interaction: '两比特闲置工作点', d03_cz_width: 'CZ宽度粗扫', d04_cz_amplitude: 'CZ幅度精调',
  d05_cz_detuning: 'CZ失谐与条件相位精调', d06_cz_xeb: 'CZ XEB测试', d07_cz_optimizer: 'CZ参数优化', supervisor: '流程总控',
};
const actionLabels: Record<string, string> = {
  reject_and_split_capture: '拒绝当前采集请求，拆分为多次采集', redesign_scan: '重新设计扫描',
  invalidate_dependents: '将受影响的后续实验标记为待复测', wait_for_signcheck: '等待频率修正方向验证',
  restrict_claim: '按已有证据限定结论', validate_paired_evidence: '验证配对采集证据',
  reconcile: '核查执行回执', backtrack: '回退到前置实验', retry: '重新测量', adjust: '调整参数',
  suggest_parameters: '建议调整参数', abort: '终止任务', pause: '暂停任务', approve: '确认结果',
};
const record = (value: unknown): Record<string, unknown> | undefined => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
const prose = (value: unknown, fallback: string): string => typeof value === 'string' && value.trim()
  ? /[\u3400-\u9fff]/.test(value) ? value : fallback
  : value == null ? '未提供' : fallback;
function scopeText(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.map(scopeText).join('、') : '未提供';
  if (typeof value === 'string') return scopeLabels[value] ?? prose(value, '未翻译的范围，见原始字段');
  const object = record(value);
  if (object) {
    const parts: string[] = [];
    if (typeof object.resourceId === 'string') parts.push(object.resourceId);
    if (typeof object.ownerId === 'string') parts.push(scopeText(object.ownerId));
    if (Object.keys(object).some(key => !['resourceId', 'ownerId'].includes(key)) || !parts.length) parts.push('其他范围条件，见原始字段');
    return parts.join(' · ');
  }
  return value == null ? '未提供' : '范围条件见原始字段';
}
function actionText(value: unknown): string {
  const object = record(value), kind = object?.kind ?? value;
  const label = typeof kind === 'string' ? actionLabels[kind] ?? prose(kind, '未翻译的动作，见原始字段') : '动作内容见原始字段';
  return object && Object.keys(object).some(key => !['kind', 'automaticExecution'].includes(key)) ? `${label}；具体设置见原始字段` : label;
}

/** Display recorded policy metadata, not a claim that an action was bound or run. */
export function ruleDisplay(rule: Rule) {
  const candidate = rule.policyStatus === "scoped_candidate_rule";
  const action = rule.action && typeof rule.action === "object" && !Array.isArray(rule.action)
    ? rule.action as Record<string, unknown> : undefined;
  const hasPolicyStatus = typeof rule.policyStatus === "string" && !!rule.policyStatus.trim();
  const publishedSuggestion = !hasPolicyStatus && typeof rule.publishedAt === "string"
    && !!rule.publishedAt.trim() && action?.automaticExecution === false;
  const suggestion = candidate || publishedSuggestion;
  return {
    statusLabel: candidate ? "经验建议 · 未发布为自动规则"
      : publishedSuggestion ? "已发布经验建议 · 不自动执行"
      : "规则条目 · 自动执行状态未确认",
    statusValue: hasPolicyStatus ? rule.policyStatus! : "未声明",
    fields: [
      { label: "记录的适用范围", value: rule.scope, text: scopeText(rule.scope) },
      { label: "触发条件", value: rule.trigger, text: prose(rule.trigger, '触发条件见原始字段') },
      { label: suggestion ? "建议动作" : "记录动作", value: rule.action, text: actionText(rule.action) },
      { label: suggestion ? "建议的验证要求" : "记录的验证要求", value: rule.verification, text: prose(rule.verification, '验证要求见原始字段') },
    ],
  };
}
