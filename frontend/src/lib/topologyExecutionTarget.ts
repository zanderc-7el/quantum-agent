import type { ChipTopologyData } from './configurationDiagrams';
import type { Qubit } from './workspaceGateway';

/** A published resource can be selected explicitly even when its diagram is absent. */
export function isConfirmedExecutionQubit(qubit: Qubit | undefined): boolean {
  return !!qubit?.id?.trim() && qubit.identityStatus === 'confirmed' && !!qubit.physicalId?.trim();
}

/** Diagram labels are never resolved as aliases for an executable resource. */
export function topologyExecutionTarget(topology: ChipTopologyData | undefined, qubits: readonly Qubit[], id: string): { resourceId?: string; reason?: string } {
  const matches = qubits.filter(qubit => qubit.id === id);
  if (matches.length !== 1) return { reason: '此编号不能唯一对应当前芯片版本的实验对象，仅供浏览。' };
  if (!isConfirmedExecutionQubit(matches[0])) return { reason: '此编号的实测身份尚未确认，仅供浏览。' };
  const nodes = (topology?.nodes ?? []).filter(node => node.resourceId === id);
  if (nodes.length !== 1 || !Number.isFinite(nodes[0].x) || !Number.isFinite(nodes[0].y)) return { reason: '此实验对象尚无唯一有效的版图坐标，请从实验对象列表选择。' };
  return { resourceId: id };
}
