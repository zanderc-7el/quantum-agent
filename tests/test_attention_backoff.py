"""Temporary smart workflow exercises the same retry checkpoint as the real scheduler."""
from copy import deepcopy
import json
import time
import unittest
from backend.common import dumps
from backend import model_recovery
from tests import test_smart_model_recovery as fixtures


class QuietAutomaticBackoff(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.RecoveryContracts();fixture.setUp();self.addCleanup(fixture.tearDown)
        self.f=fixture;self.c=fixture.c;self.id=fixture.wid
        # This is the production smart main authorization mode; old finite tasks
        # remain visible even if an outdated backoff marker survived migration.
        with self.c.store.transaction() as rt:
            wf=self.c.get(self.id,rt);wf['budget'].update(mode='accounting_only',maxAttemptsPerNode=None,maxWorkflowAttempts=None,maxTotalShots=None)
            wf.update(maxAttemptsPerNode=None,maxWorkflowAttempts=None,maxTotalShots=None)
            self.c.store.save(rt,wf,'TEST_ONLY-accounting')
        self.wf=fixture.unavailable()
        self.assertEqual(self.wf['modelRecovery']['state'],'backoff')
    def active(self):
        return [i for i in self.c.workspace.attention_items() if i['state']!='resolved' and i['workflowId']==self.id]
    def put(self,edit):
        with self.c.store.transaction() as rt:
            wf=deepcopy(self.wf);wf['revision']=self.c.get(self.id,rt)['revision'];edit(wf)
            self.c.store.save(rt,wf,'TEST_ONLY-source-snapshot')
    def test_real_scheduler_backoff_stays_quiet_and_requeues_identical_evidence(self):
        self.assertEqual(self.active(),[])
        self.assertEqual(self.active(),[])
        self.assertEqual(model_recovery.recover_due(self.c,at=104),0)
        self.assertEqual(self.c.get(self.id)['revision'],self.wf['revision'])
        self.assertEqual(model_recovery.recover_due(self.c,at=105),1)
        self.assertEqual(self.c.get(self.id)['engineState'],'decision_queued')
        self.assertEqual(self.active(),[])
    def test_blocked_invalid_evidence_budget_cancel_and_pending_effect_require_attention(self):
        mutate=[
          lambda w:w['modelRecovery'].update(state='blocked',nextAttemptAt=None),
          lambda w:w['modelRecovery'].update(nextAttemptAt='123'),
          lambda w:w['modelRecovery'].update(checkpointSha256='x'),
          lambda w:w['modelRecovery']['checkpoint'].update(workflowId='other'),
          lambda w:w['nodes'][0]['result']['parameters'].update(shots=543),
          lambda w:w['nodes'][0].update(latestAttemptId='other'),
          lambda w:w.update(budget={'mode':'limited'},cancelRequested=None),
          lambda w:w.update(cancelRequested='abort'),lambda w:w.update(sharedDeviceFault=True)]
        for index,edit in enumerate(mutate):
            self.put(edit);self.assertEqual(len(self.active()),1,index)
        self.put(lambda w:None)
        with self.c.store.transaction() as rt:
            rt.db.execute("INSERT INTO platform_effects VALUES('other',?,'att','unknown','{}',NULL)",(self.id,))
        self.assertEqual(len(self.active()),1)
    def test_legacy_backoff_modal_closes_and_reopens_for_true_block_even_at_same_revision(self):
        # Simulate a stale lease belonging to the same workflow, then drop it
        # without changing the workflow revision. This alters scheduler eligibility.
        def add_lease():
            with self.c.store.transaction() as rt:
                rt.db.execute("INSERT INTO platform_leases VALUES('fixture',?,'att','held','token')",(self.id,))
        add_lease();old=self.active()[0]
        with self.c.store.transaction() as rt:rt.db.execute("DELETE FROM platform_leases WHERE resource='fixture'")
        self.assertEqual(self.active(),[])
        unchanged=self.c.get(self.id)
        response=self.c.workspace.attention_action(old['id'],{'action':'abort','operationId':'cancel-old-only','expectedRevision':old['revision']})
        self.assertEqual(response['state'],'resolved');self.assertEqual(self.c.get(self.id),unchanged)
        add_lease();restored=self.active()[0]
        self.assertEqual(restored['id'],old['id']);self.assertGreater(restored['revision'],response['revision'])
        self.assertIn('decide',[x['id'] for x in restored['actions']])
