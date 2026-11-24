"""Read-only legacy failure views; fake acquisition and loopback HTTP only."""
from copy import deepcopy
import json
import threading
import unittest
import urllib.request

from backend.common import dumps
from backend.human_reports import reader_model
from backend.server import Server
from scripts.export_report_snapshot import snapshot
from tests import test_stopped_receipts as receipt_fixture

TECHNICAL_ERROR = receipt_fixture.TECHNICAL_ERROR

OLD_REASON = '采集已停止；部分数据保留，未完成独立验证'


class ReceiptPresentationContracts(unittest.TestCase):
    setUp = receipt_fixture.StoppedReceiptContracts.setUp
    tearDown = receipt_fixture.StoppedReceiptContracts.tearDown
    artifact = receipt_fixture.StoppedReceiptContracts.artifact
    run_failure = receipt_fixture.StoppedReceiptContracts.run_failure

    def legacy(self, **kwargs):
        wf, _ = self.run_failure(**kwargs)
        with self.c.store.transaction() as rt:
            stored = self.c.get(wf['id'], rt)
            node = stored['nodes'][0]
            node['detail'] = TECHNICAL_ERROR
            node['result'].pop('executionSummary', None)
            node['result']['quality']['reasons'] = [OLD_REASON]
            attempt_id = node['latestAttemptId']
            attempt = json.loads(rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?', (attempt_id,)).fetchone()[0])
            attempt['result'] = deepcopy(node['result'])
            self.c.store.attempt(rt, wf['id'], attempt)
            stored['events'] = [{'time': attempt['endedAt'], 'kind': 'warning', 'message': TECHNICAL_ERROR}]
            self.c.store.save(rt, stored, 'attention', {'message': TECHNICAL_ERROR})
        paused = self.c.action(wf['id'], {'operationId': 'pause-legacy', 'action': 'pause', 'expectedRevision': stored['revision']})
        self.assertEqual(paused['status'], 'paused')
        return paused

    def durable(self):
        with self.c.store.connect() as rt:
            return {table: [tuple(r) for r in rt.db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                    for table in ('platform_workflows', 'platform_attempts', 'platform_effects', 'events')}

    def test_get_list_detail_and_sse_show_paused_legacy_failure_without_mutation(self):
        wf = self.legacy()
        before = self.durable()
        server = Server(('127.0.0.1', 0), self.c)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_address[1])
        def get(path):
            with urllib.request.urlopen(base + path, timeout=5) as response:
                return json.load(response)
        try:
            session = get('/api/session')
            request = urllib.request.Request(base + '/api/workflows/' + wf['id'] + '/actions',
                headers={'Content-Type': 'application/json', 'Origin': base, 'X-Qcal-Token': session['token']},
                data=dumps({'operationId': 'http-pause', 'expectedRevision': wf['revision'], 'action': 'pause'}).encode())
            with urllib.request.urlopen(request, timeout=5) as response:
                mutated = json.load(response)
            before = self.durable()  # The authorized fake pause is durable; presentation reads must be inert.
            current = get('/api/workflows/' + wf['id'])
            self.assertIn('本次未获得测量数据', current['nodes'][0]['detail'])
            self.assertEqual(mutated['nodes'], current['nodes'])
            self.assertEqual(mutated['revision'], current['revision'])
            self.assertEqual(current['nodes'][0]['technicalDetails']['originalDetail'], TECHNICAL_ERROR)
            self.assertEqual(current['nodes'][0]['result']['technicalDetails']['originalQualityReasons'], [OLD_REASON])
            self.assertEqual(current['status'], 'paused')
            listed = next(w for w in get('/api/workflows') if w['id'] == wf['id'])
            self.assertEqual(listed['nodes'][0], current['nodes'][0])
            detail = get('/api/workflows/' + wf['id'] + '/nodes/' + wf['nodes'][0]['id'])
            self.assertEqual(detail['node'], current['nodes'][0])
            self.assertIn('本次未获得测量数据', detail['attempts'][0]['result']['quality']['reasons'][0])
            self.assertEqual(detail['attempts'][0]['error'], TECHNICAL_ERROR)
            with urllib.request.urlopen(base + '/api/events', timeout=5) as response:
                for _ in range(6):
                    line = response.readline().decode('utf-8')
                    if line.startswith('data: '):
                        stream = json.loads(line[6:]); break
                else:
                    self.fail('SSE snapshot missing')
            stream_wf = next(w for w in stream['workflows'] if w['id'] == wf['id'])
            self.assertEqual(stream_wf['nodes'][0], current['nodes'][0])
            old_events = [e for e in stream_wf['events'] if e.get('technicalDetails')]
            self.assertTrue(old_events)
            self.assertTrue(all('本次未获得测量数据' in e['message'] for e in old_events))
            self.assertTrue(all(e['technicalDetails']['originalMessage'] == TECHNICAL_ERROR for e in old_events))
        finally:
            self.c.stop_event.set()
            with self.c.store.changed:
                self.c.store.changed.notify_all()
            server.shutdown(); server.server_close(); thread.join(2)
        self.assertEqual(self.durable(), before)
        self.assertEqual(self.c.get(wf['id'])['nodes'][0]['detail'], TECHNICAL_ERROR)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_report_reader_uses_frozen_receipts_without_rewriting_raw_snapshot(self):
        wf = self.legacy()
        before = self.durable()
        frozen = snapshot(self.c.store.path, wf['id'], self.root)
        raw = deepcopy(frozen)
        model = reader_model(frozen)
        self.assertEqual(model['nodes'][0]['status'], '未获得测量数据')
        self.assertIn('本次未获得测量数据', model['nodes'][0]['summary'])
        self.assertNotIn('部分采集', model['nodes'][0]['summary'])
        self.assertEqual(model['nodes'][0]['history'][0]['stages'][0]['label'], '本次采集')
        self.assertEqual(frozen, raw)
        self.assertEqual(frozen['nodes'][0]['node']['result']['quality']['reasons'], [OLD_REASON])
        self.assertEqual(frozen['stoppedReceipts'][0]['receipt']['pointsCompleted'], 0)
        self.assertEqual(frozen['stoppedReceipts'][0]['attemptId'], wf['nodes'][0]['latestAttemptId'])
        report = self.c.report(wf['id'])
        self.assertIn('本次未获得测量数据', report['nodes'][0]['node']['detail'])
        self.assertEqual(self.durable(), before)

    def test_unbound_historical_event_gets_neutral_text_and_keeps_original(self):
        wf = self.legacy()
        wf['events'] = [{'time': '2000-01-01T00:00:00+00:00', 'message': TECHNICAL_ERROR}]
        projected = self.c.present_workflows([wf])[0]
        event = projected['events'][0]
        self.assertNotIn('部分', event['message'])
        self.assertNotIn('未获得测量数据', event['message'])
        self.assertEqual(event['technicalDetails']['originalMessage'], TECHNICAL_ERROR)
        self.assertEqual(event['technicalDetails']['receiptOperationIds'], [])

    def test_completed_selection_and_real_partial_raw_remain_distinct(self):
        wf = self.legacy(verification=True)
        projected = self.c.present_workflows([wf])[0]
        result = projected['nodes'][0]['result']
        self.assertIn('候选采集单独保留', projected['nodes'][0]['detail'])
        self.assertTrue(result['selectionResult']['artifacts'])
        frozen = snapshot(self.c.store.path, wf['id'], self.root)
        model = reader_model(frozen)
        self.assertIn('候选采集单独保留', model['nodes'][0]['summary'])
        self.assertEqual(self.c.get(wf['id'])['nodes'][0]['result']['quality']['reasons'], [OLD_REASON])

    def test_raw_partial_reader_still_reports_partial_acquisition(self):
        wf = self.legacy(raw=True, points=7, reason='timeout')
        frozen = snapshot(self.c.store.path, wf['id'], self.root)
        model = reader_model(frozen)
        self.assertEqual(model['nodes'][0]['status'], '部分采集')
        self.assertIn('部分原始数据', model['nodes'][0]['summary'])

    def test_frozen_reader_does_not_consult_later_receipts(self):
        wf = self.legacy()
        frozen = snapshot(self.c.store.path, wf['id'], self.root)
        with self.c.store.transaction() as rt:
            row = rt.db.execute('SELECT operation_id,result FROM platform_effects WHERE workflow_id=?', (wf['id'],)).fetchone()
            later = json.loads(row[1])
            later['pointsCompleted'] = 7
            rt.db.execute('UPDATE platform_effects SET result=? WHERE operation_id=?', (dumps(later), row[0]))
        self.assertEqual(reader_model(frozen)['nodes'][0]['status'], '未获得测量数据')
        self.assertEqual(reader_model(self.c.report(wf['id']))['nodes'][0]['status'], '原始数据留存待核查')

    def test_other_workflow_receipt_cannot_change_this_report(self):
        wf = self.legacy()
        frozen = snapshot(self.c.store.path, wf['id'], self.root)
        frozen['stoppedReceipts'][0]['workflowId'] = 'different-workflow'
        self.assertNotEqual(reader_model(frozen)['nodes'][0]['status'], '未获得测量数据')

    def test_missing_or_invalid_stopped_receipts_do_not_break_read_views(self):
        wf = self.legacy()
        for receipt in (None, [], 'unknown', {'status': 'failed_after_shutdown', 'shutdownConfirmed': False},
                        {'status': 'failed_after_shutdown', 'shutdownConfirmed': True, 'partialArtifacts': None}):
            with self.subTest(receipt=receipt):
                with self.c.store.transaction() as rt:
                    rt.db.execute('UPDATE platform_effects SET result=? WHERE workflow_id=?', (dumps(receipt), wf['id']))
                view = self.c.present_workflows([wf])[0]
                self.assertNotIn('executionSummary', view['nodes'][0]['result'])
                frozen = snapshot(self.c.store.path, wf['id'], self.root)
                self.assertNotEqual(reader_model(frozen)['nodes'][0]['status'], '未获得测量数据')

    def test_cancelled_effect_sql_null_does_not_crash_or_invent_frozen_receipt(self):
        wf = self.legacy()
        with self.c.store.transaction() as rt:
            rt.db.execute("UPDATE platform_effects SET state='cancelled',result=NULL WHERE workflow_id=?", (wf['id'],))
        before = self.durable()
        frozen = snapshot(self.c.store.path, wf['id'], self.root)
        self.assertEqual(frozen['stoppedReceipts'], [])
        self.assertEqual(frozen['nodes'][0]['node']['result']['quality']['reasons'], [OLD_REASON])
        self.assertNotEqual(reader_model(frozen)['nodes'][0]['status'], '未获得测量数据')
        self.assertNotIn('executionSummary', self.c.present_workflows([wf])[0]['nodes'][0]['result'])
        self.assertEqual(self.durable(), before)
