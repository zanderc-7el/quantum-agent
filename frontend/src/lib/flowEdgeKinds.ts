import type { FlowEdge } from "./flowGeometry";

export type FlowEdgeKind = "main" | "dependency" | "rollback";

/** Presentation only: the backend's dependencies remain intact, including shortcuts.
 * Its auxiliary role describes an acquisition, not whether its edges form the main path.
 * In a DAG, irreducible dependencies form the main flow; alternate paths identify
 * cross-step links. Cyclic/missing-node dependencies are never presented as rollbacks.
 */
export function classifyFlowEdges(nodes: { id: string }[], edges: FlowEdge[]): Map<FlowEdge, FlowEdgeKind> {
  const ids = new Set(nodes.map(node => node.id));
  const forward = new Map(nodes.map(node => [node.id, new Set<string>()]));
  const isRollback = (edge: FlowEdge) => edge.kind === "rollback" || edge.kind === "backtrack";
  for (const edge of edges) {
    if (!isRollback(edge) && ids.has(edge.from) && ids.has(edge.to)) forward.get(edge.from)!.add(edge.to);
  }
  function reachable(from: string, to: string, omit?: FlowEdge): boolean {
    const pending = [from], seen = new Set<string>();
    while (pending.length) {
      const current = pending.pop()!;
      if (seen.has(current)) continue;
      seen.add(current);
      for (const next of forward.get(current) ?? []) {
        if (current === omit?.from && next === omit.to) continue;
        if (next === to) return true;
        pending.push(next);
      }
    }
    return false;
  }
  return new Map(edges.map(edge => {
    const kind: FlowEdgeKind = isRollback(edge) ? "rollback"
      : !ids.has(edge.from) || !ids.has(edge.to) || edge.from === edge.to
        || reachable(edge.to, edge.from) || reachable(edge.from, edge.to, edge) ? "dependency" : "main";
    return [edge, kind];
  }));
}
