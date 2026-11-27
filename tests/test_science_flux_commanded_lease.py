"""Filesystem-only continuity contracts; no device, driver, or worker launch."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests.test_science_flux_commanded import route,Clock,confirmation
from adapters.science_flux.contracts import command_authorization
from adapters.science_flux.command_session import CommandedBiasSession
from adapters.science_runtime.policy import digest,sha

class ContinuationContracts(unittest.TestCase):
    def test_verification_ramps_from_prior_end_without_jump(self):
        r=route();writes=[];clock=Clock();e={'kind':'prior_end_command','operationId':'select',
            **{k:'a'*64 for k in ('completionSha256','commandSha256','receiptSha256','leaseTokenSha256','lockTokenSha256')}}
        auth=command_authorization(r,'verify',stage='verification',entry_evidence=e)
        def setter(value,**kw):
            writes.append(value);return {'nativeFunctions':['boardSelect','channSelect','setOffset'],
                'replies':['fixture module','fixture channel','fixture offset'],'requestedSettingV':value,'unit':'V','measuredOutput':False}
        with tempfile.TemporaryDirectory() as t:
            session=CommandedBiasSession(r,None,setter,Path(t)/'journal.json',identity={'operationId':'verify','resourceId':r['resourceId']},
                authorization=auth,check_cancel=lambda:None,monotonic=clock.monotonic,sleep=clock.sleep)
            session.open_and_read_initial();session.restore_initial('complete')
            self.assertEqual(writes[:2],[0.,.1]);self.assertEqual(writes[-1],-.1)
            self.assertTrue(all(abs(b-a)<=r['maxStepV']+1e-12 for a,b in zip([-.1]+writes,writes)))
            from adapters.science_flux.command_contracts import validate_command_journal
            validate_command_journal(json.loads((Path(t)/'journal.json').read_text()),r,session.identity,auth)

    def test_plan_only_entry_is_never_a_write_authorization(self):
        r=route()
        with tempfile.TemporaryDirectory() as t,self.assertRaises(ValueError):
            CommandedBiasSession(r,None,lambda *a,**k:self.fail('write'),Path(t)/'j',identity={'operationId':'budget','resourceId':r['resourceId']},
                authorization=command_authorization(r,'budget',stage='plan_only'),check_cancel=lambda:None)

    def fixture(self,t):
        from adapters.science_flux.command_lease import bind_locks,retain_locks,operation_folder
        from adapters.science_flux.bias_session import durable_save
        operations=Path(t)/'operations';operations.mkdir();r=route();op='select';folder=operation_folder(operations,op);folder.mkdir()
        locks=[Path(t)/'chassis.lock',Path(t)/'lf.lock'];token='token';lease='lease'
        for p in locks:durable_save(p,{'token':token,'pid':123,'operationId':op,'root':str(folder)})
        request={'operationId':op,'resourceId':r['resourceId'],'runId':'wf','nodeId':'node','attemptId':'attempt','leaseToken':lease}
        from adapters.science_flux.command_lease import lease_context
        plan={**request,'biasRoute':r,'hardwareLocks':[str(p) for p in locks],'instrument':{'host':'TEST_ONLY','port':1},
            'biasCommandAuthorization':command_authorization(r,op,start_confirmation=confirmation(request,r)),'commandLease':lease_context(request,'selection')}
        from adapters.f8 import _digest
        request['biasCommandAuthorization']=plan['biasCommandAuthorization']
        plan.update(requestHash=digest(request),transportRequestHash=_digest(request),profileSnapshot={},profileHash=digest({}),sources={},sourceHash=digest({}))
        durable_save(folder/'plan.json',plan);durable_save(folder/'adapter_intent.json',{'request':request,'requestHash':_digest(request),'profileHash':_digest({})})
        clock=Clock()
        def setter(v,**k):return {'nativeFunctions':['boardSelect','channSelect','setOffset'],'replies':['fixture module','fixture channel','fixture offset'],'requestedSettingV':v,'unit':'V','measuredOutput':False}
        (folder/'results').mkdir();session=CommandedBiasSession(r,None,setter,folder/'results/bias_journal.json',identity={'operationId':op,'resourceId':r['resourceId']},
            authorization=plan['biasCommandAuthorization'],check_cancel=lambda:None,monotonic=clock.monotonic,sleep=clock.sleep)
        initial=session.open_and_read_initial();end=session.restore_initial('acquired')
        bind_locks(plan,folder,locks,token)
        completion={'state':'completed','testOnly':False,'operationId':op,'resourceId':r['resourceId'],'shutdown_ok':True,'initial':initial,'restoration':end,
            'rfShutdown':{'verified':True},'transportClose':{'shutdown_ok':True},'providerClosed':True}
        completion['commandContinuation']=retain_locks(plan,folder,locks,token,end)
        durable_save(folder/'completion.json',completion)
        # Source metadata is registered in the immutable native adapter receipt.
        artifacts=[{'name':p.name,'sha256':sha(p)} for p in (folder/'plan.json',folder/'completion.json',folder/'adapter_intent.json',folder/'results/bias_journal.json')]
        durable_save(folder/'adapter_receipt.json',{'status':'completed','operationId':op,'result':{'artifacts':artifacts}})
        return operations,plan,locks,request,folder

    def test_same_lease_adoption_and_finalization_require_complete_file_set(self):
        from adapters.science_flux.command_lease import verification_authorization,acquire_command_locks,finalize_lease
        with tempfile.TemporaryDirectory() as t:
            operations,plan,locks,request,folder=self.fixture(t)
            verify={**request,'operationId':'verify'}
            auth=verification_authorization(operations,verify,plan['biasRoute'],plan['hardwareLocks'],'select')
            self.assertEqual(auth['entrySettingV'],-.1)
            vplan={**plan,'operationId':'verify','biasCommandAuthorization':auth,'commandLease':{**plan['commandLease'],'stage':'verification'}}
            target=operations/'verify';target.mkdir()
            with patch('adapters.science_flux.command_lease.ensure_no_connection'):
                owned,token=acquire_command_locks(vplan,target,operations)
            self.assertEqual(token,'token');self.assertEqual(set(owned),set(locks))
            self.assertEqual(json.loads(locks[0].read_text())['operationId'],'verify')
            outcome=finalize_lease(operations,plan['hardwareLocks'],'lease')
            self.assertTrue(outcome['needsReconciliation']);self.assertTrue(all(p.exists() for p in locks))

    def test_known_end_release_and_tampered_or_foreign_lease_refusal(self):
        from adapters.science_flux.command_lease import verification_authorization,finalize_lease
        with tempfile.TemporaryDirectory() as t:
            operations,plan,locks,request,folder=self.fixture(t)
            bad={**request,'operationId':'verify','leaseToken':'other'}
            with self.assertRaises(ValueError):verification_authorization(operations,bad,plan['biasRoute'],plan['hardwareLocks'],'select')
            broken=json.loads(locks[0].read_text());broken['token']='different';locks[0].write_text(json.dumps(broken))
            outcome=finalize_lease(operations,plan['hardwareLocks'],'lease');self.assertTrue(outcome['needsReconciliation'])
            self.assertTrue(all(p.exists() for p in locks))
        with tempfile.TemporaryDirectory() as t:
            operations,plan,locks,request,folder=self.fixture(t)
            outcome=finalize_lease(operations,plan['hardwareLocks'],'lease')
            self.assertTrue(outcome['released']);self.assertFalse(outcome['needsReconciliation']);self.assertFalse(any(p.exists() for p in locks))

    def test_all_locks_missing_does_not_erase_active_reservation(self):
        from adapters.science_flux.command_lease import finalize_lease
        with tempfile.TemporaryDirectory() as t:
            operations,plan,locks,request,folder=self.fixture(t)
            for path in locks:path.unlink()
            outcome=finalize_lease(operations,plan['hardwareLocks'],'lease')
            self.assertTrue(outcome['needsReconciliation']);self.assertFalse(outcome['released'])

    def test_source_changes_and_lost_lock_block_verification_before_any_write(self):
        from adapters.science_flux.command_lease import verification_authorization
        for mutation in ('completion','request','profile','journal','lock'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as t:
                operations,plan,locks,request,folder=self.fixture(t)
                if mutation=='lock':locks[-1].unlink()
                else:
                    path=folder/({'completion':'completion.json','request':'adapter_intent.json','profile':'plan.json','journal':'results/bias_journal.json'}[mutation])
                    value=json.loads(path.read_text())
                    if mutation=='completion':value['restoration']['commandEndVerified']=False
                    elif mutation=='request':value['request']['leaseToken']='another'
                    elif mutation=='profile':value['profileSnapshot']['changed']=True
                    else:value['events'][0]['outcome']='unknown'
                    path.write_text(json.dumps(value))
                with self.assertRaises((ValueError,OSError)):
                    verification_authorization(operations,{**request,'operationId':'verify'},plan['biasRoute'],plan['hardwareLocks'],'select')

if __name__=='__main__':unittest.main()
