"""Read-only, temporary-SQLite reporting contracts; never device execution."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from copy import deepcopy

from backend.common import APIError


class StabilityReports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'fixture.sqlite'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('CREATE TABLE platform_stability(id TEXT,payload TEXT);'
                'CREATE TABLE platform_workflows(id TEXT,payload TEXT);'
                'CREATE TABLE platform_attempts(id TEXT,workflow_id TEXT,payload TEXT);')

    def tearDown(self):
        self.tmp.cleanup()

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('backend.stability_reports'),
                             'R10 has no read-only range report implementation')
        from backend import stability_reports
        return stability_reports

    def add(self, identity='s1', chip='chip-a', origin='origin-a', dates=None, mismatch=False):
        dates = dates or ['2026-11-25T16:00:00Z','2026-11-25T17:00:00Z','2026-11-25T18:00:00Z']
        schedule={'id':identity,'revision':4,'name':'<script>test</script>','state':'completed',
            'sourceWorkflowId':origin,'resourceId':'Q11','chipVersionId':chip,'deploymentVersionId':'dep-a',
            'calibrationHash':'a'*64,'rounds':[]}
        with closing(sqlite3.connect(self.path)) as db, db:
            for i,date in enumerate(dates):
                child=identity+'-wf-'+str(i); f=child+'-f'; t=child+'-t'; frequency=4.8e9+i*1e6
                row={'number':i+1,'workflowId':child,'state':'failed' if i==1 else 'completed','startedAt':date,
                    'finishedAt':date,'frequencyHz':None if i==1 else frequency,'referenceFrequencyHz':4.8e9,
                    'driftHz':None if i==1 else i*1e6,'t1Seconds':None if i==1 else 40e-6,
                    'frequencyAttemptId':None if i==1 else f,'t1AttemptId':None if i==1 else t,
                    'error':'峰不可信' if i==1 else None}
                schedule['rounds'].append(row)
                wf={'id':child,'revision':9,'mode':'real','resourceIds':['Q11'],'chipVersionId':chip,
                    'deploymentVersionId':'dep-a','stabilityBinding':{'scheduleId':identity,'round':i+1},'nodes':[]}
                for method, aid in [('sq_spectroscopy',f),('sq_t1',t)]:
                    result={'dataKind':'real','acquiredAt':date,'quality':{'status':'passed'},
                        'parameters':{'drive_hz':frequency+(1e5 if mismatch and method=='sq_t1' else 0)},
                        'candidateParameters':{'drive_hz':frequency} if method=='sq_spectroscopy' else {},
                        'metrics':{'t1_s':40e-6} if method=='sq_t1' else {'peak_snr':7},
                        'series':[{'x':[0,1e-6],'y':[.2,.1],'xUnit':'s','yUnit':'IQ a.u.'}],
                        'artifacts':[{'id':'raw-'+aid,'kind':'raw','sha256':'b'*64}],
                        'provenance':{'operationId':aid+'/verify','freshAcquisition':True}}
                    result['selectionResult']={'dataKind':'real','acquiredAt':date,
                        'candidateParameters':deepcopy(result['candidateParameters']),
                        'provenance':{'operationId':aid+'/measure','freshAcquisition':True}}
                    node={'id':method,'resourceId':'Q11','procedureId':method,'latestAttemptId':aid,'engineState':'verified','result':result}
                    wf['nodes'].append(node)
                    attempt={'id':aid,'nodeId':method,'procedureId':method,'state':'verified',
                        'operationId':aid+'/measure','verificationOperationId':aid+'/verify','result':result}
                    db.execute('INSERT INTO platform_attempts VALUES(?,?,?)',(aid,child,json.dumps(attempt)))
                db.execute('INSERT INTO platform_workflows VALUES(?,?)',(child,json.dumps(wf)))
            db.execute('INSERT INTO platform_stability VALUES(?,?)',(identity,json.dumps(schedule)))
        return schedule

    def test_dates_chip_and_gap_do_not_merge_or_invent_adjacent_frequency(self):
        m=self.module();self.add();self.add('s2',chip='chip-b',origin='other')
        report=m.snapshot(self.path,{'startDate':['2026-11-26'],'endDate':['2026-11-26'],'chipVersionId':['chip-a']})
        self.assertEqual(report['summary']['rounds'],3)
        self.assertEqual(len(report['groups']),1)
        rows=report['groups'][0]['rows']
        self.assertEqual([r['frequencyHz'] for r in rows],[4.8e9,None,4.802e9])
        self.assertEqual([r['adjacentDeltaHz'] for r in rows],[None,None,None])
        self.assertEqual(rows[2]['referenceDeltaHz'],2e6)
        self.assertEqual(rows[0]['t1Evidence']['actualDriveHz'],4.8e9)
        self.assertEqual(rows[0]['t1Evidence']['acquiredAt'],'2026-11-25T16:00:00Z')
        self.assertEqual(report['filters']['timezone'],'Asia/Shanghai')

    def test_half_open_date_boundaries_and_legacy_origin_grouping(self):
        m=self.module(); self.add(chip=None,dates=['2026-11-25T15:59:59Z','2026-11-25T16:00:00Z','2026-11-26T16:00:00Z'])
        self.add('s2',chip=None,origin='other')
        report=m.snapshot(self.path,{'startDate':['2026-11-26'],'endDate':['2026-11-26']})
        self.assertEqual(report['summary']['rounds'],4)
        self.assertEqual(len(report['groups']),2)
        self.assertTrue(all(g['identityStatus']=='unconfirmed_design' for g in report['groups']))

    def test_mismatched_t1_and_incomplete_evidence_stay_missing(self):
        m=self.module();self.add(mismatch=True)
        report=m.snapshot(self.path,{})
        row=report['groups'][0]['rows'][0]
        self.assertIsNone(row['t1Seconds'])
        self.assertIn('驱动频率',row['evidenceIssues'][0])
        self.assertEqual(row['archivedValues']['t1Seconds'],40e-6)
        with closing(sqlite3.connect(self.path)) as db, db:
            a=json.loads(db.execute('SELECT payload FROM platform_attempts LIMIT 1').fetchone()[0]);a['result']['partial']=True
            db.execute('UPDATE platform_attempts SET payload=? WHERE id=?',(json.dumps(a),a['id']))
        self.assertIsNone(m.snapshot(self.path,{})['groups'][0]['rows'][0]['frequencyHz'])

    def test_empty_report_is_readonly_and_html_is_safe(self):
        m=self.module();before=self.path.read_bytes();report=m.snapshot(self.path,{})
        self.assertEqual(report['summary']['rounds'],0)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertIn('暂无周期记录',m.render_html(report).decode())
        self.add();html=m.render_html(m.snapshot(self.path,{})).decode()
        self.assertNotIn('<script>test</script>',html)
        self.assertIn('&lt;script&gt;test&lt;/script&gt;',html)
        for text in ['北京时间','本轮未测','未获得有效频率','原始曲线','快照 SHA256']:
            self.assertIn(text,html)

    def test_invalid_filters_and_round_budget_fail_explicitly(self):
        m=self.module()
        for query in ({'startDate':['2026-02-30']},{'startDate':['2026-11-27'],'endDate':['2026-11-26']},
                      {'chipVersionId':['a','b']},{'startDate':['2026-11-26T00:00:00']},{'unknown':['x']}):
            with self.subTest(query=query),self.assertRaises(APIError):m.snapshot(self.path,query)
        self.add()
        from unittest.mock import patch
        with patch.object(m,'MAX_ROUNDS',2),self.assertRaises(APIError) as caught:m.snapshot(self.path,{})
        self.assertEqual(caught.exception.code,'STABILITY_REPORT_LIMIT')

    def test_changing_attempt_scope_cannot_make_another_workflow_valid(self):
        m=self.module();self.add()
        with closing(sqlite3.connect(self.path)) as db, db:db.execute("UPDATE platform_attempts SET workflow_id='other'")
        rows=m.snapshot(self.path,{})['groups'][0]['rows']
        self.assertTrue(all(r['frequencyHz'] is None for r in rows))

    def test_report_uses_one_read_snapshot_when_attempt_changes_during_capture(self):
        m=self.module();self.add()
        from unittest.mock import patch
        with closing(sqlite3.connect(self.path)) as db:db.execute('PRAGMA journal_mode=WAL')
        connect=sqlite3.connect;changed=[]
        def opening(*args,**kwargs):
            db=connect(*args,**kwargs)
            if kwargs.get('uri'):
                def trace(sql):
                    if 'FROM platform_attempts' in sql and not changed:
                        changed.append(True)
                        with closing(connect(self.path)) as writer,writer:
                            writer.execute("UPDATE platform_attempts SET workflow_id='later-revision'")
                db.set_trace_callback(trace)
            return db
        with patch.object(m.sqlite3,'connect',side_effect=opening):report=m.snapshot(self.path,{})
        self.assertEqual(changed,[True])
        self.assertEqual(report['summary']['validFrequencyRounds'],2)
        self.assertEqual(m.snapshot(self.path,{})['summary']['validFrequencyRounds'],0)

    def test_saved_snapshot_digest_and_selected_empty_scope_are_preserved(self):
        m=self.module();report=m.snapshot(self.path,{'sourceWorkflowId':['no-schedule-yet']})
        from backend.common import digest
        self.assertEqual(report['snapshotSha256'],digest({k:v for k,v in report.items() if k!='snapshotSha256'}))
        html=m.render_html(report).decode()
        self.assertIn('value="no-schedule-yet" selected',html)

    def test_named_workflow_filter_has_readable_label_before_first_schedule(self):
        m=self.module();identity='wf-known-task-without-schedule';name='1125升级验收_Q11_Rabi_20261126'
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('INSERT INTO platform_workflows VALUES(?,?)',(identity,json.dumps({'id':identity,'name':name})))
        report=m.snapshot(self.path,{'sourceWorkflowId':[identity]})
        self.assertEqual(report['summary']['rounds'],0)
        self.assertEqual(report['choiceLabels']['sourceWorkflowId'].get(identity),name)
        html=m.render_html(report).decode()
        self.assertIn('value="'+identity+'" selected>'+name+'</option>',html)
        self.assertIn('暂无周期记录',html)

    def test_evidence_keeps_selection_and_verification_candidate_roles(self):
        m=self.module();self.add()
        with closing(sqlite3.connect(self.path)) as db,db:
            wf=json.loads(db.execute("SELECT payload FROM platform_workflows WHERE id='s1-wf-0'").fetchone()[0])
            a=json.loads(db.execute("SELECT payload FROM platform_attempts WHERE id='s1-wf-0-f'").fetchone()[0])
            a['result']['selectionResult'].update(acquiredAt='2026-11-25T15:59:00Z',candidateParameters={'drive_hz':4.8e9})
            a['result']['verificationCandidateParameters']={'drive_hz':4.8001e9}
            wf['nodes'][0]['result']=deepcopy(a['result'])
            db.execute('UPDATE platform_workflows SET payload=? WHERE id=?',(json.dumps(wf),wf['id']))
            db.execute('UPDATE platform_attempts SET payload=? WHERE id=?',(json.dumps(a),a['id']))
        evidence=m.snapshot(self.path,{})['groups'][0]['rows'][0]['frequencyEvidence']
        self.assertIsNotNone(evidence.get('selection'),'Selection source is missing from the report')
        self.assertEqual(evidence['selection']['acquiredAt'],'2026-11-25T15:59:00Z')
        self.assertEqual(evidence['selection']['candidateParameters']['drive_hz'],4.8e9)
        self.assertEqual(evidence['verificationCandidateParameters']['drive_hz'],4.8001e9)
        self.assertEqual(evidence['value'],4.8e9)

    def test_fresh_measure_and_verify_operations_must_belong_to_this_attempt(self):
        m=self.module();self.add()
        with closing(sqlite3.connect(self.path)) as db:
            original=json.loads(db.execute("SELECT payload FROM platform_attempts WHERE id='s1-wf-0-f'").fetchone()[0])
            workflow=json.loads(db.execute("SELECT payload FROM platform_workflows WHERE id='s1-wf-0'").fetchone()[0])
        for field in ('verify_op','selection_op','verify_fresh','selection_real'):
            with self.subTest(field=field):
                a=deepcopy(original);wf=deepcopy(workflow)
                if field=='verify_op':a['result']['provenance']['operationId']='other/verify'
                if field=='selection_op':a['result']['selectionResult']['provenance']['operationId']='other/measure'
                if field=='verify_fresh':a['result']['provenance']['freshAcquisition']=False
                if field=='selection_real':a['result']['selectionResult']['dataKind']='simulation'
                wf['nodes'][0]['result']=deepcopy(a['result'])
                with closing(sqlite3.connect(self.path)) as db,db:
                    db.execute('UPDATE platform_attempts SET payload=? WHERE id=?',(json.dumps(a),a['id']))
                    db.execute('UPDATE platform_workflows SET payload=? WHERE id=?',(json.dumps(wf),wf['id']))
                self.assertIsNone(m.snapshot(self.path,{})['groups'][0]['rows'][0]['frequencyHz'])

    def test_labels_resolve_existing_chip_deployment_and_origin_names(self):
        m=self.module();self.add()
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE config_versions(id TEXT,payload TEXT)')
            for identity,name in [('chip-a','八号机芯片甲'),('dep-a','接线核对版')]:
                db.execute('INSERT INTO config_versions VALUES(?,?)',(identity,json.dumps({'name':name})))
            db.execute('INSERT INTO platform_workflows VALUES(?,?)',('origin-a',json.dumps({'id':'origin-a','name':'历史工作点标定'})))
        report=m.snapshot(self.path,{})
        self.assertEqual(report.get('choiceLabels',{}).get('chipVersionId',{}).get('chip-a'),'八号机芯片甲')
        html=m.render_html(report).decode()
        for label in ('八号机芯片甲','接线核对版','历史工作点标定'):self.assertIn(label,html)
        self.assertIn('<details><summary>报告来源与快照摘要</summary>',html)

    def test_actual_archived_attempt_operation_schema_is_accepted_without_alias(self):
        m=self.module()
        path=Path(__file__).parent/'fixtures/stability_actual_operation_projection.json'
        if not path.is_file():
            self.skipTest('Private local fixture not included in the source export')
        actual=json.loads(path.read_text(encoding='utf-8'))
        self.assertNotIn('selectionOperationId',actual['attempt'])
        self.assertTrue(callable(getattr(m,'operation_binding',None)),'Actual operation binding contract missing')
        self.assertTrue(m.operation_binding(actual['attempt']))
        bad=deepcopy(actual['attempt']);bad['result']['selectionResult']['provenance']['operationId']='other/measure'
        self.assertFalse(m.operation_binding(bad))

    def test_controller_persisted_round_projects_measure_and_verification_operations(self):
        # Explicit fake transport, real controller/scheduler persistence; no hardware.
        from tests.test_controller import make_fixture
        from backend.stability import StabilityService
        m=self.module();controller,adapter=make_fixture(Path(self.tmp.name)/'controller-contract')
        original=adapter.execute
        def execute(request,emit,cancel):
            result=original(request,emit,cancel)
            result['provenance']={'operationId':request['operationId'],'freshAcquisition':True}
            return result
        adapter.execute=execute
        try:
            service=StabilityService(controller)
            source=controller.create({'name':'test-only source','mode':'real','resourceIds':['Q11'],'coverage':'main'})
            controller.action(source['id'],{'operationId':'test-start','expectedRevision':source['revision'],'action':'start'})
            controller.run_workflow(source['id'])
            schedule=service.create({'operationId':'test-schedule','sourceWorkflowId':source['id'],
                'resourceId':'Q11','intervalSeconds':3600,'maxRounds':1,'spanHz':2e6,'points':31})
            service.action(schedule['id'],{'operationId':'test-schedule-start','expectedRevision':schedule['revision'],'action':'start'})
            service.tick();row=service.get(schedule['id'])['rounds'][0]
            adapter.candidate={'drive_hz':4.401e9}
            controller.run_workflow(row['workflowId']);service.tick()
            report=m.snapshot(controller.store.path,{})
            self.assertEqual(report['summary']['validFrequencyRounds'],1)
            self.assertEqual(report['summary']['validT1Rounds'],1)
            row=report['groups'][0]['rows'][0]
            for key in ('frequencyEvidence','t1Evidence'):
                self.assertEqual(row[key]['operationId'],row[key]['attemptId']+'/measure')
                self.assertEqual(row[key]['verificationOperationId'],row[key]['attemptId']+'/verify')
        finally:controller.close()


if __name__=='__main__':unittest.main()


