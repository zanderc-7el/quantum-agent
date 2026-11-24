"""Version-bound multi-resource ArcLight bridge; planning never loads native drivers."""
from copy import deepcopy
from pathlib import Path
import ast
from .arclight import ArcLightAdapter, CALIBRATION_KEYS
from .f8 import AdapterError, _load, _digest
from .history import sha256
from .arclight_configurable_runtime.routing import apply_job_routing, validate_route, validate_instrument

RUNTIME = 'adapters/arclight_configurable_runtime'


def build_dynamic_profile(root, base_profile, chip_version, deployment_version, resource_ids):
    """Build a detached profile from explicit resource seeds and deployment routes.

    chip_version.resources[id].parameters must provide every calibration key.
    deployment_version.routes[id] contains physical pairs (XY, readout, ADC),
    with optional logical pairs which must agree with the bounded F8 mapping.
    Neither seed values nor routing are inherited from the legacy Q11 resource.
    """
    root = Path(root)
    base = _load(base_profile) if isinstance(base_profile,(str,Path)) else deepcopy(base_profile)
    profile = deepcopy(base)
    ids = list(resource_ids)
    if not ids or len(set(ids)) != len(ids):
        raise AdapterError('INVALID_RESOURCE_BINDING','Select distinct resource IDs')
    resources = chip_version.get('resources',{})
    routes = deployment_version.get('routes',{})
    profile['resources'] = {}
    profile['routingByResource'] = {}
    try:
        profile['instrument'] = validate_instrument(deployment_version.get('instrument',{}))
        for resource in ids:
            if resource not in resources or resource not in routes:
                raise ValueError('Resource or deployment route unavailable: '+str(resource))
            entry = resources[resource]
            seeds = entry.get('parameters',entry)
            missing = set(CALIBRATION_KEYS)-set(seeds)
            if missing:
                raise ValueError('Resource '+str(resource)+' missing explicit initialization: '+','.join(sorted(missing)))
            profile['resources'][resource] = {key:deepcopy(seeds[key]) for key in CALIBRATION_KEYS}
            profile['resources'][resource].update(q=resource,physical_id=entry.get('physical_id'),bus_id=entry.get('bus_id'),
                calibrationStatus='imported_initialization_requires_fresh_measurement')
            profile['routingByResource'][resource] = validate_route(routes[resource])
    except (TypeError,ValueError) as exc:
        raise AdapterError('INVALID_RESOURCE_BINDING',str(exc)) from exc
    profile.update(enabled=False,runtimeRoot=RUNTIME,runner=RUNTIME+'/platform_worker.py',sourceManifest=RUNTIME+'/source_manifest.json')
    profile['runnerSha256'] = sha256(root/profile['runner'])
    profile['configurationBinding'] = {'chipVersionId':chip_version.get('id',chip_version.get('versionId')),
        'deploymentVersionId':deployment_version.get('id',deployment_version.get('versionId')),
        'resourceIds':ids,'chipHash':_digest(chip_version),'deploymentHash':_digest(deployment_version)}
    profile['id'] = 'arclight-dynamic-'+_digest(profile['configurationBinding'])[:24]
    # Shared hardware locks and all bounds are inherited byte-for-value from base.
    adapter = DynamicArcLightAdapter(root,profile)
    for resource in ids:
        for key in CALIBRATION_KEYS:
            rule = profile['parameterBounds'][key]
            value = profile['resources'][resource][key]
            if isinstance(rule,dict):
                if value not in rule['enum']:raise AdapterError('INVALID_PARAMETER',key+' policy unsupported')
            else:adapter._validate_number(key,value,rule,integer=key=='xy_filter_mhz')
    return deepcopy(profile)


class DynamicArcLightAdapter(ArcLightAdapter):
    def __init__(self,project_root,profile):
        super().__init__(project_root,profile)
        self.operations = self.root/'data/runs/arclight_operations'

    def _routing(self, resource):
        if resource not in self.profile.get('resources',{}):
            raise AdapterError('UNKNOWN_RESOURCE','Resource not present in frozen configuration: '+str(resource))
        try:
            return validate_route(self.profile.get('routingByResource',{}).get(resource))
        except (TypeError,ValueError) as exc:
            raise AdapterError('INVALID_RESOURCE_BINDING',str(exc)) from exc

    def preflight(self):
        checks=[]
        def record(name,ok,detail):checks.append({'name':name,'ok':bool(ok),'detail':str(detail)})
        try:
            runner=self._local(self.profile['runner'])
            record('runner_hash',runner.is_file() and sha256(runner)==self.profile['runnerSha256'],runner)
            manifest=_load(self._local(self.profile['sourceManifest']))
            expected={str(p.relative_to(self.root)).replace('\\','/') for p in (self.root/RUNTIME).rglob('*') if p.is_file() and p.suffix in ('.py','.json') and p.name!='source_manifest.json'}
            manifest_paths={item['path'] for item in manifest}
            record('manifest_coverage',expected.issubset(manifest_paths),len(expected))
            for item in manifest:
                p=self._local(item['path'])
                record('source:'+item['path'],p.is_file() and sha256(p)==item['sha256'],p)
            ast.parse(runner.read_text(encoding='utf-8-sig'))
            record('syntax',True,'AST only; no native imports')
            record('python',Path(self.profile['python']).is_file(),self.profile['python'])
            record('visa',Path(self.profile['visaLibrary']).is_file(),self.profile['visaLibrary'])
            validate_instrument(self.profile['instrument'])
            record('instrument',True,self.profile['instrument']['address'])
            record('resource_map',bool(self.profile['resources']) and set(self.profile['resources'])==set(self.profile['routingByResource']),list(self.profile['resources']))
            for resource in self.profile['resources']:record('routing:'+resource,True,self._routing(resource))
            record('shared_locks',len(self.profile['hardwareLocks'])==2 and len(set(self.profile['hardwareLocks']))==2,self.profile['hardwareLocks'])
        except Exception as exc:record('preflight_error',False,exc)
        ready=bool(checks) and all(c['ok'] for c in checks)
        return {'ready':ready,'staticReady':ready,'enabled':self.profile.get('enabled') is True,
            'executionReady':ready and self.profile.get('enabled') is True,'hardwareConnected':False,'ownershipVerified':False,
            'checks':checks,'notes':['Imported working points require fresh acquisition. Native compiler Q1/B1 are internal aliases.']}

    def build_plan(self,request,output_dir=None):
        resource=request.get('resourceId'); route=self._routing(resource)
        plan=super().build_plan(request,output_dir)
        try:
            for job in plan['jobs']:
                apply_job_routing(job,resource,route,self.profile['instrument'],self.profile['visaLibrary'])
                job['operationId']=plan['operationId']
        except (TypeError,ValueError) as exc:
            raise AdapterError('INVALID_RESOURCE_BINDING',str(exc)) from exc
        plan['routing']=route
        plan['configurationBinding']=deepcopy(self.profile.get('configurationBinding',{}))
        plan['profileHash']=_digest(self.profile)
        return plan

    def _result(self,d,plan,request,completion):
        folders=completion.get('folders',[])
        if len(folders)!=len(plan['jobs']):
            raise AdapterError('RESULT_ROUTING_INVALID','Missing per-job acquisition routing receipts')
        receipts=[]
        for folder,job in zip(folders,plan['jobs']):
            folder=Path(folder).resolve()
            if not folder.is_relative_to(d.resolve()):raise AdapterError('RESULT_ROUTING_INVALID','Routing receipt outside operation')
            receipt=_load(folder/'instrument.json'); status=_load(folder/'status.json')
            if (receipt.get('operationId')!=plan['operationId'] or status.get('operationId')!=plan['operationId']
                or receipt.get('resourceId')!=request['resourceId'] or receipt.get('routing')!=job['routing']
                or receipt.get('address')!=job['instrument']['address'] or status.get('target')!=request['resourceId']
                or status.get('actualRoute')!=job['routing']):
                raise AdapterError('RESULT_ROUTING_INVALID','Actual acquisition route differs from frozen job')
            receipts.append(deepcopy(receipt))
        result=super()._result(d,plan,request,completion)
        result['resourceId']=request['resourceId']
        result['provenance'].update(resourceId=request['resourceId'],routing=deepcopy(plan['routing']),routingReceipts=receipts,
            configurationBinding=deepcopy(plan['configurationBinding']))
        return result
