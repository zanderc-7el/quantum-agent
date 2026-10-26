import type { Policy } from "../types.ts";
export type Theme = "dark" | "light";
export interface Preferences {
  theme: Theme;
  displayName: string;
  workspaceName: string;
  defaultMachine: string;
  defaultPolicy: Policy;
  autoDemo: boolean;
  provider: "local" | "deepseek" | "minimax" | "openai" | "custom";
  model: string;
  endpoint: string;
}
export const PREFERENCES_KEY = "qcal-preferences-v1";
export const DEFAULT_PREFERENCES: Preferences = {
  theme: "dark",
  displayName: "Dr. L. Chen",
  workspaceName: "量子测控实验室",
  defaultMachine: "M7",
  defaultPolicy: "threshold",
  autoDemo: true,
  provider: "local",
  model: "",
  endpoint: "",
};
export function parsePreferences(raw: string | null): Preferences {
  try {
    const value = JSON.parse(raw || "{}");
    if (!value || typeof value !== "object") return { ...DEFAULT_PREFERENCES };
    const text = (key: keyof Preferences, max: number) =>
      typeof value[key] === "string" && value[key].trim()
        ? value[key].trim().slice(0, max)
        : DEFAULT_PREFERENCES[key];
    return {
      theme: value.theme === "light" ? "light" : "dark",
      displayName: text("displayName", 40) as string,
      workspaceName: text("workspaceName", 50) as string,
      defaultMachine: ["M7", "M3", "M5", "M8"].includes(value.defaultMachine)
        ? value.defaultMachine
        : "M7",
      defaultPolicy: ["strict", "threshold", "autopilot"].includes(
        value.defaultPolicy,
      )
        ? value.defaultPolicy
        : "threshold",
      autoDemo: typeof value.autoDemo === "boolean" ? value.autoDemo : true,
      provider: ["local", "deepseek", "minimax", "openai", "custom"].includes(
        value.provider,
      )
        ? value.provider
        : "local",
      model:
        typeof value.model === "string" ? value.model.trim().slice(0, 100) : "",
      endpoint:
        typeof value.endpoint === "string"
          ? value.endpoint.trim().slice(0, 250)
          : "",
    };
  } catch {
    return { ...DEFAULT_PREFERENCES };
  }
}
export function loadPreferences(): Preferences {
  try {
    return parsePreferences(localStorage.getItem(PREFERENCES_KEY));
  } catch {
    return { ...DEFAULT_PREFERENCES };
  }
}
export function persistPreferences(preferences: Preferences): boolean {
  try {
    localStorage.setItem(PREFERENCES_KEY, JSON.stringify(preferences));
    return true;
  } catch {
    return false;
  }
}
export function validateEndpoint(endpoint: string): boolean {
  if (!endpoint.trim()) return true;
  try {
    const url = new URL(endpoint);
    return (
      ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash
    );
  } catch {
    return false;
  }
}
