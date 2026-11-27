"""Periodic measurements use the controller's existing fake-device fixture."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from pathlib import Path
from backend.common import APIError
from backend.stability import StabilityService
from tests.test_controller import make_fixture


class StabilityContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c, self.adapter = make_fixture(Path(self.tmp.name))
        self.service = StabilityService(self.c)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def source(self):
        wf = self.c.create({'name': 'calibration', 'mode': 'real', 'resourceIds': ['Q11'], 'coverage': 'main'})
        wf = self.c.action(wf['id'], {'operationId': 'source-start', 'expectedRevision': wf['revision'], 'action': 'start'})
        self.c.run_workflow(wf['id'])
        return self.c.get(wf['id'])

    def schedule(self, **overrides):
        source = self.source()
        return self.service.create({'operationId': 'schedule-create', 'sourceWorkflowId': source['id'],
            'resourceId': 'Q11', 'intervalSeconds': 3600, 'maxRounds': 3, 'spanHz': 2e6, 'points': 31, **overrides})

    def start(self, schedule):
        return self.service.action(schedule['id'], {'operationId': 'start', 'expectedRevision': schedule['revision'], 'action': 'start'})

    def test_draft_source_is_not_calibration_and_does_not_dispatch(self):
        wf = self.c.create({'name': 'draft', 'mode': 'real', 'resourceIds': ['Q11']})
        with self.assertRaises(APIError) as caught:
            self.service.create({'operationId':'create','sourceWorkflowId':wf['id'],'resourceId':'Q11','intervalSeconds':3600,'maxRounds':3,'spanHz':2e6,'points':31})
        self.assertEqual(caught.exception.code, 'STABILITY_CALIBRATION_REQUIRED')
        self.assertEqual(self.adapter.calls, [])

    def test_two_phase_round_is_idempotent_and_t1_uses_current_frequency(self):
        schedule = self.schedule()
        self.start(schedule)
        calls_before = len(self.adapter.calls)
        self.service.tick()
        first = self.service.get(schedule['id'])
        self.assertEqual(len(first['rounds']), 1)
        child = self.c.get(first['rounds'][0]['workflowId'])
        self.assertEqual([n['procedureId'] for n in child['nodes']], ['sq_spectroscopy', 'sq_t1'])
        self.service.tick()
        self.assertEqual(len(self.service.get(schedule['id'])['rounds']), 1)
        self.adapter.candidate = {'drive_hz': 4.401e9}
        self.c.run_workflow(child['id'])
        self.service.tick()
        row = self.service.get(schedule['id'])['rounds'][0]
        self.assertEqual(row['frequencyHz'], 4.401e9)
        self.assertEqual(row['driftHz'], 1e6)
        t1_calls = [call for call in self.adapter.calls[calls_before:] if call['procedureId']=='sq_t1']
        self.assertTrue(t1_calls)
        self.assertTrue(all(call['parameters']['drive_hz']==4.401e9 for call in t1_calls))
        self.assertEqual(row['t1Seconds'], 40e-6)
        self.assertEqual(row['state'], 'completed')

    def test_missing_frequency_candidate_never_runs_t1_or_reuses_seed(self):
        schedule = self.schedule()
        self.start(schedule)
        self.service.tick()
        child_id = self.service.get(schedule['id'])['rounds'][0]['workflowId']
        before = len(self.adapter.calls)
        self.adapter.candidate = {}
        self.c.run_workflow(child_id)
        self.service.tick()
        self.assertFalse(any(call['procedureId']=='sq_t1' for call in self.adapter.calls[before:]))
        row = self.service.get(schedule['id'])['rounds'][0]
        self.assertIsNone(row['frequencyHz'])
        self.assertIsNone(row['t1Seconds'])
        self.assertEqual(row['state'], 'failed')

    def test_restart_pauses_schedule_without_duplicate_round(self):
        schedule = self.schedule()
        self.start(schedule)
        self.service.tick()
        recovered = StabilityService(self.c)
        recovered.tick()
        current = recovered.get(schedule['id'])
        self.assertEqual(current['state'], 'paused')
        self.assertEqual(len(current['rounds']), 1)
        child_id = current['rounds'][0]['workflowId']
        before = len(self.adapter.calls)
        self.c.run_workflow(child_id)
        self.assertEqual(len(self.adapter.calls), before)
        self.assertEqual(self.c.get(child_id)['status'], 'paused')

    def test_stale_stop_is_allowed_and_does_not_remove_evidence(self):
        schedule = self.schedule()
        started = self.start(schedule)
        self.service.tick()
        stopped = self.service.action(schedule['id'], {'operationId':'stop','expectedRevision':started['revision'],'action':'pause'})
        self.assertEqual(stopped['state'], 'paused')
        self.assertEqual(len(stopped['rounds']), 1)
        self.assertEqual(self.c.get(stopped['rounds'][0]['workflowId'])['status'], 'paused')

    def test_scheduler_failure_does_not_kill_the_only_measurement_worker(self):
        self.c.stability.tick = Mock(side_effect=RuntimeError('scheduler test error'))
        self.c.wake.wait = Mock(side_effect=lambda _: self.c.stop_event.set())
        self.c._loop()
        self.assertEqual(self.c._stability_error['type'], 'RuntimeError')

    def test_faulted_scheduler_cannot_issue_a_new_start_permit(self):
        schedule = self.schedule()
        self.c._stability_error = {'type': 'RuntimeError'}
        with self.assertRaises(APIError) as caught:
            self.start(schedule)
        self.assertEqual(caught.exception.code, 'STABILITY_RECONCILIATION_REQUIRED')
        self.assertEqual(self.service.get(schedule['id'])['state'], 'draft')

    def test_three_rounds_keep_frequency_and_t1_in_the_same_round(self):
        schedule = self.schedule()
        self.start(schedule)
        current = datetime.now(timezone.utc) + timedelta(seconds=1)
        for index, frequency in enumerate([4.401e9, 4.403e9, 4.402e9]):
            self.service.tick(current + timedelta(hours=index))
            row = self.service.get(schedule['id'])['rounds'][-1]
            self.adapter.candidate = {'drive_hz': frequency}
            self.c.run_workflow(row['workflowId'])
            self.service.tick(current + timedelta(hours=index))
        result = self.service.get(schedule['id'])
        self.assertEqual(result['state'], 'completed')
        self.assertEqual([r['frequencyHz'] for r in result['rounds']], [4.401e9, 4.403e9, 4.402e9])
        self.assertEqual([r['referenceFrequencyHz'] for r in result['rounds']], [4.4e9, 4.401e9, 4.403e9])
        self.assertEqual([r['t1Seconds'] for r in result['rounds']], [40e-6]*3)
        self.assertEqual(len({r['frequencyAttemptId'] for r in result['rounds']}), 3)
        self.assertEqual(len({r['t1AttemptId'] for r in result['rounds']}), 3)
