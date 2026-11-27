import type {ResultComparisonSource} from './resultComparisonSource';

const views = ['dashboard', 'blueprint', 'manual', 'chat', 'agent', 'chips', 'campaigns', 'science'] as const;
export type WorkspaceView = typeof views[number];
export type WorkspaceRoute = {view: WorkspaceView; comparisonSource?: ResultComparisonSource};

export function workspaceRoute(hash: string): WorkspaceRoute {
  const [name, search] = hash.replace(/^#/, '').split('?', 2);
  const view = views.find(item => item === name) ?? 'agent';
  if (view !== 'science' || !search) return {view};
  const params = new URLSearchParams(search);
  if (params.has('panel') && params.get('panel') !== 'signals') return {view};
  const workflowId = params.get('workflow');
  const nodeId = params.get('node');
  const attemptId = params.get('attempt');
  return workflowId && nodeId && attemptId
    ? {view, comparisonSource: {workflowId, nodeId, attemptId, ...(params.get('panel') === 'signals' ? {panel:'signals' as const} : {})}}
    : {view};
}

export function workspaceHash({view, comparisonSource}: WorkspaceRoute): string {
  if (view !== 'science' || !comparisonSource) return '#' + view;
  const {workflowId, nodeId, attemptId} = comparisonSource;
  const search = new URLSearchParams({workflow: workflowId, node: nodeId, attempt: attemptId});
  if (comparisonSource.panel) search.set('panel', comparisonSource.panel);
  return '#science?' + search.toString();
}
