"""Normal controller decision entry; TEST_ONLY native archive fixture, no hardware."""
from copy import deepcopy
from unittest.mock import Mock
from backend.common import APIError,digest
from backend import model_recovery,smart_rabi_window
from tests.test_smart_rabi_window import SmartRabiWindow


class RabiWindowEcho(SmartRabiWindow):
    def test_unchanged_width_echo_uses_ordinary_retry_without_window_authorization(self):
        with self.c.store.connect() as rt:
            w=self.c.get(self.wid,rt);n=self.c._node(w,self.nid);p,_=self.c._parameters(rt,w,n)
        decision={'action':'retry','reason':'TEST_ONLY ordinary sampling adjustment','evidenceIds':[],
                  'parameterChanges':{'drive_length_s':p['drive_length_s'],'shots':1000,'acquisitions_per_point':8}}
        before=len(self.calls);self.c._brain=Mock(return_value={'status':'ok','decision':decision})
        self.assertTrue(self.c._decide(self.wid,self.nid),self.c.get(self.wid).get('modelRecovery'))
        current=self.c.get(self.wid);node=self.c._node(current,self.nid)
        self.assertNotIn('smartRabiWindow',node)
        self.assertEqual(node['parameterOverrides']['shots'],1000)
        self.assertEqual(node['parameterOverrides']['drive_length_s'],p['drive_length_s'])
        self.assertEqual(len(self.calls),before)

    def proposal(self):
        with self.c.store.connect() as rt:
            p,_=self.c._parameters(rt,self.c.get(self.wid,rt),self.c._node(self.c.get(self.wid,rt),self.nid))
        return {'action':'retry','reason':'TEST_ONLY longer window, unchanged fields echoed','evidenceIds':[],
                'parameterChanges':{'drive_length_s':2e-6,'acquisitions_per_point':8,'capture_repeats':p['capture_repeats'],'points':p['points'],'shots':p['shots']}}

    def test_normal_decision_accepts_echo_without_dispatch(self):
        decision=self.proposal();before=len(self.calls)
        self.c._brain=Mock(return_value={'status':'ok','decision':decision})
        self.assertTrue(self.c._decide(self.wid,self.nid),self.c.get(self.wid).get('modelRecovery'))
        w=self.c.get(self.wid);n=self.c._node(w,self.nid)
        self.assertEqual(len(self.calls),before)
        self.assertEqual(n['smartRabiWindow']['authorization']['toDurationS'],2e-6)
        self.assertEqual(n['parameterOverrides']['points'],41)
        self.assertEqual(n['parameterOverrides']['shots'],2000)
        payload=self.c._brain.call_args.args[1]
        self.assertEqual(payload['smartRabiWindowScope']['proposalPolicy'],'fixed_dc_window_actual_changes_v2')

    def test_actual_changes_extra_fields_bool_and_stale_evidence_rejected(self):
        with self.c.store.connect() as rt:
            w=self.c.get(self.wid,rt);n=self.c._node(w,self.nid);p,v=self.c._parameters(rt,w,n)
            for delta in ({'points':81},{'capture_repeats':3},{'shots':1000},{'amp_stop':.99},
                          {'readout_amp':.4},{'drive_hz':p['drive_hz']+1e6},{'shots':True},{'unknown':None}):
                changes={**self.proposal()['parameterChanges'],**delta}
                with self.subTest(delta=delta),self.assertRaises(APIError):
                    smart_rabi_window.prepare_retry(self.c,rt,w,n,{**p,**changes},v,{'parameterChanges':changes})
            changes=self.proposal()['parameterChanges']
            for extra in ({'amp_stop':.99},{'unknown':1}):
                with self.subTest(hiddenTrialChange=extra),self.assertRaises(APIError):
                    smart_rabi_window.prepare_retry(self.c,rt,w,n,{**p,**changes,**extra},v,{'parameterChanges':changes})
            self.assertNotIn('smartRabiWindow',n)

    def test_normal_new_decision_revalidates_old_rule_rejection_preserves_history(self):
        proposal=self.proposal()
        with self.c.store.transaction() as rt:
            w=self.c.get(self.wid,rt);n=self.c._node(w,self.nid)
            value,_=model_recovery.checkpoint(self.c,rt,w,n)
            value.pop('proposalRules',None)
            old={'schema':model_recovery.SCHEMA,'checkpoint':value,'checkpointSha256':digest(value),'state':'blocked',
                 'failureCount':1,'rejectedProposals':[{'sha256':digest(model_recovery.proposal_identity(proposal)),
                     'proposal':model_recovery.proposal_identity(proposal),'errorCode':'SMART_RABI_WINDOW_EVIDENCE_REQUIRED'}]}
            w['modelRecovery']=deepcopy(old);w.update(status='attention',engineState='waiting_model')
            n['engineState']='waiting_model';self.c.store.save(rt,w,'TEST_ONLY R36 rejected checkpoint')
        w=self.c.get(self.wid)
        w=self.c.action(self.wid,{'action':'decide','nodeId':self.nid,'expectedRevision':w['revision'],'operationId':'TEST_ONLY-new-user-model-request'})
        before=len(self.calls);self.c._brain=Mock(return_value={'status':'ok','decision':deepcopy(proposal)})
        self.assertTrue(self.c._decide(self.wid,self.nid),self.c.get(self.wid).get('modelRecovery'))
        current=self.c.get(self.wid)
        self.assertEqual(len(self.calls),before)
        self.assertEqual(current['modelRecoveryHistory'][-1]['record'],old)
        self.assertEqual(current['modelRecovery']['state'],'resolved')
        self.assertEqual(self.c._brain.call_args.args[1]['modelRecovery']['rejectedProposals'],[])
        # No global bypass: rejection under the current rules still deduplicates.
        current['modelRecovery']['rejectedProposals']=deepcopy(old['rejectedProposals'])
        with self.assertRaises(APIError):model_recovery.guard_proposal(current,proposal)


def load_tests(loader,tests,pattern):
    names=[name for name in vars(RabiWindowEcho) if name.startswith('test_')]
    return loader.suiteClass(RabiWindowEcho(name) for name in names)
