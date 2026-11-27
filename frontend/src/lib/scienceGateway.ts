import type { WorkspaceGateway } from './workspaceGateway';
export type ScienceKind = 'pulse'|'drag'|'predistortion'|'predistortion_comparison'|'bloch'|'shelve'|'flux'|'simulation'|'measurement'|'working_point'|'readout_calibration';
export type ScienceInput = Record<string, any>;
export interface ScienceScope {workflowId?:string;resourceId?:string;chipVersionId?:string;deploymentVersionId?:string;observedResourceBindingSha256?:string}
export interface ScienceTrace {id:string;label:string;quantity:string;unit:string;values:(number|null)[];x?:number[];xUnit?:string}
export interface ScienceSegment {id:string;label:string;startNs:number;endNs:number;channel?:string}
export interface ScienceResult {
 kind?:string; modelVersion?:string; dataKind?:string; source?:string; dispatched?:boolean;
 procedureId?:string; scienceFlux?:Record<string,any>; provenance?:Record<string,any>;
 axes?:{x:{name:string;unit:string;values:number[]}}; series?:ScienceTrace[];sourceSeries?:ScienceTrace[];
 metrics?:Record<string,unknown>; segments?:ScienceSegment[];
 trajectory?:{t:number;x:number;y:number;z:number;p1:number}[];
 [key:string]:any;
}
export interface ScienceRecord {id:string;kind:string;revision?:number;createdAt?:string;scope:ScienceScope;input:ScienceInput;result?:ScienceResult;provenance?:Record<string,any>;reference?:{workflowId:string;attemptId:string};[key:string]:any}
export interface ScienceManualContext {recordId:string;biasMeaning?:'commanded_setpoint'|'hardware_setting_register';measuredOutputV?:number|null;executionProfile?:'science-flux';deploymentVersionId?:string;workflowId?:string;chipVersionId?:string;resourceId?:string;purpose:'idle'|'work';fluxChannel:string;bias:number;biasUnit:string;readoutFrequencyHz:number;driveFrequencyHz?:number;sourceRecordId:string}
export interface ScienceOverview {temperature?:{status:string;value:number|null;unit?:string;updatedAt?:string};counts?:Record<string,number>;records?:ScienceRecord[]}
export interface ScienceRecordPage {records:ScienceRecord[];hasMore:boolean;nextCursor:string|null;responseBytes?:number;summary?:boolean;limit?:number}
export const createScienceGateway = (gateway:WorkspaceGateway) => ({
 overview:()=>gateway.get<ScienceOverview>('/science/overview'),
 records:async(workflowId?:string)=>{const result=await gateway.get<ScienceRecord[]|{records:ScienceRecord[]}>(`/science/records${workflowId?`?workflowId=${encodeURIComponent(workflowId)}`:''}`);return Array.isArray(result)?result:result.records;},
 recordsPage:(workflowId?:string,cursor?:string)=>{const query=new URLSearchParams({summary:'true'});if(workflowId)query.set('workflowId',workflowId);if(cursor)query.set('cursor',cursor);return gateway.get<ScienceRecordPage>('/science/records?'+query.toString());},
 record:(id:string)=>gateway.get<ScienceRecord>(`/science/records/${encodeURIComponent(id)}`),
 preview:(kind:string,input:ScienceInput)=>gateway.write<ScienceResult>('/science/preview',{kind,input}),
 save:(kind:string,input:ScienceInput,scope:ScienceScope,extra:ScienceInput={})=>gateway.write<ScienceRecord>('/science/records',{operationId:crypto.randomUUID(),kind,input,scope,...extra}),
 readoutCalibrationTemplate:(sourceRecordId:string,sourceSeriesId:string)=>gateway.write<ScienceInput>('/science/readout-calibration-template',{sourceRecordId,sourceSeriesId}),
 channelComparisonTemplate:(scope:ScienceScope)=>gateway.write<ScienceInput>('/science/channel-comparison-template',{scope}),
 channelComparisonPreview:(input:ScienceInput,scope:ScienceScope)=>gateway.write<ScienceResult>('/science/channel-comparison-preview',{scope,input}),
 compare:(leftRecordId:string,rightRecordId:string,leftSeriesId:string,rightSeriesId:string,excludedLeftPoints:number[]=[])=>gateway.write<ScienceResult>('/science/compare',{operationId:crypto.randomUUID(),leftRecordId,rightRecordId,leftSeriesId,rightSeriesId,excludedLeftPoints}),
});
