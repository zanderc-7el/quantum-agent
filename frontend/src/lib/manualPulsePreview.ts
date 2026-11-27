import {manualParameterChanges,validateManualParameterDraft} from './manualParameters.ts';
import type {ManualParameterDraft} from './manualParameters.ts';

export interface PulsePreviewSegment {
 id:string;channel:'XY'|'Wait'|'Read in'|'Read out';label:string;startS:number;durationS:number;
 envelopeStartS?:number;envelopeDurationS?:number;bufferS?:number;
 amplitude:number|null;frequencyHz:number|null;phaseRad:number|null;phaseReference?:string;shape?:string;
}
export interface ManualPulsePreviewResult {
 schema:'manual-pulse-preview/1';status:'ready'|'unavailable';reason?:string;
 workflowId:string;nodeId:string;resourceId:string;workflowRevision:number;
 requestedChanges:Record<string,number|number[]>;normalizedChanges:Record<string,number|number[]>;
 effectiveParameters?:Record<string,unknown>;planSha256?:string;
 segments:PulsePreviewSegment[];axes:{name:string;binding:string;values:(number|string)[];index:number}[];
 variant?:string;sweepMode?:'zip'|'cartesian';readoutStartS?:number;readoutDurationS?:number;readoutAmplitude?:number;controlReadoutGapS?:number;
 xyTriggerDelayS?:number;referenceClockHz?:number|null;zSetting?:number|null;limitations?:string[];
 dispatched:false;hardwareConnected:false;
}
export function manualPreviewChanges(draft:ManualParameterDraft,currentRevision:number,problem=''){
 if(problem)throw new Error(problem);
 if(draft.revision!==currentRevision)throw new Error('配置版本已更新，读取当前配置后才能预览。');
 return Object.keys(manualParameterChanges(draft)).length?validateManualParameterDraft(draft,currentRevision):{};
}

export function pulseEditKey(channel:string,property:'amplitude'|'duration'|'frequency',procedureId:string){
 if(channel==='Read in')return {amplitude:'readout_amp',duration:'readout_width_s',frequency:'readout_hz'}[property];
 if(channel!=='XY')return undefined;
 if(property==='amplitude')return procedureId==='sq_rabi_amplitude'?'amp_start':'drive_amp';
 if(property==='frequency')return 'drive_hz';
 return ['sq_rabi_time','sq_rabi_frequency'].includes(procedureId)?'start_s':procedureId==='sq_spectroscopy'?'spectroscopy_width_s':'drive_length_s';
}
