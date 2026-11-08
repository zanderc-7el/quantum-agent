/** A deterministic spectroscopy simulator, not a physical fit or device recipe. */
export const SPECTRUM_PROFILE = Object.freeze({ minHz: 4_500_000_000, maxHz: 5_800_000_000,
  resonanceHz: 5_100_000_000, maxPoints: 601 });
export const F01_SPECTRUM_SKILL = Object.freeze({ id: "f01-spectrum", version: 1,
  intent: "在已验证读出参数基础上寻找 Q12 的 0→1 频谱候选",
  command: "f01_spectroscopy", requiredInputs: ["targetQubitId", "centerHz", "spanHz", "points", "sourceReadoutRevision", "motivation"],
  expectedArtifacts: ["raw_points", "analysis", "decision_audit"],
  edges: ["accept_candidate", "retry_wider_scan", "human_review"], dataSource: "simulator" });

export function sampleSpectrum(input) {
  const { centerHz, spanHz, points } = input.parameters;
  return Array.from({ length: points }, (_, index) => {
    const frequencyHz = centerHz - spanHz / 2 + spanHz * index / (points - 1);
    const detuning = (frequencyHz - SPECTRUM_PROFILE.resonanceHz) / 8_000_000;
    const response = 0.1 + 0.75 / (1 + detuning * detuning) + Math.sin(index * 1.31) * 0.002;
    return { frequencyHz, response: Number(response.toFixed(6)) };
  });
}

export function analyzeSpectrum(points) {
  if (points.length < 3 || points.some(p => !Number.isFinite(p.frequencyHz) || !Number.isFinite(p.response)))
    return { quality: "inconclusive", reason: "DATA_INCOMPLETE", candidateHz: null };
  let peak = 0;
  for (let i = 1; i < points.length; i++) if (points[i].response > points[peak].response) peak = i;
  if (peak === 0 || peak === points.length - 1)
    return { quality: "inconclusive", reason: "BOUNDARY_MAXIMUM", candidateHz: null };
  const height = points[peak].response - (points[0].response + points.at(-1).response) / 2;
  if (height < 0.1) return { quality: "inconclusive", reason: "WEAK_PEAK", candidateHz: null };
  return { quality: "pass", reason: null, candidateHz: points[peak].frequencyHz,
    peakHeight: Number(height.toFixed(5)), method: "discrete-maximum-demo-v1",
    note: "模拟频谱候选；尚非物理拟合或已验证的 0→1 跃迁" };
}

export function decideSpectrum({ analysis, points, input, artifact, followupDepth = 0, upstream }) {
  const evidence = { artifactId: artifact?.artifactId ?? null, artifactSha256: artifact?.sha256 ?? null,
    analysisMethod: analysis?.method ?? null, pointCount: points.length,
    skillId: F01_SPECTRUM_SKILL.id, skillVersion: 1,
    sourceReadoutRevision: upstream?.revision ?? null, sourceReadoutArtifactSha256: upstream?.artifactSha256 ?? null };
  if (!artifact?.complete || points.length !== input.parameters.points || !upstream?.artifactSha256)
    return { action: "human_review", reason: "INCOMPLETE_EVIDENCE", candidateHz: null, publishAllowed: false, evidence };
  if (analysis?.quality === "pass" && Number.isFinite(analysis.candidateHz))
    return { action: "accept_candidate", reason: "SIMULATOR_GATE_PASS", candidateHz: analysis.candidateHz,
      publishAllowed: false, evidence };
  if (followupDepth >= 4)
    return { action: "human_review", reason: "RETRY_BUDGET_EXHAUSTED", candidateHz: null, publishAllowed: false, evidence };
  const p = input.parameters;
  if (analysis?.reason === "BOUNDARY_MAXIMUM" &&
      p.spanHz * 2 <= 2 * Math.min(p.centerHz - SPECTRUM_PROFILE.minHz, SPECTRUM_PROFILE.maxHz - p.centerHz))
    return { action: "retry_wider_scan", reason: "BOUNDARY_MAXIMUM", candidateHz: null,
      proposal: { centerHz: p.centerHz, spanHz: p.spanHz * 2, points: Math.min(p.points * 2 - 1, 601) },
      publishAllowed: false, evidence };
  return { action: "human_review", reason: analysis?.reason ?? "QUALITY_GATE_FAILED",
    candidateHz: null, publishAllowed: false, evidence };
}
