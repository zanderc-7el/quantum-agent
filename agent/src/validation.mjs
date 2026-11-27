const actions=new Set(['continue','retry','backtrack','wait','stop']);
export const record=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
function safeKeys(v,depth=0){if(depth>20)throw new Error('input_too_deep');if(Array.isArray(v)){v.forEach(x=>safeKeys(x,depth+1));return;}if(record(v))for(const [k,x]of Object.entries(v)){if(['__proto__','prototype','constructor'].includes(k))throw new Error('forbidden_object_key');safeKeys(x,depth+1);}}
export function validateInput(input){if(!record(input))throw new Error('object_required');safeKeys(input);if(JSON.stringify(input).length>200000)throw new Error('input_too_large');}
export function validateDecisionInput(i){validateInput(i);for(const k of ['runId','nodeId','goal'])if(typeof i[k]!=='string'||!i[k].trim())throw new Error(`missing_${k}`);if(!Number.isInteger(i.revision)||i.revision<0)throw new Error('invalid_revision');if(!['real','replay'].includes(i.mode))throw new Error('invalid_mode');if(!Array.isArray(i.allowedActions)||!i.allowedActions.length||i.allowedActions.some(a=>!actions.has(typeof a==='string'?a:a?.action)))throw new Error('invalid_allowedActions');if(!record(i.parameterBounds))throw new Error('parameterBounds_required');}
export function collectEvidence(input){const ids=new Set();function visit(v){if(Array.isArray(v))v.forEach(visit);else if(record(v)){for(const k of ['id','artifactId','evidenceId','attemptId'])if(typeof v[k]==='string')ids.add(v[k]);if(Array.isArray(v.evidenceIds))v.evidenceIds.filter(x=>typeof x==='string').forEach(x=>ids.add(x));Object.values(v).forEach(visit);}}visit(input.observations);visit(input.history);return ids;}
function validateParameter(key, value, bounds) {
  if (!record(bounds)) throw new Error(`parameter_not_allowed:${key}`);
  if (Array.isArray(bounds.enum)) {
    if (!bounds.enum.includes(value)) throw new Error(`parameter_enum:${key}`);
    return;
  }
  const inRange = item => Number.isFinite(item) && Number.isFinite(bounds.min)
    && Number.isFinite(bounds.max) && item >= bounds.min && item <= bounds.max;
  if (bounds.type === 'integerArray' || bounds.type === 'numberArray') {
    const validItem = item => inRange(item) && (bounds.type !== 'integerArray' || Number.isSafeInteger(item));
    if (!Array.isArray(value) || !Number.isSafeInteger(bounds.minItems) || !Number.isSafeInteger(bounds.maxItems)
      || value.length < bounds.minItems || value.length > bounds.maxItems || !value.every(validItem)
      || (bounds.strictlyIncreasing && value.some((item, index) => index > 0 && item <= value[index - 1]))
      || (bounds.unique && new Set(value).size !== value.length)) throw new Error(`parameter_bounds:${key}`);
    return;
  }
  if (!inRange(value) || (bounds.integer && !Number.isSafeInteger(value))) throw new Error(`parameter_bounds:${key}`);
}

function validateRabiWindow(d,input){
  const scope=input.smartRabiWindowScope,changes=d.parameterChanges;
  if(scope?.proposalPolicy!=='fixed_dc_window_actual_changes_v2'||!record(changes)||!Object.hasOwn(changes,'drive_length_s'))return;
  const current=scope.currentParameters;
  if(scope.schema!=='qcal-smart-rabi-window/1'||!record(current)||d.action!=='retry')throw new Error('rabi_window_scope');
  const same=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)
    ?Math.abs(a-b)<=Math.max(Math.abs(b)*1e-12,1e-12)
    :typeof a===typeof b&&JSON.stringify(a)===JSON.stringify(b);
  if(same(changes.drive_length_s,current.drive_length_s))return;
  for(const [key,value]of Object.entries(changes)){
    if(!['drive_length_s','acquisitions_per_point'].includes(key)
        &&(!Object.hasOwn(current,key)||!same(value,current[key])))throw new Error(`rabi_window_fixed_parameter:${key}`);
  }
  const width=changes.drive_length_s,blocks=changes.acquisitions_per_point??current.acquisitions_per_point;
  if(blocks!==8)throw new Error('rabi_window_requires_eight_blocks');
  if(!Number.isFinite(current.drive_length_s)||width<=current.drive_length_s
      ||Math.abs(width*2e9-Math.round(width*2e9))>1e-7)throw new Error('rabi_window_duration_grid');
}

export function validateDecision(d,input){
  validateInput(d);const fields=['action','reason','evidenceIds','parameterChanges','targetNodeId','ruleId'];if(Object.keys(d).some(k=>!fields.includes(k)))throw new Error('unknown_decision_field');
  if(!input.allowedActions.some(a=>(typeof a==='string'?a:a?.action)===d.action))throw new Error('action_not_allowed');
  if(typeof d.reason!=='string'||!d.reason.trim()||d.reason.length>4000)throw new Error('invalid_reason');
  const evidence=collectEvidence(input);if(!Array.isArray(d.evidenceIds)||d.evidenceIds.length>100||d.evidenceIds.some(x=>typeof x!=='string'||!evidence.has(x)))throw new Error('unknown_evidence_id');
  if(d.ruleId!==undefined&&!input.rules?.some(r=>r.id===d.ruleId))throw new Error('unknown_rule');
  if (d.parameterChanges !== undefined) {
    if (!record(d.parameterChanges)) throw new Error('invalid_parameter_changes');
    for (const [key, value] of Object.entries(d.parameterChanges)) validateParameter(key, value, input.parameterBounds[key]);
  }
  validateRabiWindow(d,input);
  if(d.action==='backtrack'){const targets=new Set([...(input.allowedBacktrackTargets||[]),...(input.allowedActions||[]).flatMap(a=>record(a)?a.targetNodeIds||[]:[]),...(input.rules||[]).flatMap(r=>[r.targetNodeId,r.action?.targetNodeId].filter(x=>typeof x==='string'))]);if(typeof d.targetNodeId!=='string'||!targets.has(d.targetNodeId))throw new Error('backtrack_target_not_allowed');}
  else if(d.targetNodeId!==undefined)throw new Error('unexpected_backtrack_target');return d;
}
