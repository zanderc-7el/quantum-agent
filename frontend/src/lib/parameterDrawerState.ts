import type { ExperimentNode, Workflow } from '../types.ts';

/** Resolve against the latest store snapshot; never hold an old node object. */
export function selectDrawerNode(workflow: Workflow, pinnedNodeId?: string): ExperimentNode | undefined {
  const pinned = pinnedNodeId && workflow.nodes.find(node => node.id === pinnedNodeId);
  if (pinned) return pinned;
  const active = workflow.nodes.find(node => node.status === 'attention')
    || workflow.nodes.find(node => node.status === 'running');
  if (active) return active;
  const terminal = workflow.status === 'aborted' || workflow.status === 'completed' || workflow.status === 'failed';
  if (!terminal) {
    const next = workflow.nodes.find(node => node.status === 'pending');
    if (next) return next;
  }
  return [...workflow.nodes].reverse().find(node => node.attempt > 0 || node.progress > 0)
    || workflow.nodes[0];
}
