"""Independent repeat5 profile plus the unchanged scientific downstream chain."""
from copy import deepcopy
from pathlib import Path
from adapters.f8 import AdapterError
from adapters.science_runtime.policy import digest,sha
from adapters.science_v3_adapter import ScienceV3Adapter,build_science_profile
from adapters.science_v3.plans import POLICY as PULSE_POLICY,BOUNDS as PULSE_BOUNDS,INTEGERS,build_rows
from adapters.science_v3.evidence import check_pulse_dependencies
from adapters.arclight_configurable_runtime.routing import validate_instrument
from adapters.xy_passband import require_xy_frequencies
from adapters.science_v3_irb.plans import POLICY,PROCEDURE,ROLES,FIELDS,DEFAULTS,BOUNDS,normalize_parameters,verification_parameters,identity
from .policy import verify_sources
from .plans import POLICY as REPEAT_POLICY,PROCEDURE as REPEAT5,PROFILE_POLICY,METHODS as V3_METHODS,build_repeat5,method_fields
from adapters.science_v3_irb.client import plan_native
from adapters.science_v3_irb.half_scan import PROCEDURE as HALF_PROCEDURE,POLICY as HALF_POLICY,ROLES as HALF_ROLES,SCAN_FIELDS,build_half_scan,target_identity
from .timing import authenticate_dependency,rabi_lineage,inherited_rabi_lineage
from adapters.science_v3_irb import beta

def build_repeat5_profile(root,base):
    p=build_science_profile(root,base)
    p.update(id='science-repeat5-'+digest(base)[:20],enabled=False,scienceIRBPolicy=POLICY,
             runner='adapters/science_shape_repeat5/execute_worker.py',runtimeRoot='adapters/science_shape_repeat5',
             sourceManifest='adapters/science_shape_repeat5/source_manifest.json',scienceRepeat5Policy=PROFILE_POLICY)
    p['runnerSha256']=sha(Path(root)/p['runner']);p['parameterBounds'].update(deepcopy(BOUNDS))
    p['parameterBounds'].update(drag_mode={'enum':['normalized_alpha',beta.MODE]},
        **{key:[-beta.MAX_BETA_S,beta.MAX_BETA_S] for key in ('drag_beta_s','beta_start_s','beta_stop_s')})
    p['parameterBounds']['points']=[5,241]
    return p

class ScienceRepeat5Adapter(ScienceV3Adapter):
    def authenticate_dependency(self,request,dependency):return authenticate_dependency(self.root,request,dependency,
        expected_route=self._routing(request['resourceId']),expected_binding=self.profile.get('configurationBinding',{}),
        expected_locks=self.profile['hardwareLocks'],expected_instrument=self.profile['instrument'],
        expected_observed=self.profile.get('observedResourceBinding'))
    def capabilities(self):
        return {'id':self.profile['id'],'dataKind':'real','enabled':self.profile.get('enabled') is True,
                'physicalHardware':True,'isolatedProcess':True,'supportsReconcile':True,'supportsCancel':'finite ADC block boundaries',
                'resources':list(self.profile['resources']),'procedures':{**super().capabilities()['procedures'],REPEAT5:{'available':True,'status':'requires_current_prerequisites','ownerId':'s03_rabi_amplitude','requires':['f01','readout']},HALF_PROCEDURE:{'available':True,'status':'conditional_current_rabi_lineage_and_independent_verification',
                    'ownerId':'s03_rabi_amplitude','requires':list(HALF_ROLES)},PROCEDURE:{'available':True,
                    'status':'conditional_exact_half_drag_and_current_rabi_lineage','ownerId':'s08_single_qubit_rb',
                    'requires':list(ROLES)}},'policy':POLICY}
    def preflight(self):
        result=super().preflight()
        if not result['ready']:return result
        try:
            if self.profile.get('scienceIRBPolicy')!=POLICY or self.profile.get('scienceRepeat5Policy')!=PROFILE_POLICY:raise ValueError('Explicit third-batch IRB policy missing')
            result['sources']=verify_sources(self.root)
        except (ValueError,OSError) as exc:result.update(ready=False,staticReady=False,reason=str(exc))
        return result
    def parameter_defaults(self,procedure_id):
        if procedure_id in V3_METHODS:return super().parameter_defaults('sq_shape_rabi_amplitude' if procedure_id==REPEAT5 else procedure_id)
        if procedure_id==HALF_PROCEDURE:return {**super().parameter_defaults('sq_drag_scan'),'drag_mode':'normalized_alpha'}
        if procedure_id!=PROCEDURE:return {}
        from adapters.science_v3.plans import DEFAULTS as PULSE_DEFAULTS
        return {**{k:v for k,v in PULSE_DEFAULTS.items() if k in FIELDS-{'capture_repeats','order_seed','drive_amp','drive_length_s'}},**deepcopy(DEFAULTS),'drag_mode':'normalized_alpha'}
    def _beta_schema(self,scan=False):
        schema={'drag_mode':{'enum':['normalized_alpha',beta.MODE],'default':'normalized_alpha'},
                'drag_beta_s':{'min':-beta.MAX_BETA_S,'max':beta.MAX_BETA_S,'unit':'s','default':0.}}
        if scan:schema.update({k:{'min':-beta.MAX_BETA_S,'max':beta.MAX_BETA_S,'unit':'s','default':v} for k,v in (('beta_start_s',-1e-9),('beta_stop_s',1e-9))})
        return schema
    def parameter_schema(self,procedure_id):
        if procedure_id in V3_METHODS:return super().parameter_schema('sq_shape_rabi_amplitude' if procedure_id==REPEAT5 else procedure_id)
        if procedure_id==HALF_PROCEDURE:
            schema=super().parameter_schema('sq_drag_scan')
            schema.update(self._beta_schema(True))
            schema['points'].update(min=5,max=41)
            for k in ('drive_amp','drive_length_s'):schema.pop(k,None)
            for k,v in self.parameter_defaults(HALF_PROCEDURE).items():schema[k]['default']=v
            return schema
        if procedure_id!=PROCEDURE:return {}
        editable=FIELDS-{'capture_repeats','order_seed','drive_amp','drive_length_s'}
        bounds={**{k:v for k,v in PULSE_BOUNDS.items() if k in editable},**BOUNDS}
        schema={k:(deepcopy(v) if isinstance(v,dict) else {'min':v[0],'max':v[1],**({'integer':True} if k in INTEGERS or k in BOUNDS else {})}) for k,v in bounds.items()}
        schema.update(rb_target={'enum':['X/2']},rb_lengths={'type':'integerArray','minItems':5,'maxItems':12,'min':1,'max':512,'strictlyIncreasing':True})
        schema.update(self._beta_schema())
        for key,value in self.parameter_defaults(procedure_id).items():
            if key in schema:schema[key]['default']=value
        return schema
    def validation_parameter_keys(self):return tuple(sorted(set(super().validation_parameter_keys())|FIELDS|set(DEFAULTS)|SCAN_FIELDS))
    def parameter_keys_by_mode(self,procedure_id):
        keys=set(self.parameter_schema(procedure_id))
        return {'normalized_alpha':sorted(keys-{'drag_beta_s','beta_start_s','beta_stop_s'}),
                beta.MODE:sorted(keys-{'drag_lambda1','anharmonicity_hz','lambda_start','lambda_stop'})}
    def _check_explicit_mode(self,request):
        allowed=self.parameter_keys_by_mode(request['procedureId'])[beta.mode_of(request.get('parameters',{}))]
        if set(request.get('explicitParameterKeys',[]))-set(allowed):raise ValueError('Explicit parameter does not belong to the selected DRAG coefficient mode')
    def build_verification_parameters(self,procedure_id,parameters,candidate):
        if procedure_id in V3_METHODS:return super().build_verification_parameters('sq_shape_rabi_amplitude' if procedure_id==REPEAT5 else procedure_id,parameters,candidate)
        if procedure_id==HALF_PROCEDURE:
            if beta.empirical(parameters):
                if set(candidate)!={'drag_beta_s'}:raise AdapterError('SCIENCE_CANDIDATE_MISSING','Empirical beta candidate is missing or ambiguous')
                return {**deepcopy(parameters),'verify_drag_beta_s':beta.finite_beta(candidate['drag_beta_s'])}
            return super().build_verification_parameters('sq_drag_scan',parameters,candidate)
        if procedure_id!=PROCEDURE or candidate:raise AdapterError('IRB_VERIFICATION_INVALID','IRB verifies the frozen pulse; it does not select a new calibration')
        return verification_parameters(parameters)
    def _dependencies(self,request,required):
        if self.profile.get('observedResourceBinding')!=request.get('observedResourceBinding'):raise ValueError('Observed-resource binding differs')
        proof=request.get('scienceDependencies',{});keys=('schema','policyVersion','kind','scope','inputs','rootReferences','nodes','edges')
        if (proof.get('assessment',{}).get('executionEligible') is not True
                or proof['assessment'].get('consumerSha256')!=digest({k:v for k,v in request.items() if k!='scienceDependencies'})
                or digest({k:proof[k] for k in keys})!=proof.get('closureSha256')):raise ValueError('Current authenticated dependency closure must match the exact consumer')
        nodes={d['recordId']:d for d in proof['nodes']};deps={ref['role']:nodes[ref['recordId']] for ref in proof['rootReferences']}
        if not set(required)<=set(deps):raise ValueError('Missing prerequisite roles: '+','.join(sorted(set(required)-set(deps))))
        attestations=[]
        for dep in proof['nodes']:
            roles=sorted({e['role'] for e in proof['edges'] if e.get('toRecordId')==dep['recordId']})
            attestations.append(self.authenticate_dependency(request,{**dep,'sourceRoles':roles}))
        return proof,deps,attestations
    def _build_v3_plan(self,request,output_dir=None):
        method=request.get('procedureId');resource=request.get('resourceId')
        if method not in V3_METHODS:raise AdapterError('UNSUPPORTED_PROCEDURE','V3 method is unavailable')
        if resource not in self.profile['resources']:raise AdapterError('UNKNOWN_RESOURCE','Unknown resource')
        if not isinstance(request.get('operationId'),str) or not request['operationId'] or len(request['operationId'])>256:raise AdapterError('INVALID_OPERATION_ID','Missing bounded operation identity')
        p={k:v for k,v in self.profile['resources'][resource].items() if k in PULSE_BOUNDS or k=='pulse_compatibility'}
        p.update(request.get('parameters',{}))
        try:
            explicit=request.get('explicitParameterKeys',[])
            if not isinstance(explicit,list) or any(not isinstance(k,str) for k in explicit):raise ValueError('Explicit parameter keys must be a list of strings')
            if set(explicit)-method_fields(method):raise ValueError('Explicit parameters do not belong to this method: '+','.join(sorted(set(explicit)-method_fields(method))))
            if set(explicit)-set(request.get('parameters',{})):raise ValueError('Explicit parameter value is missing')
            p,rows=build_repeat5(p) if method==REPEAT5 else build_rows(method,p);route=self._routing(resource)
            if self.profile.get('observedResourceBinding') and request.get('observedResourceBinding')!=self.profile['observedResourceBinding']:
                raise ValueError('Consumer observed-resource binding differs from frozen profile')
            if len(rows)*round((80e-6+p['readout_width_s']+1e-6)*6e9)>400_000_000:raise ValueError('Total native sample compilation budget exceeded')
            required=set(V3_METHODS[method][2])-{'xy_passband'}
            proof,dependencies,attestation=self._dependencies(request,required)
            from adapters.science_v3.evidence import check_pulse_dependencies
            check_pulse_dependencies(method,p,rows,dependencies)
            if 'xy_passband' in V3_METHODS[method][2]:
                require_xy_frequencies(route,(g['pulse']['frequencyHz'] for row in rows for g in row['gates']))
            sources=verify_sources(self.root)
        except (ValueError,OSError,KeyError,TypeError) as exc:raise AdapterError('SCIENCE_PLAN_REJECTED',str(exc)) from exc
        return {'version':3,'adapter':'science-repeat5','policy':REPEAT_POLICY if method==REPEAT5 else PULSE_POLICY,'operationId':request['operationId'],
                'procedureId':method,'resourceId':resource,'parameters':p,'rows':rows,
                'totalRows':len(rows),'estimated_total_shots':len(rows)*p['shots']*p['acquisitions_per_point'],
                'max_seconds':min(float(self.profile.get('maximumRunSeconds',3600)),3600.),
                'requestIdentity':{k:request.get(k) for k in ('runId','nodeId','attemptId','operationId','resourceId','procedureId','parameterVersions','leaseToken','observedResourceBinding')},
                'requestHash':digest({k:v for k,v in request.items() if k!='outputDir'}),
                'profileHash':digest(self.profile),'profileSnapshot':deepcopy(self.profile),'sources':sources,'sourceHash':digest(sources),
                'validationEvidence':proof,'nativeAttestations':attestation,
                'configurationBinding':deepcopy(self.profile.get('configurationBinding',{})),
                'observedResourceBinding':deepcopy(self.profile.get('observedResourceBinding')),
                'hardwareLocks':self.profile['hardwareLocks'],'instrument':validate_instrument(self.profile['instrument']),
                'routing':route,'visaLibrary':self.profile['visaLibrary']}
    def build_plan(self,request,output_dir=None):
        plan=self._build_v3_plan(request,output_dir) if request.get('procedureId') in V3_METHODS else self._build_irb_plan(request,output_dir)
        if 'outputDir' in request:plan['consumerOutputDir']=request['outputDir']
        return plan

    def _build_irb_plan(self,request,output_dir=None):
        if request.get('procedureId')==HALF_PROCEDURE:return self._build_half_plan(request)
        try:
            if request.get('procedureId')!=PROCEDURE:raise ValueError('Only sq_drag_irb is accepted by this isolated adapter')
            self._check_explicit_mode(request)
            resource=request['resourceId']
            if resource not in self.profile['resources']:raise ValueError('Unknown resource')
            if not isinstance(request.get('operationId'),str) or not 1<=len(request['operationId'])<=256:raise ValueError('Bounded operation identity required')
            explicit=request.get('explicitParameterKeys',[])
            if not isinstance(explicit,list) or any(type(k) is not str for k in explicit) or set(explicit)-set(self.parameter_schema(PROCEDURE)):raise ValueError('Explicit parameter does not belong to IRB')
            allowed=FIELDS|set(DEFAULTS)|{'pulse_compatibility'}
            supplied={k:v for k,v in self.profile['resources'][resource].items() if k in allowed}
            supplied.update(request.get('parameters',{}));p,budget=normalize_parameters(supplied)
            proof,deps,attestations=self._dependencies(request,beta.required_roles(PROCEDURE,p))
            pulse_params={k:v for k,v in p.items() if k in FIELDS}
            if beta.empirical(p):
                base,pulse=beta.normalized_pulse(p);rows=[{'gates':[{'pulse':pulse}]}]
            else:base,rows=build_rows('sq_drag_scan',{**{k:v for k,v in pulse_params.items() if k!='drag_mode'},'capture_repeats':2,'scan_axis_values':[-1.,-.5,0.,.5,1.]})
            check_pulse_dependencies('sq_drag_scan',base,rows,{k:v for k,v in deps.items() if k!='drag' and (k!='anharmonicity' or not beta.empirical(p))})
            selected=deps['drag'].get('targetPulseIdentity')
            if deps['drag'].get('procedureId')!=HALF_PROCEDURE or selected!=identity(p):raise ValueError('IRB requires the exact independently accepted half-DRAG pulse identity')
            # All readout/route conditions must also match the selected DRAG source.
            check_pulse_dependencies('sq_drag_scan',base,rows,{'drag':deps['drag']})
            lineage=inherited_rabi_lineage(self.root,proof,attestations,p)
            sources=verify_sources(self.root);campaign=plan_native(self.root,self.profile['python'],p)
            if campaign['budget']!=budget:raise ValueError('Native campaign budget differs')
        except (ValueError,OSError,KeyError,TypeError) as exc:raise AdapterError('SCIENCE_IRB_PLAN_REJECTED',str(exc)) from exc
        return self._plan(request,p,campaign,proof,attestations,sources,beta.policy_for(PROCEDURE,p),lineage)
    def _plan(self,request,p,campaign,proof,attestations,sources,policy,lineage):
        resource=request['resourceId'];budget=campaign['budget']
        return {'version':3,'adapter':'science-repeat5','policy':policy,'pulsePolicy':beta.pulse_policy(p),
            'operationId':request['operationId'],'procedureId':request['procedureId'],'resourceId':resource,'parameters':p,
            'campaign':campaign,'rows':campaign['rows'],'totalRows':budget['rows'],'estimated_total_shots':budget['shots'],'sampleBudget':budget['samples'],
            'max_seconds':min(float(self.profile.get('maximumRunSeconds',3600)),3600.),
            'requestIdentity':{k:request.get(k) for k in ('runId','nodeId','attemptId','operationId','resourceId','procedureId','parameterVersions','leaseToken','observedResourceBinding')},
            'requestHash':digest({k:v for k,v in request.items() if k!='outputDir'}),'profileHash':digest(self.profile),'profileSnapshot':deepcopy(self.profile),
            'sources':sources,'sourceHash':digest(sources),'validationEvidence':deepcopy(proof),'nativeAttestations':attestations,
            'rabiLineage':deepcopy(lineage),
            'configurationBinding':deepcopy(self.profile.get('configurationBinding',{})),
            'observedResourceBinding':deepcopy(self.profile.get('observedResourceBinding')),
            'hardwareLocks':deepcopy(self.profile['hardwareLocks']),'instrument':validate_instrument(self.profile['instrument']),
            'routing':self._routing(resource),'visaLibrary':self.profile['visaLibrary']}
    def _build_half_plan(self,request):
        explicit=request.get('explicitParameterKeys',[])
        if not isinstance(explicit,list) or any(type(k) is not str for k in explicit) or set(explicit)-set(self.parameter_schema(HALF_PROCEDURE)):
            raise AdapterError('SCIENCE_IRB_PLAN_REJECTED','Explicit parameter does not belong to half-DRAG scan')
        try:
            self._check_explicit_mode(request)
            resource=request['resourceId']
            if resource not in self.profile['resources']:raise ValueError('Unknown resource')
            if not isinstance(request.get('operationId'),str) or not 1<=len(request['operationId'])<=256:raise ValueError('Bounded operation identity required')
            supplied={k:v for k,v in self.profile['resources'][resource].items() if k in SCAN_FIELDS}
            supplied.update({k:v for k,v in request.get('parameters',{}).items() if k in SCAN_FIELDS})
            p,campaign=build_half_scan(supplied)
            proof,deps,attestations=self._dependencies(request,beta.required_roles(HALF_PROCEDURE,p))
            check_pulse_dependencies('sq_drag_scan',p,campaign['rows'],{k:v for k,v in deps.items() if k!='anharmonicity' or not beta.empirical(p)})
            lineage=rabi_lineage(proof,attestations,p)
            sources=verify_sources(self.root)
        except (ValueError,OSError,KeyError,TypeError) as exc:raise AdapterError('SCIENCE_IRB_PLAN_REJECTED',str(exc)) from exc
        return self._plan(request,p,campaign,proof,attestations,sources,beta.policy_for(HALF_PROCEDURE,p),lineage)
    def _result(self,d,plan,request,completion):
        if plan['procedureId'] in V3_METHODS:
            result=super()._result(d,{**plan,'procedureId':'sq_shape_rabi_amplitude' if plan['procedureId']==REPEAT5 else plan['procedureId']},request,completion)
            if plan['procedureId']==REPEAT5:
                from .plans import POLICY as REPEAT_POLICY
                result['methodLabel']='Five-X shaped amplitude Rabi'
                result['provenance']['policy']=REPEAT_POLICY
                result['provenance']['shapeRabiCalibration']=deepcopy(completion['analysis'].get('shapeRabiCalibration'))
                result['provenance']['compiledSequenceEvidence']=deepcopy(completion.get('repeat5Evidence',{}))
            return result
        if plan['procedureId']==HALF_PROCEDURE:
            result=super()._result(d,{**plan,'procedureId':'sq_drag_scan'},request,completion)
            result['methodLabel']='Independent exact X/2 DRAG coefficient diagnostic'
            result['provenance']['campaignPolicy']=plan['policy']
            result['provenance']['policy']=beta.pulse_policy(plan['parameters'])
            result['provenance']['nativeRuntime']=deepcopy(completion.get('runtime',{}))
            result['provenance']['scienceHalfDrag']={'candidateComparison':deepcopy(completion['analysis'].get('candidateComparison')),
                'metricSemantics':deepcopy(completion['analysis']['analysis'].get('metricSemantics',{})),
                'rabiLineage':deepcopy(plan['rabiLineage']),
                'targetRotation':'X/2','zeroBaselineAcquired':0. in plan['parameters']['scan_axis_values']}
            candidate=result['candidateParameters'].get(beta.candidate_key(plan['parameters']))
            result['targetPulseIdentity']=target_identity(plan['parameters'],candidate) if candidate is not None else None
            if beta.empirical(plan['parameters']):result['provenance']['dragCoefficient']=beta.coefficient_evidence(plan['parameters'],candidate)
            return result
        if completion.get('operationId')!=plan['operationId'] or completion.get('resourceId')!=plan['resourceId'] or completion.get('routing')!=plan['routing'] or completion.get('shutdown_ok') is not True:raise AdapterError('IRB_RESULT_INVALID','IRB acquisition/cleanup identity differs')
        analysis=completion['analysis'];artifacts=self._artifacts(d,completion['files'])
        if not any(a['name']=='raw_scan.npz' for a in artifacts):raise AdapterError('IRB_RAW_MISSING','Completed campaign ADC data missing')
        return {'dataKind':'real','sourceRunId':plan['operationId'],'acquiredAt':completion['acquiredAt'],
            'parameters':deepcopy(plan['parameters']),'metrics':analysis['metrics'],'candidateParameters':{},'series':analysis.get('series',[]),
            'analysisQuality':analysis['analysis'],'artifacts':artifacts,'pulseIdentities':{'target01':plan['campaign']['pulseIdentity']},
            'targetPulseIdentity':plan['campaign']['targetPulseIdentity'],'methodLabel':'Independent DRAG X/2 paired IRB',
            'provenance':{'freshAcquisition':True,'policy':beta.pulse_policy(plan['parameters']),'campaignPolicy':plan['policy'],'operationId':plan['operationId'],
                'parameterVersions':deepcopy(request.get('parameterVersions',{})),'sourceHash':plan['sourceHash'],'profileHash':plan['profileHash'],
                'routing':plan['routing'],'shutdownCommandsCompleted':True,'shutdownReadbackVerified':False,
                'nativeRuntime':deepcopy(completion.get('runtime',{})),
                **({'dragCoefficient':beta.coefficient_evidence(plan['parameters'])} if beta.empirical(plan['parameters']) else {}),
                'uploadReceipt':str(d/'results/uploads.jsonl'),'cleanupReceipt':str(d/'completion.json'),
                'sequenceManifestSha256':plan['campaign']['sha256'],'scienceDependencies':deepcopy(plan['validationEvidence']),
                'scienceIRB':{**{k:deepcopy(analysis[k]) for k in ('irb','reference') if k in analysis},'rabiLineage':deepcopy(plan['rabiLineage'])}}}
