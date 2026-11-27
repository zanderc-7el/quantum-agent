"""Crash after a real controller completion transaction; fake hardware only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.common import now
from backend.controller import Controller
from tests.test_controller import make_fixture


class CompletedCheckpoint(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='qcal-smart-main-checkpoint-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.c,self.fake=make_fixture(self.root);self.addCleanup(self.c.close)
        self.fake.profile['id']='arclight-private-f8-q11-v3'
        capabilities=self.fake.capabilities
        self.fake.capabilities=lambda:{'procedures':capabilities()['procedures']+['ro_power_shift']}
        self.c.adapter_id=self.fake.profile['id'];self.c.adapter_profile_hash=self.c._adapter_fingerprint()
        w=self.c.create({'name':'TEST_ONLY crash checkpoint','mode':'real','policy':'autopilot',
            'coverage':'full','resourceIds':['Q11'],'budget':{'mode':'accounting_only'}})
        self.wid=w['id'];self.nid=w['nodes'][0]['id']
        with self.c.store.transaction() as rt:
            w=self.c.get(self.wid,rt)
            w.update(status='queued',engineState='queued',hardwareConfirmed=True,
                smartRun={'schema':'q11-full-same-gate/1','adapterProfileHash':w['adapterProfileHash']})
            self.c.store.save(rt,w,'test_only_start')

    def complete_then_crash(self,passed=True):
        execute=self.fake.execute
        def result(*args):
            value=execute(*args)
            if not passed:value['metrics']['contrast_snr']=0
            return value
        cancel=self.c._cancel_requested
        def crash_at_boundary(wid):
            node=self.c._node(self.c.get(wid),self.nid)
            if node.get('engineState') in ('verified','quality_attention') and node.get('latestAttemptId'):
                raise SystemExit('TEST_ONLY process exit after commit')
            return cancel(wid)
        with patch.object(self.fake,'execute',side_effect=result),patch.object(self.c,'_cancel_requested',side_effect=crash_at_boundary):
            with self.assertRaisesRegex(SystemExit,'after commit'):
                self.c._execute_node(self.wid,self.nid)
        return self.c.get(self.wid)

    def reopen(self):
        self.c.close()
        reopened=Controller(self.root,adapter=self.fake,start_worker=False)
        self.addCleanup(reopened.close)
        return reopened

    def test_passed_commits_queue_atomically_and_restart_skips_completed_node(self):
        w=self.complete_then_crash()
        self.assertEqual((w['status'],w['engineState']),('queued','queued'))
        self.assertEqual(len(self.fake.calls),2)
        c=self.reopen();current=c.get(self.wid)
        self.assertEqual(current['revision'],w['revision'])
        self.assertEqual(c._node(current,self.nid)['engineState'],'verified')
        next_ids=[]
        with patch.object(c,'_execute_node',side_effect=lambda wid,nid:next_ids.append(nid) or False):
            c.run_workflow(self.wid)
        self.assertEqual(len(next_ids),1);self.assertNotEqual(next_ids[0],self.nid)
        self.assertEqual(len(self.fake.calls),2)

    def test_failed_commits_decision_queue_and_restart_never_reacquires_blindly(self):
        w=self.complete_then_crash(False)
        self.assertEqual((w['status'],w['engineState']),('queued','decision_queued'))
        self.assertEqual(len(self.fake.calls),1)
        c=self.reopen();calls=[]
        c._brain=lambda route,body:calls.append(body) or {'status':'ok','decision':{
            'action':'wait','reason':'TEST_ONLY needs a different supported plan','evidenceIds':[]}}
        c.run_workflow(self.wid);c.run_workflow(self.wid)
        self.assertEqual(len(calls),1);self.assertEqual(len(self.fake.calls),1)
        self.assertEqual(c.get(self.wid)['engineState'],'waiting_operator')

    def test_pending_unknown_and_lease_checkpoint_remain_quarantined(self):
        for fault in ('pending','unknown','lease'):
            with self.subTest(fault=fault):
                if fault!='pending':
                    self.tearDownFixtureOnly()
                    self.setUp()
                self.complete_then_crash()
                with self.c.store.transaction() as rt:
                    if fault=='lease':rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)',
                        ('TEST_ONLY-resource',self.wid,self.nid,'TEST_ONLY-token','active'))
                    else:rt.db.execute("UPDATE platform_effects SET state=? WHERE operation_id=(SELECT operation_id FROM platform_effects LIMIT 1)",(fault,))
                c=self.reopen();w=c.get(self.wid)
                self.assertEqual((w['status'],w['engineState']),('attention','needs_reconciliation'))
                before=len(self.fake.calls);c.run_workflow(self.wid);self.assertEqual(len(self.fake.calls),before)

    def tearDownFixtureOnly(self):
        self.c.close()

    def test_old_task_does_not_get_smart_checkpoint(self):
        with self.c.store.transaction() as rt:
            w=self.c.get(self.wid,rt);w.pop('smartRun');self.c.store.save(rt,w,'test_old')
        w=self.complete_then_crash()
        self.assertEqual(w['status'],'running')
        c=self.reopen();self.assertEqual(c.get(self.wid)['engineState'],'needs_reconciliation')

    def test_strict_or_cancelled_completed_windows_do_not_autoresume(self):
        from backend.smart_main_checkpoint import record,recover
        self.complete_then_crash()
        with self.c.store.transaction() as rt:
            original=self.c.get(self.wid,rt)
            for changes in ({'policy':'strict'},{'cancelRequested':'pause'},
                            {'status':'paused'},{'sharedDeviceFault':{'state':'unknown'}}):
                with self.subTest(changes=changes):
                    value=deepcopy(original);value.update(changes)
                    value['smartRun'].pop('mainCheckpoint',None)
                    self.assertFalse(record(self.c,rt,value,self.nid))
                    self.assertNotIn(recover(self.c,rt,value),('valid','restored'))
                    self.assertNotIn('mainCheckpoint',value['smartRun'])

    def test_prior_completed_running_window_restores_only_from_matching_receipts(self):
        self.complete_then_crash()
        with self.c.store.transaction() as rt:
            w=self.c.get(self.wid,rt);w['smartRun'].pop('mainCheckpoint')
            w.update(status='running',engineState='acquiring')
            self.c.store.save(rt,w,'test_pre_checkpoint_completed_window')
        c=self.reopen();w=c.get(self.wid)
        self.assertEqual((w['status'],w['engineState']),('queued','queued'))
        self.assertEqual(len(self.fake.calls),2)

    def test_tampered_completion_snapshot_and_foreign_effect_scope_are_rejected(self):
        for fault in ('attempt','scope','receipt'):
            with self.subTest(fault=fault):
                if fault!='attempt':self.tearDownFixtureOnly();self.setUp()
                self.complete_then_crash()
                with self.c.store.transaction() as rt:
                    if fault=='attempt':
                        row=rt.db.execute('SELECT payload FROM platform_attempts LIMIT 1').fetchone()
                        attempt=json.loads(row[0]);attempt['result']['metrics']['contrast_snr']=99
                        self.c.store.attempt(rt,self.wid,attempt)
                    elif fault=='scope':
                        row=rt.db.execute('SELECT operation_id,request FROM platform_effects LIMIT 1').fetchone()
                        request=json.loads(row['request']);request['runId']='foreign'
                        rt.db.execute('UPDATE platform_effects SET request=? WHERE operation_id=?',
                                     (json.dumps(request),row['operation_id']))
                    else:
                        row=rt.db.execute('SELECT operation_id,result FROM platform_effects LIMIT 1').fetchone()
                        value=json.loads(row['result']);value['metrics']['contrast_snr']=99
                        rt.db.execute('UPDATE platform_effects SET result=? WHERE operation_id=?',
                                     (json.dumps(value),row['operation_id']))
                c=self.reopen();self.assertEqual(c.get(self.wid)['engineState'],'needs_reconciliation')

    def test_normal_completion_yields_scheduler_without_inline_model_or_second_effect(self):
        execute=self.fake.execute
        def weak(*args):
            result=execute(*args);result['metrics']['contrast_snr']=0;return result
        with patch.object(self.fake,'execute',side_effect=weak),patch.object(self.c,'_decide',side_effect=AssertionError('No inline decision')):
            self.assertFalse(self.c._execute_node(self.wid,self.nid))
        self.assertEqual(self.c.get(self.wid)['engineState'],'decision_queued')
        self.assertEqual(len(self.fake.calls),1)


if __name__=='__main__':unittest.main()
