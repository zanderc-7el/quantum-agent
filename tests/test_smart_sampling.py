"""TEST_ONLY injected native receipts; no hardware, no calibration claim."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from adapters.arclight import ArcLightAdapter
from adapters.f8 import _digest
from backend.common import ROOT,APIError,now,digest
from backend.controller import Controller
from backend.smart_q11_launch import launch,START_SCOPE,PROFILE
from backend import smart_sampling,attempt_execution


class SmartSamplingBackend(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='TEST_ONLY-smart-sampling-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);imports=self.root/'data/imports';imports.mkdir(parents=True)
        (imports/'history.json').write_text(json.dumps({'version':1,'resources':[{'id':'Q11','label':'Q11'}],'records':[]}))
        profile=json.loads((ROOT/'adapters/arclight_profile_v3.json').read_text(encoding='utf-8'))
        profile['enabled']=True
        self.adapter=ArcLightAdapter(ROOT,profile);self.adapter.operations=self.root/'data/runs/arclight_operations'
        self.adapter.execute=self.transport;self.adapter.with_execution_enabled=lambda:self.adapter
        self.c=Controller(self.root,adapter=self.adapter,start_worker=False);self.addCleanup(self.c.close)
        self.c.hardware_ownership=lambda **k:{'verified':True,'owners':[]}
        self.c._brain=Mock(return_value={'status':'waiting_model','message':'TEST_ONLY'})
        self.calls=[]
        w=launch(self.c,{'name':'TEST_ONLY smart source','resourceId':'Q11','operationId':'TEST_ONLY-launch',
            'adapterId':PROFILE,'scope':START_SCOPE,'stationConfirmed':True})
        self.wid=w['id'];self.nid=next(n['id'] for n in w['nodes'] if n['procedureId']=='sq_rabi_amplitude')
        with self.c.store.transaction() as rt:
            w=self.c.get(self.wid,rt);n=self.c._node(w,self.nid)
            n['parameterOverrides']={'shots':2000,'capture_repeats':2,'acquisitions_per_point':8,
                'points':41,'amp_start':.02,'amp_stop':.98,'drive_length_s':375e-9}
            self.c.store.save(rt,w,'TEST_ONLY scan setup')
        # The real scheduler yields after every closed smart attempt. Drive a
        # bounded sequence of normal queued turns, not a private fast path.
        for _ in range(15):
            current=self.c.get(self.wid)
            if current['status']!='queued':break
            self.c.run_workflow(self.wid)
        self.w=self.c.get(self.wid);self.n=self.c._node(self.w,self.nid)
        self.assertEqual(self.n['result']['quality']['status'],'failed')

    def transport(self,request,*unused):
        self.calls.append(deepcopy(request));plan=self.adapter.build_plan(request)
        folder=self.adapter._operation_dir(request['operationId']);folder.mkdir(parents=True)
        raw=folder/'raw_scan.npz';raw.write_bytes(('TEST_ONLY / no physical ADC / '+request['operationId']).encode())
        fail=request['procedureId']=='sq_rabi_amplitude'
        analysis={'metrics':{'contrast_snr':8,'peak_snr':8,'r_squared':.95,'cycles':2,'relative_stderr':.1,'scan_coverage':1},
            'candidateParameters':{},'series':[], 'analysis':{'accepted':not fail,'fixture':'TEST_ONLY',
                'reasons':['Oscillation amplitude is not resolved relative to residual/shot noise.'] if fail else []}}
        completion={'state':'completed','shutdown_ok':True,'acquiredAt':now(),'analysis':analysis,
            'files':[str(raw),str(folder/'plan.json')]}
        canonical={k:v for k,v in request.items() if k!='outputDir'}
        def put(name,data):(folder/name).write_text(json.dumps(data),encoding='utf-8')
        put('adapter_intent.json',{'operationId':request['operationId'],'requestHash':_digest(canonical),
            'request':canonical,'profileHash':_digest(self.adapter.profile)})
        put('plan.json',plan);put('completion.json',completion)
        result=self.adapter._result(folder,plan,request,completion)
        put('adapter_receipt.json',{'status':'completed','operationId':request['operationId'],
            'requestHash':_digest(canonical),'result':result})
        return result

    def prepared(self):
        with self.c.store.transaction() as rt:
            w=self.c.get(self.wid,rt);n=self.c._node(w,self.nid);params,versions=self.c._parameters(rt,w,n)
            trial={**params,'acquisitions_per_point':16};before=deepcopy(w)
            bound=smart_sampling.authorize_retry(self.c,rt,w,n,trial,versions,
                {'action':'retry','reason':'TEST_ONLY actual weak eight-block source','parameterChanges':{'acquisitions_per_point':16}})
            self.assertEqual(w,before,'Preparation must not persist a selected authorization before plan acceptance')
            return w,n,trial,versions,bound

    def test_only_authenticated_current_eight_block_source_adds_model_option(self):
        bounds={'acquisitions_per_point':{'min':1,'max':8}}
        self.assertEqual(smart_sampling.model_bounds(self.c,self.w,self.n,bounds)['acquisitions_per_point']['max'],16)
        old=deepcopy(self.w);old.pop('smartRun')
        self.assertEqual(smart_sampling.model_bounds(self.c,old,self.n,bounds),bounds)
        raw=self.adapter._operation_dir(self.n['latestAttemptId']+'/measure')/'raw_scan.npz'
        raw.write_bytes(b'TEST_ONLY tampered')
        self.assertEqual(smart_sampling.model_bounds(self.c,self.w,self.n,bounds),bounds)
        with self.assertRaises(APIError):self.prepared()

    def test_prepare_then_commit_preserves_base_identity_and_attempt_ordinal(self):
        w,n,trial,versions,bound=self.prepared()
        self.assertEqual(bound.adapter_id,'arclight-private-f8-q11-smart-rabi16-v1')
        before=(w['adapterId'],w['adapterProfileHash'])
        request={'runId':w['id'],'nodeId':n['id'],'attemptId':'parameter-validation','operationId':'TEST_ONLY-plan',
            'resourceId':'Q11','procedureId':'sq_rabi_amplitude','parameters':trial,'parameterVersions':versions,
            'adapterId':bound.adapter_id,'adapterProfileHash':bound.adapter_profile_hash}
        attempt_execution.attach_plan(bound,request);plan=bound.adapter.build_plan(request)
        self.assertEqual(plan['estimated_total_shots'],2624000)
        smart_sampling.commit_retry(w,n,bound)
        auth=w['attemptExecutionAuthorizations'][n['nextExecutionAuthorizationId']]['authorization']
        self.assertEqual(auth['attemptNumber'],n['attempt']+1)
        self.assertEqual(auth['sourceAttemptId'],n['latestAttemptId'])
        self.assertEqual((w['adapterId'],w['adapterProfileHash']),before)
        self.assertEqual(auth['sourceEvidenceSha256'],digest(auth['sourceEvidence']))

    def test_different_workpoint_and_counts_do_not_create_authorization(self):
        with self.c.store.transaction() as rt:
            w=self.c.get(self.wid,rt);n=self.c._node(w,self.nid);params,versions=self.c._parameters(rt,w,n)
            original=deepcopy(w)
            for delta in ({'acquisitions_per_point':20},{'acquisitions_per_point':16,'shots':2048},
                          {'acquisitions_per_point':16,'readout_amp':.123}):
                with self.subTest(delta=delta),self.assertRaises(APIError):
                    smart_sampling.authorize_retry(self.c,rt,w,n,{**params,**delta},versions,{'action':'retry'})
                self.assertEqual(w,original)

    def test_normal_model_retry_prepares_real_16_block_pair_without_dispatch(self):
        self.c._brain=Mock(return_value={'status':'ok','decision':{'action':'retry','reason':'TEST_ONLY weak response after eight blocks',
            'evidenceIds':[],'parameterChanges':{'acquisitions_per_point':16}}})
        before=len(self.calls)
        self.assertTrue(self.c._decide(self.wid,self.nid), self.c.get(self.wid).get('engineState'))
        w=self.c.get(self.wid);n=self.c._node(w,self.nid)
        self.assertEqual(n['parameters']['acquisitions_per_point'],16)
        self.assertEqual(w['status'],'queued');self.assertEqual(len(self.calls),before)
        auth=w['attemptExecutionAuthorizations'][n['nextExecutionAuthorizationId']]['authorization']
        self.assertEqual(auth['executionVersion'],'smart-rabi16-v1')
        self.assertEqual(auth['attemptNumber'],n['attempt']+1)
        payload=self.c._brain.call_args.args[1]
        self.assertEqual(payload['parameterBounds']['acquisitions_per_point']['max'],16)

    def test_invalid_intermediate_block_proposal_recovers_without_persisted_authorization(self):
        from backend.model_recovery import recover_due
        self.c._brain=Mock(return_value={'status':'ok','decision':{'action':'retry','reason':'TEST_ONLY invalid intermediate count',
            'evidenceIds':[],'parameterChanges':{'acquisitions_per_point':12}}})
        self.assertFalse(self.c._decide(self.wid,self.nid))
        w=self.c.get(self.wid)
        self.assertEqual(w['modelRecovery']['state'],'backoff')
        self.assertEqual(w['modelRecovery']['lastError']['code'],'PARAMETER_OUT_OF_RANGE')
        self.assertFalse(w.get('attemptExecutionAuthorizations'))
        self.assertEqual(recover_due(self.c,at=w['modelRecovery']['nextAttemptAt']),1)
        self.c._brain=Mock(return_value={'status':'ok','decision':{'action':'retry','reason':'TEST_ONLY corrected sixteen blocks',
            'evidenceIds':[],'parameterChanges':{'acquisitions_per_point':16}}})
        self.assertTrue(self.c._decide(self.wid,self.nid))

    def test_rejected_model_plan_leaves_no_execution_authorization(self):
        from adapters.arclight_smart_rabi16_adapter import ArcLightSmartRabi16Adapter
        from adapters.f8 import AdapterError
        self.c._brain=Mock(return_value={'status':'ok','decision':{'action':'retry','reason':'TEST_ONLY plan rejection',
            'evidenceIds':[],'parameterChanges':{'acquisitions_per_point':16}}})
        with patch.object(ArcLightSmartRabi16Adapter,'build_plan',side_effect=AdapterError('SEQUENCE_WINDOW','TEST_ONLY plan too long')):
            self.assertFalse(self.c._decide(self.wid,self.nid))
        w=self.c.get(self.wid);n=self.c._node(w,self.nid)
        self.assertFalse(w.get('attemptExecutionAuthorizations'));self.assertNotIn('nextExecutionAuthorizationId',n)
        self.assertEqual(n['parameterOverrides']['acquisitions_per_point'],8)

    def test_versioned_reader_can_return_to_ordinary_eight_block_replanning(self):
        w,n,trial,versions,bound=self.prepared()
        with self.c.store.connect() as rt:
            planned=smart_sampling.authorize_retry(bound,rt,w,n,{**trial,'acquisitions_per_point':8},versions,{'action':'retry'})
        self.assertIs(planned,self.c)
        with patch.object(self.c,'_decide',return_value=False) as decide:
            self.assertFalse(bound._decide(self.wid,self.nid))
        decide.assert_called_once_with(self.wid,self.nid,_smart_parent=None)

    def test_joint_budget_requires_exact_new_identity_and_policy(self):
        from backend.acquisition_budget import verification_probe,SMART_RABI16_IDENTITY
        w,n,trial,versions,bound=self.prepared()
        request={'runId':w['id'],'nodeId':n['id'],'attemptId':'plan-check','operationId':'plan-check',
            'resourceId':'Q11','procedureId':'sq_rabi_amplitude','parameters':trial,'parameterVersions':versions,
            'adapterId':bound.adapter_id,'adapterProfileHash':bound.adapter_profile_hash}
        attempt_execution.attach_plan(bound,request);plan=bound.adapter.build_plan(request)
        args=dict(implementation_id=SMART_RABI16_IDENTITY[0],profile_id=SMART_RABI16_IDENTITY[1],
            procedure_id='sq_rabi_amplitude',request_parameters=trial,selection_plan=plan)
        self.assertEqual(verification_probe(**args)[0],trial)
        for delta in ({'implementation_id':'custom.Unreviewed'}, {'procedure_id':'sq_rabi_time'},
                      {'selection_plan':{**plan,'executionPolicy':'legacy'}},
                      {'selection_plan':{**plan,'executionBinding':None}}):
            with self.subTest(delta=delta),self.assertRaises(APIError):verification_probe(**{**args,**delta})

    def test_actual_ledger_attempt11_has_two_effects_one_lease_and_fully_counted_archives(self):
        from adapters.arclight_smart_rabi16_adapter import ArcLightSmartRabi16Adapter
        from tests.rabi16_archive_fixture import archive_result
        # Ten complete attempts are made through the controller, never fake an
        # ordinal by editing the attempt counter or aliasing the old R31 policy.
        for ordinal in range(2,11):
            self.c._brain=Mock(return_value={'status':'ok','decision':{'action':'retry',
                'reason':'TEST_ONLY changed bounded scan axis','evidenceIds':[],
                'parameterChanges':{'amp_stop':.98-ordinal*.0001}}})
            self.assertTrue(self.c._decide(self.wid,self.nid))
            self.c._brain=Mock(return_value={'status':'waiting_model','message':'TEST_ONLY'})
            self.c._execute_node(self.wid,self.nid)
        self.assertEqual(self.c._node(self.c.get(self.wid),self.nid)['attempt'],10)
        self.c._brain=Mock(return_value={'status':'ok','decision':{'action':'retry','evidenceIds':[],
            'reason':'TEST_ONLY weak source, add independent blocks','parameterChanges':{'acquisitions_per_point':16}}})
        self.assertTrue(self.c._decide(self.wid,self.nid))
        before=self.c.get(self.wid)['reservedShots'];requests=[]
        original=ArcLightSmartRabi16Adapter.__init__
        def init(adapter,*args,**kwargs):
            original(adapter,*args,**kwargs);adapter.operations=self.adapter.operations
        def execute(adapter,request,*unused):
            with self.c.store.connect() as rt:
                leases=rt.db.execute('select token from platform_leases where workflow_id=?',(self.wid,)).fetchall()
                self.assertEqual([r[0] for r in leases],[request['leaseToken']])
            requests.append(deepcopy(request));return archive_result(adapter,request)
        with patch.object(ArcLightSmartRabi16Adapter,'__init__',init),\
             patch.object(ArcLightSmartRabi16Adapter,'execute',execute),\
             patch.object(ArcLightAdapter,'hardware_ownership',return_value={'verified':True,'owners':[]}),\
             patch.object(ArcLightAdapter,'preflight',return_value={'ready':True,'enabled':False}):
            self.c._execute_node(self.wid,self.nid)
            final=self.c.get(self.wid);node=self.c._node(final,self.nid)
            self.assertEqual(len(requests),2,node.get('detail'))
            self.assertEqual(node['attempt'],11)
            self.assertEqual(node['engineState'],'verified',node.get('detail'))
            first,second=requests
            self.assertEqual(first['executionBinding'],second['executionBinding'])
            self.assertEqual(first['executionBinding']['attemptNumber'],11)
            self.assertEqual(first['leaseToken'],second['leaseToken'])
            self.assertNotEqual(first['operationId'],second['operationId'])
            self.assertEqual(final['reservedShots']-before,5248000)
            with self.c.store.connect() as rt:
                self.assertEqual(rt.db.execute('select count(*) from platform_leases').fetchone()[0],0)
                for request in requests:
                    effect=rt.db.execute('select state,result from platform_effects where operation_id=?',(request['operationId'],)).fetchone()
                    self.assertEqual(effect['state'],'completed')
                    evidence=attempt_execution.sampling_evidence(self.c,rt,request,json.loads(effect['result']))
                    self.assertEqual(evidence['totalShots'],2624000)
                    self.assertEqual(len(evidence['checkedFiles']),1401)


if __name__=='__main__':unittest.main()
