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

    def test_unmeasured_invalidated_node_is_waiting_not_retest(self):
        report=fixture(); item=report['nodes'][0]
        item['node'].update(engineState='stale',attempt=0)
        item['node'].pop('result'); item['node'].pop('latestAttemptId')
        item['attempts']=[]
        self.assertEqual(reader_model(report)['nodes'][0]['status'],'待测（前置参数已更新）')
        item['attempts']=[{'id':'failed-dispatch','number':1,'state':'failed'}]
        self.assertEqual(reader_model(report)['nodes'][0]['status'],'前置参数已变化，需复测')

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

    def test_unmeasured_html_sections_fold_without_hiding_failed_attempts(self):
        class C:root=ROOT
        report=fixture()
        waiting={'node':{'id':'Q18:waiting','label':'待采集测项','qubits':'Q18','attempt':0,'engineState':'stale'},
                 'attempts':[], 'decisions':[{'action':'wait','reason':'前置参数更新'}]}
        report['nodes'].append(waiting)
        output=html_report(C(),report).decode()
        before, folded=output.split('<details><summary>未采集项目（1项）</summary>',1)
        self.assertIn('<td>待采集测项</td>',before)
        self.assertIn('<h2>Q18 · 单比特 RB</h2>',before)
        self.assertNotIn('<h2>Q18 · 待采集测项</h2>',before)
        self.assertIn('<h2>Q18 · 待采集测项</h2>',folded)
        self.assertIn('前置参数更新',folded)
        self.assertIn('技术记录与数据来源',folded)



class FailedAttemptPresentationTests(unittest.TestCase):
 def snapshot(self):
  path=ROOT/'tests/fixtures/failed-attempt-report-snapshot.json'
  if not path.is_file(): self.skipTest('Private local fixture not included in the source export')
  return json.loads(path.read_text(encoding='utf-8'))
 def test_actual_failure_shows_bound_attempt_and_scan_count_while_all_old_figures_survive(self):
  report=self.snapshot();before=copy.deepcopy(report);model=reader_model(report)
  row=next(n for n in model['nodes'] if n['id']=='Q11:s02_qubit_frequency')
  self.assertIn('第2次尝试',row['summary']);self.assertIn('0/324',row['summary']);self.assertIn('设备连接失败',row['summary'])
  self.assertEqual(row['figures'],[])
  self.assertEqual([f['artifactId'] for h in row['history'] if not h['current'] for s in h['stages'] for f in s['figures']],['real-c185e0ecab12103d4398d786'])
  self.assertEqual(sum(len(s['figures']) for n in model['nodes'] for a in n['history'] for s in a['stages']),9)
  class C:root=ROOT
  output=html_report(C(),report).decode();self.assertIn(row['summary'],output);self.assertEqual(report,before)
 def test_older_attempt_telemetry_cannot_supply_current_attempt_total(self):
  report=self.snapshot();node=next(n['node'] for n in report['nodes'] if n['node']['id']=='Q11:s02_qubit_frequency');node['telemetry']['controllerAttemptId']='older-attempt'
  row=next(n for n in reader_model(report)['nodes'] if n['id']==node['id'])
  self.assertIn('第2次尝试',row['summary']);self.assertNotIn('0/324',row['summary']);self.assertIn('已采集0点',row['summary'])

 def test_multi_job_current_row_total_is_not_presented_as_attempt_total(self):
  report=self.snapshot();node=next(n['node'] for n in report['nodes'] if n['node']['id']=='Q11:s02_qubit_frequency');node['telemetry']['jobCount']=2
  row=next(n for n in reader_model(report)['nodes'] if n['id']==node['id'])
  self.assertNotIn('0/324',row['summary']);self.assertIn('已采集0点',row['summary'])
 def test_missing_diagnostic_artifacts_do_not_produce_a_file_retention_claim(self):
  report=self.snapshot();report['stoppedReceipts'][0]['receipt']['partialArtifacts']=[]
  row=next(n for n in reader_model(report)['nodes'] if n['id']=='Q11:s02_qubit_frequency')
  self.assertNotIn('日志与诊断记录已保留',row['summary']);self.assertIn('退出回执已保留',row['summary'])

if __name__=='__main__':unittest.main()
