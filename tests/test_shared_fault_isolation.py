"""Shared-device queue faults: temporary DB, explicit FakeAdapter, no I/O."""
from pathlib import Path
import tempfile
import unittest

from backend.workspace import Workspace
from tests.test_controller import make_fixture


class SharedFaultIsolation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='qcal-round2-fake-')
        self.c, self.adapter = make_fixture(Path(self.tmp.name))
        self.w = Workspace(self.c, initialize_catalog=False)
        self.c._workspace_service = self.w

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

    def create(self, name, **changes):
        return self.c.create({'name': name, 'resourceIds': ['Q11'], 'mode': 'real', 'policy': 'threshold', **changes})

    def action(self, wf, action, op):
        return self.c.action(wf['id'], {'operationId': op, 'expectedRevision': wf['revision'], 'action': action})

    def test_confirmed_connection_failure_immediately_pauses_same_device_peer(self):
        owner, peer = self.create('fake owner'), self.create('fake peer')
        self.action(peer, 'start', 'queue-peer')
        self.adapter.fail = True
        self.adapter.reconcile = lambda operation: {
            'status': 'failed_after_shutdown', 'shutdownConfirmed': True, 'pointsCompleted': 0,
            'partialArtifacts': [], 'reason': 'DeviceConnectionError: VI_ERROR_RSRC_NFOUND'}
        self.assertFalse(self.c._execute_node(owner['id'], owner['nodes'][0]['id']))
        failed, parked = self.c.get(owner['id']), self.c.get(peer['id'])
        self.assertTrue(failed.get('sharedDeviceFault'))
        self.assertEqual((parked['status'], parked['engineState']), ('paused', 'device_queue_paused'))
        self.assertEqual(parked['deviceBlockerId'], owner['id'])
        self.assertEqual(self.w.queue_blocker(parked)['id'], owner['id'])
        self.assertEqual(len(self.adapter.calls), 1)

    def test_pausing_fault_owner_does_not_release_same_device_blocker(self):
        owner, peer = self.create('fake owner'), self.create('fake peer')
        self.action(peer, 'start', 'queue-peer')
        self.c._attention(owner['id'], 'waiting_hardware', 'fake ownership fault', shared_device_fault=True)
        paused = self.action(self.c.get(owner['id']), 'pause', 'pause-owner')
        self.assertEqual(paused['status'], 'paused')
        blocker = self.w.queue_blocker(self.c.get(peer['id']))
        self.assertIsNotNone(blocker)
        self.assertEqual(blocker['id'], owner['id'])
        self.c._pause_shared_queue()
        self.assertEqual(self.c.get(peer['id'])['engineState'], 'device_queue_paused')
        self.assertEqual(self.adapter.calls, [])

    def test_failure_scope_does_not_park_other_device_or_quality_only_peer(self):
        owner, peer = self.create('fake owner'), self.create('fake peer')
        self.action(peer, 'start', 'queue-peer')
        self.c._attention(owner['id'], 'quality_attention', 'fake fit failed')
        self.c._pause_shared_queue()
        self.assertEqual(self.c.get(peer['id'])['status'], 'queued')
        self.c._attention(owner['id'], 'waiting_hardware', 'fake ownership fault', shared_device_fault=True)
        unrelated = {'id': 'different', 'mode': 'real', 'configurationBinding': {
            'profile': {'instrument': {'resourceGroup': 'other-device'}}}}
        self.assertIsNone(self.w.queue_blocker(unrelated))

    def test_explicit_retry_releases_fault_gate_but_never_auto_resumes_parked_peer(self):
        owner, peer = self.create('fake owner'), self.create('fake peer')
        self.action(peer, 'start', 'queue-peer')
        self.c._attention(owner['id'], 'waiting_hardware', 'fake ownership fault', shared_device_fault=True)
        self.c._pause_shared_queue()
        paused = self.action(self.c.get(owner['id']), 'pause', 'pause-owner')
        self.action(paused, 'resume', 'explicit-owner-retry')
        parked = self.c.get(peer['id'])
        self.assertEqual(parked['status'], 'paused')
        self.assertIsNone(self.w.queue_blocker(parked))
        self.assertEqual(self.adapter.calls, [])

    def test_aborting_fault_owner_requires_explicit_peer_resume_and_keeps_evidence(self):
        owner, peer = self.create('fake owner'), self.create('fake peer')
        self.action(peer, 'start', 'queue-peer')
        self.c._attention(owner['id'], 'waiting_hardware', 'fake ownership fault', shared_device_fault=True)
        aborted = self.action(self.c.get(owner['id']), 'abort', 'abort-owner')
        self.assertTrue(aborted['sharedDeviceFault'])
        self.assertEqual(aborted['status'], 'aborted')
        parked = self.c.get(peer['id'])
        self.assertEqual((parked['status'], parked['engineState']), ('paused', 'device_queue_paused'))
        self.c.run_workflow(peer['id'])
        self.assertEqual(self.adapter.calls, [])
        resumed = self.action(parked, 'resume', 'explicit-peer-retry')
        self.assertEqual(resumed['status'], 'queued')
        self.assertIsNone(self.w.queue_blocker(resumed))
        with self.c.store.connect() as rt:
            self.assertIsNotNone(rt.db.execute('SELECT 1 FROM platform_commands WHERE id=?', ('explicit-peer-retry',)).fetchone())

    def test_individual_peer_resume_cannot_dispatch_while_fault_owner_is_paused(self):
        owner, peer = self.create('fake owner'), self.create('fake peer')
        self.action(peer, 'start', 'queue-peer')
        self.c._attention(owner['id'], 'waiting_hardware', 'fake ownership fault', shared_device_fault=True)
        self.action(self.c.get(owner['id']), 'pause', 'pause-owner')
        self.action(self.c.get(peer['id']), 'resume', 'resume-peer')
        self.c.run_workflow(peer['id'])
        self.assertEqual(self.c.get(peer['id'])['engineState'], 'device_queue_paused')
        self.assertEqual(self.adapter.calls, [])

    def test_explicit_retry_then_quality_failure_does_not_reactivate_old_device_fault(self):
        for action in ('resume', 'retry'):
            with self.subTest(action=action):
                owner, peer = self.create('fake owner ' + action), self.create('fake peer ' + action)
                self.c._attention(owner['id'], 'waiting_hardware', 'FAKE previous device fault',
                                  node_id=owner['nodes'][0]['id'], shared_device_fault=True)
                paused = self.action(self.c.get(owner['id']), 'pause', 'pause-' + action)
                self.action(paused, action, 'explicit-' + action)
                self.c._attention(owner['id'], 'quality_attention', 'FAKE subsequent fit failure')
                self.assertFalse(self.c.get(owner['id']).get('sharedDeviceFault'))
                self.assertIsNone(self.w.queue_blocker(peer))
                events = self.c.get(owner['id'])['events']
                self.assertTrue(any('previous device fault' in e.get('message', '') for e in events))
                ledger = self.c.store.events(owner['id'])
                self.assertTrue(any(e['payload'].get('sharedDeviceFaultRetryAuthorized') for e in ledger))
                self.assertEqual(self.adapter.calls, [])

    def test_paused_fault_keeps_visible_actionable_attention_until_explicit_abort(self):
        owner = self.create('fake owner')
        self.c._attention(owner['id'], 'waiting_hardware', 'FAKE device fault', shared_device_fault=True)
        item = next(i for i in self.w.attention_items() if i['workflowId'] == owner['id'])
        self.action(self.c.get(owner['id']), 'pause', 'pause-owner')
        updated = next(i for i in self.w.attention_items() if i['id'] == item['id'])
        self.assertEqual(updated['state'], 'open')
        self.assertEqual(updated['scope'], 'device')
        self.assertEqual({a['id'] for a in updated['actions']}, {'resume', 'abort'})
        self.action(self.c.get(owner['id']), 'abort', 'abort-owner')
        resolved = next(i for i in self.w.attention_items() if i['id'] == item['id'])
        self.assertEqual(resolved['state'], 'resolved')
        self.assertEqual(self.adapter.calls, [])

    def test_manual_fault_attention_uses_step_entry_and_explicit_step_clears_old_fault(self):
        owner = self.create('fake manual owner', policy='strict')
        node_id = owner['nodes'][0]['id']
        self.c._attention(owner['id'], 'waiting_hardware', 'FAKE device fault', node_id=node_id, shared_device_fault=True)
        paused = self.action(self.c.get(owner['id']), 'pause', 'pause-manual')
        item = next(i for i in self.w.attention_items() if i['workflowId'] == owner['id'] and i['state'] == 'open')
        self.assertEqual({a['id'] for a in item['actions']}, {'open_workflow', 'abort'})
        queued = self.c.action(owner['id'], {'operationId': 'explicit-step-retry', 'expectedRevision': paused['revision'],
            'action': 'run_step', 'nodeId': node_id})
        self.assertEqual(queued['engineState'], 'manual_step_queued')
        self.assertFalse(queued.get('sharedDeviceFault'))
        self.assertEqual(self.adapter.calls, [])
