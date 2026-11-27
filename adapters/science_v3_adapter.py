"""Opt-in V3 science bridge; controller remains the sole lease/effect owner."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from .arclight_dynamic import DynamicArcLightAdapter
from .arclight_configurable_runtime.routing import validate_route,validate_instrument
from .f8 import AdapterError
from .science_runtime.policy import verify_sources,digest,sha
from .science_v3.plans import POLICY,METHODS,BOUNDS,INTEGERS,VERIFY,METHOD_FIELDS,DEFAULTS,build_rows,pulse_identity
from .science_v3.evidence import authenticate_dependency
from .xy_passband import validate_xy_passband,require_xy_frequencies

def build_science_profile(root,base_profile):
    root=Path(root);p=deepcopy(base_profile)
    p.update(id='science-v3-'+digest(base_profile)[:20],enabled=False,sciencePolicy=POLICY,
             runner='adapters/science_v3/execute_worker.py',runtimeRoot='adapters/science_v3',
             sourceManifest='adapters/science_v3/source_manifest.json')
    p['runnerSha256']=sha(root/p['runner'])
    p['parameterBounds']={**p.get('parameterBounds',{}),**deepcopy(BOUNDS)}
    if not p.get('routingByResource'):
        if len(p.get('resources',{}))!=1:raise ValueError('Each resource requires an explicit route')
        p['routingByResource']={next(iter(p['resources'])):validate_route(p['instrument'])}
    return p

def verify_extension(root):
    sources=verify_sources(root);path=Path(root)/'adapters/science_v3/source_manifest.json'
    rows=json.loads(path.read_text(encoding='utf-8'))
    for row in rows:
        file=(Path(root)/row['path']).resolve()
        if not file.is_relative_to(Path(root).resolve()) or sha(file)!=row['sha256']:raise ValueError('V3 source hash mismatch: '+row['path'])
    sources['adapters/science_v3/source_manifest.json']={'sha256':sha(path),'fileCount':len(rows)}
    return sources

class ScienceV3Adapter(DynamicArcLightAdapter):
    def _artifacts(self,d,files):
        artifacts=super()._artifacts(d,files);folder=Path(d).resolve()
        for artifact in artifacts:
            path=Path(artifact['sourcePath']).resolve()
            # Repeated captures intentionally have identical compiled bytes.
            # Preserve each physical file identity instead of overwriting its
            # different name/path in the controller's artifact registry.
            artifact['id']='science-'+digest({'schema':'science-artifact-file/1','operationDirectory':str(folder),
                'relativePath':path.relative_to(folder).as_posix(),'contentSha256':artifact['sha256']})[:32]
            artifact['url']='/api/artifacts/'+artifact['id']
        return artifacts
    def authenticate_dependency(self,request,dependency):return authenticate_dependency(self.root,request,dependency,
        expected_route=self._routing(request['resourceId']),expected_binding=self.profile.get('configurationBinding',{}),
        expected_locks=self.profile['hardwareLocks'],expected_instrument=self.profile['instrument'],
        expected_observed=self.profile.get('observedResourceBinding'))
    def capabilities(self):
        capabilities={}
        for key,item in METHODS.items():
            missing=[]
            if 'xy_passband' in item[2]:
                for resource in self.profile['resources']:
                    route=self.profile.get('routingByResource',{}).get(resource,{})
                    try:validate_xy_passband(route,required=True)
                    except ValueError:missing.append(resource)
            capabilities[key]={'available':not missing,'status':'route_confirmation_required' if missing else 'requires_current_prerequisites',
                    'ownerId':item[1],'kind':key,'requires':list(item[2]),'missingPassbandResources':missing}
        return {'id':self.profile['id'],'dataKind':'real','enabled':self.profile.get('enabled') is True,
                'physicalHardware':True,'isolatedProcess':True,'supportsReconcile':True,'supportsCancel':'finite ADC block boundaries',
                'resources':list(self.profile['resources']),'procedures':capabilities,
                'unavailable':{'sq_drag_irb':'v3 independent RB sequence/analysis admission not implemented',
                               'flux':'Explicit LF route and restoration/readback capability required'}}
    def preflight(self):
        try:
            sources=verify_extension(self.root)
            if sha(self._local(self.profile['runner']))!=self.profile['runnerSha256']:raise ValueError('Runner changed')
            if self.profile.get('sciencePolicy')!=POLICY:raise ValueError('V3 policy missing')
            if not Path(self.profile['python']).is_file():raise ValueError('Configured native interpreter missing')
            validate_instrument(self.profile['instrument'])
            for resource in self.profile['resources']:self._routing(resource)
            if len(set(self.profile['hardwareLocks']))!=2:raise ValueError('Existing shared chassis locks required')
            return {'ready':True,'staticReady':True,'enabled':self.profile.get('enabled') is True,'hardwareConnected':False,'sources':sources}
        except Exception as exc:return {'ready':False,'staticReady':False,'enabled':False,'hardwareConnected':False,'reason':str(exc)}
    def parameter_schema(self,procedure_id):
        if procedure_id not in METHODS:return {}
        return {key:{**(deepcopy(rule) if isinstance(rule,dict) else {'min':rule[0],'max':rule[1],**({'integer':True} if key in INTEGERS else {})}),
                     **({'default':self.parameter_defaults(procedure_id)[key]} if key in self.parameter_defaults(procedure_id) else {})} for key,rule in BOUNDS.items() if key in METHOD_FIELDS[procedure_id]}
    def parameter_defaults(self,procedure_id):
        defaults={k:v for k,v in DEFAULTS.items() if k in METHOD_FIELDS.get(procedure_id,set())}
        if procedure_id=='sq_drag_scan':defaults['points']=21
        return defaults
    def validation_parameter_keys(self):return tuple(BOUNDS)+('scan_axis_values','pulse_compatibility')
    def normalize_candidate(self,procedure_id,candidate):return deepcopy(candidate)
    def normalize_parameter_changes(self,procedure_id,changes,current_parameters):return self.normalize_candidate(procedure_id,changes)
    def build_verification_parameters(self,procedure_id,parameters,candidate):
        p=deepcopy(parameters);key=VERIFY.get(procedure_id)
        if key:
            if key not in candidate:raise AdapterError('SCIENCE_CANDIDATE_MISSING','Selected candidate is missing')
            p['verify_'+key]=candidate[key]
        return p
    def build_plan(self,request,output_dir=None):
        method=request.get('procedureId');resource=request.get('resourceId')
        if method not in METHODS:raise AdapterError('UNSUPPORTED_PROCEDURE','V3 method is unavailable')
        if resource not in self.profile['resources']:raise AdapterError('UNKNOWN_RESOURCE','Unknown resource')
        if not isinstance(request.get('operationId'),str) or not request['operationId'] or len(request['operationId'])>256:raise AdapterError('INVALID_OPERATION_ID','Missing bounded operation identity')
        p={k:v for k,v in self.profile['resources'][resource].items() if k in BOUNDS or k=='pulse_compatibility'}
        p.update(request.get('parameters',{}))
        try:
            explicit=request.get('explicitParameterKeys',[])
            if not isinstance(explicit,list) or any(not isinstance(k,str) for k in explicit):raise ValueError('Explicit parameter keys must be a list of strings')
            if set(explicit)-METHOD_FIELDS[method]:raise ValueError('Explicit parameters do not belong to this method: '+','.join(sorted(set(explicit)-METHOD_FIELDS[method])))
            if set(explicit)-set(request.get('parameters',{})):raise ValueError('Explicit parameter value is missing')
            p,rows=build_rows(method,p);route=self._routing(resource)
            if self.profile.get('observedResourceBinding') and request.get('observedResourceBinding')!=self.profile['observedResourceBinding']:
                raise ValueError('Consumer observed-resource binding differs from frozen profile')
            if len(rows)*round((80e-6+p['readout_width_s']+1e-6)*6e9)>400_000_000:raise ValueError('Total native sample compilation budget exceeded')
            proof=request.get('scienceDependencies',{})
            if proof.get('assessment',{}).get('executionEligible') is not True or not isinstance(proof.get('nodes'),list):raise ValueError('Current authenticated science dependencies are required')
            binding_keys=('schema','policyVersion','kind','scope','inputs','rootReferences','nodes','edges')
            if digest({k:proof[k] for k in binding_keys})!=proof.get('closureSha256'):raise ValueError('Dependency closure hash differs')
            nodes={item['recordId']:item for item in proof['nodes']}
            dependencies={r['role']:nodes[r['recordId']] for r in proof['rootReferences']}
            required=set(METHODS[method][2])-{'xy_passband'}
            if not required<=set(dependencies):raise ValueError('Missing prerequisite roles: '+','.join(sorted(required-set(dependencies))))
            attestation=[self.authenticate_dependency(request,{**dependencies[role],'sourceRoles':sorted(r for r,d in dependencies.items() if d['recordId']==dependencies[role]['recordId'])}) for role in sorted(required)]
            from .science_v3.evidence import check_pulse_dependencies
            check_pulse_dependencies(method,p,rows,dependencies)
            if 'xy_passband' in METHODS[method][2]:
                require_xy_frequencies(route,(g['pulse']['frequencyHz'] for row in rows for g in row['gates']))
            sources=verify_extension(self.root)
        except (ValueError,OSError,KeyError,TypeError) as exc:raise AdapterError('SCIENCE_PLAN_REJECTED',str(exc)) from exc
        return {'version':3,'adapter':'science-v3','policy':POLICY,'operationId':request['operationId'],
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
    def _result(self,d,plan,request,completion):
        if completion.get('operationId')!=plan['operationId'] or completion.get('resourceId')!=plan['resourceId'] or completion.get('routing')!=plan['routing']:
            raise AdapterError('RESULT_ROUTING_INVALID','V3 acquisition identity differs')
        analysis=completion['analysis'];artifacts=self._artifacts(d,completion['files'])
        if not any(a['name']=='raw_scan.npz' for a in artifacts):raise AdapterError('RAW_DATA_MISSING','No completed raw ADC data')
        readout={k:plan['parameters'][k] for k in ('readout_hz','readout_amp','readout_width_s','xy_delay_s')}
        pulses={}
        for row in plan['rows']:
            for gate in row['gates']:
                label=gate['pulse']['label'];identity=pulse_identity(gate['pulse'],readout)
                if label not in pulses:pulses[label]={'identity':identity,'sha256':digest(identity)}
        # Scan pulse identities are candidates, never the first scan point.
        candidate=analysis.get('candidateParameters',{})
        if 'prep01' in pulses:pulses['prep01']['identity']['amplitude']=plan['parameters']['drive_amp']
        if 'probe12' in pulses:pulses['probe12']['identity']['amplitude']=plan['parameters']['probe12_amplitude']
        if 'target01' in pulses:pulses['target01']['identity']['amplitude']=plan['parameters']['shape_pi_amp']
        for label,key in [('target01','shape_pi_amp'),('probe12','probe12_amplitude')]:
            if label in pulses and key in candidate:
                pulses[label]['identity']['amplitude']=candidate[key];pulses[label]['sha256']=digest(pulses[label]['identity'])
        if 'target01' in pulses and 'drag_lambda1' in candidate:
            pulses['target01']['identity']['lambda1']=candidate['drag_lambda1'];pulses['target01']['sha256']=digest(pulses['target01']['identity'])
        if 'probe12' in pulses and 'f12_hz' in candidate:
            pulses['probe12']['identity']['frequencyHz']=candidate['f12_hz'];pulses['probe12']['sha256']=digest(pulses['probe12']['identity'])
        if plan['procedureId']=='sq_shape_half_verify' and 'target01' in pulses:
            import math
            pulses['target01']['identity'].update(amplitude=plan['parameters']['shape_pi_amp']/2,rotationRad=math.pi/2)
        for pulse in pulses.values():pulse['sha256']=digest(pulse['identity'])
        return {'dataKind':'real','sourceRunId':plan['operationId'],'acquiredAt':completion['acquiredAt'],
                'metrics':analysis.get('metrics',{}),'candidateParameters':candidate,'series':analysis.get('series',[]),
                'analysisQuality':analysis['analysis'],'artifacts':artifacts,'parameters':plan['parameters'],
                **{k:deepcopy(analysis[k]) for k in ('comparison','classifierProtocol') if k in analysis},
                'methodLabel':METHODS[plan['procedureId']][0],
                'pulseIdentities':{k:v for k,v in pulses.items() if k in ('prep01','probe12')},
                'targetPulseIdentity':pulses.get('target01'),
                'provenance':{'freshAcquisition':True,'policy':POLICY,'operationId':plan['operationId'],
                    'parameterVersions':request.get('parameterVersions',{}),'sourceHash':plan['sourceHash'],
                    'profileHash':plan['profileHash'],'routing':plan['routing'],'shutdownCommandsCompleted':True,
                    'shutdownReadbackVerified':False,'uploadReceipt':str(d/'results/uploads.jsonl'),
                    'cleanupReceipt':str(d/'completion.json'),'dependencyClosureSha256':plan['validationEvidence'].get('closureSha256'),
                    'scienceDependencies':deepcopy(plan['validationEvidence'])}}
    def publish_calibration(self,snapshot):raise AdapterError('SCIENCE_PUBLICATION_UNAVAILABLE','V3 IRB publication is not implemented')
