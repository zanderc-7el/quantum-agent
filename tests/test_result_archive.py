"""Only temporary databases, explicit fake adapter, and generated fixture files."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.common import APIError, dumps, now
from backend.result_archive import backfill, record_effect, register_report_archive
from backend.store import Store
from tests.test_controller import make_fixture


class ResultArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-archive-offline-')
        self.root = Path(self.directory.name)
        self.controller, self.adapter = make_fixture(self.root)
        self.wf = self.controller.create({'name': 'OFFLINE archive', 'mode': 'real',
            'policy': 'threshold', 'resourceIds': ['Q11']})
        self.node = self.wf['nodes'][0]['id']

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def complete(self):
        self.assertTrue(self.controller._execute_node(self.wf['id'], self.node))
        return self.controller.archive_list(self.wf['id'])['items']

    def test_acquisition_and_final_assessment_are_atomic_and_distinct(self):
        items = self.complete()
        self.assertEqual(len(items), 3)
        observations = [i for i in items if i['kind'] == 'acquisition']
        self.assertEqual({i['role'] for i in observations}, {'selection', 'verification'})
        self.assertEqual({i['qualityStatus'] for i in observations}, {'unverified'})
        review = next(i for i in items if i['kind'] == 'attempt')
        self.assertEqual(review['qualityStatus'], 'passed')
        detail = self.controller.archive_item(review['id'])
        self.assertEqual(detail['parameters'], self.adapter.calls[0]['parameters'])
        self.assertEqual(detail['parameterVersions'], self.adapter.calls[0]['parameterVersions'])
        self.assertEqual(detail['artifactCount'], 2)
        self.assertTrue(all(a['registered'] and a['relativePath'].startswith('data/') for a in detail['artifacts']))
        self.assertNotIn('rawData', detail)
        status = self.controller.archive_status()
        self.assertEqual(status['cloud']['pendingCount'], 3)
        self.assertFalse(status['cloud']['configured'])
        self.assertEqual(status['cloud']['syncedCount'], 0)

    def test_backfill_and_restart_are_idempotent_without_workflow_edits(self):
        self.complete()
        before = self.controller.get(self.wf['id'])
        with self.controller.store.connect() as rt:
            stored_before = self.controller.store.get(rt, self.wf['id'])
        with self.controller.store.transaction() as rt:
            self.assertEqual(backfill(rt.db, self.root), 0)
        replacement = Store(self.controller.store.path, root=self.root)
        self.assertEqual(replacement.workflows()[0]['revision'], before['revision'])
        self.assertEqual(self.controller.archive_status()['local']['recordCount'], 3)
        with self.controller.store.connect() as rt:
            self.assertEqual(self.controller.store.get(rt, self.wf['id']), stored_before)

    def test_old_database_migration_preserves_ledgers_and_backfills(self):
        self.complete()
        with self.controller.store.transaction() as rt:
            original = [tuple(r) for r in rt.db.execute('SELECT * FROM platform_workflows')]
            for table in ('platform_archive_outbox', 'platform_result_archive', 'platform_archive_migrations'):
                rt.db.execute('DROP TABLE ' + table)
        replacement = Store(self.controller.store.path, root=self.root)
        with replacement.connect() as rt:
            self.assertEqual([tuple(r) for r in rt.db.execute('SELECT * FROM platform_workflows')], original)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_result_archive').fetchone()[0], 3)
            self.assertEqual(rt.db.execute('SELECT max(version) FROM platform_archive_migrations').fetchone()[0], 1)

    def test_outbox_failure_rolls_back_both_archive_and_attempt(self):
        with self.controller.store.transaction() as rt:
            rt.db.execute("CREATE TRIGGER offline_abort BEFORE INSERT ON platform_archive_outbox BEGIN SELECT RAISE(ABORT,'offline failure'); END")
        attempt = {'id': 'offline-attempt', 'nodeId': self.node, 'number': 1, 'mode': 'real',
            'state': 'attention', 'endedAt': now(), 'result': {'quality': {'status': 'failed', 'reasons': ['fixture']}, 'artifacts': []}}
        with self.assertRaises(Exception):
            with self.controller.store.transaction() as rt:
                self.controller.store.attempt(rt, self.wf['id'], attempt)
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_attempts').fetchone()[0], 0)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_result_archive').fetchone()[0], 0)

    def test_failed_and_partial_results_are_not_dropped_or_claimed_passed(self):
        attempt = {'id': 'offline-failed', 'nodeId': self.node, 'number': 1, 'mode': 'real', 'state': 'attention',
            'endedAt': now(), 'result': {'partial': True, 'metrics': {'r_squared': .4},
            'quality': {'status': 'failed', 'reasons': ['fixture rejection']}, 'artifacts': []}}
        with self.controller.store.transaction() as rt:
            self.controller.store.attempt(rt, self.wf['id'], attempt)
        detail = self.controller.archive_item(self.controller.archive_list()['items'][0]['id'])
        self.assertEqual(detail['qualityStatus'], 'failed')
        self.assertTrue(detail['details']['partial'])
        self.assertEqual(detail['metrics']['r_squared'], .4)

    def test_pending_effect_is_not_completion(self):
        with self.controller.store.transaction() as rt:
            rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)', ('offline/pending', self.wf['id'], 'a', 'pending', '{}', '{}'))
            self.assertIsNone(record_effect(rt.db, 'offline/pending', self.root))
        self.assertEqual(self.controller.archive_status()['local']['recordCount'], 0)

    def test_archive_is_immutable_after_current_result_invalidated(self):
        review = next(i for i in self.complete() if i['kind'] == 'attempt')
        with self.controller.store.transaction() as rt:
            original = rt.db.execute('SELECT payload FROM platform_result_archive WHERE id=?', (review['id'],)).fetchone()[0]
            wf = self.controller.get(self.wf['id'], rt)
            self.controller._invalidate(wf, self.node, include_target=True)
            self.controller.store.save(rt, wf, 'offline_invalidate')
        detail = self.controller.archive_item(review['id'])
        self.assertEqual(detail['qualityStatus'], 'passed')
        self.assertEqual(detail['currentContext']['nodeState'], 'stale')
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT payload FROM platform_result_archive WHERE id=?', (review['id'],)).fetchone()[0], original)

    def test_replay_is_explicitly_historical(self):
        replay = self.controller.create({'name': 'OFFLINE replay', 'mode': 'replay', 'resourceIds': ['Q11']})
        attempt = {'id': 'offline-history', 'nodeId': replay['nodes'][0]['id'], 'number': 1,
            'mode': 'replay', 'state': 'historical', 'endedAt': now(),
            'result': {'dataKind': 'historical', 'quality': {'status': 'passed'}, 'artifacts': []}}
        with self.controller.store.transaction() as rt:
            self.controller.store.attempt(rt, replay['id'], attempt)
        item = self.controller.archive_list(replay['id'])['items'][0]
        self.assertEqual((item['mode'], item['role'], item['qualityStatus']), ('replay', 'historical', 'historical'))

    def test_paging_and_unknown_ids(self):
        self.complete()
        page = self.controller.archive_list(limit=2)
        last = self.controller.archive_list(limit=2, cursor=page['nextCursor'])
        self.assertEqual(len(last['items']), 1)
        self.assertIsNone(last['nextCursor'])
        self.assertFalse({i['id'] for i in page['items']} & {i['id'] for i in last['items']})
        for limit, cursor in [(0, None), (101, None), (3, '1 OR 1=1')]:
            with self.assertRaises(APIError): self.controller.archive_list(limit=limit, cursor=cursor)
        with self.assertRaises(APIError): self.controller.archive_item('missing')

    def test_report_archive_is_hash_checked_and_idempotent(self):
        path = self.root / 'report.pptx'
        path.write_bytes(b'OFFLINE fixture, not an actual pptx')
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        args = (self.controller.store.path, self.wf['id'], 'offline-export', path, sha, 'a' * 64, 7)
        first = register_report_archive(*args, root=self.root, audit={'skill': 'fixture'})
        self.assertEqual(first, register_report_archive(*args, root=self.root, audit={'skill': 'fixture'}))
        item = self.controller.archive_item(first)
        self.assertEqual(item['revision'], 7)
        self.assertEqual(item['qualityStatus'], 'not_applicable')
        self.assertEqual(self.controller.archive_status()['local']['reportCount'], 1)
        path.write_bytes(b'changed bytes')
        with self.assertRaises(APIError): register_report_archive(*args, root=self.root)

    def test_no_network_during_local_archive_or_outbox_reads(self):
        with patch('socket.create_connection', side_effect=AssertionError('no cloud configured')):
            self.complete()
            self.assertEqual(len(self.controller.archive.pending_sync()), 3)
            self.assertEqual(self.controller.archive_status()['cloud']['state'], 'not_configured')

    def test_report_pins_workflow_attempts_and_events_to_one_read_snapshot(self):
        original_get = self.controller.get
        revision = original_get(self.wf['id'])['revision']
        fired = False
        def get_then_commit(workflow_id, rt=None):
            nonlocal fired
            result = original_get(workflow_id, rt)
            if rt is not None and not fired:
                fired = True
                with self.controller.store.transaction() as write_rt:
                    current = self.controller.store.get(write_rt, workflow_id)
                    attempt = {'id': 'concurrent-fixture', 'nodeId': self.node, 'number': 1, 'state': 'historical',
                        'endedAt': now(), 'result': {'quality': {'status': 'unverified'}, 'artifacts': []}}
                    self.controller.store.attempt(write_rt, workflow_id, attempt)
                    self.controller.store.save(write_rt, current, 'offline_concurrent_write')
            return result
        with patch.object(self.controller, 'get', side_effect=get_then_commit):
            report = self.controller.report(self.wf['id'])
        self.assertTrue(fired)
        self.assertEqual(report['workflow']['revision'], revision)
        self.assertFalse(any(item['attempts'] for item in report['nodes']))
        self.assertFalse(any(e['event'] == 'offline_concurrent_write' for e in report['events']))
        self.assertEqual(original_get(self.wf['id'])['revision'], revision + 1)


class SelectedChatNodeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-chat-selected-offline-')
        self.controller, self.adapter = make_fixture(self.directory.name)
        self.wf = self.controller.create({'name': 'OFFLINE selected chat', 'mode': 'real', 'resourceIds': ['Q11']})
        self.calls = []
        self.controller._brain = lambda path, body: self.calls.append(deepcopy(body)) or {'status': 'ok', 'message': 'OFFLINE'}

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def test_selected_node_comes_only_from_same_snapshot(self):
        node = self.wf['nodes'][1]
        before = self.controller.get(self.wf['id'])
        self.controller.chat({'workflowId': self.wf['id'], 'nodeId': node['id'], 'message': 'Explain this step',
            'selectedNode': {'quality': 'passed'}, 'context': {'node': 'forged'}})
        selected = self.calls[-1]['context']['selectedNode']
        self.assertEqual(selected['id'], node['id'])
        self.assertEqual(selected['workflowRevision'], before['revision'])
        self.assertTrue(selected['readOnly'])
        self.assertIsNone(selected['result']['quality'])
        self.assertEqual(before, self.controller.get(self.wf['id']))
        self.assertFalse(self.adapter.calls)

    def test_unknown_or_malformed_node_rejected_before_model(self):
        for payload in ({'nodeId': self.wf['nodes'][0]['id']},
                        {'workflowId': self.wf['id'], 'nodeId': 'other-node'},
                        {'workflowId': self.wf['id'], 'nodeId': {'id': 'forged'}}):
            with self.assertRaises(APIError): self.controller.chat({'message': 'read-only', **payload})
        self.assertFalse(self.calls)
