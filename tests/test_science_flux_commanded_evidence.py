"""Synthetic on-disk effect/native identity contract; never hardware evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests import test_science_flux_evidence as measured_fixture
from tests.test_science_flux_commanded import route,confirmation
from tests.test_science_flux import Clock
from adapters.science_runtime.policy import digest,sha

def write(path,value):path.write_text(json.dumps(value),encoding='utf8')
def read(path):return json.loads(path.read_text(encoding='utf8'))

def refresh(folder,operation):
    from backend.science_dependencies import result_content
    receipt=read(folder/'adapter_receipt.json');artifacts=receipt['result']['artifacts']
    for artifact in artifacts:artifact['sha256']=sha(Path(artifact['localPath']))
    write(folder/'adapter_receipt.json',receipt)
    operation['registeredArtifacts']=[{k:a[k] for k in ('id','name','kind','sha256')} for a in artifacts]
    operation['resultContentSha256']=digest(result_content(receipt['result']))

def fixture(root):
    from adapters.science_flux.command_session import CommandedBiasSession
    from adapters.science_flux.contracts import command_authorization,COMMAND_POLICY
    from adapters.science_flux_adapter import shared_locks
    from adapters.f8 import _digest
    from adapters.science_flux.command_lease import lease_context,bind_locks,retain_locks,verification_authorization
    request,dep,kwargs=measured_fixture.NativeFilesContracts().fixture(root);r=route();kwargs['bias_route']=r
    for op in dep['operations']:
        folder=root/'data/runs/arclight_operations'/hashlib.sha256(op['operationId'].encode()).hexdigest()
        old=folder/'results/bias_journal.json';old.unlink();clock=Clock()
        plan=read(folder/'plan.json');intent=read(folder/'adapter_intent.json');actual=intent['request']
        actual.update(nodeId='TEST_ONLY-node',leaseToken='TEST_ONLY-lease')
        profile=plan['profileSnapshot'];profile['biasRoutes']={'Qfixture':r};profile['hardwareLocks']=shared_locks(root,profile)
        identity={'operationId':op['operationId'],'resourceId':'Qfixture'}
        auth=(command_authorization(r,op['operationId'],start_confirmation=confirmation(actual,r)) if op['stage']=='selection' else
            verification_authorization(folder.parent,actual,r,profile['hardwareLocks'],'selection'))
        def setter(v,**kw):return {'nativeFunctions':['boardSelect','channSelect','setOffset'],'replies':['fixture module','fixture channel','fixture offset'],
                                  'requestedSettingV':v,'unit':'V','measuredOutput':False}
        session=CommandedBiasSession(r,None,setter,old,identity=identity,authorization=auth,check_cancel=lambda:None,
                                    monotonic=clock.monotonic,sleep=clock.sleep)
        initial=session.open_and_read_initial();held=session.ramp_to(.2,'bias-0');end=session.restore_initial('fixture')
        actual['biasCommandAuthorization']=auth
        plan.update(policy=COMMAND_POLICY,biasRoute=r,profileHash=digest(profile),hardwareLocks=profile['hardwareLocks'],
                    requestHash=digest(actual),transportRequestHash=_digest(actual),biasCommandAuthorization=auth,commandLease=lease_context(actual,op['stage']))
        intent.update(profileHash=_digest(profile),requestHash=_digest(actual));op['adapterRequestSha256']=digest(actual)
        write(folder/'plan.json',plan);write(folder/'adapter_intent.json',intent)
        completion=read(folder/'completion.json');completion.update(initial=initial,restoration=end,policy=COMMAND_POLICY)
        if op['stage']=='selection':
            for path in profile['hardwareLocks']:
                lock=Path(path);lock.parent.mkdir(parents=True,exist_ok=True);write(lock,{'token':'TEST_ONLY-lock','operationId':op['operationId']})
            bind_locks(plan,folder,profile['hardwareLocks'],'TEST_ONLY-lock')
            completion['commandContinuation']=retain_locks(plan,folder,profile['hardwareLocks'],'TEST_ONLY-lock',end)
        point=completion['points'][0];point['biasReceipt']=held;point['cutSha256']=digest({k:v for k,v in point.items() if k!='cutSha256'})
        write(folder/'completion.json',completion)
        write(folder/'results/bias_row_0000.json',{'pointId':'bias-0','rowId':0,'before':held,'after':held})
        receipt=read(folder/'adapter_receipt.json');science=receipt['result']['provenance']['scienceFlux']
        science.update(points=completion['points'],initial=initial,restoration=end,policy=COMMAND_POLICY,commandAuthorization=auth,
            commandLease=plan['commandLease'],commandContinuation=completion.get('commandContinuation'),completionSha256=sha(folder/'completion.json'))
        receipt['operationId']=op['operationId']
        write(folder/'adapter_receipt.json',receipt);refresh(folder,op)
        kwargs['expected_locks']=profile['hardwareLocks']
    return request,dep,kwargs

class NativeCommandFiles(unittest.TestCase):
    def test_verification_hook_requires_actual_completed_database_effect(self):
        from adapters.science_flux_adapter import ScienceFluxAdapter
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);_,dep,_=fixture(root);operations=root/'data/runs/arclight_operations'
            folder=operations/hashlib.sha256(b'selection').hexdigest();intent=read(folder/'adapter_intent.json');receipt=read(folder/'adapter_receipt.json')
            profile=read(folder/'plan.json')['profileSnapshot'];profile.update(id='TEST_ONLY',resources={'Qfixture':{}})
            adapter=ScienceFluxAdapter(root,profile);adapter.operations=operations
            request={**intent['request'],'operationId':'verification'}
            source={'request':intent['request'],'result':receipt['result']}
            auth=adapter.command_verification_authorization(request,'selection',source_effect=source)
            self.assertEqual(auth['stage'],'verification');self.assertEqual(auth['entrySettingV'],-.1)
            for variant in ('missing','result','request'):
                altered=deepcopy(source)
                if variant=='result':altered['result']['metrics']={'fabricated':1}
                elif variant=='request':altered['request']['leaseToken']='another'
                else:altered=None
                with self.subTest(variant=variant),self.assertRaises(ValueError):
                    adapter.command_verification_authorization(request,'selection',source_effect=altered)

    def test_commanded_source_real_receipt_mapping_and_no_fake_measurement(self):
        from adapters.science_flux.evidence import authenticate_flux_dependency
        for mutation in ('none','end_flag','unknown_journal','auth','measured_claim','missing_metadata','reply_error'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);request,dep,kwargs=fixture(root);op=dep['operations'][0]
                folder=root/'data/runs/arclight_operations'/hashlib.sha256(op['operationId'].encode()).hexdigest()
                if mutation=='end_flag':
                    x=read(folder/'completion.json');x['restoration']['commandEndVerified']=False;write(folder/'completion.json',x)
                elif mutation=='unknown_journal':
                    x=read(folder/'results/bias_journal.json');x['events'][1]['outcome']='unknown';write(folder/'results/bias_journal.json',x)
                elif mutation=='auth':
                    x=read(folder/'plan.json');x['biasCommandAuthorization']['endSettingV']=0;write(folder/'plan.json',x)
                elif mutation in ('measured_claim','reply_error'):
                    x=read(folder/'results/bias_row_0000.json')
                    if mutation=='measured_claim':x['before']['measurement']={'kind':'measured_output','valueV':.2}
                    else:
                        c=x['before']['command'];c['reply']['replies'][-1]='ERROR';c['sha256']=digest({k:v for k,v in c.items() if k!='sha256'})
                    write(folder/'results/bias_row_0000.json',x)
                elif mutation=='missing_metadata':
                    x=read(folder/'adapter_receipt.json');x['result']['artifacts']=[a for a in x['result']['artifacts'] if a['name']!='bias_journal.json'];write(folder/'adapter_receipt.json',x)
                if mutation!='none':refresh(folder,op)
                with patch('adapters.science_flux_adapter.verify_sources',return_value={'fixtureSource':True}):
                    if mutation=='none':self.assertEqual(authenticate_flux_dependency(root,request,dep,**kwargs)['status'],'valid')
                    else:
                        with self.assertRaises(ValueError):authenticate_flux_dependency(root,request,dep,**kwargs)

if __name__=='__main__':unittest.main()
