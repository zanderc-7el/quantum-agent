import type { Catalog, CreateWorkflowInput } from '../types.ts';
import type { Configuration, Qubit } from './workspaceGateway.ts';
import { selectableResources } from './workflowResources.ts';
import { defaultCoverage, workflowCoverageError } from './workflowCoverage.ts';
import { isConfirmedExecutionQubit } from './topologyExecutionTarget.ts';

export type CreationBinding = 'fixed' | 'published';
export type PublishedConfiguration = Omit<Configuration, 'versions'> & { entityId: string; digest: string; publishedAt: string; choiceLabel: string };

export function publishedVersions(items: Configuration[]): PublishedConfiguration[] {
  return items.flatMap(item => (item.versions ?? []).flatMap((version, index) => {
    const v = version as PublishedConfiguration;
    return v.entityId === item.id && typeof v.id === 'string' && !!v.id && !!v.publishedAt && /^[0-9a-f]{64}$/i.test(v.digest ?? '')
      ? [{ ...v, choiceLabel: `${v.name || item.name} · 发布 ${index + 1}` }] : [];
  }));
}

/** A resource name alone never carries initialization across configuration boundaries. */
export function resetCreationBinding(form: CreateWorkflowInput, changes: Partial<CreateWorkflowInput> = {}): CreateWorkflowInput {
  const { chipVersionId: _chip, deploymentVersionId: _deployment, sourceWorkflowId: _source,
    manualWorkingPointId: _point, manualScienceReferences: _references, manualParameters: _parameters,
    manualProcedureId: _procedure, executionProfile: _profile, biasDeploymentVersionId: _bias,
    operationId: _operation, ...base } = form;
  const mode = changes.mode ?? form.mode;
  return { ...base, target: '', resourceIds: [], coverage: defaultCoverage(mode), ...changes };
}

export function isReadyCreationQubit(qubit: Qubit): boolean {
  return isConfirmedExecutionQubit(qubit) && (qubit as Qubit & { ready?: boolean }).ready !== false;
}

export function creationInput(form: CreateWorkflowInput, binding: CreationBinding, chips: Configuration[], deployments: Configuration[], catalog?: Catalog): CreateWorkflowInput {
  let resources: { id: string; label: string }[];
  let input = { ...form };
  if (binding === 'published') {
    if (form.mode !== 'real' || form.experiment !== 'single') throw new Error('发布配置用于真实单比特测量。');
    const chip = publishedVersions(chips).find(v => v.id === form.chipVersionId);
    const deployment = publishedVersions(deployments).find(v => v.id === form.deploymentVersionId);
    if (!chip || !deployment) throw new Error('请选择已发布的芯片与接线版本。');
    if (form.machine !== (deployment.machine || deployment.name)) throw new Error('接线版本不属于所选机台。');
    if (!deployment.wiringConfirmed) throw new Error('所选接线版本尚未确认。');
    if (form.coverage === 'working_point') throw new Error('所选发布配置不使用原固定配置的工作点。');
    resources = (chip.qubits ?? []).filter(isReadyCreationQubit).filter(q => !!deployment.routes?.[q.id]).map(q => ({ id: q.id, label: q.label || q.id }));
    input = { ...resetCreationBinding(form), chipVersionId: chip.id, deploymentVersionId: deployment.id, machine: deployment.machine || deployment.name, coverage: form.coverage, resourceIds: form.resourceIds, qubits: 1 };
  } else {
    resources = selectableResources(catalog, form.mode);
    const reason = workflowCoverageError(catalog, form); if (reason) throw new Error(reason);
    // Fixed/replay workflows must not accidentally keep a formerly selected published binding.
    const { chipVersionId: _chip, deploymentVersionId: _deployment, ...fixed } = input; input = fixed;
  }
  const ids = input.resourceIds ?? [];
  if (!ids.length || new Set(ids).size !== ids.length || ids.some(id => !resources.some(q => q.id === id))
      || ids.length !== (input.experiment === 'two' ? 2 : 1)) throw new Error('请选择当前配置中的有效目标资源。');
  const target = ids.map(id => resources.find(q => q.id === id)!.label).join(' / ');
  return { ...input, target, name: input.name.trim() || `${target} · ${input.mode === 'replay' ? '历史实测回放' : input.coverage === 'working_point' ? '已有工作点复刻' : '单比特测控'}` };
}
