"""Persistent spectral recovery using explicit fake-device controller fixtures."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import unittest
from backend.common import APIError,digest
from backend.stability import StabilityService
from tests.test_controller import make_fixture


class ExpansionContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='stability-expansion-fixture-');self.addCleanup(self.tmp.cleanup)
        self.c,self.adapter=make_fixture(Path(self.tmp.name));self.addCleanup(self.c.close)
        self.adapter.profile['parameterBounds'].update(span_hz=[1e3,1e8],start_hz=[3e9,6e9],stop_hz=[3e9,6e9])
        # Finish constructing the explicit fake profile before creating any run.
        self.c.adapter_profile_hash=self.c._adapter_fingerprint()
        self.mode=None;original=self.adapter.execute
        def execute(request,emit,cancel):
            result=original(request,emit,cancel)
            if request['procedureId']=='sq_spectroscopy' and self.mode is not None:
                p=request['parameters'];lo=p.get('start_hz',p['drive_hz']-p['span_hz']/2);hi=p.get('stop_hz',p['drive_hz']+p['span_hz']/2)
                candidate=None if self.mode=='missing' else hi-(hi-lo)/(p['points']-1) if self.mode=='edge' else (lo+hi)/2
                result['candidateParameters']={} if candidate is None else {'drive_hz':candidate}
                result['analysisQuality']={'kind':'spectrum','accepted':candidate is not None,'groups':[{'repeat_validated':candidate is not None}],
                    'reasons':[] if candidate is not None else ['No repeat-validated interior response peak']}
                result['provenance']={'freshAcquisition':True,'fixtureOnly':True}
                result['series']=[{'x':[lo+(hi-lo)*j/(p['points']-1) for j in range(p['points'])],'y':[0.]*p['points']}]
                if candidate is None:result['metrics']['peak_snr']=1
            return result
        self.adapter.execute=execute
        self.service=StabilityService(self.c)
    def schedule(self,**extra):
        source=self.c.create({'name':'explicit calibration fixture','mode':'real','resourceIds':['Q11'],'coverage':'main'})
        self.c.action(source['id'],{'operationId':'source/start','expectedRevision':source['revision'],'action':'start'})
        self.c.run_workflow(source['id'])
        schedule=self.service.create({'operationId':'schedule/create','sourceWorkflowId':source['id'],'resourceId':'Q11',
            'intervalSeconds':3600,'maxRounds':1,'spanHz':2e6,'points':31,**extra})
        self.service.action(schedule['id'],{'operationId':'schedule/start','expectedRevision':schedule['revision'],'action':'start'})
        self.service.tick();row=self.service.get(schedule['id'])['rounds'][0]
        return schedule['id'],row['workflowId']
    def test_missing_peak_expands_once_and_only_fresh_verified_frequency_starts_t1(self):
        sid,wid=self.schedule();self.mode='missing';before=len(self.adapter.calls)
        self.c.run_workflow(wid);self.service.tick();row=self.service.get(sid)['rounds'][0]
        self.assertEqual(row['state'],'queued');self.assertEqual(len(row['recoveryEvents']),1)
        event=row['recoveryEvents'][0];self.assertEqual(event['reason']['code'],'frequency_peak_missing')
        self.assertEqual(event['before']['span_hz'],2e6);self.assertEqual(event['after']['span_hz'],4e6)
        self.assertIsNone(row['frequencyHz']);self.assertIsNone(row['t1Seconds'])
        self.assertFalse(any(c['procedureId']=='sq_t1' for c in self.adapter.calls[before:]))
        count=len(self.adapter.calls);self.service.tick();self.service.tick()
        self.assertEqual(len(self.adapter.calls),count);self.assertEqual(len(self.service.get(sid)['rounds'][0]['recoveryEvents']),1)
        self.mode='valid';self.c.run_workflow(wid);self.service.tick();row=self.service.get(sid)['rounds'][0]
        self.assertEqual(row['state'],'completed');self.assertIsNotNone(row['frequencyAttemptId'])
        t1=[c for c in self.adapter.calls[before:] if c['procedureId']=='sq_t1']
        self.assertTrue(t1);self.assertTrue(all(c['parameters']['drive_hz']==row['frequencyHz'] for c in t1))
    def test_recovery_is_bounded_by_attempt_count_and_never_reuses_old_frequency(self):
        sid,wid=self.schedule();self.mode='missing';before=len(self.adapter.calls)
        for _ in range(3):self.c.run_workflow(wid);self.service.tick()
        row=self.service.get(sid)['rounds'][0]
        self.assertEqual(row['state'],'failed');self.assertEqual(len(row['recoveryEvents']),2)
        self.assertEqual(row['recoveryStop']['code'],'FREQUENCY_RECOVERY_LIMIT')
        self.assertIsNone(row['frequencyHz']);self.assertIsNone(row['t1Seconds'])
        calls=[c for c in self.adapter.calls[before:] if c['procedureId']=='sq_spectroscopy']
        self.assertEqual([c['parameters']['span_hz'] for c in calls],[2e6,4e6,8e6])
        self.assertFalse(any(c['procedureId']=='sq_t1' for c in self.adapter.calls[before:]))
    def test_verified_but_edge_verification_peak_does_not_start_t1(self):
        sid,wid=self.schedule();self.mode='edge';before=len(self.adapter.calls)
        self.c.run_workflow(wid)
        self.assertFalse(any(c['procedureId']=='sq_t1' for c in self.adapter.calls[before:]))
        self.service.tick();row=self.service.get(sid)['rounds'][0]
        self.assertEqual(row['state'],'queued');self.assertEqual(row['recoveryEvents'][0]['reason']['code'],'frequency_peak_at_edge')
        self.assertIsNone(row['frequencyHz'])
    def test_restart_and_operator_pause_do_not_repeat_or_renew_expansion(self):
        sid,wid=self.schedule();self.mode='missing';self.c.run_workflow(wid);self.service.tick()
        self.assertEqual(len(self.service.get(sid)['rounds'][0]['recoveryEvents']),1)
        before=len(self.adapter.calls);recovered=StabilityService(self.c)
        recovered.tick(datetime.now(timezone.utc)+timedelta(days=1));self.c.run_workflow(wid)
        self.assertEqual(len(self.adapter.calls),before);current=recovered.get(sid)
        self.assertEqual(current['state'],'paused');self.assertEqual(len(current['rounds']),1)
        self.assertEqual(len(current['rounds'][0]['recoveryEvents']),1)
    def test_maximum_span_stops_before_any_retry_is_queued(self):
        sid,wid=self.schedule(frequencyRecovery={'maxExpansions':2,'maxSpanHz':2e6})
        self.mode='missing';self.c.run_workflow(wid);before=len(self.adapter.calls);self.service.tick()
        row=self.service.get(sid)['rounds'][0]
        self.assertEqual(row['state'],'failed');self.assertEqual(row['recoveryEvents'],[])
        self.assertEqual(row['recoveryStop']['code'],'FREQUENCY_WINDOW_LIMIT');self.assertEqual(len(self.adapter.calls),before)
    def test_explicit_frequency_axis_is_not_overridden_by_ineffective_scalar_expansion(self):
        sid,wid=self.schedule()
        with self.c.store.transaction() as rt:
            child=self.c.get(wid,rt);child['nodes'][0]['parameterOverrides']['frequencies_hz']=[4.399e9+2e6*j/30 for j in range(31)]
            self.c.store.save(rt,child,'explicit_axis_fixture',{'message':'TEST ONLY explicit native frequency array'})
        self.mode='missing';self.c.run_workflow(wid);self.service.tick();row=self.service.get(sid)['rounds'][0]
        self.assertEqual(row['state'],'failed');self.assertEqual(row['recoveryEvents'],[])
        self.assertEqual(row['recoveryStop']['code'],'FREQUENCY_WINDOW_FIXED_AXIS')

    def test_hardware_frequency_upper_bound_cannot_be_crossed(self):
        self.adapter.profile['resources']['Q11']['drive_hz']=5.999e9;self.c.adapter_profile_hash=self.c._adapter_fingerprint()
        sid,wid=self.schedule();self.mode='missing';self.c.run_workflow(wid);self.service.tick()
        row=self.service.get(sid)['rounds'][0]
        self.assertEqual(row['state'],'failed');self.assertEqual(row['recoveryEvents'],[])
        self.assertEqual(row['recoveryStop']['code'],'FREQUENCY_WINDOW_LIMIT')
    def test_invalid_recovery_policy_does_not_dispatch(self):
        for policy in ({'maxExpansions':100},{'maxSpanHz':200e6},{'factor':3},{'edgeGuardSteps':0}):
            with self.subTest(policy=policy),self.assertRaises(APIError) as caught:
                self.service.create({'operationId':'invalid/create','sourceWorkflowId':'unused','resourceId':'Q11',
                    'intervalSeconds':3600,'maxRounds':1,'spanHz':2e6,'points':31,'frequencyRecovery':policy})
            self.assertEqual(caught.exception.code,'INVALID_FREQUENCY_RECOVERY')
        self.assertEqual(self.adapter.calls,[])

    def test_operator_pause_after_failure_cannot_queue_an_expansion(self):
        sid,wid=self.schedule();self.mode='missing';self.c.run_workflow(wid)
        schedule=self.service.get(sid)
        self.service.action(sid,{'operationId':'operator/pause','expectedRevision':schedule['revision'],'action':'pause'})
        before=len(self.adapter.calls);self.service.tick()
        self.assertEqual(self.service.get(sid)['rounds'][0]['recoveryEvents'],[])
        self.assertEqual(self.service.get(sid)['state'],'paused');self.assertEqual(len(self.adapter.calls),before)

    def test_unknown_device_outcome_pauses_without_retry(self):
        sid,wid=self.schedule();self.adapter.fail=True;before=len(self.adapter.calls)
        self.c.run_workflow(wid);self.service.tick();schedule=self.service.get(sid)
        self.assertEqual(schedule['state'],'paused');self.assertEqual(schedule['rounds'][0]['recoveryEvents'],[])
        self.assertFalse(any(c['procedureId']=='sq_t1' for c in self.adapter.calls[before:]))

    def test_controller_budget_rejection_preserves_the_failed_attempt_without_queuing(self):
        from unittest.mock import patch
        sid,wid=self.schedule();self.mode='missing';self.c.run_workflow(wid);before=len(self.adapter.calls)
        def insufficient(*args,**kwargs):
            self.assertTrue(kwargs.get('joint_budget'));raise APIError('BUDGET_EXCEEDED','explicit exhausted-budget fixture')
        with patch.object(self.c,'_validate_plan',side_effect=insufficient):self.service.tick()
        row=self.service.get(sid)['rounds'][0]
        self.assertEqual(row['state'],'failed');self.assertEqual(row['recoveryEvents'],[])
        self.assertEqual(row['recoveryStop']['code'],'BUDGET_EXCEEDED');self.assertEqual(len(self.adapter.calls),before)

    def test_schedule_write_failure_rolls_back_retry_parameters_and_command_together(self):
        from unittest.mock import patch
        sid,wid=self.schedule();self.mode='missing';self.c.run_workflow(wid)
        before=self.c.get(wid)
        with patch.object(self.service,'_save',side_effect=RuntimeError('explicit schedule persistence failure')):
            with self.assertRaisesRegex(RuntimeError,'persistence'):self.service.tick()
        after=self.c.get(wid)
        # The read projection's wall-clock elapsed field is not persisted state.
        before.pop('elapsed',None);after.pop('elapsed',None)
        self.assertEqual(after,before);self.assertEqual(self.service.get(sid)['rounds'][0]['recoveryEvents'],[])
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT COUNT(*) FROM platform_commands WHERE id LIKE ?',('%/expand/%',)).fetchone()[0],0)
        # Retrying this failed transaction once still produces only one permit.
        self.service.tick();self.service.tick()
        self.assertEqual(len(self.service.get(sid)['rounds'][0]['recoveryEvents']),1)

    def test_source_calibration_invalidation_blocks_retry(self):
        sid,wid=self.schedule();self.mode='missing';self.c.run_workflow(wid)
        source_id=self.service.get(sid)['sourceWorkflowId']
        with self.c.store.transaction() as rt:
            source=self.c.get(source_id,rt)
            for node in source['nodes']:
                if node['procedureId'] in ('ro_iq01','ro_optimize'):
                    node['engineState']='stale';node['result']['stale']=True
            self.c.store.save(rt,source,'invalidation_fixture',{'message':'TEST ONLY source calibration invalidation'})
        before=len(self.adapter.calls);self.service.tick();row=self.service.get(sid)['rounds'][0]
        self.assertEqual(row['state'],'failed');self.assertEqual(row['recoveryEvents'],[])
        self.assertEqual(row['recoveryStop']['code'],'STABILITY_CALIBRATION_REQUIRED');self.assertEqual(len(self.adapter.calls),before)

    def test_attempt_history_records_final_independent_result_not_pending_selection(self):
        sid,wid=self.schedule();self.mode='valid';pending=[]
        def checkpoint(request,*unused):
            if request['procedureId']=='sq_spectroscopy' and request['operationId'].endswith('/verify'):
                self.service.tick();pending.append(deepcopy(self.service.get(sid)['rounds'][0]['frequencyAttempts']))
        self.adapter.before_return=checkpoint
        self.c.run_workflow(wid);self.service.tick()
        self.assertEqual(pending,[[]])
        row=self.service.get(sid)['rounds'][0];spectrum=self.c.get(wid)['nodes'][0]
        self.assertEqual(len(row['frequencyAttempts']),1)
        attempt=row['frequencyAttempts'][0]
        self.assertEqual(attempt['resultSha256'],digest(spectrum['result']))
        self.assertTrue(attempt['accepted']);self.assertIsNotNone(attempt['window'])


if __name__=='__main__':unittest.main()
