import test from 'node:test';
import assert from 'node:assert/strict';
import {sameHalfGateEvidence} from '../src/lib/scienceGateEvidence.ts';
import * as evidence from '../src/lib/scienceGateEvidence.ts';
test('gate coefficient labels distinguish signed empirical ns from dimensionless lambda',()=>{
 assert.equal(typeof evidence.scienceGateCoefficientLabel,'function');
 assert.equal(evidence.scienceGateCoefficientLabel({dragMode:'empirical_beta',betaSeconds:-.75e-9,lambda1:null}),'β −0.75 ns');
 assert.equal(evidence.scienceGateCoefficientLabel({lambda1:.25}),'λ 0.25');
 assert.equal(evidence.scienceGateCoefficientLabel({dragMode:'empirical_beta',betaSeconds:null,lambda1:null}),'β 未记录');
});
test('R11 link requires exact current half-scan record and paired IRB target',()=>{
 const base:any={kind:'measurement',dataKind:'real',summary:false,evidenceCurrent:true,evidenceIntact:true,provenance:{independentlyVerified:true}};
 const scan={...base,id:'scan',result:{procedureId:'sq_drag_half_scan',quality:{status:'passed'},targetPulseIdentity:{sha256:'exact-half'}}};
 const irb={...base,id:'irb',result:{procedureId:'sq_drag_irb',quality:{status:'passed'},targetPulseIdentity:{sha256:'exact-half'},gateCharacterization:{reportable:true,sourceReferences:[{role:'drag',recordId:'scan'}]}}};
 assert.equal(sameHalfGateEvidence(scan,irb).ready,true);
 for(const changed of [{...scan,evidenceCurrent:false},{...scan,result:{...scan.result,procedureId:'sq_drag_scan'}},{...scan,id:'other-scan'},{...scan,result:{...scan.result,targetPulseIdentity:{sha256:'other-half'}}}])assert.equal(sameHalfGateEvidence(changed,irb).ready,false);
 assert.equal(sameHalfGateEvidence(scan,{...irb,result:{...irb.result,procedureId:'sq_rb'}}).ready,false);
});
