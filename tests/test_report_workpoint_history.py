"""Report-only fixtures. No physical acceptance, controller, driver or network."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from backend.human_reports import reader_model, html_report
from scripts.export_report_snapshot import snapshot


def fixture():
    raw='a'*64
    def result(attempt, amp, phase='measure', failed=False):
        return {'dataKind':'real','sourceRunId':attempt+'/'+phase,'acquiredAt':'2026-11-26T12:00:00+00:00',
            'provenance':{'operationId':attempt+'/'+phase,'freshAcquisition':True},
            'parameters':{'readout_amp':amp,'readout_hz':6.1e9,'readout_width_s':2e-6,
                'drive_hz':4.2e9,'drive_amp':.7,'drive_length_s':4e-7,
                'amp_start':.02,'amp_stop':.98,'points':41,'capture_repeats':2},
            'quality':{'status':'failed' if failed else 'passed','reasons':['Fit reached boundary. Insufficient BIC improvement.']},
            'metrics':{'r_squared':.88,'contrast_snr':10.5},
            'analysisQuality':{'procedureId':'sq_rabi_amplitude','accepted':not failed,'raw_sha256':raw,
                'groups':[{'repeats':[
                    {'repeat':1,'fit':{'model':'sinusoid_decay_unresolved','value':3.3,'cycles_in_window':3.17},'diagnostics':{'rejection_reasons':[]}},
                    {'repeat':2,'fit':None,'diagnostics':{'rejection_reasons':['Several candidate failures']}}]}]},
            'artifacts':[{'id':'raw-'+attempt+'-'+phase,'kind':'raw','sha256':raw},
                         {'id':'image-'+attempt+'-'+phase,'kind':'image','sha256':'b'*64}]}
    old=result('old',.61,'verify');old['selectionResult']=result('old',.61)
    current=result('new',.58,failed=True)
    node={'id':'Q27:rabi','resourceId':'Q27','qubits':'Q27','procedureId':'sq_rabi_amplitude',
        'label':'幅度 Rabi','engineState':'quality_attention','latestAttemptId':'new','attempt':2,'result':current}
    attempts=[{'id':'old','nodeId':node['id'],'number':1,'state':'verified','operationId':'old/measure','verificationOperationId':'old/verify',
        'startedAt':'2026-11-26T10:00:00+00:00','endedAt':'2026-11-26T10:10:00+00:00','parameters':{'readout_amp':.99},'result':old},
        {'id':'new','nodeId':node['id'],'number':2,'state':'attention','operationId':'new/measure',
        'startedAt':'2026-11-26T12:00:00+00:00','endedAt':'2026-11-26T12:10:00+00:00','parameters':{'readout_amp':.99},'result':current}]
    return {'schema':'qcal-report/1','generatedAt':'2026-11-26T12:11:00+00:00',
        'workflow':{'id':'wf-test','target':'Q27','mode':'real','revision':4},'nodes':[{'node':node,'attempts':attempts,'decisions':[]}],
        'committedParameters':{'schema':'qcal-report-committed-parameters/1','resources':{'Q27':{'readout_amp':{'value':.58,'revision':3},'drive_length_s':{'value':3.4e-7,'revision':1}}}}}


class WorkpointHistoryTests(unittest.TestCase):
    def test_recorded_failed_rabi_and_old_readout_context_remain_distinct(self):
        path=Path(__file__).parent/'fixtures/report-rabi-workpoint.json'
        if not path.is_file():
            self.skipTest('Private local fixture not included in the source export')
        report=json.loads(path.read_text('utf-8'))
        before=deepcopy(report);model=reader_model(report)
        amplitude=next(n for n in model['nodes'] if n['label']=='幅度 Rabi')
        self.assertIn('375 ns',amplitude['acquisitionSummary']);self.assertIn('0.76',amplitude['acquisitionSummary'])
        self.assertIn('1/2',amplitude['summary']);self.assertIn('第2次',amplitude['summary'])
        self.assertEqual(model['verified'],0);self.assertFalse(amplitude['accepted'])
        self.assertTrue(all('第1次重复拟合' in m['label'] for m in amplitude['metrics']))
        iq=next(n for n in model['nodes'] if n['label']=='读出状态区分')
        self.assertIn('0.8',iq['acquisitionSummary']);self.assertIn('需复测',iq['status'])
        ro=next(n for n in model['nodes'] if n['label']=='读出参数优化')
        self.assertIn('首测',ro['acquisitionSummary']);self.assertIn('扫描',ro['acquisitionSummary'])
        self.assertIn('独立复测',ro['acquisitionSummary']);self.assertIn('0.76',ro['acquisitionSummary'])
        self.assertTrue(all(r['parameterSource']=='result.parameters' for r in model['acquisitionHistory']))
        self.assertEqual(report,before)

    def test_actual_stage_parameters_current_ledger_and_history_are_distinct(self):
        report=fixture();before=deepcopy(report);model=reader_model(report)
        self.assertIn('0.58',model['workpoints'][0]['summary'])
        self.assertIn('340 ns',model['workpoints'][0]['summary'])
        self.assertIn('未通过',model['workpoints'][0]['qualificationSummary'])
        self.assertEqual(len(model['acquisitionHistory']),3)
        rows=model['acquisitionHistory'];self.assertIn('0.61',rows[0]['workpoint'])
        self.assertIn('0.58',rows[-1]['workpoint']);self.assertIn('400 ns',rows[-1]['workpoint'])
        self.assertNotIn('0.99',str(rows));self.assertIn('驱动幅度扫描',rows[-1]['workpoint'])
        self.assertEqual(rows[-1]['operationId'],'new/measure');self.assertEqual(rows[-1]['attemptId'],'new')
        self.assertIn('历史',rows[1]['status']);self.assertEqual(model['verified'],0);self.assertEqual(report,before)

    def test_failed_capture_summary_does_not_attach_union_reasons_to_selected_fit(self):
        row=reader_model(fixture())['nodes'][0]
        self.assertEqual(row['rabiFitEvidence']['captures'][0]['valid'],True)
        self.assertIn('1/2',row['summary']);self.assertIn('第2次',row['summary'])
        self.assertNotIn('BIC',row['summary']);self.assertNotIn('置信区间过宽',row['summary'])
        self.assertTrue(all('第1次重复拟合' in m['label'] for m in row['metrics']))
        self.assertLess(len(row['presentationSummary']),130)

    def test_cross_attempt_missing_parameters_or_timestamp_never_substitutes_draft(self):
        for change in ('source','parameters','time','node','operation'):
            report=fixture();r=report['nodes'][0]['attempts'][1]['result']
            if change=='source':r['sourceRunId']='old/measure'
            elif change=='parameters':r.pop('parameters')
            elif change=='time':r['acquiredAt']='yesterday'
            elif change=='node':report['nodes'][0]['attempts'][1]['nodeId']='another-node'
            else:report['nodes'][0]['attempts'][1]['operationId']='another-operation'
            row=reader_model(report)['acquisitionHistory'][-1]
            self.assertEqual(row['workpoint'],'采集参数来源不足，未推断工作点')
            self.assertNotIn('0.99',row['workpoint'])

    def test_legacy_snapshot_and_partial_or_replay_keep_honest_fallback(self):
        report=fixture();report.pop('committedParameters');report['workflow']['mode']='replay'
        report['nodes'][0]['node']['result']['partial']=True
        model=reader_model(report)
        self.assertEqual(model['workpoints'],[]);self.assertEqual(model['verified'],0)
        self.assertNotIn('rabiFitEvidence',model['nodes'][0])
        self.assertTrue(all('历史' in r['status'] for r in model['acquisitionHistory']))

    def test_resources_do_not_share_committed_values_or_currentness(self):
        report=fixture();report['committedParameters']['resources']['Q18']={'readout_amp':{'value':.123,'revision':1}}
        model=reader_model(report);by_resource={r['resourceId']:r['summary'] for r in model['workpoints']}
        self.assertIn('0.58',by_resource['Q27']);self.assertNotIn('0.123',by_resource['Q27'])

    def test_time_rabi_and_readout_scan_do_not_display_unused_base_as_fixed_value(self):
        report=fixture();item=report['nodes'][0];item['node']['procedureId']='sq_rabi_time'
        for a in item['attempts']:
            for r in (a['result'],a['result'].get('selectionResult',{})):
                if r:r['parameters'].update(start_s=0,stop_s=1.9e-6)
        m=reader_model(report);self.assertIn('门长扫描',m['acquisitionHistory'][-1]['workpoint']);self.assertNotIn('400 ns',m['acquisitionHistory'][-1]['workpoint'])
        item['node']['procedureId']='ro_optimize'
        item['attempts'][-1]['result']['parameters']['amplitudes']=[.2,.5,.7]
        m=reader_model(report);self.assertIn('读出幅度扫描',m['acquisitionHistory'][-1]['workpoint'])
        self.assertNotIn('读出幅度 0.58',m['acquisitionHistory'][-1]['workpoint'])

    def test_html_has_same_context_history_and_escaped_fields(self):
        class Controller:
            root=Path(__file__).resolve().parents[1]
            def artifact(self,_):raise ValueError('TEST_ONLY absent image')
        report=fixture();report['nodes'][0]['node']['qubits']='<script>resource</script>'
        text=html_report(Controller(),report).decode()
        self.assertIn('已提交参数快照',text);self.assertIn('采集轮次与工作点',text)
        self.assertNotIn('<script>',text);self.assertIn('0.58',text);self.assertIn('1/2',text)

    def test_explicit_axes_precede_unused_default_endpoints(self):
        report=fixture();item=report['nodes'][0]
        p=item['attempts'][-1]['result']['parameters']
        p['amplitudes']=[.21,.32,.43]
        row=reader_model(report)['acquisitionHistory'][-1]
        self.assertIn('0.21–0.43',row['workpoint']);self.assertNotIn('0.02–0.98',row['workpoint'])
        item['node']['procedureId']='sq_spectroscopy';p['frequencies_hz']=[4.5e9,4.6e9]
        row=reader_model(report)['acquisitionHistory'][-1]
        self.assertIn('4.5–4.6 GHz',row['workpoint']);self.assertNotIn('中心 4.2',row['workpoint'])
        p.pop('frequencies_hz');p.update(start_hz=4.4e9,stop_hz=4.7e9)
        self.assertIn('4.4–4.7 GHz',reader_model(report)['acquisitionHistory'][-1]['workpoint'])


class LedgerSnapshotTests(unittest.TestCase):
    def test_scoped_ledger_is_frozen_read_only_and_no_other_workflow_leaks(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);db=root/'test.sqlite'
            with sqlite3.connect(db) as c:
                c.executescript('CREATE TABLE platform_workflows(id TEXT,payload TEXT); CREATE TABLE platform_attempts(workflow_id TEXT,node_id TEXT,number INTEGER,payload TEXT); CREATE TABLE platform_decisions(workflow_id TEXT,node_id TEXT,payload TEXT); CREATE TABLE platform_artifacts(id TEXT,path TEXT,sha256 TEXT,payload TEXT); CREATE TABLE parameters(name TEXT,value TEXT,revision INTEGER);')
                c.execute('INSERT INTO platform_workflows VALUES (?,?)',('wf',json.dumps({'id':'wf','nodes':[{'id':'n','resourceId':'Q27'}]})))
                c.executemany('INSERT INTO parameters VALUES (?,?,?)',[('wf:Q27:readout_amp','0.58',3),('other:Q27:readout_amp','0.99',5),('wf:Q27:drive_amp','true',2)])
            c.close()
            before=db.read_bytes();result=snapshot(db,'wf',root)
            self.assertEqual(result['committedParameters']['resources'],{'Q27':{'readout_amp':{'value':.58,'revision':3}}})
            self.assertEqual(db.read_bytes(),before)


if __name__=='__main__':unittest.main()
