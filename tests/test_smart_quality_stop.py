"""Temporary Q11 records and static plan doubles. No instrument is opened."""
import unittest
from unittest.mock import patch
from copy import deepcopy

from backend import model_recovery
from backend.common import APIError, dumps
try:
    from test_smart_model_recovery import RecoveryContracts as _Base
except ImportError:
    from tests.test_smart_model_recovery import RecoveryContracts as _Base

class QualityStop(unittest.TestCase):
    setUp = _Base.setUp
    tearDown = _Base.tearDown

    def archived(self, *, accounting=True, shots=1000):
        with self.c.store.transaction() as rt:
            w=self.c.get(self.wid,rt)
            if accounting:
                w['budget']={'mode':'accounting_only'}
                w.update(maxAttemptsPerNode=None,maxWorkflowAttempts=None,maxTotalShots=None)
            n=self.c._node(w,self.nid)
            n['result']['parameters']['shots']=shots
            n['parameterOverrides']['shots']=shots
            self.c.store.save(rt,w,'TEST_ONLY_archive')
            rt.db.execute('INSERT INTO platform_attempts VALUES (?,?,?,?,?)',
                          ('TEST_ONLY-source',self.wid,self.nid,1,dumps({'result':n['result']})))
            rt.db.execute('INSERT INTO platform_effects VALUES (?,?,?,?,?,?)',
                          ('TEST_ONLY-source/measure',self.wid,'TEST_ONLY-source','completed',
                           dumps({'runId':self.wid,'nodeId':self.nid,'attemptId':'TEST_ONLY-source',
                                  'operationId':'TEST_ONLY-source/measure','parameters':{'shots':shots}}),
                           dumps(n['result'])))

    def setup_brain(self, second='wait'):
        def reply(path,payload):
            self.payloads.append(deepcopy(payload))
            action='wait' if len(self.payloads)==1 else second
            decision={'action':action,'reason':'TEST_ONLY model judgment '+str(len(self.payloads)),'evidenceIds':[]}
            if action=='retry':
                decision['parameterChanges']={'shots':2000}
            return {'status':'ok','decision':decision}
        self.c._brain=reply

    def test_first_wait_has_compiled_untried_suggestion_second_wait_is_final(self):
        self.archived()
        self.setup_brain()
        with patch.object(self.c,'_validate_plan',return_value={'reservation':False,'requiredShots':4000}):
            self.assertFalse(self.c._decide(self.wid,self.nid))
            record=self.c.get(self.wid)['modelRecovery']
            self.assertEqual(record['state'],'backoff')
            self.assertEqual(record['qualityStopReplan']['alternative']['parameterChanges'],{'shots':2000})
            self.assertEqual(model_recovery.recover_due(self.c,at=record['nextAttemptAt']+1),1)
            self.assertFalse(self.c._decide(self.wid,self.nid))
        w=self.c.get(self.wid)
        self.assertEqual(w['engineState'],'waiting_operator')
        self.assertEqual(self.payloads[1]['modelRecovery']['qualityStopReplan']['alternative']['sourceOperationCount'],1)
        self.assertIn('TEST_ONLY model judgment 2',self.c._node(w,self.nid)['detail'])
        self.assertEqual(w['modelRecovery']['state'],'resolved')
        att,history=self.c.store.detail(self.wid,self.nid)
        final=next(d for d in history if d['action']=='wait')
        self.assertEqual(final['source'],'model')
        self.assertEqual(final['qualityStopReview']['firstAction'],'wait')
        self.assertEqual(len(att),1)

    def test_model_can_take_disclosed_retry_without_creating_any_effect(self):
        self.archived()
        self.setup_brain('retry')
        with patch.object(self.c,'_validate_plan',return_value={'reservation':False}):
            self.assertFalse(self.c._decide(self.wid,self.nid))
            record=self.c.get(self.wid)['modelRecovery']
            self.assertEqual(model_recovery.recover_due(self.c,at=record['nextAttemptAt']+1),1)
            self.assertTrue(self.c._decide(self.wid,self.nid))
        w=self.c.get(self.wid)
        self.assertEqual(self.c._node(w,self.nid)['parameterOverrides']['shots'],2000)
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],1)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],0)

    def test_native_rejection_preserves_wait(self):
        self.archived()
        self.setup_brain()
        with patch.object(self.c,'_validate_plan',side_effect=APIError('PULSE_IDENTITY_MISMATCH','fixture hard boundary')):
            self.assertFalse(self.c._decide(self.wid,self.nid))
        self.assertEqual(self.c.get(self.wid)['engineState'],'waiting_operator')

    def test_finite_task_has_no_challenge(self):
        self.archived(accounting=False)
        self.setup_brain()
        with patch.object(self.c,'_validate_plan') as checked:
            self.assertFalse(self.c._decide(self.wid,self.nid))
            checked.assert_not_called()
        self.assertEqual(self.c.get(self.wid)['engineState'],'waiting_operator')

    def test_hard_cap_is_respected(self):
        self.archived(shots=2000)
        self.setup_brain()
        with patch.object(self.c,'_validate_plan') as checked:
            self.assertFalse(self.c._decide(self.wid,self.nid))
            checked.assert_not_called()
        self.assertEqual(self.c.get(self.wid)['engineState'],'waiting_operator')

    def test_nearest_eligible_dependency_precedes_earlier_readout_steps(self):
        from backend.smart_quality_stop import _nearest_predecessors
        nodes=[{'id':'survey','dependsOn':[]},{'id':'s21','dependsOn':['survey']},
               {'id':'f01','dependsOn':['s21']},{'id':'rabi','dependsOn':['f01']}]
        wf={'nodes':nodes}
        self.assertEqual([n['id'] for n in _nearest_predecessors(wf,nodes[-1],['survey','f01','s21'])],
                         ['f01','s21','survey'])

    def test_versioned_block_suggestion_must_pass_scoped_authorization_without_committing(self):
        from backend.smart_quality_stop import alternative
        self.archived()
        with self.c.store.transaction() as rt:
            wf=self.c.get(self.wid,rt)
            node=self.c._node(wf,self.nid)
            node['result']['parameters']['acquisitions_per_point']=8
            rt.db.execute("UPDATE platform_effects SET result=json_set(result,'$.parameters.acquisitions_per_point',8)")
            scoped=unittest.mock.Mock()
            scoped._parameter_schema.return_value={'acquisitions_per_point':{'min':1,'max':16}}
            scoped._validate_plan.return_value={'reservation':False,'selectionShots':2624000,'verificationShots':2624000}
            with patch.object(self.c,'_parameter_schema',return_value={'acquisitions_per_point':{'min':1,'max':8}}), \
                 patch('backend.smart_sampling.eligible',return_value=True), \
                 patch('backend.smart_sampling.authorize_retry',return_value=scoped) as versioned, \
                 patch('backend.smart_sampling.commit_retry',side_effect=AssertionError('plan only')):
                view=alternative(self.c,rt,wf,node,{'acquisitions_per_point':{'min':1,'max':16}},[])
            self.assertEqual(view['parameterChanges'],{'acquisitions_per_point':16})
            versioned.assert_called_once()
            scoped._validate_plan.assert_called_once()
            self.assertNotIn('nextExecutionAuthorizationId',node)

    def test_observed_setting_is_not_suggested_again(self):
        self.archived()
        self.setup_brain()
        with self.c.store.transaction() as rt:
            w=self.c.get(self.wid,rt)
            n=self.c._node(w,self.nid)
            n['attempt']=2
            self.c.store.save(rt,w,'TEST_ONLY_second_count')
            rt.db.execute('UPDATE platform_attempts SET number=2 WHERE id=?',('TEST_ONLY-source',))
            rt.db.execute('INSERT INTO platform_attempts VALUES(?,?,?,?,?)',('test-earlier',self.wid,self.nid,1,dumps({})))
            rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)',('temporal-first',self.wid,'test-earlier','completed',
                dumps({'runId':self.wid,'nodeId':self.nid,'attemptId':'test-earlier','operationId':'temporal-first'}),
                dumps({'dataKind':'real','parameters':{'shots':2000}})))
        with patch.object(self.c,'_validate_plan') as checked:
            self.assertFalse(self.c._decide(self.wid,self.nid))
            checked.assert_not_called()
        self.assertEqual(self.c.get(self.wid)['engineState'],'waiting_operator')

    def test_model_stop_on_review_is_retained_as_model_decision(self):
        self.archived()
        self.setup_brain('stop')
        with patch.object(self.c,'_validate_plan',return_value={'reservation':False}):
            self.assertFalse(self.c._decide(self.wid,self.nid))
            rec=self.c.get(self.wid)['modelRecovery']
            self.assertEqual(model_recovery.recover_due(self.c,at=rec['nextAttemptAt']+1),1)
            self.assertFalse(self.c._decide(self.wid,self.nid))
        w=self.c.get(self.wid)
        self.assertEqual((w['status'],w['engineState']),('failed','budget_or_evidence_stop'))
        _,history=self.c.store.detail(self.wid,self.nid)
        final=next(d for d in history if d['action']=='stop')
        self.assertEqual((final['source'],final['reason']),('model','TEST_ONLY model judgment 2'))

    def test_incomplete_source_never_presents_proven_new_setting(self):
        self.archived()
        self.setup_brain()
        with self.c.store.transaction() as rt:
            rt.db.execute('UPDATE platform_effects SET result=NULL')
        with patch.object(self.c,'_validate_plan') as checked:
            self.assertFalse(self.c._decide(self.wid,self.nid))
            checked.assert_not_called()
        self.assertEqual(self.c.get(self.wid)['engineState'],'waiting_operator')

del _Base

