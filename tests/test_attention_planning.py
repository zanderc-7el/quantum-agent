"""Resource attention belongs to an operator only after automatic planning finishes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.common import digest,dumps
from backend.workspace import Workspace
from tests.test_controller import make_fixture


class AutomaticPlanningAttention(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.c,self.ad=make_fixture(self.root);self.addCleanup(self.c.close)
        wf=self.c.create({'name':'TEST_ONLY direct planning','resourceIds':['Q11'],'policy':'autopilot','mode':'real'})
        self.workspace=self.c.workspace
        self.id=wf['id'];self.node=wf['nodes'][0]['id']
    def transition(self,state,**fields):
        with self.c.store.transaction() as rt:
            wf=self.c.get(self.id,rt);wf.update(status='attention',currentNodeId=self.node,engineState=state,**fields)
            wf['nodes'][0].update(engineState=state,detail='本次自动模型分析',latestAttemptId='TEST_ONLY-attempt')
            self.c.store.save(rt,wf,'TEST_ONLY-checkpoint')
        return wf
    def legacy(self,state,disposition='open'):
        wf=self.transition(state);node=wf['nodes'][0]
        signature=digest([wf['id'],node['id'],node['latestAttemptId'],state])
        item={'id':'old-auto','workflowId':self.id,'workflowRevision':wf['revision'],'revision':2,
              'nodeId':node['id'],'attemptId':node['latestAttemptId'],'engineState':state,'scope':'resource',
              'createdAt':wf['createdAt'],'state':disposition,'message':'智能体正在根据实测证据选择下一步','recommendation':'继续',
              'actions':[{'id':'resume','label':'继续'},{'id':'abort','label':'终止该任务'}]}
        with self.c.store.transaction() as rt:rt.db.execute('insert into platform_attention values(?,?,?)',(item['id'],signature,dumps(item)))
        return item
    def test_intermediate_states_are_not_listed_or_persisted_under_repeated_polls(self):
        for state in ('deciding','decision_queued'):
            wf=self.transition(state)
            for _ in range(2):
                self.assertEqual(self.c.workspace.attention_items(),[])
                self.assertEqual(self.c.get(self.id)['revision'],wf['revision'])
        self.assertEqual(self.ad.calls,[])
    def test_legacy_open_and_deferred_close_once_across_reloaded_workspace(self):
        old=self.legacy('deciding','deferred');wf_before=self.c.get(self.id)
        current=self.c.workspace.attention_items()[0]
        self.assertEqual((current['state'],current['revision']),('resolved',old['revision']+1))
        # New service object against the persisted database, no controller or device action.
        reopened=Workspace(self.c,initialize_catalog=False)
        self.assertEqual(reopened.attention_items()[0],current)
        self.assertEqual(self.c.get(self.id),wf_before);self.assertEqual(self.ad.calls,[])
    def test_stale_modal_resume_abort_defer_acknowledge_only_and_are_idempotent(self):
        item=self.legacy('deciding','open');wf_before=self.c.get(self.id)
        with patch.object(self.c,'action',side_effect=AssertionError('old modal cannot change a workflow')):
            for op in ('resume','abort','defer'):
                body={'operationId':'stale-'+op,'expectedRevision':item['revision'],'action':op}
                result=self.c.workspace.attention_action(item['id'],body)
                self.assertEqual(result['state'],'resolved')
                self.assertEqual(result,self.c.workspace.attention_action(item['id'],body))
        self.assertEqual(self.c.get(self.id),wf_before)
    def test_waits_faults_and_quality_still_offer_manual_choices(self):
        expected={'waiting_hardware':'confirm_hardware','quality_attention':'decide','waiting_model':'decide',
                  'waiting_operator':'resume','needs_reconciliation':'reconcile'}
        for state,action in expected.items():
            self.transition(state,modelRecovery={'state':'blocked'} if state=='waiting_model' else {})
            items=[i for i in self.c.workspace.attention_items() if i['state']=='open']
            self.assertEqual(len(items),1,state);self.assertEqual(items[0]['engineState'],state)
            self.assertIn(action,[x['id'] for x in items[0]['actions']])
        self.transition('deciding',sharedDeviceFault=True)
        item=next(i for i in self.c.workspace.attention_items() if i['state']=='open')
        self.assertEqual(item['scope'],'device')

    def test_controller_restart_does_not_reopen_historical_decision_modal(self):
        self.legacy('deciding')
        old=self.c.workspace.attention_items()[0]
        self.assertEqual(old['state'],'resolved')
        self.c.close()
        restarted, adapter=make_fixture(self.root)
        try:
            for _ in range(2):
                rows=restarted.workspace.attention_items()
                stored=next(i for i in rows if i['id']==old['id'])
                self.assertEqual(stored,old)
                self.assertFalse(any(i['engineState']=='deciding' and i['state']=='open' for i in rows))
            self.assertEqual(adapter.calls,[])
        finally:restarted.close()
