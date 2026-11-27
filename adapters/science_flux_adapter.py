"""Opt-in finite LF bias bridge. No provider/configuration means no dispatch."""
from copy import deepcopy
import json
from pathlib import Path
from .arclight_dynamic import DynamicArcLightAdapter
from .arclight_configurable_runtime.routing import validate_route as rf_route,validate_instrument
from .f8 import AdapterError,_digest as transport_digest
from .science_runtime.policy import digest,sha
from .science_flux.contracts import (POLICY,REQUIRED,validate_route,provider_for,is_commanded,policy_for,
    end_timeout_for,required_fields,command_authorization)
from .science_flux.plans import METHODS,BOUNDS,INTEGERS,DEFAULTS,FIELDS,AXIS_RULES,build_campaign
from .xy_passband import require_xy_frequencies

def shared_locks(root,profile):
    locks=set(profile.get('scienceFluxBaseLocks',profile.get('hardwareLocks',[])))
    for route in profile.get('biasRoutes',{}).values():
        if not isinstance(route,dict) or not route.get('deviceKey') or type(route.get('board')) is not int:continue
        names=['lf-module:'+route['deviceKey']+':'+str(route['board'])]
        try:
            provider=provider_for(route)
            if provider is not None:names.extend(provider.lock_names)
        except ValueError:pass
        locks.update(str(Path(root).resolve()/'data/hardware_locks'/('flux-'+digest(name)+'.lock')) for name in names)
    return sorted(locks)

def build_flux_profile(root,base_profile):
    root=Path(root);p=deepcopy(base_profile)
    p.update(id='science-flux-'+digest(base_profile)[:20],enabled=False,scienceFluxPolicy=POLICY,
        runner='adapters/science_flux/execute_worker.py',runtimeRoot='adapters/science_flux',
        sourceManifest='adapters/science_flux/source_manifest.json')
    p['runnerSha256']=sha(root/p['runner']);p['scienceFluxBaseLocks']=deepcopy(base_profile['hardwareLocks'])
    p['hardwareLocks']=shared_locks(root,p);p['parameterBounds']={**p.get('parameterBounds',{}),**BOUNDS}
    if not p.get('routingByResource') and len(p.get('resources',{}))==1:
        p['routingByResource']={next(iter(p['resources'])):rf_route(p['instrument'])}
    # STOP/timeout must leave enough time for native RF/ADC stop, bounded
    # register-driven restoration, provider close, and station close.
    reserve=max([r.get('endTimeoutSeconds',r.get('restoreTimeoutSeconds',180))+90 for r in p.get('biasRoutes',{}).values() if isinstance(r,dict)]+[270])
    p['cancelGraceSeconds']=max(float(p.get('cancelGraceSeconds',0)),reserve+30)
    return p

def verify_sources(root):
    from .science_v3_adapter import verify_extension
    from .science_flux.policy import FROZEN_V3_MANIFEST
    root=Path(root).resolve();sources=verify_extension(root);path=root/'adapters/science_flux/source_manifest.json'
    if sha(root/'adapters/science_v3/source_manifest.json')!=FROZEN_V3_MANIFEST:raise ValueError('Flux native RF/ADC dependency version changed')
    rows=json.loads(path.read_text(encoding='utf-8'));expected={str(p.relative_to(root)).replace('\\','/') for p in (root/'adapters/science_flux').glob('*.py')}|{'adapters/science_flux_adapter.py'}
    if {r['path'] for r in rows}!=expected:raise ValueError('Flux source manifest coverage differs')
    for item in rows:
        source=(root/item['path']).resolve()
        if not source.is_relative_to(root) or sha(source)!=item['sha256']:raise ValueError('Flux source integrity differs: '+item['path'])
    sources['adapters/science_flux/source_manifest.json']={'sha256':sha(path),'fileCount':len(rows)}
    return sources

class ScienceFluxAdapter(DynamicArcLightAdapter):
    def _missing(self,resource):
        missing=[];route=self.profile.get('biasRoutes',{}).get(resource)
        if not isinstance(route,dict):return ['biasRoutes.'+resource]
        missing.extend('biasRoutes.'+resource+'.'+k for k in sorted(required_fields(route)-set(route)))
        try:
            validate_route(route)
            if route['resourceId']!=resource:missing.append('biasRoutes.'+resource+'.resourceId mismatch')
            instrument=self.profile['instrument'];device='device:'+str(instrument['host']).strip().lower()+':'+str(int(instrument['port']))
            if route['deviceKey']!=device:missing.append('biasRoutes.'+resource+'.deviceKey mismatch')
        except (ValueError,KeyError,TypeError) as exc:missing.append(str(exc))
        try:provider_for(route)
        except (ValueError,KeyError,TypeError) as exc:missing.append(str(exc))
        return missing

    def capabilities(self):
        missing={r:self._missing(r) for r in self.profile['resources']}
        return {'id':self.profile['id'],'dataKind':'real','enabled':self.profile.get('enabled') is True,
            'physicalHardware':True,'isolatedProcess':True,'supportsCancel':'bounded ADC blocks, ramp commands and settling; restoration ignores acquisition STOP',
            'supportsReconcile':True,'resources':list(self.profile['resources']),'policy':POLICY,
            'policiesByResource':{r:policy_for(self.profile.get('biasRoutes',{}).get(r)) for r in self.profile['resources']},
            'procedures':{k:{'available':not any(missing.values()),'ownerId':v[1],'kind':k,
                'requires':list(v[2]),'status':'configuration_or_provider_required' if any(missing.values()) else 'requires_current_prerequisites',
                'missingFields':deepcopy(missing)} for k,v in METHODS.items()},
            'biasHoldAfterOperation':False,'restorationPolicy':'explicit_route_policy'}

    def preflight(self):
        missing={r:self._missing(r) for r in self.profile['resources']}
        try:
            sources=verify_sources(self.root)
            if self.profile.get('scienceFluxPolicy')!=POLICY or sha(self._local(self.profile['runner']))!=self.profile['runnerSha256']:raise ValueError('Flux runner/policy changed')
            if not Path(self.profile['python']).is_file():raise ValueError('Native Python interpreter is missing')
            validate_instrument(self.profile['instrument'])
            for resource in self.profile['resources']:self._routing(resource)
            static=True;reason=None
        except Exception as exc:static=False;sources={};reason=str(exc)
        ready=static and not any(missing.values())
        if not reason and any(missing.values()):reason='偏置路由、起止授权依据或所选测压策略的 provider 不完整；未连接仪器。'
        return {'ready':ready,'staticReady':static,'enabled':self.profile.get('enabled') is True,'hardwareConnected':False,
                'executionReady':ready and self.profile.get('enabled') is True,'missingFields':missing,'sources':sources,'reason':reason}

    def parameter_schema(self,procedure_id):
        if procedure_id not in METHODS:return {}
        schema={k:{'min':v[0],'max':v[1],**({'integer':True} if k in INTEGERS else {}),
            **({'default':DEFAULTS[k]} if k in DEFAULTS else {})} for k,v in BOUNDS.items() if k in FIELDS[procedure_id]}
        for key,(lo,hi,mn,mx) in AXIS_RULES.items():
            if key not in FIELDS[procedure_id]:continue
            if key=='bias_values_v':
                routes=[r for r in self.profile.get('biasRoutes',{}).values() if isinstance(r,dict) and type(r.get('minimumV')) in (int,float) and type(r.get('maximumV')) in (int,float)]
                if routes:mn=max(r['minimumV'] for r in routes);mx=min(r['maximumV'] for r in routes)
            schema[key]={'type':'numberArray','minItems':lo,'maxItems':hi,'min':mn,'max':mx,'unique':True,
                'required':key!='drive_frequencies_hz',**({'strictlyIncreasing':True} if key!='bias_values_v' else {})}
        return schema
    def parameter_defaults(self,procedure_id):return deepcopy(DEFAULTS) if procedure_id in METHODS else {}
    def validation_parameter_keys(self):return tuple(BOUNDS)+tuple(AXIS_RULES)
    def normalize_candidate(self,procedure_id,candidate):return {}
    def normalize_parameter_changes(self,procedure_id,changes,current_parameters):return deepcopy(changes)
    def build_verification_parameters(self,procedure_id,parameters,candidate):return deepcopy(parameters)

    def command_verification_authorization(self,request,source_operation_id,*,source_effect=None):
        from .science_flux.command_lease import verification_authorization
        from backend.science_dependencies import result_content
        if not isinstance(source_effect,dict) or set(source_effect)!={'request','result'}:raise ValueError('Completed controller selection effect is required')
        folder=self._operation_dir(source_operation_id)
        intent=json.loads((folder/'adapter_intent.json').read_text(encoding='utf8'))
        receipt=json.loads((folder/'adapter_receipt.json').read_text(encoding='utf8'))
        canonical={k:v for k,v in source_effect['request'].items() if k!='outputDir'}
        if (digest(intent.get('request'))!=digest(canonical)
            or digest(result_content(receipt.get('result',{})))!=digest(result_content(source_effect['result']))):
            raise ValueError('Prior end command differs from immutable controller selection effect')
        route=validate_route(self.profile.get('biasRoutes',{}).get(request['resourceId']))
        return verification_authorization(self.operations,request,route,shared_locks(self.root,self.profile),source_operation_id)

    def finalize_command_lease(self,lease_token):
        from .science_flux.command_lease import finalize_lease
        return finalize_lease(self.operations,shared_locks(self.root,self.profile),lease_token)

    def authenticate_dependency(self,request,dependency):
        from .science_flux.evidence import authenticate_flux_dependency,consumer
        roles=set(dependency.get('sourceRoles',[]));resource=request['resourceId']
        if roles=={'cavity_seed'}:
            if dependency.get('procedureId') not in ('ro_resonator_survey','ro_power_shift'):raise ValueError('Cavity seed must be independently accepted cavity acquisition')
            from .science_v3.evidence import authenticate_dependency
            mapped={**deepcopy(dependency),'sourceRoles':['readout']}
            attestation=authenticate_dependency(self.root,request,mapped,expected_route=self._routing(resource),
                expected_binding=self.profile.get('configurationBinding',{}),expected_locks=self.profile['scienceFluxBaseLocks'],
                expected_instrument=self.profile['instrument'],expected_observed=self.profile.get('observedResourceBinding'))
            old={**dependency.get('parameters',{}),**dependency.get('resolvedOutputs',{}).get('candidateParameters',{})}
            for key in ('readout_amp','readout_width_s','xy_delay_s','xy_attenuation_db','xy_filter_mhz'):
                if key not in old or request.get('parameters',{}).get(key)!=old[key]:raise ValueError('Cavity seed physical condition differs: '+key)
            attestation.update(dependencySha256=digest(dependency),consumerSha256=digest(consumer(request)),
                roleMigration='accepted_cavity_seed_only_not_readout_classifier')
            return attestation
        return authenticate_flux_dependency(self.root,request,dependency,route=self._routing(resource),
            instrument=self.profile['instrument'],binding=self.profile.get('configurationBinding',{}),
            observed=self.profile.get('observedResourceBinding'),bias_route=validate_route(self.profile.get('biasRoutes',{}).get(resource)),
            expected_locks=shared_locks(self.root,self.profile))

    def build_plan(self,request,output_dir=None):
        from .science_flux.evidence import consumer,validate_point
        method=request.get('procedureId');resource=request.get('resourceId')
        if method not in METHODS or resource not in self.profile['resources']:raise AdapterError('FLUX_METHOD_RESOURCE','Unknown flux method/resource')
        try:
            bias=validate_route(self.profile.get('biasRoutes',{}).get(resource));provider_for(bias)
            if self._missing(resource):raise ValueError('; '.join(self._missing(resource)))
            op=request.get('operationId')
            if not isinstance(op,str) or not 1<=len(op)<=256:raise ValueError('Bounded operation identity required')
            command_auth=None
            if is_commanded(bias):
                from .science_flux.command_contracts import validate_authorization
                command_auth=validate_authorization(request.get('biasCommandAuthorization'),bias,request)
                if command_auth['stage']!='plan_only' and any(not isinstance(request.get(k),str) or not request[k] for k in ('runId','nodeId','attemptId','leaseToken')):
                    raise ValueError('Physical command authorization requires complete run/node/attempt/lease identity')
            explicit=request.get('explicitParameterKeys',[])
            if not isinstance(explicit,list) or any(k not in FIELDS[method] for k in explicit):raise ValueError('Explicit parameter is not part of this flux method')
            p={k:v for k,v in self.profile['resources'][resource].items() if k in BOUNDS};p.update(request.get('parameters',{}))
            proof=request.get('scienceDependencies',{})
            keys=('schema','policyVersion','kind','scope','inputs','rootReferences','nodes','edges')
            if proof.get('assessment',{}).get('executionEligible') is not True or digest({k:proof[k] for k in keys})!=proof.get('closureSha256') or proof['assessment'].get('consumerSha256')!=digest(consumer(request)):raise ValueError('Current exact server dependency closure required')
            nodes={n['recordId']:n for n in proof['nodes']};roots={r['role']:nodes[r['recordId']] for r in proof['rootReferences']}
            if not set(METHODS[method][2])<=set(roots):raise ValueError('Missing flux prerequisite roles')
            attestations={}
            for dep in proof['nodes']:
                roles=sorted({e['role'] for e in proof['edges'] if e['toRecordId']==dep['recordId']})
                attestations[dep['recordId']]=self.authenticate_dependency(request,{**dep,'sourceRoles':roles})
            point=None
            if method=='sq_flux_point_recheck':point=validate_point(request.get('fluxPointEvidence'),roots['flux_scan'],attestations[roots['flux_scan']['recordId']],bias)
            campaign=build_campaign(method,p,bias,point=point);routing=self._routing(resource)
            if campaign['parameters'].get('drive_frequencies_hz'):
                require_xy_frequencies(routing,(row['frequencyHz'] for row in campaign['rows'] if row['phase']=='f01'))
            maximum=min(14400.,float(self.profile.get('maximumRunSeconds',3600)))
            if campaign['requiredSeconds']>maximum:raise ValueError('Entire flux acquisition/readback/restoration budget exceeds profile maximumRunSeconds')
            if self.profile.get('observedResourceBinding')!=request.get('observedResourceBinding'):raise ValueError('Observed-resource consumer differs')
            if self.profile.get('configurationBinding',{})!=request.get('configurationBinding',{}):raise ValueError('Published configuration consumer differs')
            sources=verify_sources(self.root)
        except (ValueError,KeyError,TypeError,OSError) as exc:raise AdapterError('FLUX_PLAN_REJECTED',str(exc)) from exc
        from .science_flux.command_lease import lease_context,validate_prior_entry
        plan={'version':1,'adapter':'science-flux','policy':policy_for(bias),**campaign,'operationId':op,'resourceId':resource,'procedureId':method,
            **({'biasCommandAuthorization':command_auth} if command_auth is not None else {}),
            'requestHash':digest({k:v for k,v in request.items() if k!='outputDir'}),'transportRequestHash':transport_digest({k:v for k,v in request.items() if k!='outputDir'}),
            'profileHash':digest(self.profile),'profileSnapshot':deepcopy(self.profile),'sourceHash':digest(sources),'sources':sources,
            'max_seconds':maximum,'hardwareLocks':shared_locks(self.root,self.profile),'biasRoute':bias,'routing':routing,
            'instrument':validate_instrument(self.profile['instrument']),'visaLibrary':self.profile['visaLibrary'],
            'configurationBinding':deepcopy(self.profile.get('configurationBinding',{})),
            'observedResourceBinding':deepcopy(self.profile.get('observedResourceBinding')),
            'validationEvidence':deepcopy(proof),'nativeAttestations':attestations}
        if command_auth is not None:
            plan['commandLease']=lease_context(request,command_auth['stage'])
            validate_prior_entry(self.operations,plan)
        return plan

    def _artifacts(self,d,files):
        artifacts=super()._artifacts(d,files)
        for a in artifacts:
            a['id']='flux-'+digest({'operationDirectory':str(Path(d).resolve()),'path':str(Path(a['sourcePath']).resolve().relative_to(Path(d).resolve())),'sha256':a['sha256']})[:32]
            a['url']='/api/artifacts/'+a['id']
        return artifacts

    def _result(self,d,plan,request,completion):
        if completion.get('testOnly') or completion.get('state')!='completed' or completion.get('shutdown_ok') is not True:raise AdapterError('FLUX_UNQUALIFIED_RESULT','Fixture/incomplete/unrestored result cannot become real acquisition')
        if any(completion.get(k)!=plan.get(k) for k in ('operationId','resourceId','routing','policy')):raise AdapterError('FLUX_RESULT_IDENTITY','Native flux result identity differs')
        analysis=completion['analysis'];artifacts=self._artifacts(d,completion['files'])
        if not any(a['name']=='raw_scan.npz' for a in artifacts):raise AdapterError('RAW_DATA_MISSING','Native flux raw data is absent')
        commanded=is_commanded(plan['biasRoute']);policy=policy_for(plan['biasRoute'])
        if commanded:
            from .science_flux.command_contracts import validate_command_journal,validate_command_receipt
            journal=json.loads((Path(d)/'results/bias_journal.json').read_text(encoding='utf-8'))
            identity={'operationId':plan['operationId'],'resourceId':plan['resourceId']}
            commands=validate_command_journal(journal,plan['biasRoute'],identity,plan['biasCommandAuthorization'])
            if any(journal.get(k)!=completion.get(k) for k in ('initial','restoration')):raise AdapterError('FLUX_END_EVIDENCE','End/start journal differs from completion')
            for point_result in completion['points']:
                receipt=point_result['biasReceipt'];validate_command_receipt(receipt,plan['biasRoute'],setting=point_result['requestedSettingV'],identity=identity)
                if commands.get(receipt['command']['id'])!=receipt['command']:raise AdapterError('FLUX_COMMAND_EVIDENCE','Point command is missing from journal')
        point=plan.get('sourcePoint');context={'schema':'qcal-bias-context/1','policy':policy,'routeSha256':digest(plan['biasRoute']),
            'resourceId':plan['resourceId'],'operationId':plan['operationId'],'heldAfterOperation':False,
            'requiresReapplicationAndMeasuredReadback':not commanded,'scopedCandidateParameters':deepcopy(analysis.get('scopedCandidateParameters',{}))}
        if commanded:context.update(requiresAuthorizedReapplication=True,biasCoordinate='commanded_setpoint',measuredOutputV=None)
        if point:context.update(workingPointId=point['workingPointId'],pointId=point['pointId'],requestedSettingV=point['requestedSettingV'],sourcePointSha256=point['sha256'])
        context['sha256']=digest(context)
        science={'schema':'qcal-science-flux-result/1','policy':policy,'route':plan['biasRoute'],'routeSha256':digest(plan['biasRoute']),
            'points':completion['points'],'initial':completion['initial'],'restoration':completion['restoration'],
            'rfShutdown':completion['rfShutdown'],'transportClose':completion['transportClose'],'providerClosed':completion['providerClosed'],'biasContext':context,
            'sourcePoint':point,'sourceClosure':plan['validationEvidence']['closureSha256'],
            'quantity':'measured terminal voltage plus commanded offset; raw IQ, not normalized S21',
            'restorationMeaning':'Original hardware offset register restored, independently measured output verified at the original plane'}
        if commanded:science.update(commandAuthorization=deepcopy(plan['biasCommandAuthorization']),
            commandLease=deepcopy(plan.get('commandLease')),commandContinuation=deepcopy(completion.get('commandContinuation')),
            completionSha256=sha(Path(d)/'completion.json') if (Path(d)/'completion.json').is_file() else None,
            quantity='commanded voltage setpoint; raw acquired IQ, not normalized S21; output voltage not independently measured',
            restorationMeaning='Native return-to-declared-end command completed; unknown entry physical state is not restored or measured')
        series=[]
        for point_result in completion['points']:
            for index,trace in enumerate(point_result['analysis'].get('series',[])):
                name=point_result['phase']+'/'+point_result['pointId']+'/'+str(index+1)
                series.append({**deepcopy(trace),'name':name,'label':name,'xUnit':'Hz','yUnit':'ADC a.u.',
                    'quantity':'paired_excursion' if point_result['phase']=='f01' else 'readout_iq_magnitude'})
        return {'dataKind':'real','sourceRunId':plan['operationId'],'acquiredAt':completion['acquiredAt'],
            'parameters':plan['parameters'],'metrics':analysis['metrics'],'candidateParameters':{},'series':series,
            'analysisQuality':analysis['analysis'],'artifacts':artifacts,
            'methodLabel':'Commanded bias / cavity scan' if commanded and plan['procedureId']=='ro_flux_scan' else METHODS[plan['procedureId']][0],
            'provenance':{'freshAcquisition':True,'policy':policy,'operationId':plan['operationId'],'sourceHash':plan['sourceHash'],
                'profileHash':plan['profileHash'],'parameterVersions':request.get('parameterVersions',{}),'routing':plan['routing'],
                'scienceDependencies':deepcopy(plan['validationEvidence']),'scienceFlux':science,
                'shutdownCommandsCompleted':True,'shutdownReadbackVerified':False,'biasRestorationReadbackVerified':not commanded,
                **({'biasEndCommandVerified':completion['restoration'].get('commandEndVerified') is True} if commanded else {})}}
    def publish_calibration(self,snapshot):raise AdapterError('FLUX_GLOBAL_PUBLICATION_FORBIDDEN','Bias-scoped frequencies require reapplication/readback, not global calibration publication')

def validate_admission(root,plan,folder):
    root=Path(root);folder=Path(folder);intent=json.loads((folder/'adapter_intent.json').read_text(encoding='utf-8'));request=intent.get('request',{})
    if not request.get('leaseToken') or any(plan.get(k) for k in ('testOnly','compileOnly','compileOnlyEvidence')):raise ValueError('Physical flux admission needs a controller lease and non-fixture plan')
    if plan.get('requestHash')!=digest(request) or plan.get('transportRequestHash')!=transport_digest(request) or intent.get('requestHash')!=transport_digest(request):raise ValueError('Controller intent/canonical plan request hashes differ')
    profile=plan.get('profileSnapshot',{})
    if digest(profile)!=plan.get('profileHash') or transport_digest(profile)!=intent.get('profileHash') or profile.get('enabled') is not True:raise ValueError('Frozen execution profile identity differs')
    rebuilt=ScienceFluxAdapter(root,profile).build_plan(request)
    if rebuilt!=plan:raise ValueError('Native flux plan differs from authenticated bounded rebuild')
    if is_commanded(plan['biasRoute']):
        from .science_flux.command_lease import lease_context,validate_prior_entry
        stage=plan['biasCommandAuthorization']['stage']
        if stage=='plan_only' or plan.get('commandLease')!=lease_context(request,stage):raise ValueError('Budget or mismatched lease cannot authorize LF writes')
        validate_prior_entry(folder.parent,plan,require_locks=True)
    return request
