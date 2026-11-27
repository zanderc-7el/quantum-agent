"""Actual planner/numeric/command-session with injected transport and lock attestations.
No hardware, OS reservation or physical source is authorized by these fixtures.
"""
from copy import deepcopy
from pathlib import Path
import tempfile,unittest,json,hashlib
from unittest.mock import patch
from contextlib import ExitStack
from tests.test_controller import make_fixture
class CommandedLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.c,self.fake=make_fixture(Path(self.tmp.name));self.addCleanup(self.c.close)
    def test_two_real_plans_command_receipts_one_lease_and_no_global_commit(self):self.lifecycle()
    def test_unknown_final_lock_release_retains_controller_quarantine(self):self.lifecycle(True)
    def test_failed_selection_closes_known_command_lease_without_starting_verification(self):self.lifecycle(selection_fails=True)
    def test_foreign_tcp_owner_blocks_verification_despite_held_own_locks(self):self.lifecycle(ownership_case='tcp')
    def test_wrong_operation_lock_blocks_verification(self):self.lifecycle(ownership_case='wrong-operation')
    def test_partial_lock_set_blocks_verification(self):self.lifecycle(ownership_case='missing-lock')
    def test_fresh_native_authentication_failure_blocks_owner_exception(self):self.lifecycle(ownership_case='fresh-auth-reject')
    def lifecycle(self,fail_finalization=False,selection_fails=False,ownership_case=None):
        """Actual planner + injected transports; synthetic ADC, no source eligibility."""
        from backend.common import ROOT,digest,now,dumps
        from backend.workspace import device_key
        from adapters.science_flux_adapter import ScienceFluxAdapter,build_flux_profile
        root=ROOT
        fixture=json.loads((root/'tests/fixtures/science_flux_commanded_numeric_result.json').read_text(encoding='utf8'))
        self.assertTrue(fixture['neverAcquiredFromHardware'])
        base=json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf8'))
        # Real inherited ArcLight ownership reads these temporary lock files;
        # never create locks at deployment paths from the source profile.
        base['hardwareLocks']=[str(self.c.root/'fixture-locks'/name) for name in ('chassis.lock','rf.lock')]
        route=deepcopy(fixture['selectionResult']['provenance']['scienceFlux']['route'])
        route.update(resourceId='Q11',deviceKey=device_key(base))
        base['biasRoutes']={'Q11':route};base['resources']['Q11'].update(fixture['plan']['parameters'])
        profile=build_flux_profile(root,base);profile['connectionConfirmedAt']=now()
        from adapters.science_flux_adapter import shared_locks
        profile['hardwareLocks']=shared_locks(self.c.root,profile)
        manifest=self.c.root/profile['sourceManifest'];manifest.parent.mkdir(parents=True);manifest.write_text('[]')
        native=ScienceFluxAdapter(self.c.root,profile)
        self.c.adapter=native;self.c.profile=native.profile;self.c._physical_adapter=True
        self.c.adapter_id=profile['id'];self.c.adapter_profile_hash=self.c._adapter_fingerprint();observed=[];results_by_operation={};finalized=[];auth_calls=[]
        def resolve(rt,**kwargs):
            request=kwargs['consumer'];proof={'schema':'qcal-science-dependencies/1','policyVersion':'science-dependency-policy/1',
                'kind':'measurement','scope':kwargs['scope'],'inputs':{},'rootReferences':[{'role':'cavity_seed','recordId':'fixture-source'}],
                'nodes':[{'recordId':'fixture-source','procedureId':'ro_resonator_survey'}],
                'edges':[{'role':'cavity_seed','toRecordId':'fixture-source','fromRecordId':None}]}
            proof['closureSha256']=digest(proof)
            proof['assessment']={'executionEligible':True,'consumerSha256':digest({k:v for k,v in request.items() if k!='scienceDependencies'})}
            return proof
        def execute(adapter,request,emit,cancel):
            plan=adapter.build_plan(request)
            with self.c.store.connect() as rt:observed.append((deepcopy(request),plan,rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0]))
            result=deepcopy(fixture['selectionResult' if len(observed)==1 else 'verificationResult'])
            result.update(sourceRunId=request['operationId'],acquiredAt=now(),parameters=plan['parameters'])
            flux=result['provenance']['scienceFlux'];flux.update(route=route,routeSha256=digest(route),sourceClosure=plan['validationEvidence']['closureSha256'])
            context=flux['biasContext'];context.update(resourceId='Q11',routeSha256=digest(route),operationId=request['operationId'])
            context['sha256']=digest({k:v for k,v in context.items() if k!='sha256'})
            result['provenance'].update(scienceDependencies=plan['validationEvidence'],operationId=request['operationId'])
            from tests.test_science_flux import Clock
            from adapters.science_flux.command_session import CommandedBiasSession
            from adapters.science_flux.command_lease import lease_context
            clock=Clock();auth=request['biasCommandAuthorization']
            def setter(value,*,timeout_seconds):return {'nativeFunctions':['boardSelect','channSelect','setOffset'],'replies':['fixture module','fixture channel','fixture offset'],'requestedSettingV':value,'unit':'V','measuredOutput':False}
            folder=self.c.root/'fixture-command-session'/str(len(observed));folder.mkdir(parents=True)
            session=CommandedBiasSession(route,None,setter,folder/'journal.json',identity={'operationId':request['operationId'],'resourceId':'Q11'},authorization=auth,check_cancel=lambda:None,monotonic=clock.monotonic,sleep=clock.sleep)
            flux['initial']=session.open_and_read_initial()
            for point in flux['points']:
                session.ramp_to(point['requestedSettingV'],point['pointId']);point['biasReceipt']=session.verify_held(point['pointId'])
            flux['restoration']=session.restore_initial('fixture_completed');flux['commandAuthorization']=auth
            flux['commandLease']=lease_context(request,auth['stage']);flux['completionSha256']=digest({'fixtureOnly':request['operationId']})
            flux['commandContinuation']=None
            if auth['stage']=='selection':
                continuation={'schema':'qcal-command-continuation/1','state':'held_for_verification','operationId':request['operationId'],'lease':flux['commandLease'],'routeSha256':digest(route),'lockTokenSha256':digest('fixture-no-os-lock'),'hardwareLocks':plan['hardwareLocks'],'endCommandSha256':flux['restoration']['command']['sha256'],'endReceiptSha256':digest(flux['restoration']),'endSettingV':route['endSettingV']}
                continuation['sha256']=digest(continuation);flux['commandContinuation']=continuation
                for name in plan['hardwareLocks']:
                    lock=Path(name);lock.parent.mkdir(parents=True,exist_ok=True)
                    lock.write_text(dumps({'pid':123,'operationId':request['operationId'],'commandLease':flux['commandLease'],
                        'commandLeaseState':'awaiting_verification','token':'fixture-no-os-lock','biasRouteSha256':digest(route),
                        'commandContinuationSha256':continuation['sha256']}),encoding='utf8')
                if ownership_case=='wrong-operation':
                    lock=Path(plan['hardwareLocks'][0]);value=json.loads(lock.read_text(encoding='utf8'));value['operationId']='another-operation';lock.write_text(dumps(value),encoding='utf8')
                if ownership_case=='missing-lock':Path(plan['hardwareLocks'][0]).unlink()
            artifacts=[]
            for index,point in enumerate(flux['points']):
                path=self.c.root/'data/fixture-flux'/str(len(observed))/f'cut-{index}.npz';path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(('explicit synthetic ADC '+request['operationId']+str(index)).encode())
                sha=hashlib.sha256(path.read_bytes()).hexdigest();point.update(rawSha256=sha,rawPath=str(path))
                point['cutSha256']=digest({k:v for k,v in point.items() if k!='cutSha256'})
                artifacts.append({'id':digest(str(path)),'name':path.name,'kind':'raw','localPath':str(path),'sha256':sha})
            if selection_fails:result['analysisQuality']={'accepted':False,'reasons':['fixture failed selection']}
            result['artifacts']=artifacts;results_by_operation[request['operationId']]=deepcopy(result);return result
        def verification_authorization(adapter,request,source_operation,*,source_effect):
            auth_calls.append(request['operationId'])
            if ownership_case=='fresh-auth-reject' and len(auth_calls)>=2:raise ValueError('fixture native lock authentication failed')
            from adapters.science_flux.contracts import command_authorization
            first=results_by_operation[source_operation]['provenance']['scienceFlux'];end=first['restoration']
            self.assertEqual(source_effect['request']['operationId'],source_operation)
            self.assertEqual(source_effect['result']['provenance']['scienceFlux']['completionSha256'],first['completionSha256'])
            return command_authorization(route,request['operationId'],stage='verification',entry_evidence={'kind':'prior_end_command','operationId':source_operation,'completionSha256':first['completionSha256'],'commandSha256':end['command']['sha256'],'receiptSha256':digest(end),'leaseTokenSha256':digest(request['leaseToken']),'lockTokenSha256':first['commandContinuation']['lockTokenSha256']})
        def finalize(adapter,token):
            finalized.append(token);return {'released':not fail_finalization,'needsReconciliation':fail_finalization,'reason':'fixture-only unknown reservation' if fail_finalization else 'fixture-only released'}
        with ExitStack() as stack:
            stack.enter_context(patch.object(ScienceFluxAdapter,'command_verification_authorization',verification_authorization))
            stack.enter_context(patch.object(ScienceFluxAdapter,'finalize_command_lease',finalize))
            stack.enter_context(patch('adapters.science_flux.command_lease.validate_prior_entry',return_value=None))
            stack.enter_context(patch('backend.science_dependencies.ScienceDependencyResolver.resolve',side_effect=resolve))
            stack.enter_context(patch('adapters.science_flux_adapter.verify_sources',return_value={}))
            stack.enter_context(patch.object(ScienceFluxAdapter,'authenticate_dependency',return_value={'fixtureOnly':True}))
            stack.enter_context(patch.object(ScienceFluxAdapter,'preflight',return_value={'ready':True,'staticReady':True,'hardwareConnected':False}))
            stack.enter_context(patch.object(ScienceFluxAdapter,'execute',execute))
            # Only the host socket listing is stubbed. The inherited adapter
            # reports actual retained lock files during verification admission.
            stack.enter_context(patch('adapters.f8.F8Adapter.hardware_ownership',side_effect=lambda *args,**kwargs:{'verified':True,'owners':[{'pid':999,'localPort':1234}] if ownership_case=='tcp' and observed else []}))
            wf=self.c.create({'name':'synthetic flux lifecycle','mode':'real','policy':'strict','resourceIds':['Q11'],
                'executionProfile':'science-flux','manualProcedureId':'ro_flux_scan','manualParameters':{
                    key:fixture['plan']['parameters'][key] for key in ('bias_values_v','readout_frequencies_hz')},
                'manualScienceReferences':[{'role':'cavity_seed','recordId':'fixture-source'}]})
            node=wf['nodes'][0]['id']
            confirmation={'confirmed':True,'routeSha256':digest(route),'startSettingV':route['startSettingV'],'endSettingV':route['endSettingV']}
            wf=self.c.action(wf['id'],{'action':'run_step','nodeId':node,'operationId':'first','expectedRevision':wf['revision'],'biasStartConfirmation':confirmation})
            self.c.run_workflow(wf['id']);wf=self.c.get(wf['id']);self.assertEqual(wf['engineState'],'waiting_hardware')
            wf=self.c.action(wf['id'],{'action':'confirm_hardware','operationId':'fixture-confirm','expectedRevision':wf['revision']})
            wf=self.c.action(wf['id'],{'action':'run_step','nodeId':node,'operationId':'fixture-permit','expectedRevision':wf['revision'],'biasStartConfirmation':confirmation})
            self.c.run_workflow(wf['id']);wf=self.c.get(wf['id'])
            if ownership_case:
                self.assertEqual(wf['engineState'],'execution_failed',wf.get('events'));self.assertEqual(len(observed),1)
                self.assertEqual(len(finalized),1)
                if ownership_case=='fresh-auth-reject':self.assertEqual(len(auth_calls),2)
                else:
                    check=next(n for n in wf['nodes'] if n['id']==node)['ownershipCheck']
                    self.assertEqual(check['stage'],'dispatch')
                    self.assertEqual(check['socketCount'],1 if ownership_case=='tcp' else 0)
                    self.assertGreater(check['lockCount'],0)
                    self.assertTrue(any('未下发测量' in e['message'] for e in wf['events']))
                return
            if fail_finalization:
                self.assertEqual(wf['engineState'],'needs_reconciliation',wf.get('events'))
                with self.c.store.connect() as rt:self.assertEqual(rt.db.execute("SELECT count(*) FROM platform_leases WHERE state='quarantined'").fetchone()[0],1)
                self.assertEqual(len(finalized),1);return
            if selection_fails:
                self.assertEqual(wf['engineState'],'quality_attention',wf.get('events'))
                self.assertEqual(len(observed),1);self.assertEqual(len(finalized),1)
                with self.c.store.connect() as rt:
                    self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],0)
                    self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],1)
                from backend.common import APIError
                with self.assertRaises(APIError) as error:self.c.action(wf['id'],{'action':'run_step','nodeId':node,'operationId':'new-permit-without-confirmation','expectedRevision':wf['revision']})
                self.assertEqual(error.exception.code,'BIAS_START_CONFIRMATION_REQUIRED')
                return
            self.assertEqual(wf['engineState'],'awaiting_acceptance',wf.get('events'))
            wf=self.c.action(wf['id'],{'action':'approve','nodeId':node,'operationId':'accept','expectedRevision':wf['revision']})
        self.assertEqual(len(finalized),1);self.assertEqual(observed[0][0]['biasCommandAuthorization']['stage'],'selection');self.assertEqual(observed[1][0]['biasCommandAuthorization']['stage'],'verification');self.assertNotEqual(route['startSettingV'],route['endSettingV']);
        self.assertEqual(wf['status'],'completed');self.assertEqual(len(observed),2)
        self.assertTrue(all(lease==1 for _,_,lease in observed));self.assertNotEqual(observed[0][0]['operationId'],observed[1][0]['operationId'])
        self.assertEqual(observed[0][1]['parameters'],observed[1][1]['parameters'])
        self.assertEqual(wf['reservedShots'],sum(p['estimated_total_shots'] for _,p,_ in observed))
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],2)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],0)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM parameters').fetchone()[0],0)
