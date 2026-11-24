export type Scope = { kind: 'workspace' | 'campaign' | 'workflow'; id?: string };
export interface ConversationInspection { tool: string; arguments?: Record<string, unknown>; checkedAt?: string; ok: boolean; source?: unknown; summary?: unknown }
export interface Conversation { id: string; revision: number; scope: Scope; messages: {id: string; role: string; content: string; createdAt: string; analysisMessage?: string; inspections?: ConversationInspection[]}[]; pending?: unknown; proposals?: unknown[]; toolResults?: unknown[]; error?: string }
export interface Qubit { id: string; label: string; physicalId?: string; busId?: string; frequencyAlias?: string; identityStatus?: string; issues?: unknown[]; issueMessages?: string[]; parameters: Record<string, unknown>; provenance?: unknown }
export interface Configuration { id: string; revision: number; name: string; status?: string; qubits?: Qubit[]; machine?: string; instrument?: Record<string, unknown>; routes?: Record<string, Record<string, unknown>>; wiringConfirmed?: boolean; issues?: unknown[]; versions?: {id: string; name?: string; qubits?: Qubit[]}[] }
export interface ImportRecord { id: string; revision: number; filename?: string; status?: string; draft?: Configuration; issues?: unknown[]; [key: string]: unknown }
export interface Campaign { id: string; revision: number; name: string; state: string; chipVersionId: string; deploymentVersionId: string; resourceIds: string[]; children: {resourceId: string; workflowId: string; status: string; engineState?: string; currentStep?: string; verifiedCount: number; totalCount: number; error?: unknown}[] }
export interface Attention { id: string; revision: number; workflowId?: string; campaignId?: string; nodeId?: string; title: string; message: string; recommendation?: string; severity: string; scope: string; state: 'open' | 'deferred' | 'resolved'; actions: {id: string; label: string}[] }
export const operationId = () => crypto.randomUUID();
export const namespace = (project: string, scope: Scope) => `qcal.workspace.v1:${encodeURIComponent(project)}:${scope.kind}:${encodeURIComponent(scope.id ?? '')}`;
export function attentionCandidate(items: Attention[], seen: Record<string, number>) { return items.find(i => i.state === 'open' && seen[i.id] !== i.revision); }
export function campaignAction(campaign: Campaign, action: string, resourceIds?: string[]) {
  if (resourceIds && (!resourceIds.length || resourceIds.some(id => !campaign.resourceIds.includes(id)))) throw new Error('请选择本批次中的比特');
  return { operationId: operationId(), expectedRevision: campaign.revision, action, ...(resourceIds ? {resourceIds} : {}) };
}
export function uploadBody(filename: string, dataUrl: string) {
  if (!/\.(pdf|pptx|docx|xlsx|png|jpe?g|bmp|tiff?)$/i.test(filename)) throw new Error('请选择 PDF、PPTX、DOCX、XLSX 或图片');
  const contentBase64 = dataUrl.slice(dataUrl.indexOf(',') + 1);
  if (!dataUrl.startsWith('data:') || !dataUrl.includes(';base64,') || !contentBase64) throw new Error('文件读取失败');
  return {operationId: operationId(), filename, contentBase64};
}
export function createWorkspaceGateway(session: () => Promise<{token: string}>, request: typeof fetch = globalThis.fetch.bind(globalThis)) {
  const read = async <T>(path: string, init?: RequestInit): Promise<T> => {
    const response = await request(`/api${path}`, {credentials: 'same-origin', ...init});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error?.message ?? value.message ?? `请求失败 (${response.status})`);
    return value as T;
  };
  return {
    get: <T>(path: string) => read<T>(path),
    write: async <T>(path: string, body: unknown, method = 'POST'): Promise<T> => {
      const auth = await session();
      // No automatic mutation replay after network failure, refresh, or token expiry.
      return read<T>(path, {method, headers: {'Content-Type': 'application/json', 'X-Qcal-Token': auth.token}, body: JSON.stringify(body)});
    },
  };
}
export type WorkspaceGateway = ReturnType<typeof createWorkspaceGateway>;
export function readStored<T>(key: string, fallback: T): T { try { return JSON.parse(localStorage.getItem(key) ?? 'null') ?? fallback; } catch { return fallback; } }
export function persist(key: string, value: unknown) { localStorage.setItem(key, JSON.stringify(value)); }

export const parameterUnit = (name: string) => /(?:_|[a-z])ghz$/i.test(name)?'GHz':/(?:_|[a-z])mhz$/i.test(name)?'MHz':/hz$/i.test(name)?'Hz':/_ns$/i.test(name)?'ns':/_us$/i.test(name)?'µs':/_ms$/i.test(name)?'ms':/_s$/i.test(name)?'s':/_db$/i.test(name)?'dB':'';

export function calibrationParameterLabel(name: string) {
 const labels:Record<string,string>={readout_hz:'读出频率',drive_hz:'驱动频率',readout_amp:'读出幅度',drive_amp:'驱动幅度',drive_length_s:'π脉冲时长',half_pi_amp:'半π幅度',xy_delay_s:'XY 延迟',xy_attenuation_db:'XY 衰减',xy_filter_mhz:'XY 滤波器',readout_width_s:'读出时长',pulse_compatibility:'脉冲兼容策略'};
 return labels[name.split('.').at(-1)??name]??name;
}
export function campaignChildAllows(child: Campaign['children'][number], action: string) {
 if(['completed','aborted','skipped'].includes(child.status)||child.engineState==='stopping_at_checkpoint')return false;
 if(child.engineState==='needs_reconciliation')return action==='pause'||action==='abort';
 if(action==='start')return child.status==='draft'&&child.engineState!=='configuration_required';
 if(action==='pause')return ['running','queued'].includes(child.status)&&!['configuration_required','pausing_at_checkpoint'].includes(child.engineState??'');
 if(action==='resume')return ['paused','attention','failed'].includes(child.status)&&!['configuration_required','budget_exhausted','plan_rejected','unsupported','unsupported_method','adapter_changed','diagnostic_closed','supplemental_acceptance_mismatch','diagnostic_failed','diagnostic_complete','diagnostic_interrupted'].includes(child.engineState??'');
 if(action==='abort'||action==='skip')return ['draft','queued','running','paused','attention','failed','blocked'].includes(child.status);
 return false;
}
export function eligibleCampaignResources(campaign:Campaign,action:string,resourceIds?:string[]) {
 return campaign.children.filter(child=>(!resourceIds||resourceIds.includes(child.resourceId))&&campaignChildAllows(child,action)).map(child=>child.resourceId);
}
