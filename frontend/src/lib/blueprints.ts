import type { Workflow } from "../types.ts";

export type BlueprintKind = Workflow["experiment"];

export const BLUEPRINTS = {
  single: { title: "单比特校准蓝图", description: "读出频率、F01、Rabi、读取优化与单比特性能", nodeCount: 9 },
  two: { title: "双比特 CZ 蓝图", description: "单比特前置检查与 CZ、XEB 校准", nodeCount: 17 },
} as const;

export const BLUEPRINT_STAGES = {
  single: [
    { title: "读出定频", nodes: ["ReadoutFreqDip"] },
    { title: "频谱与驱动", nodes: ["F01Spectroscopy", "XYGateAmpRabiImpl"] },
    { title: "读取优化", nodes: ["IqToProbability", "ReadoutParaSweepByIQProb", "IqToProbabilityRetest"] },
    { title: "相干与基准", nodes: ["T1Idle", "T2CPMG", "RB1QCal"] },
  ],
  two: [
    { title: "单比特前置", nodes: ["ReadoutFreqDip", "F01Spectroscopy", "XYGateAmpRabiImpl", "IqToProbability", "ReadoutParaSweepByIQProb", "IqToProbabilityRetest", "T1Idle", "T2CPMG", "RB1QCal"] },
    { title: "CZ 与 XEB", nodes: ["F01ZPulseDependency", "ZeroPointFinderByQQSwap", "CZSwapSweepZPulse", "TTGAczDelicateG", "TTGAczDelicateDetune", "CZXEBCal", "CZParaOptimizer", "CZXEBCalRetest"] },
  ],
} as const;

/** Explicit cross-blueprint requests must not create work under the active blueprint. */
export function requestedBlueprint(text: string): BlueprintKind | null {
  if (/双比特|\bCZ\b|CZXEB|耦合器/i.test(text)) return "two";
  if (/单比特|\b1\s*bit\b/i.test(text)) return "single";
  return null;
}

export function allowsBlueprintIntent(active: BlueprintKind, text: string): boolean {
  const requested = requestedBlueprint(text);
  return requested === null || requested === active;
}
