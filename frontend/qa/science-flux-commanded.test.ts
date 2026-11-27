import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {readWorkingPoint} from '../src/lib/scienceModels.ts';
import {scienceCatalogPath,scienceExecutionInput} from '../src/lib/scienceExecutionDraft.ts';

test('command point carries setting voltage with null measurement, never its editable input',()=>{
 const point:any={id:'point',kind:'working_point',scope:{workflowId:'scan',resourceId:'A'},input:{bias:999},
  result:{fluxPointEvidence:{schema:'qcal-flux-point-evidence/2'},point:{bias:.2,biasMeaning:'commanded_setpoint',measuredOutputV:null,biasUnit:'V',fluxChannel:'LF-A',readoutFrequencyHz:7e9,sourceRecordId:'source'}}};
 const value=readWorkingPoint(point,'A');assert.equal(value.executionProfile,'science-flux');assert.equal(value.bias,.2);
 assert.equal(value.biasMeaning,'commanded_setpoint');assert.equal(value.measuredOutputV,null);
});

test('observed resource can select a published bias deployment without inventing a chip identity',()=>{
 const scope={sourceWorkflowId:'origin',resourceId:'A',biasDeploymentVersionId:'wiring-v2'};
 const url=scienceCatalogPath(scope,'science-flux')!;assert.match(url,/biasDeploymentVersionId=wiring-v2/);
 assert.equal(scienceCatalogPath(scope,'science-v3'),undefined);
 const binding={sourceWorkflowId:'origin',resourceId:'A',identityClaim:'measured_resource_not_design_identity',physicalDesignId:null,sha256:'binding'};
 const record:any={id:'seed',kind:'measurement',scope:{workflowId:'origin',resourceId:'A'},reference:{},dataKind:'real',evidenceCurrent:true,evidenceIntact:true,provenance:{independentlyVerified:true},result:{procedureId:'ro_resonator_survey',quality:{status:'passed'}}};
 const method={procedureId:'ro_flux_scan',capability:'available',scienceSourceRoles:['cavity_seed'],scienceSourceMethods:{cavity_seed:['ro_resonator_survey']},parameterBounds:{}};
 const input=scienceExecutionInput({executionProfile:'science-flux',name:'scan',scope,catalog:{executionProfile:'science-flux',configurationBinding:{...scope,observedResourceBinding:binding},preflight:{staticReady:true},manualMethods:[method]} as any,procedureId:'ro_flux_scan',values:{},references:{cavity_seed:'seed'},records:[record]});
 assert.equal(input.biasDeploymentVersionId,'wiring-v2');assert.equal(input.sourceWorkflowId,'origin');
 assert.equal(input.chipVersionId,undefined);assert.equal(input.deploymentVersionId,undefined);
 assert.equal(Object.hasOwn(input,'biasCommandAuthorization'),false);
 const inherited=scienceExecutionInput({executionProfile:'science-flux',name:'scan',scope:{...scope,biasDeploymentVersionId:undefined},catalog:{executionProfile:'science-flux',configurationBinding:{...scope,observedResourceBinding:binding},preflight:{staticReady:true},manualMethods:[method]} as any,procedureId:'ro_flux_scan',values:{},references:{cavity_seed:'seed'},records:[record]});
 assert.equal(inherited.biasDeploymentVersionId,undefined);
});

test('actual comparison component distinguishes command coordinates from measured restoration',()=>{
 const source=buildSync({entryPoints:[fileURLToPath(new URL('../src/components/ScienceWorkbench.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
 const module={exports:{} as any};new Function('require','module','exports',source)(createRequire(import.meta.url),module,module.exports);
 const flux={route:{schema:'qcal-bias-command-route/1'},sourcePoint:{originalCut:{frequenciesHz:[7e9,7.001e9,7.002e9],responseMagnitude:[1,.2,1],readoutFrequencyHz:7.001e9}},points:[{phase:'local_cavity',frequenciesHz:[7e9,7.001e9,7.002e9],responseMagnitude:[1,.3,1],readoutFrequencyHz:7.001e9}]};
 const html=renderToStaticMarkup(React.createElement(module.exports.ScienceFluxComparison,{result:{procedureId:'sq_flux_point_recheck',scienceFlux:flux}}));
 assert.match(html,/偏置为设定电压，未独立测压/);assert.match(html,/返回声明设置/);assert.doesNotMatch(html,/偏置实测与恢复回执/);
});
