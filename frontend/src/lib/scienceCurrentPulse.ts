import type {NodeDetail} from '../types.ts';
import type {ScienceInput} from './scienceGateway.ts';

const finite=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value);
export function pulseFromCurrentParameters(detail:NodeDetail,workflowId:string):ScienceInput{
 const p={...(detail.resolvedExecutionPlan?.status==='ready'?detail.resolvedExecutionPlan.parameters:{}),...detail.parameters};
 const pick=(keys:string[],scale=1)=>{const key=keys.find(k=>finite(p[k]));return key?Number(p[key])*scale:null;};
 return {amplitude:pick(['shape_pi_amp','drive_amp']),frequencyHz:pick(['drive_hz']),durationNs:pick(['pulse_length_s','drive_length_s'],1e9),
  phaseDeg:finite(p.pulse_phase_rad)?p.pulse_phase_rad*180/Math.PI:pick(['phase_deg']),
  readoutFrequencyHz:pick(['readout_hz']),readoutAmplitude:pick(['readout_amp']),readoutDurationNs:pick(['readout_width_s'],1e9),
  readoutPhaseDeg:pick(['readout_phase_deg']),
  parameterSource:{source:'controller_current_parameters',workflowId,nodeId:detail.node.id,workflowRevision:detail.workflowRevision,
   parameterVersions:detail.parameterVersions??{},loadedAt:new Date().toISOString(),modifiedLocally:false,
   modelOnlyFields:['shape','points','xyCount','xyGapNs','delayNs','zAmplitude','acquisitionDelayNs','acquisitionDurationNs']}};
}
