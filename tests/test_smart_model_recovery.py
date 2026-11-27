"""Smart-model recovery uses temporary stores and a fake adapter only."""
from copy import deepcopy
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from backend import model_recovery as recovery
from backend.common import APIError, dumps, ident, now
from backend.controller import Controller
try:
    from test_controller import make_fixture
except ImportError:
    from tests.test_controller import make_fixture


class RecoveryContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qcal-smart-model-only-')
        self.c, self.adapter = make_fixture(self.temp.name)
        wf = self.c.create({'name': 'TEST_ONLY model recovery', 'mode': 'real', 'policy': 'autopilot',
                            'coverage': 'full', 'resourceIds': ['Q11']})
        self.wid, self.nid = wf['id'], wf['nodes'][0]['id']
        with self.c.store.transaction() as rt:
            w = self.c.get(self.wid, rt)
            w.update(status='attention', engineState='quality_attention', currentNodeId=self.nid,
                     hardwareConfirmed=True, smartRun={'schema': 'q11-full-same-gate/1',
                     'adapterProfileHash': w['adapterProfileHash']})
            n = self.c._node(w, self.nid)
            n.update(attempt=1, latestAttemptId='TEST_ONLY-source',
                     parameterOverrides={'shots': 1000}, status='attention', engineState='quality_attention',
                     result={'dataKind': 'real', 'acquiredAt': now(), 'parameters': {'shots': 1000},
                             'metrics': {'contrast_snr': 2}, 'artifacts': [],
                             'quality': {'status': 'failed', 'reasons': ['TEST_ONLY low SNR']}})
            self.c.store.save(rt, w, 'test_fixture')
            rt.db.execute('INSERT OR IGNORE INTO platform_accounting_workflows VALUES(?,NULL)', (self.wid,))
        self.payloads = []

    def tearDown(self):
        self.assertEqual(self.adapter.calls, [])
        self.c.close()
        self.temp.cleanup()

    def unavailable(self):
        def brain(path, payload):
            self.payloads.append(deepcopy(payload))
            return {'status': 'waiting_model', 'message': 'TEST_ONLY temporarily unavailable'}
        self.c._brain = brain
        with patch('backend.model_recovery.time.time', return_value=100):
            self.assertFalse(self.c._decide(self.wid, self.nid))
        return self.c.get(self.wid)

    def test_transient_error_backoff_and_due_checkpoint_are_persistent_idempotent(self):
        w = self.unavailable()
        r = w['modelRecovery']
        self.assertEqual((r['state'], r['failureCount'], r['nextAttemptAt']), ('backoff', 1, 105))
        self.assertEqual(recovery.recover_due(self.c, at=104), 0)
        self.assertEqual(recovery.recover_due(self.c, at=105), 1)
        queued = self.c.get(self.wid)
        self.assertEqual((queued['status'], queued['engineState']), ('queued', 'decision_queued'))
        self.assertEqual(recovery.recover_due(self.c, at=106), 0)
        self.assertEqual(self.c.get(self.wid)['revision'], queued['revision'])
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT COUNT(*) FROM platform_effects').fetchone()[0], 0)
            self.assertEqual(rt.db.execute('SELECT COUNT(*) FROM platform_leases').fetchone()[0], 0)

    def test_parameter_rejection_is_feedback_then_corrected_plan_uses_existing_validation(self):
        def brain(path, payload):
            self.payloads.append(deepcopy(payload))
            return {'status': 'ok', 'decision': {'action': 'retry', 'reason': 'TEST_ONLY changed sampling',
                     'evidenceIds': [], 'parameterChanges': {'shots': 3000 if len(self.payloads) == 1 else 1200}}}
        self.c._brain = brain
        with patch('backend.model_recovery.time.time', return_value=100):
            self.assertFalse(self.c._decide(self.wid, self.nid))
        first = self.c.get(self.wid)
        self.assertEqual(first['modelRecovery']['lastError']['code'], 'PARAMETER_OUT_OF_RANGE')
        self.assertEqual(self.c._node(first, self.nid)['result']['metrics'], {'contrast_snr': 2})
        self.assertEqual(recovery.recover_due(self.c, at=105), 1)
        self.assertTrue(self.c._decide(self.wid, self.nid))
        self.assertEqual(self.payloads[-1]['modelRecovery']['lastError']['code'], 'PARAMETER_OUT_OF_RANGE')
        current = self.c.get(self.wid)
        self.assertEqual(self.c._node(current, self.nid)['parameterOverrides']['shots'], 1200)
        self.assertEqual(current['modelRecovery']['state'], 'resolved')

    def test_unchanged_failed_measurement_is_not_blindly_requeued(self):
        self.c._brain = lambda *a: {'status': 'ok', 'decision': {'action': 'retry', 'reason': 'again',
                                   'evidenceIds': [], 'parameterChanges': {'shots': 1000}}}
        self.assertFalse(self.c._decide(self.wid, self.nid))
        w = self.c.get(self.wid)
        self.assertEqual(w['modelRecovery']['lastError']['code'], 'MODEL_REPLAN_REQUIRED')
        self.assertEqual(self.c._node(w, self.nid)['attempt'], 1)
        self.assertEqual(w['engineState'], 'waiting_model')

    def test_old_workflow_never_autoresumes(self):
        with self.c.store.transaction() as rt:
            w = self.c.get(self.wid, rt)
            w.pop('smartRun')
            self.c.store.save(rt, w, 'test_old_scope')
        w = self.unavailable()
        self.assertNotIn('modelRecovery', w)
        self.assertEqual(recovery.recover_due(self.c, at=10000), 0)

    def test_pause_and_abort_keep_recovery_closed(self):
        for action in ('pause', 'abort'):
            with self.subTest(action=action):
                w = self.unavailable()
                self.c.action(self.wid, {'operationId': ident('test-stop'), 'expectedRevision': w['revision'], 'action': action})
                self.assertEqual(recovery.recover_due(self.c, at=10000), 0)
                self.assertIn(self.c.get(self.wid)['status'], ('paused', 'aborted'))
                if action == 'pause':
                    with self.c.store.transaction() as rt:
                        current = self.c.get(self.wid, rt)
                        current.update(status='attention', engineState='quality_attention')
                        self.c.store.save(rt, current, 'test_reset_only')

    def test_unknown_effect_or_any_lease_never_recovery_dispatches(self):
        for state in ('pending', 'unknown'):
            with self.subTest(state=state):
                self.unavailable()
                with self.c.store.transaction() as rt:
                    rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,NULL)',
                                  ('TEST_ONLY/' + state, self.wid, 'TEST_ONLY-source', state, dumps({})))
                self.assertEqual(recovery.recover_due(self.c, at=10000), 0)
                with self.c.store.transaction() as rt:
                    rt.db.execute('DELETE FROM platform_effects')
        self.unavailable()
        with patch.object(self.c, '_has_lease', return_value=True):
            self.assertEqual(recovery.recover_due(self.c, at=10000), 0)

    def test_checkpoint_change_and_hardware_fault_do_not_reuse_old_recovery(self):
        self.unavailable()
        with self.c.store.transaction() as rt:
            w = self.c.get(self.wid, rt)
            self.c._node(w, self.nid)['parameterOverrides']['shots'] = 1100
            self.c.store.save(rt, w, 'test_changed_context')
        self.assertEqual(recovery.recover_due(self.c, at=10000), 0)
        self.assertEqual(self.c.get(self.wid)['modelRecovery']['state'], 'superseded')
        self.unavailable()
        with self.c.store.transaction() as rt:
            w = self.c.get(self.wid, rt)
            w['sharedDeviceFault'] = True
            self.c.store.save(rt, w, 'test_hardware_fault')
        self.assertEqual(recovery.recover_due(self.c, at=10000), 0)

    def test_late_model_reply_after_pause_cannot_queue_measurement(self):
        entered, release = threading.Event(), threading.Event()
        def brain(*args):
            entered.set()
            release.wait(5)
            return {'status': 'ok', 'decision': {'action': 'retry', 'reason': 'late reply',
                       'evidenceIds': [], 'parameterChanges': {'shots': 1200}}}
        self.c._brain = brain
        result = []
        thread = threading.Thread(target=lambda: result.append(self.c._decide(self.wid, self.nid)))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            w = self.c.get(self.wid)
            self.c.action(self.wid, {'operationId': ident('test-pause'), 'action': 'pause', 'expectedRevision': w['revision']})
            thread.join(1)
            self.assertFalse(thread.is_alive(), 'Waiting for Brain must not block pause/other scheduling')
            self.assertEqual(result, [False])
            self.assertEqual(self.c.get(self.wid)['status'], 'paused')
        finally:
            release.set()
            thread.join(3)

    def test_repeated_rejections_back_off_without_growth_or_repeat_effect(self):
        for i in range(9):
            w = self.unavailable()
            if i:
                self.assertGreaterEqual(w['modelRecovery']['failureCount'], 1)
            self.assertLessEqual(w['modelRecovery']['nextAttemptAt'] - 100, 300)
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT COUNT(*) FROM platform_effects').fetchone()[0], 0)

    def test_interrupted_checkpoint_recovers_model_only_after_backoff(self):
        self.unavailable()
        with self.c.store.transaction() as rt:
            w = self.c.get(self.wid, rt)
            w['modelRecovery'].update(state='calling', nextAttemptAt=None)
            w['engineState'] = self.c._node(w, self.nid)['engineState'] = 'deciding'
            self.c.store.save(rt, w, 'test_process_restart_checkpoint')
        self.assertEqual(recovery.recover_due(self.c, at=200), 0)
        w = self.c.get(self.wid)
        self.assertEqual(w['modelRecovery']['lastError']['code'], 'MODEL_REQUEST_INTERRUPTED')
        self.assertEqual(w['modelRecovery']['nextAttemptAt'], 205)
        self.assertEqual(recovery.recover_due(self.c, at=204), 0)
        self.assertEqual(recovery.recover_due(self.c, at=205), 1)
        self.assertEqual(recovery.recover_due(self.c, at=206), 0)
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT COUNT(*) FROM platform_effects').fetchone()[0], 0)

    def test_physical_plan_failure_stays_blocked_and_preserves_gates(self):
        original = deepcopy(self.c.get(self.wid)['gates'])
        self.c._brain = lambda *a: {'status': 'ok', 'decision': {'action': 'retry',
            'reason': 'TEST_ONLY changed sampling', 'evidenceIds': [], 'parameterChanges': {'shots': 1200}}}
        with patch.object(self.c, '_validate_plan', side_effect=APIError('PULSE_IDENTITY_MISMATCH', 'TEST_ONLY same gate missing', 409)):
            self.assertFalse(self.c._decide(self.wid, self.nid))
        w = self.c.get(self.wid)
        self.assertEqual(w['modelRecovery']['state'], 'blocked')
        self.assertIsNone(w['modelRecovery']['nextAttemptAt'])
        self.assertEqual(recovery.recover_due(self.c, at=10000), 0)
        self.assertEqual(w['gates'], original)
        self.assertEqual(self.c._node(w, self.nid)['parameterOverrides']['shots'], 1000)

    def test_partial_stale_or_missing_attempt_cannot_start_recovery_call(self):
        for change in ({'partial': True}, {'stale': True}, {'dataKind': 'model'}):
            with self.subTest(change=change):
                with self.c.store.transaction() as rt:
                    w = self.c.get(self.wid, rt)
                    n = self.c._node(w, self.nid)
                    n['result'].update(partial=False, stale=False, dataKind='real')
                    n['result'].update(change)
                    self.c.store.save(rt, w, 'test_ineligible_result')
                with patch.object(self.c, '_brain', side_effect=AssertionError('No model call for ineligible result')):
                    self.assertFalse(self.c._decide(self.wid, self.nid))
        with self.c.store.transaction() as rt:
            w = self.c.get(self.wid, rt)
            n = self.c._node(w, self.nid)
            n['result'].update(partial=False, stale=False, dataKind='real')
            n.pop('latestAttemptId')
            self.c.store.save(rt, w, 'test_missing_attempt')
        with patch.object(self.c, '_brain', side_effect=AssertionError('No model call without attempt')):
            self.assertFalse(self.c._decide(self.wid, self.nid))

    def test_no_pending_recovery_never_opens_write_transaction_or_scans_workflows(self):
        with patch.object(self.c.store, 'transaction', side_effect=AssertionError('No write transaction')):
            with patch.object(self.c.store, 'workflows', side_effect=AssertionError('No archive scan')):
                self.assertEqual(recovery.recover_due(self.c, at=1000), 0)

    def test_strict_child_recovery_defers_to_authenticated_parent_and_never_generic_queue(self):
        self.unavailable()
        with self.c.store.transaction() as rt:
            w = self.c.get(self.wid, rt)
            w.update(policy='strict', smartParent={'schema': 'qcal-smart-science-child/1',
                'workflowId': 'TEST_ONLY-parent'})
            w.pop('smartRun')
            # A strict child has one method; authentication is separately tested
            # by the scientific orchestrator's complete parent/source fixtures.
            w['nodes'] = [self.c._node(w, self.nid)]
            self.c.store.save(rt, w, 'test_strict_child')
        with patch('backend.smart_science.assert_authorized', return_value={'id': 'TEST_ONLY-parent'}) as auth:
            child = self.c.get(self.wid)
            self.assertFalse(recovery.child_decision_ready(self.c, child, 'TEST_ONLY-parent', at=104))
            self.assertTrue(recovery.child_decision_ready(self.c, child, 'TEST_ONLY-parent', at=105))
            self.assertEqual(recovery.recover_due(self.c, at=10000), 0)
            self.assertEqual(self.c.get(self.wid)['status'], 'attention')
            self.assertEqual(auth.call_count, 2)
            with self.c.store.transaction() as rt:
                w = self.c.get(self.wid, rt)
                recovery.record_resolution(w, {'action': 'wait'})
                self.c.store.save(rt, w, 'test_valid_wait')
            self.assertFalse(recovery.child_decision_ready(self.c, self.c.get(self.wid), 'TEST_ONLY-parent', at=10000))
        with patch('backend.smart_science.assert_authorized', side_effect=APIError('SMART_SCIENCE_SCOPE', 'not authorized')):
            with self.assertRaises(APIError):
                recovery.child_decision_ready(self.c, self.c.get(self.wid), 'wrong-parent', at=10000)

    def test_parent_revision_cancels_child_model_wait_before_late_reply(self):
        parent = self.c.create({'name': 'TEST_ONLY parent', 'mode': 'real', 'policy': 'autopilot',
                                'coverage': 'full', 'resourceIds': ['Q11']})
        entered, release = threading.Event(), threading.Event()
        def brain(*args):
            entered.set(); release.wait(5)
            return {'status': 'ok', 'decision': {'action': 'retry'}}
        self.c._brain = brain
        child = self.c.get(self.wid)
        payload = {'runId': child['id'], 'revision': child['revision'], 'modelRecovery': {
            'requestId': 'TEST_ONLY-parent-cancel', 'parentCheckpoint': {'workflowId': parent['id'], 'revision': parent['revision']}}}
        replies = []
        thread = threading.Thread(target=lambda: replies.append(recovery.call_model(self.c, payload)))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.c.store.transaction() as rt:
                w = self.c.get(parent['id'], rt)
                w.update(status='paused', cancelRequested='pause')
                self.c.store.save(rt, w, 'test_parent_pause')
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(replies[0]['status'], 'superseded')
        finally:
            release.set(); thread.join(2)

    def test_corrected_evidence_is_not_mistaken_for_same_invalid_proposal(self):
        first = {'action': 'retry', 'parameterChanges': {'shots': 1200}, 'evidenceIds': ['wrong-id']}
        corrected = {**first, 'evidenceIds': ['actual-id']}
        w = self.c.get(self.wid)
        w['modelRecovery'] = {'rejectedProposals': [{'sha256': recovery.digest(recovery.proposal_identity(first))}]}
        with self.assertRaises(APIError): recovery.guard_proposal(w, first)
        recovery.guard_proposal(w, corrected)

    def test_array_replan_keeps_exact_bounded_values_in_proposal_identity(self):
        first = {'action': 'retry', 'parameterChanges': {'rb_lengths': [1,4,16,64,128]}, 'evidenceIds': []}
        corrected = {**first, 'parameterChanges': {'rb_lengths': [1,4,8,16,32]}}
        w = self.c.get(self.wid)
        w['modelRecovery'] = {'rejectedProposals': [{'sha256': recovery.digest(recovery.proposal_identity(first))}]}
        with self.assertRaises(APIError): recovery.guard_proposal(w, deepcopy(first))
        recovery.guard_proposal(w, corrected)
        self.assertEqual(recovery.proposal_identity(corrected)['parameterChanges']['rb_lengths'], [1,4,8,16,32])
        for bad in ([float('nan')], [[1]], list(range(257))):
            identity = recovery.proposal_identity({**first, 'parameterChanges': {'rb_lengths': bad}})
            self.assertEqual(identity['parameterChanges']['rb_lengths'], {'invalid': 'unsupported array'})


if __name__ == '__main__':
    unittest.main()
