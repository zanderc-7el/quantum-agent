from copy import deepcopy
import unittest
import json
import hashlib
from pathlib import Path
import tempfile
from adapters.science_runtime.policy import digest
from tests.test_science_flux import route,Provider

def held(provider,r,prefix):
    samples=[provider.snapshot(route=r,timeout_seconds=.1,nonce=prefix+str(n)) for n in range(r['stabilitySamples'])]
    return {**samples[-1],'stabilitySamples':samples,'requestedSettingV':provider.setting,'expectedOutputV':provider.setting+.02}

def point_fixture():
    r=route();provider=Provider();provider.setting=.2;receipt=held(provider,r,'fixture-point')
    ops={}
    for stage in ('selection','verification'):
        point={'pointId':'bias-0','phase':'cavity','requestedSettingV':.2,'biasReceipt':deepcopy(receipt),
            'analysis':{'accepted':True,'candidateHz':7e9,'frequencyToleranceHz':2e6},
            'frequenciesHz':[6.99e9+i*1e6 for i in range(21)],'responseMagnitude':[1]*21,
            'readoutFrequencyHz':7e9,'rawSha256':('1' if stage=='selection' else '2')*64}
        point['cutSha256']=digest(point);ops[stage]={'operationId':stage,'points':[point]}
    native=ops['verification']['points'][0]
    source={'schema':'qcal-flux-point-evidence/1','workingPointId':'fixture-point','recordId':'fixture-source',
        'pointId':'bias-0','resourceId':'Qfixture','purpose':'idle','requestedSettingV':.2,
        'measuredOutputV':receipt['measurement']['valueV'],'measurementUncertaintyV':receipt['measurement']['uncertaintyV'],
        'routeSha256':digest(r),'usableReadoutBandHz':[native['frequenciesHz'][0],native['frequenciesHz'][-1]],
        'originalCut':{k:native[k] for k in ('frequenciesHz','responseMagnitude','readoutFrequencyHz')},
        'sourceOperations':[{'stage':stage,'operationId':stage,'cutSha256':op['points'][0]['cutSha256'],
            'pointReceiptSha256':digest(op['points'][0]['biasReceipt']),'rawSha256':op['points'][0]['rawSha256']} for stage,op in ops.items()]}
    source['sha256']=digest(source)
    return source,{'recordId':'fixture-source'},{'operationPoints':ops},r

class PointContracts(unittest.TestCase):
    def test_exact_native_point_and_source_tampering(self):
        from adapters.science_flux.evidence import validate_point
        source,dep,att,r=point_fixture();self.assertEqual(validate_point(source,dep,att,r),source)
        for key,value in [('requestedSettingV',.3),('resourceId','other'),('measuredOutputV',.3),('purpose','anything')]:
            changed={**source,key:value};changed['sha256']=digest({k:v for k,v in changed.items() if k!='sha256'})
            with self.subTest(key=key),self.assertRaises(ValueError):validate_point(changed,dep,att,r)
    def test_independent_source_bias_and_frequency_must_agree(self):
        from adapters.science_flux.evidence import validate_point
        for kind in ('measured','frequency','raw_reuse','cavity_unresolved'):
            source,dep,att,r=point_fixture();point=att['operationPoints']['selection']['points'][0]
            if kind=='measured':point['biasReceipt']['measurement']['valueV']+=.1
            elif kind=='frequency':point['readoutFrequencyHz']+=5e6;point['analysis']['candidateHz']+=5e6
            elif kind=='raw_reuse':point['rawSha256']=att['operationPoints']['verification']['points'][0]['rawSha256']
            else:point['analysis']['accepted']=False
            point['cutSha256']=digest({k:v for k,v in point.items() if k!='cutSha256'})
            source['sourceOperations'][0].update(cutSha256=point['cutSha256'],pointReceiptSha256=digest(point['biasReceipt']),rawSha256=point['rawSha256'])
            source['sha256']=digest({k:v for k,v in source.items() if k!='sha256'})
            with self.subTest(kind=kind),self.assertRaises(ValueError):validate_point(source,dep,att,r)

class NativeFilesContracts(unittest.TestCase):
    """Explicit file-identity fixture, never a physical measurement claim."""
    def fixture(self,root):
        from adapters.science_runtime.policy import sha
        from backend.science_dependencies import result_content
        from adapters.science_flux.contracts import POLICY
        from adapters.f8 import _digest
        source,dep,att,r=point_fixture();scope={'workflowId':'fixture-source-run','resourceId':'Qfixture'}
        binding={'chipVersionId':'fixture-chip','deploymentVersionId':'fixture-deployment','chipHash':'a'*64,'deploymentHash':'b'*64}
        ops=[]
        def write(path,value):path.write_text(json.dumps(value),encoding='utf-8')
        for stage,entry in att['operationPoints'].items():
            op=entry['operationId'];folder=root/'data/runs/arclight_operations'/hashlib.sha256(op.encode()).hexdigest();(folder/'results').mkdir(parents=True)
            request={'runId':scope['workflowId'],'resourceId':'Qfixture','attemptId':'fixture-attempt','operationId':op}
            from adapters.science_flux_adapter import shared_locks
            profile={'enabled':True,'biasRoutes':{'Qfixture':r},'scienceFluxBaseLocks':[str(root/'fixture-chassis.lock')],'hardwareLocks':[str(root/'fixture-chassis.lock')]}
            profile['hardwareLocks']=shared_locks(root,profile)
            plan={'policy':POLICY,'adapter':'science-flux','operationId':op,'resourceId':'Qfixture','requestHash':digest(request),'transportRequestHash':_digest(request),
                'biasRoute':r,'routing':{},'instrument':{},'configurationBinding':binding,'profileSnapshot':profile,'profileHash':digest(profile),
                'hardwareLocks':profile['hardwareLocks'],'sources':{'fixtureSource':True},'sourceHash':digest({'fixtureSource':True})}
            point=deepcopy(entry['points'][0]);raw=folder/'results/cut_000.npz';raw.write_bytes(b'explicit nonphysical file fixture '+stage.encode())
            point.update(rawPath=str(raw),rawSha256=sha(raw),rowIds=[0]);point['cutSha256']=digest({k:v for k,v in point.items() if k!='cutSha256'})
            provider=Provider();provider.setting=.2
            initial=held(provider,r,'fixture-initial');restoration={**held(provider,r,'fixture-restore'),'verified':True};points=[point]
            completion={'state':'completed','operationId':op,'resourceId':'Qfixture','testOnly':False,'shutdown_ok':True,'points':points,'initial':initial,'restoration':restoration,
                'rfShutdown':{'verified':True},'providerClosed':True,'transportClose':{'shutdown_ok':True}}
            write(folder/'adapter_intent.json',{'request':request,'requestHash':_digest(request),'profileHash':_digest(profile)});write(folder/'plan.json',plan);write(folder/'completion.json',completion)
            write(folder/'results/instrument.json',{'fixtureOnly':True})
            write(folder/'results/bias_journal.json',{'state':'restored','initial':initial,'restoration':restoration})
            write(folder/'results/bias_row_0000.json',{'pointId':'bias-0','rowId':0,'before':held(provider,r,'fixture-before'),'after':held(provider,r,'fixture-after')})
            archive=folder/'results/preflight_0000.npz';archive.write_bytes(b'explicit compiler archive fixture')
            waves={'xy_1':{'name':'xy_1','sha256':'f'*64}}
            write(folder/'results/compiled_plan.json',[{'rowId':0,'file':str(archive),'sha256':sha(archive),'waves':waves}])
            (folder/'results/uploads.jsonl').write_text(json.dumps({'rowId':0,**waves['xy_1'],'uploadCommandsCompleted':True})+'\n')
            artifacts=[]
            for index,path in enumerate(sorted(folder.rglob('*'))):
                if path.is_file():artifacts.append({'id':stage+str(index),'name':path.name,'kind':'raw' if path==raw else 'json',
                    'sha256':sha(path),'localPath':str(path)})
            result={'dataKind':'real','acquiredAt':'fixture-only','analysisQuality':{'accepted':True},'artifacts':artifacts,
                'provenance':{'freshAcquisition':True,'scienceFlux':{'points':points,'initial':initial,'restoration':restoration}}}
            write(folder/'adapter_receipt.json',{'status':'completed','result':result})
            ops.append({'stage':stage,'operationId':op,'adapterRequestSha256':digest(request),'resultContentSha256':digest(result_content(result)),
                'rawArtifacts':[{'id':'fixture-raw','sha256':sha(raw)}],
                'registeredArtifacts':[{k:a[k] for k in ('id','name','kind','sha256')} for a in artifacts]})
        dep.update(procedureId='ro_flux_scan',sourceRoles=['flux_scan'],attemptId='fixture-attempt',scope=scope,operations=ops)
        target={'runId':'fixture-consumer-run','resourceId':'Qfixture'}
        return target,dep,dict(route={},instrument={},binding=binding,observed=None,bias_route=r,expected_locks=profile['hardwareLocks'])

    def test_file_receipt_mapping_and_mutations(self):
        from adapters.science_flux.evidence import authenticate_flux_dependency
        from unittest.mock import patch
        from backend.science_dependencies import result_content
        from adapters.science_runtime.policy import sha
        for mutation in ('none','raw','compiled','upload','journal','fixture','route','result','plan_operation','source_hash','profile_hash','locks','unregistered_metadata'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);request,dep,kwargs=self.fixture(root)
                folder=root/'data/runs/arclight_operations'/hashlib.sha256(b'selection').hexdigest()
                if mutation in ('raw','compiled','upload','journal'):
                    name={'raw':'cut_000.npz','compiled':'preflight_0000.npz','upload':'uploads.jsonl','journal':'bias_journal.json'}[mutation]
                    with (folder/'results'/name).open('ab') as stream:stream.write(b'changed')
                elif mutation=='fixture':
                    path=folder/'completion.json';x=json.loads(path.read_text());x['testOnly']=True;path.write_text(json.dumps(x))
                elif mutation=='route':kwargs['bias_route']={**kwargs['bias_route'],'channel':2}
                elif mutation=='result':dep['operations'][0]['resultContentSha256']='0'*64
                elif mutation in ('plan_operation','source_hash','profile_hash','locks','unregistered_metadata'):
                    plan_path=folder/'plan.json';x=json.loads(plan_path.read_text())
                    if mutation=='plan_operation':x['operationId']='different-op'
                    if mutation=='source_hash':x['sourceHash']='0'*64
                    if mutation=='profile_hash':x['profileHash']='0'*64
                    if mutation=='locks':x['hardwareLocks']=[]
                    plan_path.write_text(json.dumps(x))
                    receipt_path=folder/'adapter_receipt.json';receipt=json.loads(receipt_path.read_text());artifacts=receipt['result']['artifacts']
                    for artifact in artifacts:
                        if artifact['name']=='plan.json':artifact['sha256']=sha(plan_path)
                    if mutation=='unregistered_metadata':receipt['result']['artifacts']=[a for a in artifacts if a['name']!='completion.json']
                    receipt_path.write_text(json.dumps(receipt));dep['operations'][0]['resultContentSha256']=digest(result_content(receipt['result']))
                    dep['operations'][0]['registeredArtifacts']=[{k:a[k] for k in ('id','name','kind','sha256')} for a in receipt['result']['artifacts']]
                if mutation=='none':
                    with patch('adapters.science_flux_adapter.verify_sources',return_value={'fixtureSource':True}):att=authenticate_flux_dependency(root,request,dep,**kwargs)
                    self.assertEqual(att['status'],'valid');self.assertEqual(att['dependencySha256'],digest(dep))
                else:
                    with patch('adapters.science_flux_adapter.verify_sources',return_value={'fixtureSource':True}),self.assertRaises((ValueError,json.JSONDecodeError)):authenticate_flux_dependency(root,request,dep,**kwargs)

if __name__=='__main__':unittest.main()
