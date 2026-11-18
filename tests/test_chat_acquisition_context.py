"""Offline synthetic receipts only; no live service, model or hardware calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend.chat_acquisition_context import (MAX_PLAN_BYTES, MAX_PLAN_READ_BYTES, acquisition_context,
    bound_chat_observations, read_chat_snapshot, registered_plan_geometry)
from backend.common import APIError, dumps, now
from backend.prior_failure_context import javascript_json_units_upper_bound

try:
    from test_controller import make_fixture
except ImportError:
    from tests.test_controller import make_fixture


class ChatAcquisitionContextTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-chat-offline-')
        self.root = Path(self.directory.name)
        self.controller, self.adapter = make_fixture(self.root)
        self.workflow = self.controller.create({'name': 'OFFLINE chat acquisition status', 'mode': 'real',
                                                'policy': 'threshold', 'resourceIds': ['Q11']})
        self.node_id = self.workflow['nodes'][0]['id']
        self.captured = []
        self.controller._brain = lambda path, request: self.captured.append(deepcopy(request)) or {
            'status': 'ok', 'message': 'OFFLINE RECORDER; no model called'}

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def plan_result(self, request, result):
        result = deepcopy(result)
        result['provenance'] = {'operationId': request['operationId'], 'freshAcquisition': True}
        result['analysisQuality'] = {'accepted': True}
        job = {'kind': 's21_power', 'captures': 3, 'acquisitions_per_point': 6,
            'spec': {'meta': {'type': '1', 'expand_mode': 'cartesian', 'shots': 2000}, 'sweeps': [
                {'name': 'readout_frequency_hz', 'values': [7e9 + i * 1e4 for i in range(61)]},
                {'name': 'readout_amplitude', 'values': [.1, .2, .4, .6, .8]}]}}
        plan = {'adapter': 'arclight', 'operationId': request['operationId'], 'resourceId': request['resourceId'],
            'procedureId': request['procedureId'], 'parameters': result['parameters'], 'jobs': [job],
            'expectedJobRows': [915], 'totalRows': 915, 'estimated_total_shots': 10980000,
            'requestIdentity': {k: request.get(k) for k in ('runId', 'nodeId', 'attemptId', 'operationId',
                                                         'resourceId', 'procedureId', 'parameterVersions')}}
        path = self.root / 'data/runs/arclight_operations' / hashlib.sha256(request['operationId'].encode()).hexdigest() / 'plan.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        content = dumps(plan).encode()
        path.write_bytes(content)
        result['artifacts'].append({'id': 'plan-' + hashlib.sha256(content).hexdigest()[:20], 'name': 'plan.json',
            'kind': 'analysis', 'localPath': str(path), 'sha256': hashlib.sha256(content).hexdigest()})
        return result, plan, path

    def chat(self):
        with patch.object(self.controller, 'artifact', side_effect=AssertionError('chat must not read raw files')):
            self.controller.chat({'workflowId': self.workflow['id'], 'message': 'Explain recorded progress only.'})
        return self.captured[-1]['context']

    def complete_fixture(self, inspect_verification=False):
        original = self.adapter.execute
        pending = []
        def execute(request, emit, cancel):
            if request['operationId'].endswith('/measure'):
                emit({'event': 'acquisition_complete', 'operationId': request['operationId'],
                      'analysisAccepted': True, 'completedRows': 915, 'totalRows': 915, 'progress': 100})
            else:
                emit({'event': 'worker_started', 'operationId': request['operationId']})
                node = self.controller.detail(self.workflow['id'], self.node_id)['node']
                self.assertNotIn('analysisAccepted', node['telemetry'])
                self.assertNotIn('completedRows', node['telemetry'])
                self.assertEqual(node['progress'], 0)
                self.assertTrue(node['result']['selectionResult']['artifacts'])
                emit({'event': 'row_acquired', 'completedRows': 245, 'totalRows': 915, 'progress': 100 * 245 / 915})
                if inspect_verification:
                    pending.append(self.chat())
            result, _, _ = self.plan_result(request, original(request, emit, cancel))
            return result
        self.adapter.execute = execute
        self.assertTrue(self.controller._execute_node(self.workflow['id'], self.node_id))
        self.assertEqual(len(self.adapter.calls), 2)
        self.assertEqual(self.controller.get(self.workflow['id'])['budget'], self.workflow['budget'])
        return pending

    def test_same_attempt_pending_verify_has_actual_rows_and_no_borrowed_plan(self):
        context = self.complete_fixture(inspect_verification=True)[0]
        state = context['acquisitionState']
        self.assertEqual(state['workflowRevision'], context['workflow']['revision'])
        self.assertTrue(state['readOnly'])
        self.assertFalse(state['rawFileRead'])
        current = next(n for n in state['nodes'] if n['nodeId'] == self.node_id)
        selection, verification = sorted(current['operations'], key=lambda o: o['stage'])
        self.assertEqual(selection['stage'], 'selection')
        self.assertEqual(selection['receiptState'], 'completed')
        self.assertTrue(selection['freshCompletedReceipt'])
        shape = selection['geometry']
        self.assertEqual(shape['totalScanPoints'], 915)
        self.assertEqual(shape['estimatedTotalShots'], 10980000)
        self.assertEqual({k: shape['jobs'][0][k] for k in ('frequencyPoints', 'amplitudePoints', 'captureCount',
            'blocksPerPoint', 'shotsPerBlock', 'shotsPerPoint')}, {'frequencyPoints': 61, 'amplitudePoints': 5,
            'captureCount': 3, 'blocksPerPoint': 6, 'shotsPerBlock': 2000, 'shotsPerPoint': 12000})
        self.assertEqual(verification['geometry']['status'], 'not_registered')
        self.assertEqual(verification['acquisitionState'], 'acquiring')
        self.assertEqual(verification['progress']['completedScanPoints'], 245)
        self.assertEqual(verification['progress']['totalScanPoints'], 915)
        self.assertEqual(verification['progress']['asOfRevision'], state['workflowRevision'])
        self.assertFalse(verification['freshCompletedReceipt'])
        self.assertIsNone(verification['nativeAnalysisAccepted'])
        self.assertEqual(current['independentAcquisition']['status'], 'pending')
        self.assertTrue(current['independentAcquisition']['sameAttemptAllowed'])
        self.assertEqual(selection['operationId'].split('/')[0], verification['operationId'].split('/')[0])
        self.assertEqual(len([n for n in state['nodes'] if n.get('acquisitionState') == 'not_measured']), 12)

    def test_completed_pair_uses_fresh_receipts_and_disjoint_registered_raw(self):
        self.complete_fixture()
        current = self.chat()['acquisitionState']['nodes'][0]
        self.assertEqual(current['independentAcquisition']['status'], 'controller_accepted')
        self.assertTrue(current['independentAcquisition']['completedFreshReceiptsWithDisjointRegisteredRaw'])
        operation = self.adapter.calls[1]['operationId']
        with self.controller.store.transaction() as rt:
            first = json.loads(rt.db.execute('SELECT result FROM platform_effects WHERE operation_id=?',
                                            (self.adapter.calls[0]['operationId'],)).fetchone()[0])
            second = json.loads(rt.db.execute('SELECT result FROM platform_effects WHERE operation_id=?', (operation,)).fetchone()[0])
            second['artifacts'] = [a for a in second['artifacts'] if a['kind'] != 'raw'] + [a for a in first['artifacts'] if a['kind'] == 'raw']
            rt.db.execute('UPDATE platform_effects SET result=? WHERE operation_id=?', (dumps(second), operation))
        current = self.chat()['acquisitionState']['nodes'][0]
        self.assertEqual(current['independentAcquisition']['status'], 'not_established')
        self.assertFalse(current['independentAcquisition']['completedFreshReceiptsWithDisjointRegisteredRaw'])
        # A read-only explanation cannot rewrite even contradictory stored quality.
        self.assertEqual(current['controllerQuality'], 'passed')

    def test_stale_or_misbound_progress_cannot_claim_verify_acquisition(self):
        self.complete_fixture()
        with self.controller.store.transaction() as rt:
            wf = self.controller.get(self.workflow['id'], rt)
            node = wf['nodes'][0]
            node['telemetry'].update(controllerOperationId='different-operation', analysisAccepted=True)
            self.controller.store.save(rt, wf, 'offline-stale-progress')
        current = self.chat()['acquisitionState']['nodes'][0]
        self.assertTrue(all('progress' not in stage for stage in current['operations']))

    def test_snapshot_is_read_only_and_pins_workflow_and_effects_across_writer_commit(self):
        self.complete_fixture()
        with read_chat_snapshot(self.controller.store.path) as db:
            wf = json.loads(db.execute('SELECT payload FROM platform_workflows WHERE id=?', (self.workflow['id'],)).fetchone()[0])
            revision = wf['revision']
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("UPDATE platform_effects SET state='unknown'")
            with self.controller.store.transaction() as rt:
                changed = self.controller.get(self.workflow['id'], rt)
                self.controller.store.save(rt, changed, 'offline-concurrent-change')
                rt.db.execute("UPDATE platform_effects SET state='unknown'")
            state = acquisition_context(db, wf, self.root, now())
            self.assertEqual(state['workflowRevision'], revision)
            self.assertTrue(all(x['receiptState'] == 'completed' for x in state['nodes'][0]['operations']))
            self.assertEqual(db.total_changes, 0)
        self.assertGreater(self.controller.get(self.workflow['id'])['revision'], revision)

    def test_chat_snapshot_does_not_initialize_runtime_or_hold_read_tx_during_model(self):
        self.complete_fixture()
        before = self.controller.get(self.workflow['id'])
        original_brain = self.controller._brain
        def recorder(path, request):
            db = sqlite3.connect(self.controller.store.path, timeout=.1)
            try:
                db.execute('BEGIN IMMEDIATE')
                db.rollback()
            finally:
                db.close()
            return original_brain(path, request)
        self.controller._brain = recorder
        with patch('backend.store.Runtime', side_effect=AssertionError('chat must not initialize Runtime')):
            self.chat()
        after = self.controller.get(self.workflow['id'])
        before.pop('elapsed')
        after.pop('elapsed')
        self.assertEqual(after, before)

    def test_registered_plan_rejects_hash_identity_geometry_path_and_size_changes(self):
        self.complete_fixture()
        request = self.adapter.calls[0]
        with read_chat_snapshot(self.controller.store.path) as db:
            result = json.loads(db.execute('SELECT result FROM platform_effects WHERE operation_id=?', (request['operationId'],)).fetchone()[0])
            artifact = next(a for a in result['artifacts'] if a['name'] == 'plan.json')
            path = Path(db.execute('SELECT path FROM platform_artifacts WHERE id=?', (artifact['id'],)).fetchone()[0])
        original = path.read_bytes()
        for damage in ('hash', 'operation', 'requestIdentity', 'rows', 'shots', 'compile_only', 'oversize', 'outside'):
            with self.subTest(damage=damage):
                plan = json.loads(original)
                if damage == 'operation': plan['operationId'] = 'different/measure'
                if damage == 'requestIdentity': plan['requestIdentity']['parameterVersions'] = {'changed': 1}
                if damage == 'rows': plan['expectedJobRows'] = [916]
                if damage == 'shots': plan['estimated_total_shots'] = 1
                if damage == 'compile_only': plan['compileOnlyEvidence'] = True
                content = dumps(plan).encode() + (b' ' * (MAX_PLAN_BYTES + 1) if damage == 'oversize' else b'')
                path.write_bytes(content)
                changed = deepcopy(result)
                updated = next(a for a in changed['artifacts'] if a['id'] == artifact['id'])
                updated['sha256'] = artifact['sha256'] if damage == 'hash' else hashlib.sha256(content).hexdigest()
                if damage == 'hash': path.write_bytes(content + b' ')
                with self.controller.store.transaction() as rt:
                    metadata = deepcopy(updated)
                    rt.db.execute('UPDATE platform_artifacts SET path=?,sha256=?,payload=? WHERE id=?',
                        (str(self.root.parent / 'outside-plan.json') if damage == 'outside' else str(path),
                         updated['sha256'], dumps(metadata), artifact['id']))
                with read_chat_snapshot(self.controller.store.path) as db:
                    geometry = registered_plan_geometry(db, self.root, request, changed, [MAX_PLAN_READ_BYTES])
                self.assertNotEqual(geometry['status'], 'registered_plan')

    def test_budget_trims_optional_observations_without_rewriting_core_or_gates(self):
        core = {'workflow': {'id': 'w', 'revision': 9, 'gates': {'snr': 5}},
                'acquisitionState': {'workflowRevision': 9, 'nodes': [{'progress': {'completedScanPoints': 245, 'totalScanPoints': 915}}]}}
        context = {**deepcopy(core), 'observations': [{'id': str(i), 'text': '😀' * 10000} for i in range(20)],
                   'observationSummary': {'provided': 20, 'omitted': 0}}
        payload = bound_chat_observations({'message': '只读', 'context': context})
        self.assertLessEqual(javascript_json_units_upper_bound(payload), 190000)
        self.assertGreater(context['observationSummary']['omitted'], 0)
        self.assertEqual({k: context[k] for k in core}, core)
        with self.assertRaises(APIError) as error:
            bound_chat_observations({'message': 'x' * 200001, 'context': {'observations': []}})
        self.assertEqual(error.exception.code, 'CHAT_CONTEXT_TOO_LARGE')


if __name__ == '__main__':
    unittest.main()
