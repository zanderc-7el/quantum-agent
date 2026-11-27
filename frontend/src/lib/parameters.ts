import type { ParameterBounds } from "../types.ts";

export function numericParameterBounds(bounds: ParameterBounds = {}): ParameterBounds {
  return Object.fromEntries(Object.entries(bounds).flatMap(([key, rule]) => {
    if (!rule || typeof rule !== "object") return [];
    if (rule.type === 'integerArray') return [];
    if (rule.enum) {
      if (!Array.isArray(rule.enum) || !rule.enum.length || rule.enum.some(value => typeof value !== "number" || !Number.isFinite(value))) return [];
      return [[key, { ...rule, min: Number.isFinite(rule.min) ? rule.min : Math.min(...rule.enum), max: Number.isFinite(rule.max) ? rule.max : Math.max(...rule.enum) }]];
    }
    return Number.isFinite(rule.min) && Number.isFinite(rule.max) && rule.min <= rule.max ? [[key, rule]] : [];
  }));
}

export function validateParameterChanges(values: Record<string, string | number>, bounds: ParameterBounds, allowedKeys = Object.keys(bounds)) {
  const allowed = new Set(allowedKeys);
  const parameters: Record<string, number> = {};
  for (const [key, raw] of Object.entries(values)) {
    if (!Object.hasOwn(bounds, key) || !allowed.has(key)) throw new Error(`参数 ${key} 未获准修改`);
    const rule = bounds[key];
    if (typeof raw !== "number" && (typeof raw !== "string" || !raw.trim())) throw new Error(`${key} 请输入数值`);
    const value = typeof raw === "number" ? raw : Number(raw);
    if (!Number.isFinite(value)) throw new Error(`${key} 必须为有限数值`);
    if (!Number.isFinite(rule.min) || !Number.isFinite(rule.max) || rule.min > rule.max) throw new Error(`${key} 的后端范围无效`);
    if (value < rule.min || value > rule.max) throw new Error(`${key} 需在 ${rule.min} 至 ${rule.max} 之间`);
    if (rule.integer && !Number.isInteger(value)) throw new Error(`${key} 必须为整数`);
    if (rule.enum && !rule.enum.includes(value)) throw new Error(`${key} 需选择允许值：${rule.enum.join("、")}`);
    parameters[key] = value;
  }
  if (!Object.keys(parameters).length) throw new Error("请至少修改一个参数");
  return parameters;
}
