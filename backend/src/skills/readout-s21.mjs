/** Simulator-only Skill decision gate. Hardware thresholds require local sign-off. */
export const READOUT_S21_SKILL = Object.freeze({
  id: "readout-s21", version: 1,
  intent: "定位目标比特的读出谐振峰，并形成待审核的候选频率",
  command: "readout_resonator_scan",
  requiredInputs: ["targetQubitId", "centerHz", "spanHz", "points", "motivation"],
  expectedArtifacts: ["raw_points", "analysis", "decision_audit"],
  edges: ["accept_candidate", "retry_wider_scan", "human_review"],
  dataSource: "simulator",
});

export function decideReadoutS21({ analysis, points, input, artifact, followupDepth = 0 }) {
  const evidence = { artifactId: artifact?.artifactId ?? null, artifactSha256: artifact?.sha256 ?? null,
    analysisMethod: analysis?.method ?? null, pointCount: points.length, skillId: READOUT_S21_SKILL.id,
    skillVersion: READOUT_S21_SKILL.version };
  if (!artifact?.complete || !analysis || points.length !== input.parameters.points)
    return { action: "human_review", reason: "INCOMPLETE_EVIDENCE", candidateHz: null, evidence };
  if (analysis.quality === "pass" && Number.isFinite(analysis.candidateHz))
    return { action: "accept_candidate", reason: "SIMULATOR_GATE_PASS", candidateHz: analysis.candidateHz,
      publishAllowed: false, evidence };
  if (followupDepth >= 4)
    return { action: "human_review", reason: "RETRY_BUDGET_EXHAUSTED", candidateHz: null,
      publishAllowed: false, evidence };
  if (analysis.reason === "BOUNDARY_MINIMUM" &&
      input.parameters.spanHz * 2 <= 2 * Math.min(input.parameters.centerHz - 5_000_000_000, 7_000_000_000 - input.parameters.centerHz))
    return { action: "retry_wider_scan", reason: "BOUNDARY_MINIMUM", candidateHz: null,
      proposal: { centerHz: input.parameters.centerHz, spanHz: input.parameters.spanHz * 2,
        points: Math.min(input.parameters.points * 2 - 1, 601) }, publishAllowed: false, evidence };
  return { action: "human_review", reason: analysis.reason ?? "QUALITY_GATE_FAILED", candidateHz: null,
    publishAllowed: false, evidence };
}
