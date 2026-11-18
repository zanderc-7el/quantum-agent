export interface MembershipStatus {
  provider: "ziyor";
  configured: boolean;
  authenticated: boolean;
  user?: { name?: string; phoneMasked?: string };
  balance?: { remainingCalls?: number; label?: string };
  modelConnection: { provider: string; model?: string; active: boolean; verified: boolean; selection?: string; note?: string };
  capabilities: { sendCode: boolean; login: boolean; connect: boolean; test: boolean };
  error?: string | { message?: string; code?: string };
}
export interface MembershipModel { id: string; label: string; provider?: string; default?: boolean; [key: string]: unknown }
export interface MembershipModelCatalog { models: MembershipModel[]; source?: string; defaultModelId?: string }
export function preferredMembershipModel(catalog: MembershipModelCatalog, currentModel?: string) {
  const { models, defaultModelId } = catalog;
  return models.find(item => item.id === currentModel)?.id
    ?? models.find(item => item.id === defaultModelId)?.id
    ?? models.find(item => item.default)?.id
    ?? models.find(item => item.provider !== "existing" && item.id !== "__existing_provider__")?.id
    ?? models[0]?.id ?? "";
}
export interface SendCodeResult { status: "sent"; retryAfter: number }
export function membershipError(value: MembershipStatus["error"]) {
  return typeof value === "string" ? value : value?.message ?? (value ? "账户服务暂时不可用" : "");
}
export function modelProviderName(provider?: string) {
  return ({ ziyor: "Ziyor 会员", zeelin: "Ziyor 会员", codex_cli: "Codex CLI", codex: "Codex", existing: "独立模型连接", openai: "独立 API 连接", openai_compatible: "独立 API 连接" } as Record<string, string>)[provider ?? ""] ?? provider ?? "尚未连接";
}
export function actualPoints(status?: MembershipStatus) {
  const points = status?.balance?.remainingCalls;
  return status?.authenticated && typeof points === "number" && Number.isFinite(points) ? new Intl.NumberFormat("zh-CN").format(points) : undefined;
}
