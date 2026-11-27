"""Temporary database fixtures only; native values copied from an exported report.

Registry/receipts are test metadata, never a physical acceptance attestation.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.common import dumps, now
from backend.chat_acquisition_context import acquisition_context, read_chat_snapshot
from tests.test_controller import make_fixture

FIXTURE=Path(__file__).resolve().parent/'fixtures/chat-cavity-roles.json'

class ChatMeasurementFacts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='qcal-chat-facts-')
        self.root=Path(self.temp.name)
        self.c,self.adapter=make_fixture(self.root)
        self.wf=self.c.create({'name':'TEST_ONLY chat facts','mode':'real','policy':'threshold','resourceIds':['Q11']})
        self.node_id=self.wf['nodes'][0]['id']
        self.calls=[]
        self.c._brain=lambda path,request: self.calls.append(deepcopy(request)) or {'status':'ok','message':'MODEL PROVIDER FIXTURE'}

    def tearDown(self):
        self.c.close();self.temp.cleanup()

    def seed(self,*,many=False,large=False,binding=False):
        if not FIXTURE.is_file():
            self.skipTest('Private local fixture not included in the source export')
        exported=json.loads(FIXTURE.resolve().read_text(encoding='utf8'))
        source=exported['nodes'][0]['attempts'][0]
        result=deepcopy(source['result'])
        aid=source['id'];selection=deepcopy(result['selectionResult'])
        verification=deepcopy(result);verification.pop('selectionResult',None)
        verification['candidateParameters']=deepcopy(result.get('verificationCandidateParameters',result['candidateParameters']))
        with self.c.store.transaction() as rt:
            wf=self.c.get(self.wf['id'],rt);node=wf['nodes'][0]
            node.update(attempt=1,latestAttemptId=aid,result=result,engineState='verified',status='success')
            attempt={'id':aid,'nodeId':self.node_id,'number':1,'mode':'real','operationId':aid+'/measure',
                'verificationOperationId':aid+'/verify','parameterVersions':source['parameterVersions']}
            if large:attempt['result']={'unused':'x'*(3*1024*1024)}
            if binding:
                node['procedureId']='sq_rabi_amplitude'
                attempt['executionBinding']={'schema':'qcal-attempt-execution-binding/1','stage':'acquisition',
                    'baseAdapterId':wf['adapterId'],'baseAdapterProfileHash':wf['adapterProfileHash'],
                    'adapterId':'arclight-private-f8-q11-rabi16-v1','adapterProfileHash':'b'*64,
                    'workflowId':wf['id'],'nodeId':self.node_id,'attemptId':aid,'resourceId':'Q11',
                    'procedureId':node['procedureId'],'attemptNumber':1,'selectionOperationId':aid+'/measure',
                    'verificationOperationId':aid+'/verify'}
            rt.db.execute('INSERT INTO platform_attempts(id,workflow_id,node_id,number,payload) VALUES(?,?,?,?,?)',
                (aid,wf['id'],self.node_id,1,dumps(attempt)))
            for stage,acquired in [('measure',selection),('verify',verification)]:
                operation=aid+'/'+stage
                request={'runId':wf['id'],'nodeId':self.node_id,'attemptId':aid,'operationId':operation,
                    'resourceId':'Q11','procedureId':node['procedureId'],'parameterVersions':attempt['parameterVersions'],
                    'adapterId':wf['adapterId'],'adapterProfileHash':wf['adapterProfileHash'],'parameters':acquired['parameters']}
                if binding:
                    request.update(executionBinding=deepcopy(attempt['executionBinding']),
                        adapterId=attempt['executionBinding']['adapterId'],adapterProfileHash='b'*64)
                    acquired['provenance']['executionBinding']=deepcopy(attempt['executionBinding'])
                count=1406 if many else 1
                acquired['artifacts']=[]
                for index in range(count):
                    sha=hashlib.sha256(f'{operation}:{index}'.encode()).hexdigest()
                    artifact={'id':'fixture-'+sha,'name':f'block-{index}.npz','kind':'raw','sha256':sha}
                    acquired['artifacts'].append(artifact)
                    rt.db.execute('INSERT INTO platform_artifacts(id,path,sha256,payload) VALUES(?,?,?,?)',
                        (artifact['id'],str(self.root/f'{index}.npz'),sha,dumps(artifact)))
                if large:
                    acquired['unusedLargeArray']='x'*(3*1024*1024)
                    acquired['provenance']['unusedLargeClosure']='x'*(3*1024*1024)
                    acquired['analysisQuality']['unusedLargeArray']='x'*(3*1024*1024)
                rt.db.execute('INSERT INTO platform_effects(operation_id,workflow_id,attempt_id,state,request,result) VALUES(?,?,?,?,?,?)',
                    (operation,wf['id'],aid,'completed',dumps(request),dumps(acquired)))
            rt.db.execute('INSERT OR REPLACE INTO parameters VALUES(?,?,?)',
                (wf['id']+':Q11:readout_hz',dumps(7009676666.666667),1))
            node['result']={**verification,'selectionResult':selection}
            self.c.store.save(rt,wf,'test-only-recorded-evidence')
        return aid

    def context(self,message='Explain recorded evidence and possible causes.'):
        with patch.object(self.adapter,'execute',side_effect=AssertionError('chat cannot acquire')):
            result=self.c.chat({'workflowId':self.wf['id'],'message':message})
        self.assertEqual(result['message'],'MODEL PROVIDER FIXTURE')
        return self.calls[-1]['context']

    def test_actual_export_preserves_candidate_configuration_commit_and_both_snrs(self):
        self.seed()
        for question in ['请只查看这条 Q11 任务的最新测量记录。读出腔定频首测和独立复测各得到多少 GHz、信噪比各是多少？目前在测哪一步？',
                         '两轮找腔频率和信噪比有何差别？请根据回执说明，并分析可能原因。']:
            context=self.context(question);node=context['acquisitionState']['nodes'][0]
            stages={item['stage']:item for item in node['operations']}
            first=stages['selection']['parameterEvidence'];second=stages['verification']['parameterEvidence']
            self.assertEqual(first['measuredCandidates']['readout_hz']['value'],7009676666.666667)
            self.assertEqual(first['measuredCandidates']['readout_hz']['frequencyUnits']['GHz'],'7.009676666666667')
            self.assertEqual(first['configuredInputs']['readout_hz']['value'],7009610000.0)
            self.assertEqual(second['measuredCandidates']['readout_hz']['value'],7009676666.666667)
            self.assertEqual(stages['selection']['metrics']['contrast_snr'],40.6003991710448)
            self.assertEqual(stages['verification']['metrics']['contrast_snr'],46.26631470033565)
            self.assertEqual(node['committedParameters']['readout_hz']['value'],7009676666.666667)
            self.assertEqual(self.calls[-1]['message'],question)

    def test_missing_candidate_is_not_filled_from_config_or_committed_ledger(self):
        aid=self.seed()
        with self.c.store.transaction() as rt:
            rt.db.execute("UPDATE platform_effects SET result=json_remove(result,'$.candidateParameters') WHERE operation_id=?",(aid+'/measure',))
        first=next(x for x in self.context()['acquisitionState']['nodes'][0]['operations'] if x['stage']=='selection')
        self.assertEqual(first['parameterEvidence']['measuredCandidates'],{})
        self.assertIn('readout_hz',first['parameterEvidence']['configuredInputs'])

    def test_large_native_receipt_is_projected_and_raw_hashes_are_bounded(self):
        self.seed(many=True,large=True,binding=True)
        context=self.context();node=context['acquisitionState']['nodes'][0]
        self.assertEqual(len(node['operations']),2)
        for operation in node['operations']:
            self.assertEqual(operation['registeredRawCount'],1406)
            self.assertLessEqual(len(operation['registeredRawSha256']),8)
            self.assertEqual(len(operation['registeredRawSetSha256']),64)
        self.assertTrue(node['independentAcquisition']['completedFreshReceiptsWithDisjointRegisteredRaw'])
        self.assertLess(len(dumps(context)),190000)

    def test_changed_attempt_execution_identity_cannot_supply_operation_facts(self):
        aid=self.seed(binding=True)
        with self.c.store.transaction() as rt:
            rt.db.execute("UPDATE platform_effects SET request=json_set(request,'$.executionBinding.attemptId','wrong') WHERE operation_id=?",(aid+'/measure',))
        node=self.context()['acquisitionState']['nodes'][0]
        self.assertEqual([x['stage'] for x in node['operations']],['verification'])
        self.assertFalse(node['independentAcquisition']['completedFreshReceiptsWithDisjointRegisteredRaw'])

    def test_times_keep_original_and_explicit_beijing_offset(self):
        self.seed()
        with read_chat_snapshot(self.c.store.path) as db:
            wf=json.loads(db.execute('SELECT payload FROM platform_workflows WHERE id=?',(self.wf['id'],)).fetchone()[0])
            state=acquisition_context(db,wf,self.root,'2026-11-26T19:02:03+00:00')
        self.assertEqual(state['snapshotAtBeijing'],'2026-11-27T03:02:03+08:00')
        self.assertEqual(state['displayTimeZone'],'Asia/Shanghai (UTC+08:00)')
        first=next(x for x in state['nodes'][0]['operations'] if x['stage']=='selection')
        self.assertEqual(first['acquiredAt'],'2026-11-26T18:52:22.467701+00:00')
        self.assertEqual(first['acquiredAtBeijing'],'2026-11-27T02:52:22.467701+08:00')

    def test_normal_conversation_keeps_model_and_does_not_change_measurement_tables(self):
        self.seed()
        def frozen():
            with read_chat_snapshot(self.c.store.path) as db:
                return {table:[tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in
                    ['platform_workflows','platform_attempts','platform_effects','platform_leases','parameters']}
        before=frozen()
        conv=self.c.workspace.create_conversation({'operationId':'test-facts-conversation',
            'scope':{'kind':'workflow','id':self.wf['id']}})
        message='首轮找腔和重复检查分别找到了什么频率？请解释信噪比差异。'
        with patch.object(self.adapter,'execute',side_effect=AssertionError('no acquisition')):
            reply=self.c.workspace.message(conv['id'],{'operationId':'test-facts-message','message':message,'intent':'discuss'})
        self.assertEqual(self.calls[-1]['message'],message)
        observations=self.calls[-1]['context']['observations']
        self.assertTrue(any(o.get('parameterEvidence',{}).get('measuredCandidates') for o in observations))
        self.assertEqual(frozen(),before)
        self.assertIn('MODEL PROVIDER FIXTURE',dumps(reply))

    def test_registration_mismatch_suppresses_measured_candidate_and_naive_time_stays_unknown(self):
        aid=self.seed()
        with self.c.store.transaction() as rt:
            first=json.loads(rt.db.execute('SELECT result FROM platform_effects WHERE operation_id=?',(aid+'/measure',)).fetchone()[0])
            rt.db.execute('UPDATE platform_artifacts SET sha256=? WHERE id=?',('0'*64,first['artifacts'][0]['id']))
            rt.db.execute("UPDATE platform_effects SET result=json_set(result,'$.acquiredAt','2026-11-27T03:00:00') WHERE operation_id=?",(aid+'/measure',))
        first=next(x for x in self.context()['acquisitionState']['nodes'][0]['operations'] if x['stage']=='selection')
        self.assertEqual(first['registeredRawCount'],0)
        self.assertEqual(first['parameterEvidence']['measuredCandidates'],{})
        self.assertIsNone(first['acquiredAtBeijing'])

    def test_numeric_truthy_flag_is_not_retyped_into_fresh_boolean(self):
        aid=self.seed()
        with self.c.store.transaction() as rt:
            rt.db.execute("UPDATE platform_effects SET result=json_set(result,'$.provenance.freshAcquisition',1) WHERE operation_id=?",(aid+'/measure',))
        first=next(x for x in self.context()['acquisitionState']['nodes'][0]['operations'] if x['stage']=='selection')
        self.assertFalse(first['freshCompletedReceipt'])
        self.assertEqual(first['parameterEvidence']['measuredCandidates'],{})
