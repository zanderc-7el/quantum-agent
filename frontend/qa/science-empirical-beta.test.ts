import test from 'node:test';
import assert from 'node:assert/strict';
import * as draft from '../src/lib/scienceExecutionDraft.ts';

const scope={chipVersionId:'chip',deploymentVersionId:'wire',resourceId:'Q11'};
const common=['drag_mode','pulse_shape','pulse_length_s','shape_pi_amp'];
const normalized=[...common,'drag_lambda1','anharmonicity_hz','lambda_start','lambda_stop'];
const empirical=[...common,'drag_beta_s','beta_start_s','beta_stop_s'];
const method:any={procedureId:'sq_drag_half_scan',label:'半门扫描',capability:'available',
 scienceSourceRoles:['pi01','anharmonicity','readout'],scienceSourceRolesByMode:{normalized_alpha:['pi01','anharmonicity','readout'],empirical_beta:['pi01','readout']},
 scienceSourceMethods:{pi01:['sq_shape_rabi_amplitude'],anharmonicity:['sq_shelve_spectrum12'],readout:['ro_iq01'],drag:['sq_drag_half_scan']},
 parameterKeysByMode:{normalized_alpha:normalized,empirical_beta:empirical},
 parameterBounds:{drag_mode:{enum:['normalized_alpha','empirical_beta'],default:'normalized_alpha'},pulse_shape:{enum:['CosPulse','Gaussian'],default:'CosPulse'},pulse_length_s:{min:4e-9,max:2e-6,default:20e-9},shape_pi_amp:{min:.0001,max:.99,default:.4},
 drag_lambda1:{min:-10,max:10,default:0},anharmonicity_hz:{min:-2e9,max:2e9},lambda_start:{min:-10,max:10,default:-1},lambda_stop:{min:-10,max:10,default:1},
 drag_beta_s:{min:-1e-6,max:1e-6,unit:'s'},beta_start_s:{min:-1e-6,max:1e-6,unit:'s'},beta_stop_s:{min:-1e-6,max:1e-6,unit:'s'}}};
function record(id:string,procedureId:string):any{return {id,kind:'measurement',dataKind:'real',scope,reference:{},evidenceCurrent:true,evidenceIntact:true,provenance:{independentlyVerified:true},result:{procedureId,quality:{status:'passed'}}};}
const records=[record('pi','sq_shape_rabi_amplitude'),record('readout','ro_iq01')];
const catalog:any={executionProfile:'science-v3-irb',configurationBinding:scope,preflight:{staticReady:true},manualMethods:[method]};
const values={...draft.scienceParameterValues(method),drag_mode:'empirical_beta',drag_beta_s:'0.25',beta_start_s:'-1',beta_stop_s:'1'};

test('empirical coefficient inputs display ns and submit signed seconds without normalized parameters',()=>{
 assert.equal(draft.scienceParameterPresentation('drag_beta_s',method.parameterBounds.drag_beta_s).unit,'ns');
 const p=draft.scienceParameters(method,values);
 assert.equal(p.drag_beta_s,2.5e-10);assert.equal(p.beta_start_s,-1e-9);assert.equal(p.beta_stop_s,1e-9);
 for(const key of ['lambda_start','lambda_stop','drag_lambda1','anharmonicity_hz'])assert.equal(key in p,false);
 assert.throws(()=>draft.scienceParameters(method,{...values,beta_start_s:'2'}),/终点/);
});
test('empirical creation uses catalog mode roles and drops stale nonlinearity references',()=>{
 const p=draft.scienceExecutionInput({executionProfile:'science-v3-irb',name:'经验半门',scope,catalog,procedureId:method.procedureId,values,references:{pi01:'pi',readout:'readout',anharmonicity:'old-shelve'},records});
 assert.deepEqual(p.manualScienceReferences,[{role:'pi01',recordId:'pi'},{role:'readout',recordId:'readout'}]);
 assert.equal(p.manualParameters?.drag_mode,'empirical_beta');
});
test('normalized default retains its required measured nonlinearity source',()=>{
 assert.throws(()=>draft.scienceExecutionInput({executionProfile:'science-v3-irb',name:'原方案',scope,catalog,procedureId:method.procedureId,values:draft.scienceParameterValues(method),references:{pi01:'pi',readout:'readout'},records}),/非谐性/);
});
test('IRB inherits the accepted coefficient and full-pi amplitude rather than half amplitude',()=>{
 assert.equal(typeof draft.scienceInheritDragSource,'function');
 const irb={...method,procedureId:'sq_drag_irb'};
 const scan={...record('scan','sq_drag_half_scan'),summary:false,result:{procedureId:'sq_drag_half_scan',parameters:{pulse_shape:'CosPulse',pulse_length_s:24e-9,shape_pi_amp:.6,drag_mode:'empirical_beta',drag_beta_s:0},targetPulseIdentity:{sha256:'same-half',identity:{dragMode:'empirical_beta',betaSeconds:-.75e-9,amplitude:.3,shape:'CosPulse',durationS:24e-9,lambda1:null,anharmonicityHz:null}}}};
 const inherited=draft.scienceInheritDragSource(irb,values,scan);
 assert.equal(inherited.values.drag_beta_s,'-0.75');assert.equal(inherited.values.shape_pi_amp,'0.6');assert.equal(inherited.values.pulse_length_s,'24');
 assert.equal(inherited.values.drag_mode,'empirical_beta');assert.ok(inherited.lockedKeys.includes('drag_mode'));assert.ok(inherited.lockedKeys.includes('shape_pi_amp'));
 assert.equal('drag_lambda1' in inherited.values,false);
 assert.throws(()=>draft.scienceInheritDragSource(irb,values,{...scan,summary:true}),/完整/);
 const incomplete=structuredClone(scan);delete incomplete.result.parameters.shape_pi_amp;
 assert.throws(()=>draft.scienceInheritDragSource(irb,values,incomplete),/缺少.*幅度/);
});

test('IRB draft rejects a coefficient or mode changed after choosing its accepted gate',()=>{
 const irb={...method,procedureId:'sq_drag_irb',scienceSourceRoles:['drag','readout','anharmonicity'],scienceSourceRolesByMode:{normalized_alpha:['drag','readout','anharmonicity'],empirical_beta:['drag','readout']}};
 const scan={...record('scan','sq_drag_half_scan'),summary:false};
 scan.result.parameters={pulse_shape:'CosPulse',pulse_length_s:24e-9,shape_pi_amp:.6,drag_mode:'empirical_beta'};
 scan.result.targetPulseIdentity={sha256:'same-half',identity:{dragMode:'empirical_beta',betaSeconds:-.75e-9,amplitude:.3}};
 const inherited=draft.scienceInheritDragSource(irb,values,scan).values;
 const input:any={executionProfile:'science-v3-irb',name:'同门IRB',scope,catalog:{...catalog,manualMethods:[irb]},procedureId:irb.procedureId,values:inherited,references:{drag:'scan',readout:'readout'},records:[...records,scan]};
 assert.equal(draft.scienceExecutionInput(input).manualParameters?.drag_beta_s,-.75e-9);
 assert.throws(()=>draft.scienceExecutionInput({...input,values:{...inherited,drag_beta_s:'0'}}),/门参数/);
 assert.throws(()=>draft.scienceExecutionInput({...input,values:{...inherited,drag_mode:'normalized_alpha'}}));
});
