/** Orthogonal SVG routing, independent of React and workflow execution. */
export interface FlowPoint { x: number; y: number; }
export interface FlowCard extends FlowPoint { id: string; width: number; height: number; }
export interface FlowEdge { from: string; to: string; kind?: string; }
export interface FlowRoute {
  edge: FlowEdge;
  points: FlowPoint[];
  path: string;
  rollback: boolean;
  label?: string;
  labelPoint?: FlowPoint;
}
interface PlannedRoute {
  edge: FlowEdge;
  source: number;
  target: number;
  rollback: boolean;
  reverse: boolean;
  direct: boolean;
  sourceGap?: { row: number; lane: number };
  targetGap?: { row: number; lane: number };
  outsideRail?: number;
}

/** Stable presentation order; never mutates controller nodes or execution state. */
export function topologicalFlowNodes<T extends { id: string }>(nodes: T[], edges: FlowEdge[]): T[] {
  const remaining = new Set(nodes.map(node => node.id));
  const dependencies = new Map(nodes.map(node => [node.id, new Set<string>()]));
  for (const edge of edges) {
    if (edge.kind === "rollback" || edge.kind === "backtrack" || !remaining.has(edge.from)) continue;
    dependencies.get(edge.to)?.add(edge.from);
  }
  const ordered: T[] = [];
  while (remaining.size) {
    const next = nodes.find(node => remaining.has(node.id) && ![...dependencies.get(node.id)!].some(id => remaining.has(id)));
    // A malformed/cyclic dependency remains visible; it is never relabelled rollback.
    if (!next) { ordered.push(...nodes.filter(node => remaining.has(node.id))); break; }
    ordered.push(next); remaining.delete(next.id);
  }
  return ordered;
}

export function buildFlowGeometry(
  nodes: { id: string; label?: string }[],
  edges: FlowEdge[],
  options: { columns?: number; cardWidth?: number; cardHeight?: number } = {},
) {
  nodes = topologicalFlowNodes(nodes, edges);
  const columns = Math.max(1, options.columns ?? 4);
  const cardWidth = options.cardWidth ?? 205;
  const cardHeight = options.cardHeight ?? 94;
  const gapX = 52;
  const laneSpacing = 17;
  const indexes = new Map(nodes.map((node, index) => [node.id, index]));
  const rowOf = (index: number) => Math.floor(index / columns);
  const columnOf = (index: number) => rowOf(index) % 2 ? columns - 1 - index % columns : index % columns;
  const gapUses = new Map<number, number>();
  const allocateGap = (row: number) => {
    const lane = gapUses.get(row) ?? 0;
    gapUses.set(row, lane + 1);
    return { row, lane };
  };
  let outsideCount = 0;
  const plans: PlannedRoute[] = [];
  for (const edge of edges) {
    const source = indexes.get(edge.from), target = indexes.get(edge.to);
    if (source === undefined || target === undefined) continue;
    const sourceRow = rowOf(source), targetRow = rowOf(target);
    const rollback = edge.kind === "rollback" || edge.kind === "backtrack";
    const reverse = rollback || target <= source;
    const horizontalNeighbors = sourceRow === targetRow && Math.abs(columnOf(source) - columnOf(target)) === 1;
    const verticalNeighbors = targetRow === sourceRow + 1 && columnOf(source) === columnOf(target);
    if (!reverse && (horizontalNeighbors || verticalNeighbors)) {
      plans.push({ edge, source, target, rollback, reverse, direct: true });
    } else if (!reverse && sourceRow === targetRow) {
      // Every long same-row dependency has a distinct lane below the cards.
      const lane = allocateGap(sourceRow);
      plans.push({ edge, source, target, rollback, reverse, direct: false, sourceGap: lane, targetGap: lane });
    } else {
      // Cross-row and backward links leave the grid before changing rows.
      const sourceGap = allocateGap(reverse ? sourceRow - 1 : sourceRow);
      const targetGap = allocateGap(reverse ? targetRow : targetRow - 1);
      plans.push({ edge, source, target, rollback, reverse, direct: false, sourceGap, targetGap, outsideRail: outsideCount++ });
    }
  }
  const maxGapLanes = Math.max(0, ...gapUses.values());
  const gapY = Math.max(66, maxGapLanes * laneSpacing + 34);
  const topMargin = 32 + (gapUses.get(-1) ?? 0) * laneSpacing;
  const leftMargin = 24;
  const gridWidth = columns * cardWidth + (columns - 1) * gapX;
  const rightRailBase = leftMargin + gridWidth + 22;
  const cards: FlowCard[] = nodes.map((node, index) => ({
    id: node.id,
    x: leftMargin + columnOf(index) * (cardWidth + gapX),
    y: topMargin + rowOf(index) * (cardHeight + gapY),
    width: cardWidth,
    height: cardHeight,
  }));
  const gapYAt = (gap: { row: number; lane: number }) => gap.row < 0
    ? 16 + gap.lane * laneSpacing
    : topMargin + gap.row * (cardHeight + gapY) + cardHeight + 18 + gap.lane * laneSpacing;
  const sourceTotals = new Map<string, number>(), targetTotals = new Map<string, number>();
  const sourcePorts = new Map<string, number>(), targetPorts = new Map<string, number>();
  for (const plan of plans.filter(plan => !plan.direct)) {
    sourceTotals.set(plan.edge.from, (sourceTotals.get(plan.edge.from) ?? 0) + 1);
    targetTotals.set(plan.edge.to, (targetTotals.get(plan.edge.to) ?? 0) + 1);
  }
  const portX = (card: FlowCard, ports: Map<string, number>, totals: Map<string, number>) => {
    const slot = ports.get(card.id) ?? 0;
    ports.set(card.id, slot + 1);
    return card.x + card.width * (0.25 + 0.5 * (slot + 1) / ((totals.get(card.id) ?? 1) + 1));
  };
  const routes: FlowRoute[] = plans.map(plan => {
    const a = cards[plan.source], b = cards[plan.target];
    let points: FlowPoint[];
    if (plan.direct && a.y === b.y) {
      const forward = b.x > a.x;
      points = [{ x: a.x + (forward ? cardWidth : 0), y: a.y + cardHeight / 2 }, { x: b.x + (forward ? 0 : cardWidth), y: b.y + cardHeight / 2 }];
    } else if (plan.direct) {
      points = [{ x: a.x + cardWidth / 2, y: a.y + cardHeight }, { x: b.x + cardWidth / 2, y: b.y }];
    } else {
      const sourceX = portX(a, sourcePorts, sourceTotals), targetX = portX(b, targetPorts, targetTotals);
      const sourceY = plan.reverse ? a.y : a.y + cardHeight;
      const targetY = plan.reverse || a.y === b.y ? b.y + cardHeight : b.y;
      const laneY = gapYAt(plan.sourceGap!), targetLaneY = gapYAt(plan.targetGap!);
      points = [{ x: sourceX, y: sourceY }, { x: sourceX, y: laneY }];
      if (plan.outsideRail !== undefined) {
        const railX = rightRailBase + plan.outsideRail * laneSpacing;
        points.push({ x: railX, y: laneY }, { x: railX, y: targetLaneY });
      }
      points.push({ x: targetX, y: targetLaneY }, { x: targetX, y: targetY });
    }
    let labelPoint: FlowPoint | undefined;
    if (!plan.direct) {
      let longest = 0;
      for (let i = 1; i < points.length; i++) {
        const before = points[i - 1], after = points[i];
        if (before.y === after.y && Math.abs(after.x - before.x) > longest) {
          longest = Math.abs(after.x - before.x);
          labelPoint = { x: (before.x + after.x) / 2, y: before.y - 5 };
        }
      }
    }
    const targetLabel = nodes[plan.target].label ?? plan.edge.to;
    return {
      edge: plan.edge, points, rollback: plan.rollback,
      path: points.map((point, index) => `${index ? "L" : "M"}${point.x} ${point.y}`).join(" "),
      label: plan.direct ? undefined : `${plan.rollback ? "回退" : "依赖"} → ${targetLabel}`,
      labelPoint,
    };
  });
  const lastRow = Math.max(0, Math.ceil(nodes.length / columns) - 1);
  return {
    cards, routes,
    width: rightRailBase + Math.max(0, outsideCount - 1) * laneSpacing + 28,
    height: topMargin + lastRow * (cardHeight + gapY) + cardHeight + 38 + (gapUses.get(lastRow) ?? 0) * laneSpacing,
  };
}

/** Open rectangle intersection: touching a card boundary is a valid connection port. */
export function segmentCrossesCard(a: FlowPoint, b: FlowPoint, card: FlowCard): boolean {
  if (a.x === b.x) return a.x > card.x && a.x < card.x + card.width
    && Math.max(a.y, b.y) > card.y && Math.min(a.y, b.y) < card.y + card.height;
  if (a.y === b.y) return a.y > card.y && a.y < card.y + card.height
    && Math.max(a.x, b.x) > card.x && Math.min(a.x, b.x) < card.x + card.width;
  throw new Error("Flow segments must be orthogonal");
}
