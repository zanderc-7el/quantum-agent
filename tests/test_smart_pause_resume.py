import json
import unittest
from unittest.mock import patch
from backend.common import APIError
from tests import test_smart_main_checkpoint as fixture


class PausePlanning(unittest.TestCase):
    def setUp(self):
        self.f=fixture.CompletedCheckpoint(); self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.c=self.f.c; self.wid=self.f.wid;self.nid=self.f.nid
        self.counter=0

    def action(self, a, c=None, **kwargs):
        c=c or self.c;self.counter+=1
        return c.action(self.wid, {'action':a,'operationId':'review-'+str(self.counter),
            'expectedRevision':c.get(self.wid)['revision'], **kwargs})

    def pause_failed(self,state='decision_queued'):
        self.f.complete_then_crash(False)
        if state=='deciding':
            self.c._attention(self.wid,'deciding','TEST_ONLY 模型决策开始')
        return self.action('pause')

    def assert_one_model_no_acquisition(self,c):
        calls=[]
        c._brain=lambda route,body:calls.append(body) or {'status':'ok','decision':{
            'action':'wait','reason':'TEST_ONLY wait','evidenceIds':[]}}
        before=len(self.f.fake.calls)
        c.run_workflow(self.wid);c.run_workflow(self.wid)
        self.assertEqual((len(calls),len(self.f.fake.calls)),(1,before))
        self.assertEqual(c.get(self.wid)['engineState'],'waiting_operator')

    def test_queued_failure_pause_restart_resume_calls_only_model(self):
        p=self.pause_failed()
        self.assertEqual((p['status'],p['engineState']),('paused','paused'))
        c=self.f.reopen()
        self.assertEqual(c.get(self.wid)['status'],'paused')
        w=self.action('resume',c)
        self.assertEqual((w['status'],w['engineState']),('queued','decision_queued'))
        self.assert_one_model_no_acquisition(c)

    def test_pause_during_model_request_discards_late_answer_resume_replans_once(self):
        self.f.complete_then_crash(False)
        def reply(_route,_body):
            self.action('pause')
            return {'status':'ok','decision':{'action':'retry','reason':'TEST_ONLY late response','parameterChanges':{}}}
        self.c._brain=reply
        self.c.run_workflow(self.wid)
        self.assertEqual((self.c.get(self.wid)['status'],len(self.f.fake.calls)),('paused',1))
        resumed=self.action('resume')
        self.assertEqual(resumed['engineState'],'decision_queued')
        self.assert_one_model_no_acquisition(self.c)

    def test_preupgrade_paused_without_marker_proves_last_pause_and_actual_frozen_receipt(self):
        self.pause_failed('deciding')
        with self.c.store.transaction() as rt:
            row=rt.db.execute('SELECT payload FROM platform_workflows WHERE id=?',(self.wid,)).fetchone()
            w=json.loads(row[0]); w['smartRun'].pop('pausedPlanning',None)
            rt.db.execute('UPDATE platform_workflows SET payload=? WHERE id=?',(json.dumps(w),self.wid))
        self.assertEqual(self.action('resume')['engineState'],'decision_queued')
        self.assert_one_model_no_acquisition(self.c)

    def test_different_effect_result_or_parameters_fails_closed(self):
        self.pause_failed()
        with self.c.store.transaction() as rt:
            row=rt.db.execute('SELECT operation_id,result FROM platform_effects WHERE workflow_id=?',(self.wid,)).fetchone()
            original=row['result'];modified=json.loads(original);modified['metrics']['changed']=1
            rt.db.execute('UPDATE platform_effects SET result=? WHERE operation_id=?',(json.dumps(modified),row['operation_id']))
        with self.assertRaises(APIError):self.action('resume')
        with self.c.store.transaction() as rt:
            rt.db.execute('UPDATE platform_effects SET result=? WHERE operation_id=?',(original,row['operation_id']))
            w=self.c.get(self.wid,rt);n=self.c._node(w,self.nid)
            n.setdefault('parameterOverrides',{})['readout_amp']=.12
            self.c.store.save(rt,w,'TEST_ONLY_drift')
        with self.assertRaises(APIError):self.action('resume')
        self.assertEqual((self.c.get(self.wid)['status'],len(self.f.fake.calls)),('paused',1))

    def test_unknown_effect_without_lease_blocks_resume(self):
        self.pause_failed()
        with self.c.store.transaction() as rt:
            rt.db.execute("UPDATE platform_effects SET state='unknown' WHERE workflow_id=?",(self.wid,))
        with self.assertRaises(APIError): self.action('resume')
        self.assertEqual((self.c.get(self.wid)['status'],len(self.f.fake.calls)),('paused',1))

    def test_stale_scheduler_deciding_call_cannot_overwrite_pause(self):
        self.pause_failed()
        with patch.object(self.c,'_brain',side_effect=AssertionError('paused model called')):
            self.assertFalse(self.c._decide(self.wid,self.nid))
        self.assertEqual(self.c.get(self.wid)['status'],'paused')

    def test_pause_after_passed_result_keeps_ordinary_next_node_and_does_not_request_model(self):
        self.f.complete_then_crash(True)
        self.action('pause')
        w=self.action('resume')
        self.assertEqual(w['engineState'],'queued')
        with patch.object(self.c,'_execute_node',return_value=False) as dispatch,patch.object(self.c,'_decide') as model:
            self.c.run_workflow(self.wid)
        self.assertNotEqual(dispatch.call_args.args[1],self.nid)
        model.assert_not_called()

    def test_restart_after_resume_restores_decision_queue_without_new_capture(self):
        self.pause_failed('deciding')
        self.action('resume')
        c=self.f.reopen()
        self.assertEqual(c.get(self.wid)['engineState'],'decision_queued')
        self.assert_one_model_no_acquisition(c)

    def test_duplicate_resume_receipt_does_not_create_a_second_dispatch(self):
        w=self.pause_failed()
        body={'action':'resume','operationId':'restart-same-command','expectedRevision':w['revision']}
        accepted=self.c.action(self.wid,body)
        duplicate=self.c.action(self.wid,body)
        self.assertEqual((accepted['revision'],accepted['engineState']),(duplicate['revision'],'decision_queued'))
        self.assert_one_model_no_acquisition(self.c)

    def test_queued_scheduler_claim_loses_race_to_pause_and_resume(self):
        self.f.complete_then_crash(False)
        original=self.c._attention
        def race(*args,**kwargs):
            self.action('pause');self.action('resume')
            return original(*args,**kwargs)
        with patch.object(self.c,'_attention',side_effect=race),patch.object(self.c,'_brain') as brain:
            self.assertFalse(self.c._decide(self.wid,self.nid))
        brain.assert_not_called()
        self.assert_one_model_no_acquisition(self.c)

    def test_legacy_reconciliation_without_marker_does_not_resume_unsettled_hardware(self):
        self.pause_failed()
        with self.c.store.transaction() as rt:
            w=self.c.get(self.wid,rt);w['smartRun'].pop('pausedPlanning',None)
            w.update(status='attention',engineState='needs_reconciliation')
            rt.db.execute("UPDATE platform_effects SET state='unknown' WHERE workflow_id=?",(self.wid,))
            self.c.store.save(rt,w,'TEST_ONLY_reconciliation')
        with self.assertRaises(APIError) as exc:self.action('resume')
        self.assertEqual(exc.exception.code,'RECONCILIATION_REQUIRED')

    def test_parameters_already_changed_before_pause_cannot_reuse_previous_attempt(self):
        self.f.complete_then_crash(False)
        with self.c.store.transaction() as rt:
            w=self.c.get(self.wid,rt)
            self.c._node(w,self.nid).setdefault('parameterOverrides',{})['readout_amp']=.12
            self.c.store.save(rt,w,'TEST_ONLY_drift_before_pause')
        self.action('pause')
        with self.assertRaises(APIError) as exc:self.action('resume')
        self.assertEqual(exc.exception.code,'SMART_PLANNING_CHECKPOINT_CHANGED')
        self.assertEqual(len(self.f.fake.calls),1)



