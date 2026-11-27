"""TEST_ONLY normal smart controller + native receipt contract; no device I/O."""
from copy import deepcopy
import json
from unittest.mock import Mock, patch
from backend.common import APIError, now
from adapters.f8 import _digest
from tests.test_smart_sampling import SmartSamplingBackend


class SmartRabiWindow(SmartSamplingBackend):
    # Reuse setup/physical-plan fixture, without inheriting its unrelated tests.
    def transport(self,request,*unused):
        self.calls.append(deepcopy(request));plan=self.adapter.build_plan(request)
        folder=self.adapter._operation_dir(request['operationId']);folder.mkdir(parents=True)
        raw=folder/'raw_scan.npz';raw.write_bytes(('TEST_ONLY no ADC '+request['operationId']).encode())
        fail=request['procedureId']=='sq_rabi_amplitude' and not getattr(self,'rabi_pass',False)
        candidates={}
        if request['procedureId']=='sq_spectroscopy':candidates={'drive_hz':request['parameters']['drive_hz']}
        elif request['procedureId']=='sq_rabi_amplitude' and not fail:candidates={'drive_amp':.8}
        reason='Fitted oscillation contains fewer than 1.5 cycles.'
        quality={'accepted':not fail,'fixture':'TEST_ONLY'}
        if fail:quality.update(kind='rabi_amplitude',reasons=[reason,'At least two separately acquired, valid repeat fits are required.'],
            groups=[{'fit':None,'repeat_validated':False,'repeats':[
                {'repeat':i,'fit':None,'diagnostics':{'rejection_reasons':[reason]}} for i in (1,2)]}])
        analysis={'metrics':{'contrast_snr':8,'peak_snr':8,'r_squared':.95,'cycles':2,'relative_stderr':.1,'scan_coverage':1},
            'candidateParameters':candidates,'series':[],'analysis':quality}
        completion={'state':'completed','shutdown_ok':True,'acquiredAt':now(),'analysis':analysis,
            'files':[str(raw),str(folder/'plan.json')]}
        canonical={k:v for k,v in request.items() if k!='outputDir'}
        def put(name,data):(folder/name).write_text(json.dumps(data),encoding='utf8')
        put('adapter_intent.json',{'operationId':request['operationId'],'requestHash':_digest(canonical),
            'request':canonical,'profileHash':_digest(self.adapter.profile)})
        put('plan.json',plan);put('completion.json',completion)
        # Exercise the unchanged frozen worker's real admission function, not a
        # local reconstruction. It performs no native/device import or I/O.
        from adapters.arclight_analysis_v3.execute_worker import validate_admission
        self.assertEqual(validate_admission(self.adapter.root,folder,plan),canonical)
        result=self.adapter._result(folder,plan,request,completion)
        put('adapter_receipt.json',{'status':'completed','operationId':request['operationId'],'requestHash':_digest(canonical),'result':result})
        return result

    def decide(self,changes):
        self.c._brain=Mock(return_value={'status':'ok','decision':{'action':'retry',
            'reason':'TEST_ONLY retained no-fit diagnostics; explore a longer fixed DC window without asserting a measured period',
            'evidenceIds':[],'parameterChanges':changes}})
        return self.c._decide(self.wid,self.nid)

    def test_normal_model_tool_exposes_only_authenticated_window_and_runs_independent_pair(self):
        before=len(self.calls)
        self.assertTrue(self.decide({'drive_length_s':750e-9}),self.c.get(self.wid).get('modelRecovery'))
        self.assertEqual(len(self.calls),before)
        payload=self.c._brain.call_args.args[1]
        self.assertEqual(payload['parameterBounds']['drive_length_s']['max'],2e-6)
        self.assertFalse(payload['smartRabiWindowScope']['isCalibration'])
        self.assertNotIn('drive_length_s',payload['modelParameterSemantics']['excludedParameters'])
        wf=self.c.get(self.wid);node=self.c._node(wf,self.nid)
        record=node['smartRabiWindow'];self.assertEqual(record['state'],'selected')
        self.assertEqual(record['authorization']['sourceAttemptId'],self.n['latestAttemptId'])
        self.assertEqual(record['authorization']['attemptNumber'],2)
        self.rabi_pass=True
        self.c._execute_node(self.wid,self.nid)
        wf=self.c.get(self.wid);node=self.c._node(wf,self.nid)
        self.assertEqual(node['engineState'],'verified')
        pair=self.calls[before:];self.assertEqual(len(pair),2)
        self.assertEqual([r['operationId'].rsplit('/',1)[1] for r in pair],['measure','verify'])
        self.assertTrue(all(r['parameters']['drive_length_s']==750e-9 and r['parameters']['acquisitions_per_point']==8 for r in pair))
        self.assertEqual(pair[0]['rabiWindowAuthorization'],pair[1]['rabiWindowAuthorization'])
        self.assertEqual(node['result']['candidateParameters'],{'drive_amp':.8,'half_pi_amp':.8})
        self.assertEqual(node['result']['selectionResult']['parameters']['drive_length_s'],750e-9)
        with self.c.store.connect() as rt:
            self.assertIsNone(rt.db.execute('SELECT value FROM parameters WHERE name=?',(self.wid+':Q11:drive_length_s',)).fetchone())
        plan=self.adapter.build_plan(pair[0]);self.assertEqual(plan['jobs'][0]['gate_repeats'],5)
        self.assertEqual(plan['jobs'][0]['config']['Q1']['gate']['U_gates']['pi_len'],750e-9)

    def test_ordinary_strict_and_other_failure_do_not_gain_width_tool(self):
        from backend.smart_rabi_window import model_scope
        p=self.c._parameter_schema('sq_rabi_amplitude');p.pop('drive_length_s')
        for foreign in ({**self.w,'policy':'strict'},{**self.w,'smartRun':{}},{**self.w,'resourceIds':['Q18']}):
            bounds,scope=model_scope(self.c,foreign,self.n,p)
            self.assertNotIn('drive_length_s',bounds);self.assertIsNone(scope)
        # A self-reported diagnostic mutation cannot supersede native files.
        with self.c.store.transaction() as rt:
            wf=self.c.get(self.wid,rt);node=self.c._node(wf,self.nid)
            node['result']['analysisQuality']['groups'][0]['repeats'][0]['fit']={'value':1}
            self.c.store.save(rt,wf,'TEST_ONLY mismatch')
        self.assertFalse(self.decide({'drive_length_s':750e-9}))

    def test_only_duration_and_reset_to_eight_are_allowed_no_power_or_axis_change(self):
        from backend.smart_rabi_window import prepare_retry
        with self.c.store.connect() as rt:
            wf=self.c.get(self.wid,rt);node=self.c._node(wf,self.nid);p,v=self.c._parameters(rt,wf,node)
            for changes in ({'drive_length_s':750e-9,'amp_stop':.99},
                            {'drive_length_s':750e-9,'readout_amp':.4},
                            {'drive_length_s':750e-9,'drive_hz':p['drive_hz']+1e6},
                            {'drive_length_s':750e-9,'acquisitions_per_point':16},
                            {'drive_length_s':3e-6},{'drive_length_s':200e-9},
                            {'drive_length_s':750.25e-9}):
                with self.subTest(changes=changes),self.assertRaises(APIError):
                    prepare_retry(self.c,rt,wf,node,{**p,**changes},v,{'parameterChanges':changes})
            self.assertNotIn('smartRabiWindow',node)

    def test_pending_and_tampered_f01_do_not_offer_or_dispatch(self):
        from backend.smart_rabi_window import model_scope
        p={'points':{'min':9,'max':161}}
        f01=next(n for n in self.w['nodes'] if n['procedureId']=='sq_spectroscopy')
        path=self.adapter._operation_dir(f01['latestAttemptId']+'/verify')/'raw_scan.npz'
        original=path.read_bytes();path.write_bytes(b'TEST_ONLY tampered')
        self.assertNotIn('drive_length_s',model_scope(self.c,self.w,self.n,p)[0]);path.write_bytes(original)
        self.assertTrue(self.decide({'drive_length_s':750e-9}))
        path.write_bytes(b'TEST_ONLY changed after proposal')
        before=len(self.calls);self.assertFalse(self.c._execute_node(self.wid,self.nid))
        current=self.c.get(self.wid);node=self.c._node(current,self.nid)
        self.assertEqual(len(self.calls),before);self.assertEqual(node['attempt'],1)
        self.assertEqual(node['engineState'],'plan_rejected')
        with self.c.store.connect() as rt:self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],0)

    def test_plan_rejection_does_not_leave_authorization(self):
        original=self.c._validate_plan
        def fail(*args,**kwargs):raise APIError('SEQUENCE_WINDOW','TEST_ONLY plan refusal')
        with patch.object(self.c,'_validate_plan',side_effect=fail):
            self.assertFalse(self.decide({'drive_length_s':750e-9}))
        node=self.c._node(self.c.get(self.wid),self.nid)
        self.assertNotIn('smartRabiWindow',node)
        self.assertEqual(node['parameterOverrides']['drive_length_s'],375e-9)

    def test_real_registered_sixteen_block_failure_returns_to_fresh_eight_block_window(self):
        from adapters.arclight import ArcLightAdapter
        from adapters.arclight_smart_rabi16_adapter import ArcLightSmartRabi16Adapter
        from adapters.arclight_rabi16_runtime import execute_worker
        from tests.rabi16_archive_fixture import archive_result
        from backend import attempt_execution,smart_sampling
        self.assertTrue(self.decide({'acquisitions_per_point':16}))
        original_init=ArcLightSmartRabi16Adapter.__init__
        original_sampling=execute_worker.sampling_receipt
        requests=[]
        def init(adapter,*args,**kwargs):
            original_init(adapter,*args,**kwargs);adapter.operations=self.adapter.operations
        def sampling(folder,plan,completion):
            # The fixture is synthetic, but the complete per-row 16-block
            # archive, hashes, registration and receipt authentication are real.
            reason='Fitted oscillation contains fewer than 1.5 cycles.'
            completion['analysis']['candidateParameters']={}
            completion['analysis']['analysis']={'accepted':False,'fixtureOnly':True,
                'kind':'rabi_amplitude','reasons':['At least two separately acquired, valid repeat fits are required.'],
                'groups':[{'fit':None,'repeat_validated':False,'repeats':[
                    {'repeat':i,'fit':None,'diagnostics':{'rejection_reasons':[reason]}} for i in (1,2)]}]}
            return original_sampling(folder,plan,completion)
        def execute(adapter,request,*unused):
            requests.append(deepcopy(request));return archive_result(adapter,request)
        with patch.object(ArcLightSmartRabi16Adapter,'__init__',init),\
             patch.object(ArcLightSmartRabi16Adapter,'execute',execute),\
             patch.object(execute_worker,'sampling_receipt',sampling),\
             patch.object(ArcLightAdapter,'hardware_ownership',return_value={'verified':True,'owners':[]}),\
             patch.object(ArcLightAdapter,'preflight',return_value={'ready':True,'enabled':False}):
            self.c._execute_node(self.wid,self.nid)
            w=self.c.get(self.wid);n=self.c._node(w,self.nid)
            self.assertEqual(n['attempt'],2);self.assertEqual(n['result']['quality']['status'],'failed')
            self.assertEqual(len(requests),1,'A failed selection must not fabricate a verification')
            with self.c.store.connect() as rt:
                effect=rt.db.execute('SELECT result FROM platform_effects WHERE operation_id=?',(requests[0]['operationId'],)).fetchone()
                evidence=attempt_execution.sampling_evidence(self.c,rt,requests[0],json.loads(effect[0]))
                self.assertEqual(evidence['totalShots'],2624000)
                self.assertEqual(len(evidence['checkedFiles']),1401)
            failure_id=n['latestAttemptId'];before=len(self.calls);shots_before=w['reservedShots']
            self.assertTrue(self.decide({'drive_length_s':750e-9,'acquisitions_per_point':8}),
                self.c.get(self.wid).get('modelRecovery'))
            n=self.c._node(self.c.get(self.wid),self.nid)
            self.assertEqual(n['smartRabiWindow']['authorization']['sourceAttemptId'],failure_id)
            self.assertEqual(n['smartRabiWindow']['authorization']['sourceProof']['failure']['sourceBlocks'],16)
            # An old 375 ns source cannot authorize sixteen blocks at 750 ns.
            with self.c.store.connect() as rt:
                w=self.c.get(self.wid,rt);n=self.c._node(w,self.nid);p,v=self.c._parameters(rt,w,n)
                with self.assertRaises(APIError):smart_sampling.authorize_retry(self.c,rt,w,n,{**p,'acquisitions_per_point':16},v,{'action':'retry'})
            self.rabi_pass=True;self.c._execute_node(self.wid,self.nid)
            w=self.c.get(self.wid);n=self.c._node(w,self.nid);pair=self.calls[before:]
            self.assertEqual(n['engineState'],'verified',n.get('detail'));self.assertEqual(n['attempt'],3)
            self.assertEqual(len(pair),2);self.assertEqual(w['reservedShots']-shots_before,2624000)
            self.assertTrue(all(r['adapterId']==w['adapterId'] and 'executionBinding' not in r
                and r['parameters']['drive_length_s']==750e-9 and r['parameters']['acquisitions_per_point']==8 for r in pair))
            self.assertEqual(pair[0]['leaseToken'],pair[1]['leaseToken'])
            self.assertEqual(pair[0]['rabiWindowAuthorization'],pair[1]['rabiWindowAuthorization'])
            with self.c.store.connect() as rt:
                self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],0)
                old=json.loads(rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?',(failure_id,)).fetchone()[0])
                self.assertEqual(old['executionBinding']['adapterId'],'arclight-private-f8-q11-smart-rabi16-v1')

    def test_pending_effect_unknown_or_lease_and_wrong_version_disable_offer(self):
        from backend.smart_rabi_window import model_scope
        p={'points':{'min':9,'max':161}}
        for state in ('pending','unknown'):
            with self.c.store.transaction() as rt:
                original=rt.db.execute('SELECT operation_id,state FROM platform_effects WHERE attempt_id=?',(self.n['latestAttemptId'],)).fetchone()
                rt.db.execute('UPDATE platform_effects SET state=? WHERE operation_id=?',(state,original['operation_id']))
            self.assertNotIn('drive_length_s',model_scope(self.c,self.w,self.n,p)[0])
            with self.c.store.transaction() as rt:rt.db.execute('UPDATE platform_effects SET state=? WHERE operation_id=?',(original['state'],original['operation_id']))
        with patch.object(self.c,'_has_lease',return_value=True):
            self.assertNotIn('drive_length_s',model_scope(self.c,self.w,self.n,p)[0])
        foreign={**self.w,'adapterProfileHash':'0'*64}
        self.assertNotIn('drive_length_s',model_scope(self.c,foreign,self.n,p)[0])

    def test_cancel_after_plan_refuses_before_attempt(self):
        self.assertTrue(self.decide({'drive_length_s':750e-9}))
        with self.c.store.transaction() as rt:
            w=self.c.get(self.wid,rt);w['cancelRequested']=True
            self.c.store.save(rt,w,'TEST_ONLY cancel before attempt')
        before=len(self.calls);self.assertFalse(self.c._execute_node(self.wid,self.nid))
        self.assertEqual(len(self.calls),before)
        self.assertEqual(self.c._node(self.c.get(self.wid),self.nid)['attempt'],1)

    def test_changed_axis_after_failure_requires_new_current_response(self):
        from backend.smart_rabi_window import model_scope
        with self.c.store.transaction() as rt:
            w=self.c.get(self.wid,rt);n=self.c._node(w,self.nid)
            n['parameterOverrides']['amp_stop']=.95
            self.c.store.save(rt,w,'TEST_ONLY stale scan axis')
        w=self.c.get(self.wid);n=self.c._node(w,self.nid)
        self.assertIsNone(model_scope(self.c,w,n,{})[1])

    def manual_change_after_window(self,changes):
        self.assertTrue(self.decide({'drive_length_s':750e-9}))
        w=self.c.get(self.wid)
        w=self.c.action(self.wid,{'action':'pause','operationId':'TEST_ONLY-pause-window','expectedRevision':w['revision']})
        w=self.c.set_parameters(self.wid,self.nid,{'parameters':changes,
            'operationId':'TEST_ONLY-edit-window','expectedRevision':w['revision']})
        self.assertTrue(self.c._node(w,self.nid)['result']['stale'])
        self.c.action(self.wid,{'action':'resume','operationId':'TEST_ONLY-resume-window','expectedRevision':w['revision']})
        before=len(self.calls);self.c._execute_node(self.wid,self.nid)
        self.assertEqual(len(self.calls),before,'A stale or edited selection must not use the previously selected authorization')
        self.assertEqual(self.c._node(self.c.get(self.wid),self.nid)['attempt'],1)

    def test_normal_pause_edit_amplitude_cannot_reuse_selected_authorization(self):
        self.manual_change_after_window({'drive_amp':.63})

    def test_even_noop_manual_edit_invalidates_source_before_selected_authorization(self):
        self.manual_change_after_window({'drive_length_s':750e-9})

    def test_verification_parameters_must_derive_from_completed_native_selection(self):
        self.assertTrue(self.decide({'drive_length_s':750e-9}));self.rabi_pass=True
        original=self.c._call_adapter;before=len(self.calls)
        def wrong_candidate(request,token):
            if request['operationId'].endswith('/verify'):
                request['parameters'].update(drive_amp=.63,half_pi_amp=.63)
                with self.c.store.transaction() as rt:
                    rt.db.execute('UPDATE platform_effects SET request=? WHERE operation_id=?',(json.dumps(request),request['operationId']))
            return original(request,token)
        with patch.object(self.c,'_call_adapter',side_effect=wrong_candidate):self.c._execute_node(self.wid,self.nid)
        self.assertEqual(len(self.calls)-before,1,'Only selection is permitted; altered verification must not dispatch')
        self.assertNotEqual(self.c._node(self.c.get(self.wid),self.nid)['engineState'],'verified')


# The inherited setup is useful; unrelated inherited tests stay in their original suite.
for _name in list(vars(SmartSamplingBackend)):
    if _name.startswith('test_'):
        setattr(SmartRabiWindow,_name,None)

def load_tests(loader,tests,pattern):
    return loader.loadTestsFromTestCase(SmartRabiWindow)
