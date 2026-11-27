"""Consistent report snapshots while acquisition revisions advance; no hardware."""
from contextlib import closing
import json
import sqlite3
import threading
import unittest
from unittest.mock import Mock, patch

from backend.common import APIError
from backend.report_exports import snapshot
from tests import test_report_exports as export_fixtures
from tests import test_http as http_fixtures


class RunningSnapshotContracts(unittest.TestCase):
    setUp = export_fixtures.ExportsTest.setUp
    tearDown = export_fixtures.ExportsTest.tearDown
    service = export_fixtures.ExportsTest.service
    body = export_fixtures.ExportsTest.body

    def queued_service(self):
        service = self.service(lambda *_: self.fail('No rendering in snapshot contracts'))
        service.executor.submit = Mock()
        return service

    def advance(self, revision, *, node=False, connection=None):
        wf = {'id': 'wf-1', 'revision': revision, 'name': 'Running acquisition',
              'status': 'running', 'mode': 'real', 'target': 'Q19',
              'nodes': [{'id': 'n', 'engineState': 'measuring'}] if node else []}
        with closing((connection or sqlite3.connect)(self.db)) as db, db:
            db.execute('UPDATE platform_workflows SET payload=? WHERE id=?', (json.dumps(wf), 'wf-1'))
            if node:
                db.execute('DELETE FROM platform_attempts')
                db.execute('INSERT INTO platform_attempts VALUES(?,?,?,?)',
                           ('wf-1', 'n', 1, json.dumps({'id': 'attempt', 'state': 'measuring', 'recordedRevision': revision})))

    def frozen(self, job):
        return json.loads((self.root/'data/report_exports'/job['id']/'report.json').read_text(encoding='utf-8'))

    def test_old_browser_revision_exports_latest_committed_running_snapshot(self):
        self.advance(12, node=True)
        service = self.queued_service()
        job = service.create('wf-1', self.body())
        frozen = self.frozen(job)
        self.assertEqual((job['requestedRevision'], job['revision']), (7, 12))
        self.assertEqual(job['snapshotPolicy'], 'latest_committed')
        self.assertEqual(job['capturedAt'], frozen['generatedAt'])
        self.assertEqual(frozen['workflow']['status'], 'running')
        self.assertEqual(frozen['nodes'][0]['attempts'][0]['recordedRevision'], 12)
        self.assertFalse(frozen['workflowComplete'])
        self.assertEqual(frozen['exportRequest'], {'operationId': 'op1', 'observedRevision': 7,
            'capturedRevision': 12, 'capturedAt': job['capturedAt'], 'snapshotPolicy': 'latest_committed'})
        self.assertEqual(service.executor.submit.call_count, 1)

    def test_progress_between_request_and_snapshot_needs_no_retry(self):
        service = self.queued_service()
        def advancing_snapshot(*args):
            self.advance(8)
            return snapshot(*args)
        with patch('backend.report_exports.snapshot', side_effect=advancing_snapshot) as capture:
            job = service.create('wf-1', self.body())
        self.assertEqual(job['revision'], 8)
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(len(service.list('wf-1')), 1)

    def test_workflow_and_attempts_share_one_read_transaction_during_progress(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('PRAGMA journal_mode=WAL')
        self.advance(7, node=True)
        service = self.queued_service()
        original_connect = sqlite3.connect
        advanced = []
        def connect(*args, **kwargs):
            db = original_connect(*args, **kwargs)
            if kwargs.get('uri'):
                def trace(statement):
                    if 'SELECT payload FROM platform_attempts' in statement and not advanced:
                        advanced.append(True)
                        self.advance(8, node=True, connection=original_connect)
                db.set_trace_callback(trace)
            return db
        with patch('scripts.export_report_snapshot.sqlite3.connect', side_effect=connect):
            job = service.create('wf-1', self.body())
        frozen = self.frozen(job)
        self.assertEqual(advanced, [True])
        self.assertEqual(frozen['workflow']['revision'], 7)
        self.assertEqual(frozen['nodes'][0]['attempts'][0]['recordedRevision'], 7)
        with closing(original_connect(self.db)) as db:
            self.assertEqual(json.loads(db.execute('SELECT payload FROM platform_workflows').fetchone()[0])['revision'], 8)

    def test_future_revision_rejected_without_job_or_snapshot_directory(self):
        service = self.queued_service()
        with self.assertRaises(APIError) as raised:
            service.create('wf-1', {**self.body(), 'expectedRevision': 8})
        self.assertEqual(raised.exception.code, 'REVISION_CONFLICT')
        self.assertEqual(service.list('wf-1'), [])
        self.assertEqual(list(service.output_root.iterdir()), [])
        service.executor.submit.assert_not_called()

    def test_concurrent_same_operation_and_later_progress_keep_one_frozen_job(self):
        self.advance(9)
        service = self.queued_service()
        barrier = threading.Barrier(3)
        jobs, errors = [], []
        def submit():
            barrier.wait()
            try: jobs.append(service.create('wf-1', self.body()))
            except Exception as exc: errors.append(exc)
        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join(3)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]['id'], jobs[1]['id'])
        self.advance(15)
        replay = service.create('wf-1', self.body())
        self.assertEqual((replay['id'], replay['revision']), (jobs[0]['id'], 9))
        self.assertEqual(self.frozen(replay)['workflow']['revision'], 9)
        self.assertEqual(service.executor.submit.call_count, 1)
        with self.assertRaises(APIError) as raised:
            service.create('wf-1', {**self.body(), 'expectedRevision': 15})
        self.assertEqual(raised.exception.code, 'IDEMPOTENCY_CONFLICT')

    def test_different_operation_cannot_queue_second_active_export(self):
        service = self.queued_service()
        service.create('wf-1', self.body())
        with self.assertRaises(APIError) as raised:
            service.create('wf-1', {**self.body(), 'operationId': 'second'})
        self.assertEqual(raised.exception.code, 'REPORT_EXPORT_ACTIVE')
        self.assertEqual(len(service.list('wf-1')), 1)
        self.assertEqual(service.executor.submit.call_count, 1)


class RunningExportHTTPContracts(unittest.TestCase):
    setUp = http_fixtures.HTTPContracts.setUp
    tearDown = http_fixtures.HTTPContracts.tearDown
    request = http_fixtures.HTTPContracts.request
    session = http_fixtures.HTTPContracts.session

    def test_report_accepts_old_view_but_measurement_command_still_rejects_it(self):
        workflow = self.controller.create({'name': 'Running export fixture', 'resourceIds': ['Q11'], 'mode': 'replay'})
        with self.controller.store.transaction() as rt:
            current = self.controller.store.get(rt, workflow['id'])
            current['status'] = 'running'
            self.controller.store.save(rt, current, 'progress', {'message': 'Fixture acquisition point'})
        current = self.controller.get(workflow['id'])
        service = self.controller.report_exports
        service.executor.submit = Mock()
        endpoint = '/api/workflows/' + workflow['id']
        headers = {'X-Qcal-Token': self.session()}
        body = {'operationId': 'http-running-report', 'expectedRevision': workflow['revision'], 'format': 'pptx', 'appendixCount': 16}
        status, _, job = self.request('POST', endpoint+'/report-exports', body, headers)
        self.assertEqual(status, 202, job)
        self.assertEqual(job['revision'], current['revision'])
        self.assertEqual(job['requestedRevision'], workflow['revision'])
        self.assertEqual(self.request('POST', endpoint+'/report-exports', body, headers)[2]['id'], job['id'])
        status, _, error = self.request('POST', endpoint+'/actions',
            {'action': 'start', 'operationId': 'stale-control', 'expectedRevision': workflow['revision']}, headers)
        self.assertEqual(status, 409, error)
        self.assertEqual(error['error']['code'], 'STALE_REVISION')
        self.assertEqual(self.controller.get(workflow['id'])['revision'], current['revision'])
        self.assertEqual(self.adapter.calls, [])
        service.executor.submit.assert_called_once()
