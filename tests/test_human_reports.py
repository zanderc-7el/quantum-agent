import copy
import json
from pathlib import Path
import unittest

from backend.human_reports import reader_model, writing_audit, html_report

ROOT = Path(__file__).resolve().parents[1]

def fixture():
    result = {'dataKind':'real','provenance':{'freshAcquisition':True},'quality':{'status':'failed','reasons':['参考漂移超过10%']},
              'metrics':{'gate_fidelity':.945,'reference_drift_fraction':.269},'artifacts':[]}
    node={'id':'Q18:rb','label':'单比特 RB','qubits':'Q18','resourceId':'Q18','procedureId':'sq_rb','engineState':'quality_attention','latestAttemptId':'a1','result':result}
    attempt={'id':'a1','nodeId':node['id'],'number':1,'result':copy.deepcopy(result),'state':'failed'}
    return {'schema':'qcal-report/1','workflow':{'id':'wf-test','name':'实验','target':'Q18','machine':'八号机','revision':8,'mode':'real'},'generatedAt':'2026-11-19T00:00:00Z','nodes':[{'node':node,'attempts':[attempt],'decisions':[{'action':'retry','reason':'缩短时间以复核漂移'}]}]}

class ReportsTest(unittest.TestCase):
    def test_failed_rb_never_accepted(self):
        model=reader_model(fixture())
        self.assertEqual(model['verified'],0)
        self.assertEqual(model['nodes'][0]['status'],'未通过')
        self.assertIn('未验收候选', next(m for m in model['nodes'][0]['metrics'] if m['key']=='gate_fidelity')['label'])
        self.assertIn('尚未通过验收',model['nodes'][0]['summary'])

    def test_stale_and_replay_never_count_current_pass(self):
        for stale,replay in [(True,False),(False,True)]:
            report=fixture();n=report['nodes'][0]['node'];n['engineState']='stale' if stale else 'verified';n['result']['quality']['status']='passed'
            if replay:report['workflow']['mode']='replay'
            self.assertEqual(reader_model(report)['verified'],0)

    def test_current_stage_not_old_attempt(self):
        report=fixture();report['nodes'][0]['attempts'][0]['id']='old';report['nodes'][0]['attempts'][0]['result']['artifacts']=[{'id':'old-figure','kind':'image'}]
        model=reader_model(report)
        self.assertEqual(model['nodes'][0]['figures'],[])
        self.assertEqual(model['nodes'][0]['history'][0]['stages'][0]['figures'][0]['artifactId'],'old-figure')

    def test_technical_json_is_inside_details(self):
        class C:root=ROOT
        report=fixture();report['nodes'][0]['node']['label']='<script>alert(1)</script>'
        output=html_report(C(),report).decode()
        self.assertNotIn('<script>',output)
        self.assertIn('<summary>技术记录与数据来源（JSON）</summary><pre>',output)
        self.assertNotIn('<pre>',output.split('<details>')[0])
        self.assertIn('单门保真度（未验收候选）',output)

    def test_skill_two_pass_and_no_fake_human_acceptance(self):
        audit=writing_audit(reader_model(fixture()),ROOT/'knowledge/report-writing/defensive-language-audit')
        self.assertTrue(audit['policyApplied']);self.assertFalse(audit['humanAccepted'])
        self.assertEqual(audit['passA'][0]['sentence'],audit['passB']['text'][0])
        self.assertTrue(audit['claimLedger']);self.assertTrue(audit['retainedLimitations'])

if __name__=='__main__':unittest.main()
