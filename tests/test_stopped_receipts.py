"""Stopped acquisition presentation: isolated temporary DB and FakeAdapter only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from backend.common import dumps
from backend.workspace import Workspace
from tests.test_controller import make_fixture


TECHNICAL_ERROR = 'ArcLight worker stopped and cleanup completed; partial data retained'
CONNECTION_ERROR = 'DeviceConnectionError: [402] VI_ERROR_RSRC_NFOUND (-1073807343)'


class StoppedReceiptContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='stopped-receipt-test-')
        self.root = Path(self.tmp.name)
        self.c, self.adapter = make_fixture(self.root)
        self.w = Workspace(self.c, initialize_catalog=False)
        self.c._workspace_service = self.w

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def artifact(self, name, kind):
        path = self.root / name
        path.write_text('{}', encoding='utf-8')
        return {'id': name, 'name': name, 'kind': kind, 'localPath': str(path),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def run_failure(self, *, raw=False, points=0, reason=CONNECTION_ERROR, confirmed=True, verification=False):
        wf = self.c.create({'name': 'offline stopped receipt', 'mode': 'real',
                            'resourceIds': ['Q11'], 'policy': 'threshold'})
        artifacts = [self.artifact('status.json', 'json')]
        if raw:
            artifacts.append(self.artifact('raw_scan.npz', 'raw'))
        receipt = {'status': 'failed_after_shutdown', 'shutdownConfirmed': confirmed,
                   'partialArtifacts': artifacts, 'pointsCompleted': points, 'reason': reason}
        original = self.adapter.execute

        def fail_execute(request, emit, cancel):
            if verification and not request['operationId'].endswith('/verify'):
                return original(request, emit, cancel)
            self.adapter.calls.append(deepcopy(request))
            raise RuntimeError(TECHNICAL_ERROR)

        self.adapter.execute = fail_execute
        self.adapter.reconcile = lambda operation: deepcopy(receipt)
        self.assertFalse(self.c._execute_node(wf['id'], wf['nodes'][0]['id']))
        return self.c.get(wf['id']), receipt

    def test_connection_failure_reports_no_measurement_data_without_erasing_evidence(self):
        wf, receipt = self.run_failure()
        node = wf['nodes'][0]
        self.assertIn('设备连接失败', node['detail'])
        self.assertIn('未获得测量数据', node['detail'])
        self.assertNotIn('部分数据保留', node['detail'])
        self.assertIn('未核验仪器输出状态', node['detail'])
        self.assertEqual(node['result']['executionSummary']['measurementData'], 'none')
        self.assertEqual(len(node['result']['artifacts']), 1)
        self.assertEqual(wf['engineState'], 'failed_after_shutdown')
        with self.c.store.connect() as rt:
            attempt = json.loads(rt.db.execute('SELECT payload FROM platform_attempts').fetchone()[0])
            effect = json.loads(rt.db.execute('SELECT result FROM platform_effects').fetchone()[0])
            self.assertEqual(attempt['error'], TECHNICAL_ERROR)
            self.assertEqual(effect['reason'], receipt['reason'])
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0], 0)
        item = self.w.attention_items()[0]
        self.assertIn('设备连接失败', item['message'])
        self.assertIn('网络', item['recommendation'])
        self.assertEqual(item['actions'][0]['id'], 'open_workflow')
        self.assertNotIn('resume', [a['id'] for a in item['actions']])
        self.assertEqual(len(self.adapter.calls), 1)

    def test_raw_partial_data_is_still_retained_and_not_called_empty(self):
        wf, _ = self.run_failure(raw=True, points=7, reason='timeout after acquisition')
        result = wf['nodes'][0]['result']
        self.assertIn('部分原始数据', wf['nodes'][0]['detail'])
        self.assertEqual(result['executionSummary']['measurementData'], 'partial')
        self.assertEqual(result['executionSummary']['pointsCompleted'], 7)
        self.assertEqual(result['quality']['status'], 'unverified')
        self.assertEqual([a['kind'] for a in result['artifacts']], ['json', 'raw'])

    def test_point_count_without_raw_does_not_claim_data_preserved(self):
        wf, _ = self.run_failure(points=7, reason='writer failure')
        self.assertIn('尚无可核验的原始数据', wf['nodes'][0]['detail'])
        self.assertEqual(wf['nodes'][0]['result']['executionSummary']['measurementData'], 'unconfirmed')

    def test_failed_verification_preserves_completed_selection_separately(self):
        wf, _ = self.run_failure(verification=True)
        result = wf['nodes'][0]['result']
        self.assertIn('候选采集单独保留', wf['nodes'][0]['detail'])
        self.assertEqual(result['selectionResult']['dataKind'], 'real')
        self.assertTrue(result['selectionResult']['artifacts'])
        self.assertEqual(result['quality']['status'], 'unverified')

    def test_unknown_shutdown_stays_quarantined_and_requires_reconciliation(self):
        wf, _ = self.run_failure(confirmed=False)
        self.assertEqual(wf['engineState'], 'needs_reconciliation')
        item = self.w.attention_items()[0]
        self.assertEqual(item['actions'][0]['id'], 'reconcile')
        self.assertNotIn('executionSummary', item)
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT state FROM platform_leases').fetchone()[0], 'quarantined')

    def test_existing_attention_is_represented_from_receipts_without_rewriting_workflow(self):
        wf, _ = self.run_failure()
        with self.c.store.transaction() as rt:
            legacy = self.c.get(wf['id'], rt)
            legacy['nodes'][0]['detail'] = TECHNICAL_ERROR
            legacy['nodes'][0]['result'].pop('executionSummary', None)
            self.c.store.save(rt, legacy, 'attention', {'message': TECHNICAL_ERROR})
        with self.c.store.connect() as rt:
            original_workflow = rt.db.execute('SELECT payload FROM platform_workflows WHERE id=?', (wf['id'],)).fetchone()[0]
        first = self.w.attention_items()[0]
        with self.c.store.transaction() as rt:
            first.pop('executionSummary', None)
            first.update(message=TECHNICAL_ERROR, recommendation='继续',
                         actions=[{'id': 'resume', 'label': '继续'}], state='deferred')
            rt.db.execute('UPDATE platform_attention SET payload=? WHERE id=?', (dumps(first), first['id']))
        corrected = self.w.attention_items()[0]
        self.assertIn('设备连接失败', corrected['message'])
        self.assertEqual(corrected['state'], 'deferred')
        self.assertEqual(corrected['revision'], first['revision'] + 1)
        self.assertEqual(self.w.attention_items()[0], corrected)
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT payload FROM platform_workflows WHERE id=?', (wf['id'],)).fetchone()[0], original_workflow)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_reconciliation_uses_same_no_data_explanation(self):
        wf, receipt = self.run_failure(confirmed=False)
        receipt['shutdownConfirmed'] = True
        self.adapter.reconcile = lambda operation: deepcopy(receipt)
        resolved = self.c.action(wf['id'], {'operationId': 'reconcile-offline',
            'expectedRevision': wf['revision'], 'action': 'reconcile'})
        self.assertIn('未获得测量数据', resolved['nodes'][0]['detail'])
        self.assertEqual(resolved['nodes'][0]['result']['executionSummary']['measurementData'], 'none')
        self.assertEqual(len(self.adapter.calls), 1)
