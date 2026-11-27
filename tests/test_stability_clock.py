"""R33 normal scheduler and temporary-SQLite contracts. Explicit fake ADC only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from backend.common import APIError
from backend.stability import StabilityService
from tests import test_stability as fixtures


class MeasurementClock(unittest.TestCase):
    setUp=fixtures.StabilityContracts.setUp
    tearDown=fixtures.StabilityContracts.tearDown
    source=fixtures.StabilityContracts.source
    schedule=fixtures.StabilityContracts.schedule
    start=fixtures.StabilityContracts.start

    def begin(self):
        self.plan=self.schedule(intervalSeconds=10800,maxRounds=3)
        self.start(self.plan)
        self.t0=datetime(2040,1,1,tzinfo=timezone.utc)
        self.service.tick(self.t0)
        self.child=self.service.get(self.plan['id'])['rounds'][0]['workflowId']
        original=self.adapter.execute
        self.sampled=self.t0+timedelta(hours=2)
        def actual(request,emit,cancel):
            result=original(request,emit,cancel)
            result.update(acquiredAt=self.sampled.isoformat(),sourceRunId=request['operationId'],
                provenance={'operationId':request['operationId'],'freshAcquisition':True})
            return result
        self.adapter.execute=actual
        self.adapter.candidate={'drive_hz':4.401e9}

    def current(self):return self.service.get(self.plan['id'])

    def test_delayed_admission_uses_first_actual_measure_completion_not_queue(self):
        self.begin();self.sampled=self.t0+timedelta(hours=8)
        self.service.tick(self.sampled)
        self.assertEqual(len(self.current()['rounds']),1)
        self.assertIsNone(self.current()['nextDueAt'])
        self.c.run_workflow(self.child);self.service.tick(self.sampled)
        row=self.current()['rounds'][0]
        self.assertEqual(row['intervalAnchor']['acquiredAt'],self.sampled.isoformat())
        self.assertTrue(row['intervalAnchor']['operationId'].endswith('/measure'))
        self.assertEqual(self.current()['nextDueAt'],(self.sampled+timedelta(hours=3)).isoformat())
        self.service.tick(self.sampled+timedelta(hours=3)-timedelta(microseconds=1))
        self.assertEqual(len(self.current()['rounds']),1)
        self.service.tick(self.sampled+timedelta(hours=3))
        self.assertEqual(len(self.current()['rounds']),2)

    def test_long_round_never_overlaps_even_when_measure_interval_elapsed(self):
        self.begin();seen=[]
        def during_verify(request,emit,cancel):
            if request['procedureId']=='sq_spectroscopy' and request['operationId'].endswith('/verify'):
                self.service.tick(self.t0+timedelta(hours=6))
                seen.append(len(self.current()['rounds']))
                self.assertEqual(self.current()['rounds'][0]['intervalAnchor']['acquiredAt'],self.sampled.isoformat())
        self.adapter.before_return=during_verify
        self.c.run_workflow(self.child)
        self.assertEqual(seen,[1])
        self.service.tick(self.t0+timedelta(hours=7))
        self.assertEqual(len(self.current()['rounds']),2)

    def test_pause_resume_restart_cannot_reset_due_to_now(self):
        self.begin();self.c.run_workflow(self.child);self.service.tick(self.sampled)
        due=self.current()['nextDueAt']
        restarted=StabilityService(self.c)
        plan=restarted.get(self.plan['id']);self.assertEqual(plan['state'],'paused')
        self.assertEqual(plan['nextDueAt'],due)
        restarted.action(plan['id'],{'operationId':'resume','expectedRevision':plan['revision'],'action':'start'})
        restarted.tick(self.sampled+timedelta(hours=1))
        self.assertEqual(len(restarted.get(plan['id'])['rounds']),1)
        self.assertEqual(restarted.get(plan['id'])['nextDueAt'],due)

    def test_no_completed_acquisition_never_invents_measurement_time(self):
        self.begin()
        child=self.c.get(self.child)
        self.c.action(child['id'],{'operationId':'stop-before-data','expectedRevision':child['revision'],'action':'pause'})
        self.service.tick(self.t0+timedelta(days=10))
        plan=self.current()
        self.assertEqual(plan['state'],'paused')
        self.assertIsNone(plan['rounds'][0].get('intervalAnchor'))
        self.assertIsNone(plan['nextDueAt'])
        self.assertEqual(len(plan['rounds']),1)
        self.assertIn('采集',plan['error'])
        with self.assertRaises(APIError):
            self.service.action(plan['id'],{'operationId':'resume-no-data','expectedRevision':plan['revision'],'action':'start'})

    def test_changed_completed_effect_or_raw_cannot_authorize_next_round(self):
        for what in ('result','raw'):
            with self.subTest(what=what):
                if hasattr(self,'plan'):
                    self.tearDown();self.setUp()
                self.begin();self.c.run_workflow(self.child);self.service.tick(self.sampled)
                anchor=self.current()['rounds'][0]['intervalAnchor']
                with self.c.store.transaction() as rt:
                    row=rt.db.execute('SELECT result FROM platform_effects WHERE operation_id=?',(anchor['operationId'],)).fetchone()
                    result=json.loads(row[0])
                    if what=='result':
                        result['acquiredAt']=(self.t0+timedelta(hours=1)).isoformat()
                        rt.db.execute('UPDATE platform_effects SET result=? WHERE operation_id=?',(json.dumps(result),anchor['operationId']))
                    else:
                        from pathlib import Path
                        rawpath=rt.db.execute('SELECT path FROM platform_artifacts WHERE id=?',
                            (result['artifacts'][0]['id'],)).fetchone()[0]
                        Path(rawpath).write_bytes(b'TEST_ONLY changed raw')
                self.service.tick(self.sampled+timedelta(hours=3))
                self.assertEqual((self.current()['state'],len(self.current()['rounds'])),('paused',1))

    def test_three_rounds_independent_anchors_bound_count_including_quality_failure(self):
        self.begin()
        for index in range(3):
            if index:
                self.service.tick(self.sampled+timedelta(hours=3))
                self.child=self.current()['rounds'][-1]['workflowId']
                self.sampled+=timedelta(hours=4)
            self.adapter.candidate={} if index==1 else {'drive_hz':4.401e9}
            self.c.run_workflow(self.child);self.service.tick(self.sampled)
            self.assertEqual(len(self.current()['rounds']),index+1)
        plan=self.current()
        self.assertEqual(plan['state'],'completed')
        self.assertEqual([r['state'] for r in plan['rounds']],['completed','failed','completed'])
        self.assertEqual(len({r['intervalAnchor']['operationId'] for r in plan['rounds']}),3)
        self.service.tick(self.sampled+timedelta(days=30))
        self.assertEqual(len(self.current()['rounds']),3)

    def test_legacy_schedule_keeps_original_enqueue_clock_and_receipts(self):
        self.begin()
        with self.c.store.transaction() as rt:
            plan=self.service.get(self.plan['id'],rt)
            plan.pop('clockPolicy',None)
            plan['nextDueAt']=(self.t0+timedelta(hours=3)).isoformat()
            for row in plan['rounds']:row.pop('intervalAnchor',None)
            self.service._save(rt,plan)
        self.sampled=self.t0+timedelta(hours=8)
        self.c.run_workflow(self.child);self.service.tick(self.sampled)
        self.assertEqual(len(self.current()['rounds']),2)
        self.assertNotIn('intervalAnchor',self.current()['rounds'][0])

    def test_failed_rawless_or_wrong_operation_result_does_not_supply_anchor(self):
        for kind in ('rawless','wrong_operation','partial','naive_timestamp'):
            with self.subTest(kind=kind):
                if hasattr(self,'plan'):
                    self.tearDown();self.setUp()
                self.begin();self.c.run_workflow(self.child)
                with self.c.store.transaction() as rt:
                    row=rt.db.execute("SELECT operation_id,result FROM platform_effects WHERE workflow_id=? AND operation_id LIKE '%/measure' ORDER BY rowid LIMIT 1",(self.child,)).fetchone()
                    result=json.loads(row['result'])
                    if kind=='rawless':result['artifacts']=[]
                    elif kind=='wrong_operation':result['sourceRunId']='other/measure'
                    elif kind=='partial':result['partial']=True
                    else:result['acquiredAt']='2040-01-01T02:00:00'
                    rt.db.execute('UPDATE platform_effects SET result=? WHERE operation_id=?',(json.dumps(result),row['operation_id']))
                self.service.tick(self.t0+timedelta(days=10))
                self.assertEqual((self.current()['state'],len(self.current()['rounds'])),('paused',1))
                self.assertIsNone(self.current()['rounds'][0].get('intervalAnchor'))

    def test_range_report_filters_actual_anchor_and_keeps_original_queue_time(self):
        self.begin();self.c.run_workflow(self.child);self.service.tick(self.sampled)
        from backend.stability_reports import snapshot,render_html
        report=snapshot(self.c.store.path,{'startDate':['2040-01-01'],'endDate':['2040-01-01']})
        self.assertEqual(report['summary']['rounds'],1)
        row=report['groups'][0]['rows'][0]
        self.assertEqual(row['displayAt'],self.sampled.isoformat())
        self.assertEqual(row['timeBasis'],'首测采集完成')
        self.assertNotEqual(row['startedAt'],row['displayAt'])
        self.assertIn('首测采集完成',render_html(report).decode())
        with self.c.store.transaction() as rt:
            plan=self.current();plan['rounds'][0]['intervalAnchor']['acquiredAt']='2041-01-01T00:00:00+00:00'
            self.service._save(rt,plan)
        changed=snapshot(self.c.store.path,{})['groups'][0]['rows'][0]
        self.assertIsNone(changed['plotAt'])
        self.assertEqual(changed['displayAt'],changed['startedAt'])
        self.assertIn('尚无可核验',changed['timeBasis'])

    def test_normal_http_create_exposes_new_clock_without_dispatch(self):
        from tests.test_http import HTTPContracts
        http=HTTPContracts('test_session_and_authenticated_workflow_creation');http.setUp()
        try:
            controller=http.controller
            source=controller.create({'name':'TEST_ONLY HTTP source','mode':'real','resourceIds':['Q11'],'coverage':'main'})
            controller.action(source['id'],{'operationId':'http-source','expectedRevision':source['revision'],'action':'start'})
            controller.run_workflow(source['id'])
            before=len(http.adapter.calls)
            body={'operationId':'http-clock','sourceWorkflowId':source['id'],'resourceId':'Q11',
                'intervalSeconds':10800,'maxRounds':3,'spanHz':2e6,'points':101}
            code,_,plan=http.request('POST','/api/stability/schedules',body,{'X-Qcal-Token':http.session()})
            self.assertEqual(code,201,plan)
            self.assertEqual(plan['clockPolicy'],'first_frequency_acquisition_completed_v1')
            self.assertEqual((plan['state'],plan['rounds'],plan['nextDueAt']),('draft',[],None))
            self.assertEqual(http.request('GET','/api/stability/schedules/'+plan['id'])[2],plan)
            self.assertEqual(len(http.adapter.calls),before)
        finally:http.tearDown()

    def test_persisted_anchor_does_not_bypass_new_pending_action_or_lease(self):
        for unsafe in ('lease','pending','unknown'):
            with self.subTest(unsafe=unsafe):
                if hasattr(self,'plan'):
                    self.tearDown();self.setUp()
                self.begin();self.c.run_workflow(self.child);self.service.tick(self.sampled)
                with self.c.store.transaction() as rt:
                    if unsafe=='lease':
                        rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)',
                            ('TEST_ONLY',self.child,'TEST_ONLY-unresolved','quarantined','TEST_ONLY-token'))
                    else:
                        rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,NULL)',
                            ('TEST_ONLY-unresolved/measure',self.child,'TEST_ONLY-unresolved',unsafe,'{}'))
                before=len(self.adapter.calls)
                self.service.tick(self.sampled+timedelta(days=1))
                self.assertEqual((self.current()['state'],len(self.current()['rounds'])),('paused',1))
                self.assertEqual(len(self.adapter.calls),before)


if __name__=='__main__':unittest.main()
