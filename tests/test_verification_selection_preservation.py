"""Offline fake acquisitions: completed selection survives incomplete verification."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from test_controller import make_fixture
except ImportError:
    from tests.test_controller import make_fixture


class VerificationSelectionPreservationTests(unittest.TestCase):
    def assert_pending_selection(self, controller, workflow_id, node_id, selection):
        detail = controller.detail(workflow_id, node_id)
        attempt, node = detail['attempts'][0], detail['node']
        pending = node['result']
        self.assertEqual(node['engineState'], 'verifying')
        self.assertFalse(node['mainProcedureComplete'])
        self.assertEqual(attempt['state'], 'running')
        self.assertNotIn('endedAt', attempt)
        self.assertEqual(attempt['result'], pending)
        self.assertTrue(pending['partial'])
        self.assertTrue(pending['verificationPending'])
        self.assertEqual(pending['quality']['status'], 'unverified')
        self.assertEqual(pending['metrics'], {})
        self.assertEqual(pending['artifacts'], [])
        actual = pending['selectionResult']
        self.assertEqual(actual['dataKind'], 'real')
        self.assertFalse(actual.get('partial'))
        self.assertEqual(actual['quality']['status'], 'unverified')
        self.assertNotIn('selectionResult', actual)
        self.assertNotIn('verificationPending', actual)
        for key in ('metrics', 'series', 'parameters', 'acquiredAt'):
            self.assertEqual(actual.get(key), selection.get(key))
        self.assertEqual([{key: item[key] for key in ('id', 'kind', 'name', 'sha256')} for item in actual['artifacts']],
            [{key: item[key] for key in ('id', 'kind', 'name', 'sha256')} for item in selection['artifacts']])
        for artifact in actual['artifacts']:
            self.assertTrue(controller.artifact(artifact['id'])[0].is_file())
        report = next(item for item in controller.report(workflow_id)['nodes'] if item['node']['id'] == node_id)
        self.assertEqual(report['attempts'][0]['result'], pending)
        with controller.store.connect() as rt:
            effects = {row['operation_id']: row['state'] for row in rt.db.execute(
                'SELECT operation_id,state FROM platform_effects WHERE attempt_id=?', (attempt['id'],))}
        self.assertEqual(effects, {attempt['id'] + '/measure': 'completed', attempt['id'] + '/verify': 'pending'})
        self.assertFalse(controller.get(workflow_id)['workflowComplete'])

    def test_verification_started_publishes_selection_until_final_result_replaces_placeholder(self):
        for verified_quality in ('passed', 'failed'):
            with self.subTest(quality=verified_quality), tempfile.TemporaryDirectory(prefix='qcal-pending-selection-offline-') as directory:
                controller, adapter = make_fixture(directory)
                try:
                    wf = controller.create({'name': 'OFFLINE pending selection', 'mode': 'real',
                        'policy': 'threshold', 'resourceIds': ['Q11']})
                    node_id = wf['nodes'][0]['id']
                    original_execute = adapter.execute
                    saved, observed = [], []
                    def execute(request, emit, cancel):
                        if request['operationId'].endswith('/verify'):
                            self.assert_pending_selection(controller, wf['id'], node_id, saved[0])
                            observed.append(controller.detail(wf['id'], node_id)['node']['result'])
                        result = original_execute(request, emit, cancel)
                        result['series'] = [{'name': 'OFFLINE SYNTHETIC FIXTURE', 'x': [1, 2, 3], 'y': [1, .5, 1]}]
                        if request['operationId'].endswith('/measure'):
                            saved.append(deepcopy(result))
                        elif verified_quality == 'failed':
                            result['metrics']['contrast_snr'] = 1
                        return result
                    adapter.execute = execute
                    self.assertEqual(controller._execute_node(wf['id'], node_id), verified_quality == 'passed')
                    self.assertEqual(len(observed), 1, 'the verify callback must inspect the committed placeholder')
                    detail = controller.detail(wf['id'], node_id)
                    result = detail['node']['result']
                    self.assertEqual(detail['attempts'][0]['result'], result)
                    self.assertEqual(result['quality']['status'], verified_quality)
                    self.assertNotIn('verificationPending', result)
                    self.assertNotIn('partial', result)
                    self.assertNotIn('selectionResult', result['selectionResult'])
                    self.assertEqual(result['selectionResult']['series'], saved[0]['series'])
                    self.assertEqual(result['selectionResult']['artifacts'], observed[0]['selectionResult']['artifacts'])
                    self.assertNotEqual(result['artifacts'], result['selectionResult']['artifacts'])
                    self.assertEqual(detail['node']['mainProcedureComplete'], verified_quality == 'passed')
                    self.assertEqual(observed[0]['quality']['status'], 'unverified')
                    self.assertTrue(observed[0]['verificationPending'])
                finally:
                    controller.close()

    def test_failed_or_paused_verification_keeps_complete_selection_as_unverified_history(self):
        for disposition in ('cancelled', 'failed_after_shutdown', 'unknown'):
            with self.subTest(disposition=disposition), tempfile.TemporaryDirectory(prefix='qcal-selection-offline-') as directory:
                root = Path(directory)
                controller, adapter = make_fixture(root)
                try:
                    wf = controller.create({'name': 'OFFLINE selection preservation', 'mode': 'real',
                        'policy': 'threshold', 'resourceIds': ['Q11']})
                    node_id = wf['nodes'][0]['id']
                    original_execute = adapter.execute
                    image = root / 'offline-selection.svg'
                    image.write_text('<svg xmlns="http://www.w3.org/2000/svg"><title>OFFLINE FIXTURE, NO MEASUREMENT</title></svg>')
                    image_artifact = {'id': 'offline-selection-image', 'name': image.name, 'kind': 'image',
                        'localPath': str(image), 'sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
                    partial = root / 'offline-verification-partial.txt'
                    partial.write_text('OFFLINE TEST BYTES, NOT ACQUIRED IQ')
                    partial_artifact = {'id': 'offline-verification-partial', 'name': partial.name, 'kind': 'raw',
                        'localPath': str(partial), 'sha256': hashlib.sha256(partial.read_bytes()).hexdigest()}
                    saved, observed = [], []
                    def execute(request, emit, cancel):
                        if request['operationId'].endswith('/measure'):
                            result = original_execute(request, emit, cancel)
                            result['artifacts'].append(deepcopy(image_artifact))
                            result['series'] = [{'name': 'OFFLINE SYNTHETIC FIXTURE', 'x': [1, 2, 3], 'y': [1, .5, 1]}]
                            saved.append(deepcopy(result))
                            return result
                        self.assert_pending_selection(controller, wf['id'], node_id, saved[0])
                        observed.append(True)
                        adapter.calls.append(deepcopy(request))
                        if disposition == 'cancelled':
                            current = controller.get(wf['id'])
                            controller.action(wf['id'], {'action': 'pause', 'operationId': 'pause-offline-verification',
                                'expectedRevision': current['revision']})
                        raise RuntimeError('OFFLINE verification did not complete')
                    adapter.execute = execute
                    adapter.reconcile = lambda operation: ({'status': 'unknown'} if disposition == 'unknown' else {
                        'status': disposition, 'shutdownConfirmed': True, 'partialArtifacts': [partial_artifact]})
                    self.assertFalse(controller._execute_node(wf['id'], node_id))
                    self.assertEqual(observed, [True])
                    current = controller.get(wf['id'])
                    detail = controller.detail(wf['id'], node_id)
                    result = detail['node']['result']
                    selection = result['selectionResult']
                    self.assertEqual(result['quality']['status'], 'unverified')
                    self.assertNotIn('verificationPending', result)
                    self.assertEqual(selection['quality']['status'], 'unverified')
                    self.assertNotIn('selectionResult', selection)
                    self.assertEqual(selection['metrics'], saved[0]['metrics'])
                    self.assertEqual(selection['series'], saved[0]['series'])
                    self.assertEqual(selection['parameters'], saved[0]['parameters'])
                    self.assertIn(image_artifact['id'], [a['id'] for a in selection['artifacts']])
                    self.assertEqual(detail['attempts'][0]['result'], result)
                    self.assertFalse(detail['node']['mainProcedureComplete'])
                    self.assertFalse(current['workflowComplete'])
                    self.assertEqual(len(adapter.calls), 2)
                    self.assertTrue(controller.artifact(image_artifact['id'])[0].is_file())
                    report_node = next(item for item in controller.report(wf['id'])['nodes'] if item['node']['id'] == node_id)
                    self.assertEqual(report_node['attempts'][0]['result']['selectionResult']['series'], selection['series'])
                    with controller.store.connect() as rt:
                        leases = [row[0] for row in rt.db.execute('SELECT state FROM platform_leases')]
                        self.assertEqual(leases, ['quarantined'] if disposition == 'unknown' else [])
                    if disposition == 'cancelled':
                        self.assertEqual(current['status'], 'paused')
                        self.assertEqual(detail['attempts'][0]['state'], 'cancelled')
                    else:
                        self.assertEqual(current['engineState'], 'needs_reconciliation' if disposition == 'unknown' else 'failed_after_shutdown')
                finally:
                    controller.close()

    def test_post_acquisition_verification_error_clears_pending_and_retains_selection(self):
        with tempfile.TemporaryDirectory(prefix='qcal-verification-analysis-offline-') as directory:
            controller, adapter = make_fixture(directory)
            try:
                wf = controller.create({'name': 'OFFLINE verification analysis error', 'mode': 'real',
                    'policy': 'threshold', 'resourceIds': ['Q11']})
                node_id = wf['nodes'][0]['id']
                original_execute = adapter.execute
                saved, observed = [], []
                def execute(request, emit, cancel):
                    if request['operationId'].endswith('/verify'):
                        self.assert_pending_selection(controller, wf['id'], node_id, saved[0])
                        observed.append(True)
                    result = original_execute(request, emit, cancel)
                    result['series'] = [{'name': 'OFFLINE SYNTHETIC FIXTURE', 'x': [1, 2], 'y': [0, 1]}]
                    if request['operationId'].endswith('/measure'):
                        saved.append(deepcopy(result))
                    return result
                adapter.execute = execute
                with patch.object(controller, '_repeatability', side_effect=RuntimeError('OFFLINE post-acquisition analysis error')):
                    self.assertFalse(controller._execute_node(wf['id'], node_id))
                self.assertEqual(observed, [True])
                detail = controller.detail(wf['id'], node_id)
                result = detail['node']['result']
                self.assertEqual(detail['attempts'][0]['result'], result)
                self.assertEqual(detail['attempts'][0]['state'], 'execution_failed')
                self.assertEqual(detail['node']['engineState'], 'execution_failed')
                self.assertEqual(result['quality']['status'], 'unverified')
                self.assertNotIn('partial', result)
                self.assertNotIn('verificationPending', result)
                self.assertNotIn('selectionResult', result['selectionResult'])
                self.assertEqual(result['selectionResult']['series'], saved[0]['series'])
                self.assertEqual(result['series'], saved[0]['series'])
                self.assertEqual(result['metrics'], saved[0]['metrics'])
                self.assertEqual(result['artifacts'][0]['id'], 'fake-artifact-2')
                self.assertEqual(result['selectionResult']['artifacts'][0]['id'], 'fake-artifact-1')
                self.assertIn('OFFLINE post-acquisition analysis error', result['quality']['reasons'][0])
                self.assertFalse(detail['node']['mainProcedureComplete'])
                with controller.store.connect() as rt:
                    self.assertEqual([row[0] for row in rt.db.execute('SELECT state FROM platform_effects')], ['completed', 'completed'])
                    self.assertEqual(list(rt.db.execute('SELECT state FROM platform_leases')), [])
            finally:
                controller.close()

    def test_selection_failure_does_not_invent_completed_selection_history(self):
        with tempfile.TemporaryDirectory(prefix='qcal-no-selection-offline-') as directory:
            controller, adapter = make_fixture(directory)
            try:
                wf = controller.create({'name': 'OFFLINE no selection', 'mode': 'real',
                    'policy': 'threshold', 'resourceIds': ['Q11']})
                adapter.fail = True
                adapter.reconcile = lambda operation: {'status': 'failed_after_shutdown',
                    'shutdownConfirmed': True, 'partialArtifacts': []}
                self.assertFalse(controller._execute_node(wf['id'], wf['nodes'][0]['id']))
                result = controller.detail(wf['id'], wf['nodes'][0]['id'])['node']['result']
                self.assertNotIn('selectionResult', result)
                self.assertEqual(result['quality']['status'], 'unverified')
                self.assertEqual(len(adapter.calls), 1)
            finally:
                controller.close()

    def test_final_commit_rollback_clears_durable_placeholder_without_publishing_passed_result(self):
        with tempfile.TemporaryDirectory(prefix='qcal-verification-commit-offline-') as directory:
            controller, adapter = make_fixture(directory)
            try:
                wf = controller.create({'name': 'OFFLINE final verification commit error', 'mode': 'real',
                    'policy': 'threshold', 'resourceIds': ['Q11']})
                node_id = wf['nodes'][0]['id']
                pending = []
                def failed_commit(rt, current, node, candidate, versions):
                    self.assertEqual(node['result']['quality']['status'], 'passed')
                    # A separate read sees the durable pre-commit placeholder.
                    stored = controller.detail(wf['id'], node_id)['node']['result']
                    self.assertTrue(stored['verificationPending'])
                    pending.append(deepcopy(stored))
                    raise RuntimeError('OFFLINE final commit rollback')
                with patch.object(controller, '_commit_candidate', side_effect=failed_commit):
                    self.assertFalse(controller._execute_node(wf['id'], node_id))
                self.assertEqual(len(pending), 1)
                detail = controller.detail(wf['id'], node_id)
                result = detail['node']['result']
                self.assertEqual(detail['attempts'][0]['result'], result)
                self.assertEqual(detail['attempts'][0]['state'], 'execution_failed')
                self.assertEqual(detail['node']['engineState'], 'execution_failed')
                self.assertEqual(result['quality']['status'], 'unverified')
                self.assertEqual(result['metrics'], pending[0]['selectionResult']['metrics'])
                self.assertNotIn('partial', result)
                self.assertNotIn('verificationPending', result)
                self.assertEqual(result['selectionResult'], pending[0]['selectionResult'])
                self.assertNotIn('selectionResult', result['selectionResult'])
                self.assertEqual(result['artifacts'][0]['id'], 'fake-artifact-2')
                self.assertEqual(result['selectionResult']['artifacts'][0]['id'], 'fake-artifact-1')
                self.assertIn('OFFLINE final commit rollback', result['quality']['reasons'][0])
                self.assertFalse(detail['node']['mainProcedureComplete'])
                self.assertEqual(len(adapter.calls), 2)
            finally:
                controller.close()

    def test_reconcile_preserves_only_same_attempt_completed_selection(self):
        for disposition in ('completed', 'cancelled', 'failed_after_shutdown'):
            for binding in ('valid', 'wrong_attempt', 'changed_selection'):
                with self.subTest(disposition=disposition, binding=binding), tempfile.TemporaryDirectory(prefix='qcal-selection-reconcile-offline-') as directory:
                    controller, adapter = make_fixture(directory)
                    try:
                        wf = controller.create({'name': 'OFFLINE selection reconciliation', 'mode': 'real',
                            'policy': 'threshold', 'resourceIds': ['Q11']})
                        node_id = wf['nodes'][0]['id']
                        original_execute = adapter.execute
                        verification = []
                        def execute(request, emit, cancel):
                            result = original_execute(request, emit, cancel)
                            result['series'] = [{'name': 'OFFLINE SYNTHETIC FIXTURE', 'x': [1, 2], 'y': [0, 1]}]
                            if request['operationId'].endswith('/verify'):
                                verification.append(deepcopy(result))
                                raise RuntimeError('OFFLINE verification receipt initially unavailable')
                            return result
                        adapter.execute = execute
                        self.assertFalse(controller._execute_node(wf['id'], node_id))
                        current = controller.get(wf['id'])
                        self.assertEqual(current['engineState'], 'needs_reconciliation')
                        attempt = controller.detail(wf['id'], node_id)['attempts'][0]
                        selection = deepcopy(attempt['result']['selectionResult'])
                        with controller.store.transaction() as rt:
                            if binding == 'wrong_attempt':
                                rt.db.execute('UPDATE platform_effects SET attempt_id=? WHERE operation_id=?',
                                    ('another-offline-attempt', attempt['operationId']))
                            elif binding == 'changed_selection':
                                attempt['result']['selectionResult']['metrics']['contrast_snr'] = 999
                                controller.store.attempt(rt, wf['id'], attempt)
                        if disposition == 'completed':
                            receipt = {'status': 'completed', 'result': verification[0]}
                        else:
                            receipt = {'status': disposition, 'shutdownConfirmed': True, 'partialArtifacts': []}
                        adapter.reconcile = lambda operation: deepcopy(receipt)
                        reconciled = controller.action(wf['id'], {'action': 'reconcile',
                            'operationId': 'offline-find-verification-receipt', 'expectedRevision': current['revision']})
                        self.assertEqual((reconciled['status'], reconciled['engineState']), ('paused', 'reconciled'))
                        detail = controller.detail(wf['id'], node_id)
                        result = detail['node']['result']
                        self.assertEqual(detail['attempts'][0]['result'], result)
                        self.assertEqual(result['quality']['status'], 'unverified')
                        self.assertNotIn('verificationPending', result)
                        self.assertFalse(detail['node']['mainProcedureComplete'])
                        self.assertEqual(len(adapter.calls), 2)
                        if binding == 'valid':
                            self.assertEqual(result['selectionResult'], selection)
                            self.assertNotIn('selectionResult', result['selectionResult'])
                        else:
                            self.assertNotIn('selectionResult', result)
                    finally:
                        controller.close()


if __name__ == '__main__':
    unittest.main()
