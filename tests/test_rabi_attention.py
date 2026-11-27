from copy import deepcopy
import unittest

from backend.rabi_attention import rabi_fit_evidence
from pathlib import Path
import tempfile
from tests.test_controller import make_fixture


class RabiAttention(unittest.TestCase):
    def fixture(self):
        raw='a'*64
        repeats=[{'repeat':1,'fit':{'model':'damped_sinusoid','cycles_in_window':3.21,'value':3.6},'diagnostics':{'rejection_reasons':[]}},
                 {'repeat':2,'fit':None,'diagnostics':{'rejection_reasons':['BIC candidate failed','amplitude candidate failed']}}]
        return {'procedureId':'sq_rabi_amplitude','latestAttemptId':'a',
                'result':{'sourceRunId':'a/measure','dataKind':'real','parameters':{'capture_repeats':2},'quality':{'status':'failed'},
                'analysisQuality':{'procedureId':'sq_rabi_amplitude','accepted':False,'raw_sha256':raw,'groups':[{'repeats':repeats}]},
                'artifacts':[{'kind':'raw','sha256':raw}]}}

    def test_valid_and_missing_fit_count_only_archived_selected_values(self):
        node=self.fixture()
        prior=deepcopy(node)
        self.assertEqual(rabi_fit_evidence(node),{'schema':'qcal-rabi-fit-display/1','stage':'measure','requiredValid':2,
            'captures':[{'index':1,'valid':True,'cycles':3.21},{'index':2,'valid':False}]})
        self.assertEqual(node,prior)
        node['result']['sourceRunId']='a/verify'
        self.assertEqual(rabi_fit_evidence(node)['stage'],'verify')
        node['result']['analysisQuality']['groups'][0]['repeats'][0].update(fit=None,diagnostics={'rejection_reasons':['candidate failure']})
        self.assertEqual(sum(r['valid'] for r in rabi_fit_evidence(node)['captures']),0)

    def test_missing_malformed_or_cross_attempt_evidence_yields_no_counts(self):
        def altered(path,value):
            node=self.fixture()
            dest=node
            for key in path[:-1]:dest=dest[key]
            dest[path[-1]]=value
            return node
        for path,value in [
            (('latestAttemptId',),'old'),(('result','stale'),True),(('result','partial'),True),
            (('result','dataKind'),'replay'),(('result','parameters'),None),(('result','quality'),'corrupt'),
            (('result','artifacts'),None),(('result','analysisQuality','raw_sha256'),'b'*64),
            (('result','analysisQuality','procedureId'),'sq_t1'),(('result','analysisQuality','groups'),[]),
            (('result','analysisQuality','groups',0,'repeats',0,'repeat'),2),
            (('result','analysisQuality','groups',0,'repeats',0,'fit','cycles_in_window'),float('nan')),
            (('result','analysisQuality','groups',0,'repeats',1,'diagnostics'),{})]:
            with self.subTest(path=path):self.assertIsNone(rabi_fit_evidence(altered(path,value)))

    def test_attention_item_projects_only_current_quality_failure_and_preserves_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='r27-fit-attention-') as folder:
            controller,_=make_fixture(Path(folder))
            try:
                wf=controller.create({'name':'TEST_ONLY failed Rabi display','mode':'real','policy':'autopilot','resourceIds':['Q11']})
                with controller.store.transaction() as rt:
                    current=controller.get(wf['id'],rt)
                    node=next(n for n in current['nodes'] if n['procedureId']=='sq_rabi_amplitude')
                    node.update(self.fixture(),status='attention',engineState='quality_attention',detail='Native fit rejected')
                    current.update(status='attention',engineState='quality_attention',currentNodeId=node['id'])
                    controller.store.save(rt,current,'TEST_ONLY failure')
                item=controller.workspace.attention_items()[0]
                self.assertEqual(item['rabiFitEvidence'],rabi_fit_evidence(node))
                self.assertEqual(item['message'],'Native fit rejected')
                with controller.store.transaction() as rt:
                    current=controller.get(wf['id'],rt);current['engineState']='needs_reconciliation'
                    controller._node(current).update(engineState='needs_reconciliation',detail='Cleanup outcome unknown')
                    controller.store.save(rt,current,'TEST_ONLY unknown cleanup')
                item=next(i for i in controller.workspace.attention_items() if i['state']=='open')
                self.assertNotIn('rabiFitEvidence',item);self.assertEqual(item['message'],'Cleanup outcome unknown')
            finally:controller.close()
