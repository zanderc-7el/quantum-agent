"""Controller state-machine tests using FakeAdapter and disposable SQLite.

Fixture-labelled synthetic outcomes test authorization, not scientific source
authenticity. Native science quality/receipt contracts are tested separately.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.common import APIError, digest, now
from backend import smart_science as s
from tests.test_controller import make_fixture


class SmartScienceLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='smart-science-fixture-only-');self.addCleanup(self.tmp.cleanup)
        self.c,self.fake=make_fixture(Path(self.tmp.name));self.addCleanup(self.c.close)
        self.fake.validation_parameter_keys=lambda:tuple(self.fake.profile['parameterBounds'])
        self.fake.profile['id']='arclight-private-f8-q11-v3'
        self.c.adapter_id=self.fake.profile['id'];self.c.adapter_profile_hash=self.c._adapter_fingerprint()
        parent=self.c.create({'name':'TEST_ONLY parent','mode':'real','resourceIds':['Q11'],'policy':'autopilot','coverage':'full'})
        with self.c.store.transaction() as rt:
            parent=self.c.get(parent['id'],rt)
            for n in parent['nodes']:
                n.update(engineState='verified',latestAttemptId='fixture-'+n['id'],result={'dataKind':'real','quality':{'status':'passed'},'fixtureOnly':True})
            self.assertEqual(len(parent['nodes']),13)
            parent.update(status='running',engineState='science_running',hardwareConfirmed=True,
                smartRun={'schema':s.PARENT_SCHEMA,'adapterProfileHash':parent['adapterProfileHash'],'main':'verified','gate':'pending'})
            parent['budget']['mode']='accounting_only'
            parent['smartRun']['mainContextSha256']=s.main_context(parent)
            self.c.store.save(rt,parent,'test_only_parent')
            rt.db.execute('INSERT INTO platform_accounting_workflows VALUES(?,NULL)',(parent['id'],))
        self.parent=parent['id']

    def child(self,index=0,*,acquired=False):
        child=self.c.create({'name':'TEST_ONLY child','mode':'real','resourceIds':['Q11'],'policy':'strict'})
        node=child['nodes'][0]
        if acquired:
            child=self.c.action(child['id'],{'action':'run_step','nodeId':node['id'],'operationId':'fixture-run-'+child['id'],'expectedRevision':child['revision']})
            self.c.run_workflow(child['id']);child=self.c.get(child['id'])
            self.assertEqual(child['engineState'],'awaiting_acceptance')
        with self.c.store.transaction() as rt:
            parent=self.c.get(self.parent,rt);child=self.c.get(child['id'],rt)
            child['nodes']=child['nodes'][:1];child['nodes'][0]['procedureId']=s.METHODS[index]
            child['nodes'][0]['dependsOn']=[];child['currentNodeId']=child['nodes'][0]['id'];child['executionProfile']=s.PROFILE
            child['observedResourceBinding']={'sourceWorkflowId':self.parent,'resourceId':'Q11','sourceAdapterProfileHash':parent['adapterProfileHash']}
            child['budget']['parent']={'parentWorkflowId':self.parent,'parentSchema':s.PARENT_SCHEMA,'sourceAdapterProfileHash':parent['adapterProfileHash']}
            child['smartParent']={'schema':s.SCHEMA,'workflowId':self.parent,'procedureId':s.METHODS[index],'index':index,
                'parentAdapterProfileHash':parent['adapterProfileHash'],'mainContextSha256':s.main_context(parent),'frozenParameters':{}}
            parent['smartRun'].setdefault('children',{})[s.METHODS[index]]=child['id']
            self.c.store.save(rt,child,'test_only_child');self.c.store.save(rt,parent,'test_only_link')
        return self.c.get(child['id'])

    def test_exact_parent_link_current_main_and_stopped_authority(self):
        child=self.child();self.assertEqual(s.assert_authorized(self.c,child)['id'],self.parent)
        for key,value in [('workflowId','foreign'),('index',True),('parentAdapterProfileHash','foreign'),('mainContextSha256','foreign')]:
            bad=deepcopy(child);bad['smartParent'][key]=value
            with self.subTest(key=key),self.assertRaises(APIError):s.assert_authorized(self.c,bad)
        with self.c.store.transaction() as rt:
            p=self.c.get(self.parent,rt);p['status']='paused';self.c.store.save(rt,p,'test_stop')
        self.assertTrue(self.c._cancel_requested(child['id']))
        with self.assertRaises(APIError):self.c.action(child['id'],{'action':'run_step','nodeId':child['currentNodeId'],'operationId':'unauthorized','expectedRevision':child['revision']})
        self.assertEqual(self.fake.calls,[])

    def test_agent_acceptance_reuses_independent_gate_and_never_writes_human_acceptance(self):
        child=self.child(acquired=True);original=len(self.fake.calls)
        accepted=s._accept(self.c,child)
        node=accepted['nodes'][0]
        self.assertEqual(node['engineState'],'verified');self.assertEqual(node['agentAcceptedByParentId'],self.parent)
        self.assertNotIn('humanAcceptedAt',node);self.assertNotIn('humanAcceptedAttemptId',node)
        with self.c.store.connect() as rt:
            attempt=json.loads(rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?',(node['latestAttemptId'],)).fetchone()[0])
            self.assertNotIn('humanAcceptedAt',attempt);self.assertEqual(attempt['agentAcceptedByParentId'],self.parent)
        s._accept(self.c,self.c.get(child['id']));self.assertEqual(len(self.fake.calls),original)

    def test_failed_missing_or_changed_evidence_cannot_agent_accept(self):
        child=self.child(acquired=True)
        with self.c.store.transaction() as rt:
            current=self.c.get(child['id'],rt);current['nodes'][0]['result']['quality']['status']='failed'
            self.c.store.save(rt,current,'test_failed')
        with self.assertRaises(APIError):s._accept(self.c,self.c.get(child['id']))
        self.assertNotIn('agentAcceptedAt',self.c.get(child['id'])['nodes'][0])

    def test_parent_stop_propagates_and_no_restart_of_unknown_effect(self):
        child=self.child()
        p=self.c.get(self.parent)
        self.c.action(self.parent,{'action':'abort','operationId':'stop-parent','expectedRevision':p['revision']})
        s.synchronize_stop(self.c,self.parent)
        self.assertEqual(self.c.get(self.parent)['status'],'aborted')
        self.assertEqual(self.c.get(child['id'])['status'],'aborted')
        s.tick(self.c);self.assertEqual(self.fake.calls,[])

    def test_queued_retry_after_restart_gets_one_fresh_normal_permit(self):
        child=self.child()
        with self.c.store.transaction() as rt:
            child=self.c.get(child['id'],rt);child.update(status='queued',engineState='queued')
            child['nodes'][0]['engineState']='retry_queued'
            self.c.store.save(rt,child,'test_validated_model_retry')
        # This test checks permission issuance; no plan/executor is dispatched.
        s.advance(self.c,self.parent)
        current=self.c.get(child['id']);self.assertEqual(current['manualStep']['state'],'queued')
        operation=current['manualStep']['operationId'];revision=current['revision']
        s.advance(self.c,self.parent)
        self.assertEqual(self.c.get(child['id'])['revision'],revision)
        self.assertEqual(self.c.get(child['id'])['manualStep']['operationId'],operation)
        self.assertEqual(self.fake.calls,[])

    def test_irb_archive_and_report_enqueue_are_atomic_and_only_once(self):
        child=self.child(3,acquired=True);s._accept(self.c,child)
        with self.c.store.transaction() as rt:
            p=self.c.get(self.parent,rt);p['smartRun']['scienceRecords']={m:'fixture-record-'+m for m in s.METHODS[:3]}
            self.c.store.save(rt,p,'test_previous_stages')
        record={'id':'fixture-irb','kind':'measurement','dataKind':'real','evidenceCurrent':True,'evidenceIntact':True,
            'provenance':{'independentlyVerified':True},'result':{'procedureId':'sq_drag_irb','quality':{'status':'passed'},
            'metrics':{'gate_fidelity':.99},'gateCharacterization':{'reportable':True,'irb':{'reportable':True}},'targetPulseIdentity':{'sha256':'f'*64}}}
        with patch('backend.smart_science._archive',return_value=record):
            s.advance(self.c,self.parent);s.advance(self.c,self.parent)
        p=self.c.get(self.parent);self.assertEqual((p['engineState'],p['smartRun']['gate']),('report_pending','verified'))
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_smart_reports').fetchone()[0],1)

    def test_no_opted_in_workflow_tick_does_not_decode_ordinary_archive(self):
        with self.c.store.transaction() as rt:rt.db.execute('DELETE FROM platform_accounting_workflows')
        with (patch.object(self.c.store,'connect',side_effect=AssertionError('No Runtime')),
                patch.object(self.c,'list',side_effect=AssertionError('No archive decode'))):
            s.tick(self.c)

    def test_coordinator_restart_preserves_only_complete_clean_main_receipts(self):
        with self.c.store.transaction() as rt:
            p=self.c.get(self.parent,rt)
            for n in p['nodes']:
                attempt={'id':n['latestAttemptId'],'nodeId':n['id'],'number':1,'state':'verified',
                    'result':deepcopy(n['result']),'operationId':n['latestAttemptId']+'/measure',
                    'verificationOperationId':n['latestAttemptId']+'/verify'}
                self.c.store.attempt(rt,p['id'],attempt)
                for operation in (attempt['operationId'],attempt['verificationOperationId']):
                    rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)',(operation,p['id'],attempt['id'],'completed','{}','{}'))
            self.assertTrue(s.recover_parent(self.c,rt,p))
        self.c._recover_interrupted()
        self.assertEqual(self.c.get(self.parent)['engineState'],'science_running')
        with self.c.store.transaction() as rt:
            rt.db.execute("UPDATE platform_effects SET state='unknown' WHERE operation_id=(SELECT operation_id FROM platform_effects LIMIT 1)")
        self.c._recover_interrupted()
        self.assertEqual(self.c.get(self.parent)['engineState'],'needs_reconciliation')
        self.assertEqual(self.fake.calls,[])

    def test_model_wait_is_not_repeated_and_wrong_pulse_proposal_never_queues(self):
        child=self.child()
        with self.c.store.transaction() as rt:
            child=self.c.get(child['id'],rt);child.update(status='attention',engineState='quality_attention')
            child['nodes'][0].update(attempt=1,latestAttemptId='TEST_ONLY-failed',engineState='quality_attention',
                result={'dataKind':'real','acquiredAt':now(),'quality':{'status':'failed'},'artifacts':[]})
            self.c.store.save(rt,child,'test_only_failure')
        calls=[]
        def decide(path,payload):
            calls.append(payload)
            return {'status':'ok','decision':{'action':'wait','reason':'TEST_ONLY no trustworthy correction','evidenceIds':[]}}
        self.c._brain=decide
        s.advance(self.c,self.parent);s.advance(self.c,self.parent)
        self.assertEqual(len(calls),1);self.assertEqual(self.c.get(child['id'])['engineState'],'waiting_operator')
        parent=self.c.get(self.parent)
        self.assertEqual((parent['status'],parent['engineState']),('attention','science_attention'))
        self.assertEqual(parent['smartRun']['scienceIssue']['childWorkflowId'],child['id'])
        s.synchronize_stop(self.c,self.parent)
        self.assertEqual(self.c.get(child['id'])['engineState'],'waiting_operator')
        parent=self.c.get(self.parent)
        self.c.action(self.parent,{'action':'resume','operationId':'TEST_ONLY resume resolved wait','expectedRevision':parent['revision']})
        s.advance(self.c,self.parent)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.c.get(self.parent)['engineState'],'science_attention')
        self.assertEqual(self.fake.calls,[])
        self.assertNotIn('manualStep',self.c.get(child['id']))
        self.assertNotIn('pulse_length_s',calls[0]['parameterBounds'])

    def test_blocked_model_recovery_is_visible_on_parent_but_backoff_stays_running(self):
        child=self.child()
        with self.c.store.transaction() as rt:
            child=self.c.get(child['id'],rt);child.update(status='attention',engineState='waiting_model')
            child['nodes'][0].update(attempt=1,latestAttemptId='TEST_ONLY-failed',engineState='waiting_model',
                detail='TEST_ONLY incomplete model context',
                result={'dataKind':'real','acquiredAt':now(),'quality':{'status':'failed'},'artifacts':[]})
            child['modelRecovery']={'schema':'qcal-smart-model-recovery/1','state':'backoff','nextAttemptAt':1e20}
            self.c.store.save(rt,child,'test_only_backoff')
        with patch.object(self.c,'_decide',side_effect=AssertionError('No premature model call')):
            s.advance(self.c,self.parent)
            self.assertEqual(self.c.get(self.parent)['status'],'running')
            with self.c.store.transaction() as rt:
                child=self.c.get(child['id'],rt);child['modelRecovery']['state']='blocked'
                self.c.store.save(rt,child,'test_only_blocked')
            s.advance(self.c,self.parent);s.advance(self.c,self.parent)
        parent=self.c.get(self.parent)
        self.assertEqual((parent['status'],parent['engineState']),('attention','science_attention'))
        self.assertEqual(parent['smartRun']['scienceIssue']['code'],'SMART_CHILD_MODEL_BLOCKED')
        self.assertEqual(parent['smartRun']['scienceIssue']['childWorkflowId'],child['id'])
        self.assertEqual(self.c.get(child['id'])['modelRecovery']['state'],'blocked')
        s.synchronize_stop(self.c,self.parent)
        self.assertEqual(self.c.get(child['id'])['engineState'],'waiting_model')
        self.assertEqual(self.fake.calls,[])

    def test_array_retry_uses_existing_schema_and_keeps_scalar_and_evidence_gates(self):
        from backend.controller import Controller
        bounds={'rb_lengths':{'type':'integerArray','minItems':5,'maxItems':12,'min':1,'max':512,'strictlyIncreasing':True}}
        node={'result':{'artifacts':[]}}
        p={'action':'retry','reason':'TEST_ONLY shorter real control window','evidenceIds':[],
           'parameterChanges':{'rb_lengths':[1,2,4,8,16]}}
        self.assertEqual(s.validate_decision(self.c,{},node,p,bounds),p)
        for bad in ([1,2,2,8,16],[1,2,4,8,999],[True,2,4,8,16]):
            with self.subTest(bad=bad),self.assertRaises(APIError):s.validate_decision(self.c,{},node,{**p,'parameterChanges':{'rb_lengths':bad}},bounds)
        with self.assertRaises(APIError):s.validate_decision(self.c,{},node,{**p,'evidenceIds':['foreign']},bounds)

    def test_new_main_backtrack_clears_agent_flags_but_keeps_attempt_history(self):
        child=self.child(acquired=True);accepted=s._accept(self.c,child)
        self.c._invalidate(accepted,accepted['currentNodeId'],include_target=True)
        self.assertNotIn('agentAcceptedAt',accepted['nodes'][0])
        with self.c.store.connect() as rt:
            attempt=json.loads(rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?',(accepted['nodes'][0]['latestAttemptId'],)).fetchone()[0])
            self.assertIn('agentAcceptedAt',attempt)


if __name__=='__main__':unittest.main()
