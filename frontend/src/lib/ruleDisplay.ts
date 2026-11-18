import type { Rule } from "../types.ts";

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
      { label: "记录的适用范围", value: rule.scope },
      { label: "触发条件", value: rule.trigger },
      { label: suggestion ? "建议动作" : "记录动作", value: rule.action },
      { label: suggestion ? "建议的验证要求" : "记录的验证要求", value: rule.verification },
    ],
  };
}
