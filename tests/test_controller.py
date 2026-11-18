"""Controller contracts using only an explicit fake adapter and temporary data.

No F8 adapter is instantiated, no instrument module is imported, and no model
endpoint is called. Failures are actual control-contract regressions.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from backend.common import APIError, dumps, now
from backend.controller import Controller, configured_adapter
from backend.quality import evaluate, validate_proposal


class FakeAdapter:
    test_adapter = True
    def __init__(self, root):
        self.root = Path(root)
        self.calls = []
        self.data_kind = 'real'
        self.same_bytes = False
        self.before_return = None
        self.fail = False
        self.cancel_observed = False
        self.candidate = {}
        self.candidates_by_call = None
        self.profile = {
            'enabled': True, 'connectionConfirmedAt': now(), 'maximumRunSeconds': 5,
            'instrument': {'resourceGroup': 'fake-shared-bus'},
            'resources': {'Q11': {'q': 11, 'readout_hz': 6.8e9, 'readout_amp': .2, 'drive_hz': 4.4e9}},
            'parameterBounds': {'readout_hz': [6e9, 8e9], 'drive_hz': [3e9, 6e9],
                                'readout_amp': [0, 1], 'drive_amp': [0, 1], 'shots': [1, 2000],
                                'amp_start': [0, 1], 'amp_stop': [0, 1], 'points': [2, 161]},
        }

    def capabilities(self):
        return {'procedures': ['ro_resonator_survey', 'sq_spectroscopy', 'sq_rabi_amplitude',
                               'sq_rabi_time', 'ro_iq01', 'ro_optimize', 'sq_t1',
                               'sq_cpmg', 'sq_ramsey', 'sq_echo', 'sq_rb']}

    def preflight(self):
        return {'ready': True, 'enabled': self.profile['enabled'], 'hardwareConnected': False,
                'note': 'TEST FAKE ONLY - never connected hardware'}

    def build_verification_parameters(self, procedure, parameters, candidate):
        parameters = deepcopy(parameters)
        if procedure == 'ro_optimize' and 'readout_amp' in candidate:
            parameters['amplitudes'] = [candidate['readout_amp']]
        return parameters

    def execute(self, request, emit, cancel):
        self.calls.append(deepcopy(request))
        if self.fail:
            raise RuntimeError('fake lost receipt after dispatch')
        if self.before_return:
            self.before_return(request, emit, cancel)
        self.cancel_observed = bool(cancel())
        directory = self.root / 'data' / 'fake-raw'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ('acquisition-' + str(len(self.calls)) + '.json')
        content = b'fixed-identical-acquisition' if self.same_bytes else dumps(
            {'operation': request['operationId'], 'sample': len(self.calls)}).encode()
        path.write_bytes(content)
        return {
            'dataKind': self.data_kind, 'acquiredAt': now(),
            'parameters': deepcopy(request['parameters']),
            'metrics': {'contrast_snr': 8, 'peak_snr': 8, 'r_squared': .95, 'cycles': 2,
                        'assignment_fidelity': .92, 'relative_stderr': .1, 't1_s': 40e-6,
                        'gate_fidelity': .99, 'reference_drift_fraction': .02},
            'quality': {'status': 'passed', 'reasons': ['untrusted adapter claim']},
            'artifacts': [{'id': 'fake-artifact-' + str(len(self.calls)), 'name': path.name,
                           'kind': 'raw', 'localPath': str(path),
                           'sha256': hashlib.sha256(content).hexdigest()}],
            'candidateParameters': deepcopy(self.candidates_by_call[len(self.calls)-1]
                                             if self.candidates_by_call else self.candidate),
        }

    def reconcile(self, operation_id):
        return {'status': 'unknown', 'operationId': operation_id}


def make_fixture(root):
    root = Path(root)
    imports = root / 'data' / 'imports'
    imports.mkdir(parents=True, exist_ok=True)
    (imports / 'history.json').write_text(json.dumps({
        'version': 1, 'resources': [{'id': 'Q11', 'label': 'Q11', 'engineKey': 'Q11'}],
        'records': []}), encoding='utf-8')
    adapter = FakeAdapter(root)
    controller = Controller(root, adapter=adapter, start_worker=False)
    controller._brain = lambda *a, **kw: {'status': 'waiting_model', 'configured': False,
                                        'message': 'explicit offline test'}
    return controller, adapter


class ControllerContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-controller-test-')
        self.root = Path(self.directory.name)
        self.controller, self.adapter = make_fixture(self.root)

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def create(self, mode='real', **overrides):
        return self.controller.create({'name': 'Fake adapter contract test', 'resourceIds': ['Q11'],
                                       'mode': mode, 'policy': 'threshold', **overrides})

    def command(self, wf, action, operation='operation', **extra):
        return self.controller.action(wf['id'], {'action': action, 'operationId': operation,
                                                'expectedRevision': wf['revision'], **extra})

    def assert_code(self, code, fn):
        with self.assertRaises(APIError) as error:
            fn()
        self.assertEqual(error.exception.code, code)

    def test_adapter_factory_defaults_to_arclight_and_requires_allowlisted_profile(self):
        profiles = self.root / 'adapters'
        profiles.mkdir()
        (profiles / 'arclight_profile.json').write_text('{}')
        (profiles / 'f8_profile.json').write_text('{}')
        made = []
        constructor = lambda root, profile: made.append((root, profile)) or self.adapter
        with patch.dict('sys.modules', {'adapters.arclight': types.SimpleNamespace(ArcLightAdapter=constructor)}):
            self.assertIs(configured_adapter(self.root), self.adapter)
        self.assertEqual(made[-1][1].name, 'arclight_profile.json')
        settings = self.root / 'config'
        settings.mkdir()
        local = settings / 'runtime.local.json'
        local.write_text(json.dumps({'adapter': 'f8-native-cy8'}))
        with patch('adapters.f8.F8Adapter', side_effect=constructor):
            self.assertIs(configured_adapter(self.root), self.adapter)
        self.assertEqual(made[-1][1].name, 'f8_profile.json')
        local.write_text(json.dumps({'adapter': 'arbitrary.module'}))
        self.assert_code('UNKNOWN_ADAPTER', lambda: configured_adapter(self.root))
        local.write_text(json.dumps({'adapter': 'f8-native-cy8', 'profile': '../outside.json'}))
        self.assert_code('PATH_OUTSIDE_ROOT', lambda: configured_adapter(self.root))

    def test_unmarked_adapter_is_rejected_and_physical_requires_full_contract(self):
        class Unmarked(FakeAdapter):
            test_adapter = False
        self.assert_code('ADAPTER_CONTRACT_REQUIRED', lambda: Controller(self.root, adapter=Unmarked(self.root), start_worker=False))
        unmarked = Unmarked(self.root)
        unmarked.physical_hardware = True
        self.assert_code('ADAPTER_CONTRACT_REQUIRED', lambda: Controller(self.root, adapter=unmarked, start_worker=False))

    def test_physical_ownership_hook_cannot_silently_use_test_bypass(self):
        self.controller._physical_adapter = True
        with patch.object(self.adapter, 'hardware_ownership', return_value={'verified': False}, create=True) as owner:
            self.assertFalse(self.controller.hardware_ownership(refresh=True)['verified'])
            owner.assert_called_once_with(refresh=True)
        with patch.object(self.adapter, 'hardware_ownership', return_value={'verified': True, 'owners': [{'pid': 123}]}, create=True):
            wf = self.command(self.create(), 'start')
            self.controller.run_workflow(wf['id'])
            self.assertEqual(self.controller.get(wf['id'])['engineState'], 'waiting_hardware')
            self.assertEqual(self.adapter.calls, [])

    def test_task_cannot_resume_under_different_adapter_or_profile(self):
        wf = self.command(self.create(), 'start')
        self.controller.adapter_id = 'another-physical-device'
        self.controller.run_workflow(wf['id'])
        self.assertEqual(self.controller.get(wf['id'])['engineState'], 'adapter_changed')
        self.assertEqual(self.adapter.calls, [])

    def test_physical_source_manifest_version_is_frozen_with_task(self):
        manifest = self.root / 'offline-source-manifest.json'
        manifest.write_text('[]')
        adapter = FakeAdapter(self.root)
        adapter.physical_hardware = True
        adapter.profile.update(id='offline-physical-contract', sourceManifest=manifest.name)
        adapter.with_execution_enabled = lambda: adapter
        adapter.hardware_ownership = lambda refresh=False: {'verified': True, 'owners': []}
        adapter.build_plan = lambda request: {'estimated_total_shots': 1}
        controller = Controller(self.root, adapter=adapter, start_worker=False)
        try:
            wf = controller.create({'name': 'Manifest binding test', 'resourceIds': ['Q11'], 'mode': 'real'})
            manifest.write_text('[{"sha256":"changed-offline-test-source"}]')
            self.assert_code('ADAPTER_CHANGED', lambda: controller._execute_node(wf['id'], wf['nodes'][0]['id']))
            self.assertEqual(adapter.calls, [])
        finally:
            controller.close()

    def test_cancelled_after_shutdown_releases_lease_preserves_partial_and_resumes_new_attempt(self):
        wf = self.create()
        node_id = wf['nodes'][0]['id']
        original_execute = self.adapter.execute
        partial = self.root / 'partial-capture.json'
        partial.write_text('{"partial_real_fake_test":1}')
        artifact = {'id': 'partial-test-artifact', 'name': partial.name, 'kind': 'raw', 'localPath': str(partial),
                    'sha256': hashlib.sha256(partial.read_bytes()).hexdigest()}
        def cancelled(request, emit, cancel):
            self.adapter.calls.append(deepcopy(request))
            current = self.controller.get(wf['id'])
            self.command(current, 'pause', 'pause-during-fake-partial')
            raise RuntimeError('CANCELLED_AFTER_SHUTDOWN')
        self.adapter.execute = cancelled
        self.adapter.reconcile = lambda operation: {'status': 'cancelled', 'shutdownConfirmed': True, 'partialArtifacts': [artifact]}
        self.assertFalse(self.controller._execute_node(wf['id'], node_id))
        paused = self.controller.get(wf['id'])
        self.assertEqual(paused['status'], 'paused')
        self.assertIsNone(paused['cancelRequested'])
        detail = self.controller.detail(wf['id'], node_id)
        self.assertEqual(detail['attempts'][0]['state'], 'cancelled')
        self.assertTrue(detail['attempts'][0]['result']['partial'])
        self.assertEqual(detail['attempts'][0]['result']['quality']['status'], 'unverified')
        self.assertEqual(detail['attempts'][0]['result']['artifacts'][0]['id'], artifact['id'])
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0], 0)
            self.assertEqual(rt.db.execute('SELECT state FROM platform_effects').fetchone()[0], 'cancelled')
        old_operation = self.adapter.calls[0]['operationId']
        self.adapter.execute = original_execute
        self.command(paused, 'resume', 'resume-after-confirmed-shutdown')
        self.assertTrue(self.controller._execute_node(wf['id'], node_id))
        self.assertNotEqual(self.adapter.calls[1]['operationId'], old_operation)

    def test_shutdown_failure_receipt_is_not_success_and_unknown_shutdown_stays_quarantined(self):
        for confirmed in (True, False):
            with self.subTest(shutdown_confirmed=confirmed):
                wf = self.create()
                self.adapter.fail = True
                self.adapter.reconcile = lambda operation: {'status': 'failed_after_shutdown', 'shutdownConfirmed': confirmed, 'partialArtifacts': []}
                self.assertFalse(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
                current = self.controller.get(wf['id'])
                self.assertEqual(current['status'], 'attention')
                self.assertEqual(current['engineState'], 'failed_after_shutdown' if confirmed else 'needs_reconciliation')
                with self.controller.store.connect() as rt:
                    leases = rt.db.execute('SELECT state FROM platform_leases WHERE workflow_id=?', (wf['id'],)).fetchall()
                    self.assertEqual([row[0] for row in leases], [] if confirmed else ['quarantined'])

    def test_reconcile_cancelled_receipt_resolves_quarantine_without_reexecuting(self):
        wf = self.create()
        self.adapter.fail = True
        self.controller._execute_node(wf['id'], wf['nodes'][0]['id'])
        quarantined = self.controller.get(wf['id'])
        self.assertEqual(quarantined['engineState'], 'needs_reconciliation')
        self.adapter.reconcile = lambda operation: {'status': 'cancelled', 'shutdownConfirmed': True, 'partialArtifacts': []}
        reconciled = self.command(quarantined, 'reconcile', 'find-shutdown-receipt')
        self.assertEqual(reconciled['status'], 'paused')
        self.assertEqual(len(self.adapter.calls), 1)
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0], 0)

    def test_abort_after_confirmed_shutdown_finishes_aborted(self):
        wf = self.create()
        def abort_and_stop(request, emit, cancel):
            self.command(self.controller.get(wf['id']), 'abort', 'abort-after-point')
            raise RuntimeError('CANCELLED_AFTER_SHUTDOWN')
        self.adapter.before_return = abort_and_stop
        self.adapter.reconcile = lambda operation: {'status': 'cancelled', 'shutdownConfirmed': True, 'partialArtifacts': []}
        self.assertFalse(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
        current = self.controller.get(wf['id'])
        self.assertEqual(current['status'], 'aborted')
        self.assertIsNone(current['cancelRequested'])
        self.assertFalse(current['workflowComplete'])

    def test_parameter_save_is_validated_scoped_idempotent_and_does_not_acquire(self):
        wf = self.create()
        node = wf['nodes'][1]
        submitted = []
        with patch.object(self.adapter, 'build_plan', side_effect=lambda request: submitted.append(deepcopy(request)) or {}, create=True):
            payload = {'operationId': 'edit-params', 'expectedRevision': wf['revision'], 'parameters': {'drive_hz': 4.55e9, 'shots': 600}}
            result = self.controller.set_parameters(wf['id'], node['id'], payload)
            again = self.controller.set_parameters(wf['id'], node['id'], payload)
        self.assertEqual(result, again)
        self.assertEqual(result['status'], 'draft')
        self.assertEqual(result['nodes'][1]['parameterOverrides'], payload['parameters'])
        self.assertNotIn('parameterOverrides', result['nodes'][0])
        self.assertEqual(submitted[0]['parameters']['drive_hz'], 4.55e9)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(self.adapter.calls, [])
        detail = self.controller.detail(wf['id'], node['id'])
        self.assertEqual(detail['parameters']['drive_hz'], 4.55e9)
        self.assertTrue(detail['parameterBounds']['shots']['integer'])
        self.assertEqual(detail['decisions'][-1]['source'], 'operator')
        self.assertEqual(detail['decisions'][-1]['parameterChanges']['drive_hz'], {'from': 4.4e9, 'to': 4.55e9})
        self.assert_code('STALE_REVISION', lambda: self.controller.set_parameters(wf['id'], node['id'], {**payload, 'operationId': 'stale-edit'}))

    def test_parameter_save_rejects_unknown_nonfinite_fractional_and_unknown_effect(self):
        wf = self.create()
        node = wf['nodes'][0]
        for index, changes in enumerate(({'drive_hz': 9e9}, {'shots': 2.5}, {'unlisted': 1}, {'drive_amp': True})):
            self.assert_code('UNKNOWN_PARAMETER' if 'unlisted' in changes else 'PARAMETER_OUT_OF_RANGE', lambda: self.controller.set_parameters(
                wf['id'], node['id'], {'operationId': 'bad-param-'+str(index), 'expectedRevision': wf['revision'], 'parameters': changes}))
        with self.controller.store.transaction() as rt:
            rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,NULL)', ('unknown-only', wf['id'], 'old', 'unknown', '{}'))
        self.assert_code('RECONCILIATION_REQUIRED', lambda: self.controller.set_parameters(wf['id'], node['id'],
            {'operationId': 'no-edit-during-unknown', 'expectedRevision': wf['revision'], 'parameters': {'drive_hz': 4.5e9}}))

    def test_parameter_save_invalidates_result_and_preserves_prior_attempt(self):
        wf = self.create()
        node = wf['nodes'][0]
        self.controller._execute_node(wf['id'], node['id'])
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current.update(status='paused', engineState='paused')
            self.controller.store.save(rt, current, 'fixture_pause')
        before = self.controller.get(wf['id'])
        count = len(self.adapter.calls)
        saved = self.controller.set_parameters(wf['id'], node['id'], {'operationId': 'edit-after-capture', 'expectedRevision': before['revision'], 'parameters': {'readout_hz': 6.9e9}})
        self.assertEqual(saved['status'], 'paused')
        self.assertTrue(saved['nodes'][0]['result']['stale'])
        self.assertEqual(saved['nodes'][1]['engineState'], 'stale')
        self.assertEqual(self.controller.detail(wf['id'], node['id'])['attempts'][0]['result']['quality']['status'], 'passed')
        self.assertEqual(len(self.adapter.calls), count)

    def test_explicit_decide_queues_model_only_and_never_reacquires_on_wait(self):
        wf = self.create(policy='autopilot')
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current.update(status='attention', engineState='waiting_model', currentNodeId=current['nodes'][0]['id'])
            current['nodes'][0].update(engineState='quality_attention', result={'artifacts': [], 'quality': {'status': 'failed'}})
            self.controller.store.save(rt, current, 'fixture_model_wait')
        current = self.controller.get(wf['id'])
        queued = self.command(current, 'decide', 'explicit-decide', nodeId=current['currentNodeId'])
        self.assertEqual(queued['engineState'], 'decision_queued')
        calls = []
        self.controller._brain = lambda path, payload: calls.append(path) or {'status': 'ok', 'decision': {'action': 'wait', 'reason': 'Need current evidence', 'evidenceIds': []}}
        self.controller.run_workflow(wf['id'])
        self.assertEqual(calls, ['/decide'])
        current = self.controller.get(wf['id'])
        self.assertEqual(current['engineState'], 'waiting_operator')
        self.assertEqual(current['nodes'][0]['engineState'], 'waiting_operator')
        self.assertEqual(current['nodes'][0]['detail'], 'Need current evidence')
        self.assertEqual(self.adapter.calls, [])

    def test_model_failure_updates_node_and_workflow_without_losing_measurement(self):
        for response, message in (({}, '模型尚未返回有效决策'), (None, '模型尚未返回有效决策'),
                ({'status': 'waiting_model', 'message': '模型返回空内容'}, '模型返回空内容'),
                ({'status': 'error', 'error': '模型请求超时'}, '模型请求超时')):
            with self.subTest(response=response):
                wf = self.create(policy='autopilot')
                result = {'artifacts': [], 'metrics': {'contrast_snr': 2},
                          'quality': {'status': 'failed', 'reasons': ['weak response']}}
                with self.controller.store.transaction() as rt:
                    current = self.controller.get(wf['id'], rt)
                    current.update(status='attention', engineState='quality_attention', currentNodeId=current['nodes'][0]['id'])
                    current['nodes'][0].update(engineState='quality_attention', result=deepcopy(result), progress=100)
                    self.controller.store.save(rt, current, 'fixture_model_failure')
                self.controller._brain = lambda *args: deepcopy(response)
                self.assertFalse(self.controller._decide(wf['id'], wf['nodes'][0]['id']))
                current = self.controller.get(wf['id'])
                node = current['nodes'][0]
                self.assertEqual((current['status'], current['engineState']), ('attention', 'waiting_model'))
                self.assertEqual((node['status'], node['engineState']), ('attention', 'waiting_model'))
                self.assertEqual(node['detail'], message)
                self.assertEqual(node['result'], result)
                self.assertEqual(node['progress'], 100)
                self.assertEqual(current['events'][-1]['message'], message)
                self.assertEqual(self.adapter.calls, [])

    def test_model_stop_and_exhausted_retry_update_current_node_state(self):
        for action, state in (('stop', 'budget_or_evidence_stop'), ('retry', 'budget_exhausted')):
            with self.subTest(action=action):
                wf = self.create(policy='autopilot')
                with self.controller.store.transaction() as rt:
                    current = self.controller.get(wf['id'], rt)
                    current['currentNodeId'] = current['nodes'][0]['id']
                    current['nodes'][0].update(attempt=current['maxAttemptsPerNode'],
                        result={'artifacts': [], 'quality': {'status': 'failed'}})
                    self.controller.store.save(rt, current, 'fixture_model_terminal')
                self.controller._brain = lambda *args: {'status': 'ok', 'decision': {
                    'action': action, 'reason': 'Insufficient evidence', 'evidenceIds': []}}
                self.assertFalse(self.controller._decide(wf['id'], wf['nodes'][0]['id']))
                current = self.controller.get(wf['id'])
                self.assertEqual(current['engineState'], state)
                self.assertEqual(current['nodes'][0]['engineState'], state)
                self.assertEqual(current['nodes'][0]['status'], 'attention')
                self.assertIn('Insufficient evidence', current['nodes'][0]['detail'])
                self.assertEqual(self.adapter.calls, [])

    def test_model_proposal_rejection_preserves_completed_acquisition_and_attempt(self):
        proposals = [({'action': 'retry', 'reason': 'wrong evidence', 'evidenceIds': ['old-attempt-artifact']}, 'BAD_EVIDENCE'),
                     ({'action': 'retry', 'reason': 'bad parameters', 'evidenceIds': [], 'parameterChanges': {'drive_amp': 2}}, 'PARAMETER_OUT_OF_RANGE'),
                     ({'action': 'backtrack', 'reason': 'not an ancestor', 'targetNodeId': 'unknown', 'evidenceIds': []}, 'BAD_BACKTRACK')]
        for proposal, code in proposals:
            with self.subTest(code=code):
                wf = self.create(policy='autopilot')
                node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
                self.adapter.candidate = {'drive_amp': 2}
                self.controller._brain = lambda *args: {'status': 'ok', 'decision': deepcopy(proposal)}
                before = len(self.adapter.calls)
                self.assertFalse(self.controller._execute_node(wf['id'], node['id']))
                detail = self.controller.detail(wf['id'], node['id'])
                current = self.controller.get(wf['id'])
                self.assertEqual(current['engineState'], 'waiting_model')
                self.assertEqual(detail['node']['lastDecisionError']['code'], code)
                self.assertEqual(detail['attempts'][0]['state'], 'attention')
                self.assertNotIn('error', detail['attempts'][0])
                self.assertEqual(detail['attempts'][0]['result'], detail['node']['result'])
                self.assertEqual(detail['node']['result']['candidateParameters'], {'drive_amp': 2})
                self.assertEqual(len(self.adapter.calls) - before, 1)
                self.assertTrue(self.controller.artifact(detail['node']['result']['artifacts'][0]['id'])[0].is_file())
                events = [e for e in self.controller.store.events(wf['id']) if e['event'] == 'model_decision_rejected']
                self.assertEqual(events[-1]['payload']['errorCode'], code)

    def test_legacy_bad_evidence_failure_can_only_queue_model_for_retained_complete_result(self):
        for damage in (None, 'hardware_error', 'partial', 'stale', 'unknown_effect', 'missing_effect', 'lease'):
            with self.subTest(damage=damage):
                wf = self.create()
                node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
                self.adapter.candidate = {'drive_amp': 2}
                self.assertFalse(self.controller._execute_node(wf['id'], node['id']))
                with self.controller.store.transaction() as rt:
                    current = self.controller.get(wf['id'], rt)
                    current.update(status='attention', engineState='execution_failed', policy='autopilot', currentNodeId=node['id'])
                    current_node = self.controller._node(current, node['id'])
                    message = 'fake driver timeout' if damage == 'hardware_error' else '决策引用了当前节点之外的证据'
                    current_node.update(status='attention', engineState='execution_failed', detail=message)
                    if damage in ('partial', 'stale'):
                        current_node['result'][damage] = True
                    attempt = self.controller.store.detail(wf['id'], node['id'])[0][-1]
                    attempt.update(state='execution_failed', error=message, result=deepcopy(current_node['result']))
                    self.controller.store.attempt(rt, wf['id'], attempt)
                    if damage == 'unknown_effect':
                        rt.db.execute("UPDATE platform_effects SET state='unknown' WHERE attempt_id=?", (attempt['id'],))
                    if damage == 'missing_effect':
                        rt.db.execute('DELETE FROM platform_effects WHERE attempt_id=?', (attempt['id'],))
                    if damage == 'lease':
                        rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)',
                                      ('offline-legacy', wf['id'], attempt['id'], 'quarantined', 'offline-token'))
                    self.controller.store.save(rt, current, 'fixture_legacy_decision_failure')
                before_attempts = self.controller.store.detail(wf['id'], node['id'])[0]
                before_calls = len(self.adapter.calls)
                current = self.controller.get(wf['id'])
                command = lambda: self.command(current, 'decide', 'legacy-decide-' + wf['id'], nodeId=node['id'])
                if damage is None:
                    queued = command()
                    self.assertEqual(queued['engineState'], 'decision_queued')
                else:
                    self.assert_code('RECONCILIATION_REQUIRED' if damage == 'lease' else 'INVALID_STATE', command)
                self.assertEqual(self.controller.store.detail(wf['id'], node['id'])[0], before_attempts)
                self.assertEqual(len(self.adapter.calls), before_calls)

    def test_decision_context_and_controller_allow_current_selection_but_exclude_history_evidence(self):
        wf = self.create()
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
        self.adapter.candidates_by_call = [{'drive_amp': .4}, {'drive_amp': .8}]
        self.assertFalse(self.controller._execute_node(wf['id'], node['id']))
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current['policy'] = 'autopilot'
            current_node = self.controller._node(current, node['id'])
            self.controller._record_decision(rt, current, node['id'], {'action': 'retry',
                'reason': 'Earlier trial explanation only', 'evidenceIds': ['prior-artifact-sentinel'],
                'parameterChanges': {'shots': 1000}}, 'model')
            self.controller.store.save(rt, current, 'fixture_historical_decision')
        prior_detail = self.controller.detail(wf['id'], node['id'])
        result = prior_detail['node']['result']
        selection_id = result['selectionResult']['artifacts'][0]['id']
        expected = {a['id'] for source in (result, result['selectionResult']) for a in source['artifacts']}
        calls = []
        def brain(path, payload):
            calls.append(deepcopy(payload))
            return {'status': 'ok', 'decision': {'action': 'wait', 'reason': 'Inspect current independent pair',
                                               'evidenceIds': [selection_id]}}
        self.controller._brain = brain
        self.assertFalse(self.controller._decide(wf['id'], node['id']))
        self.assertEqual(set(calls[0]['allowedEvidenceIds']), expected)
        self.assertNotIn('prior-artifact-sentinel', dumps(calls[0]))
        self.assertEqual(calls[0]['observations']['result']['metrics'], result['metrics'])
        self.assertEqual(calls[0]['history'][-1]['parameterChanges'], {'shots': 1000})
        after = self.controller.detail(wf['id'], node['id'])
        self.assertEqual(after['node']['engineState'], 'waiting_operator')
        self.assertEqual(after['attempts'], prior_detail['attempts'])
        self.assertEqual(after['node']['result'], result)
        self.assertIn('prior-artifact-sentinel', dumps(after['decisions']))
        direct_selection_node = {'result': {'artifacts': [{'id': 'current'}],
                                           'selectionResult': {'artifacts': [{'id': 'selection'}]}}}
        proposal = {'action': 'wait', 'reason': 'Current selection', 'evidenceIds': ['selection']}
        self.assertEqual(validate_proposal(proposal, wf, direct_selection_node, {}), proposal)
        proposal['evidenceIds'] = ['prior-artifact-sentinel']
        self.assert_code('BAD_EVIDENCE', lambda: validate_proposal(proposal, wf, direct_selection_node, {}))

    def test_real_timer_starts_with_first_attempt_and_includes_pause_and_wait(self):
        started = '2026-11-18T10:00:00+00:00'
        later = '2026-11-18T10:01:00+00:00'
        wf = self.create()
        self.assertIsNone(wf['firstAcquisitionAt'])
        self.assertIsNone(wf['finishedAt'])
        with patch('backend.controller.now', return_value=started):
            draft = self.controller.get(wf['id'])
            self.assertEqual(draft['elapsed'], 0)
            self.assertIsNone(draft['firstAcquisitionAt'])
            self.assertTrue(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
        with self.controller.store.connect() as rt:
            stored = self.controller.store.get(rt, wf['id'])
            self.assertEqual(stored['firstAcquisitionAt'], started)
        for status, engine in (('paused', 'paused'), ('attention', 'waiting_model')):
            with self.controller.store.transaction() as rt:
                current = self.controller.get(wf['id'], rt)
                current.update(status=status, engineState=engine)
                self.controller.store.save(rt, current, 'fixture_wait')
            with patch('backend.controller.now', return_value=later):
                current = self.controller.get(wf['id'])
                self.assertEqual(current['elapsed'], 60)
                self.assertIsNone(current['finishedAt'])
        with patch('backend.controller.now', return_value=later):
            self.assertTrue(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
            self.assertEqual(self.controller.get(wf['id'])['firstAcquisitionAt'], started)

    def test_terminal_timer_is_persisted_and_frozen_for_complete_stop_and_abort(self):
        started, ended, later = ('2026-11-18T10:00:00+00:00', '2026-11-18T10:01:00+00:00', '2026-11-18T12:00:00+00:00')
        for ending in ('completed', 'stop', 'abort_idle', 'abort_checkpoint'):
            with self.subTest(ending=ending):
                wf = self.create(policy='autopilot')
                with patch('backend.controller.now', return_value=started):
                    self.assertTrue(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
                with patch('backend.controller.now', return_value=ended):
                    if ending == 'completed':
                        with self.controller.store.transaction() as rt:
                            current = self.controller.get(wf['id'], rt)
                            for node in current['nodes']:
                                node['engineState'] = 'verified'
                            self.controller.store.save(rt, current, 'fixture_complete')
                        self.controller._complete(wf['id'])
                    elif ending == 'stop':
                        self.controller._brain = lambda *args: {'status': 'ok', 'decision': {
                            'action': 'stop', 'reason': 'Stop this run', 'evidenceIds': []}}
                        self.controller._decide(wf['id'], wf['nodes'][0]['id'])
                    else:
                        with self.controller.store.transaction() as rt:
                            current = self.controller.get(wf['id'], rt)
                            current.update(status='paused' if ending == 'abort_idle' else 'running',
                                           cancelRequested=None if ending == 'abort_idle' else 'abort')
                            self.controller.store.save(rt, current, 'fixture_abort')
                        if ending == 'abort_idle':
                            self.command(self.controller.get(wf['id']), 'abort', 'abort-' + wf['id'])
                        else:
                            self.controller._finish_cancel(wf['id'])
                with self.controller.store.connect() as rt:
                    stored = self.controller.store.get(rt, wf['id'])
                    self.assertEqual(stored['finishedAt'], ended)
                with patch('backend.controller.now', return_value=later):
                    current = self.controller.get(wf['id'])
                    self.assertEqual(current['finishedAt'], ended)
                    self.assertEqual(current['elapsed'], 60)

    def test_legacy_timer_derivation_is_read_only_and_uses_earliest_real_attempt(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current.pop('firstAcquisitionAt')
            current.pop('finishedAt')
            current.update(status='paused', createdAt='2000-01-01T00:00:00+00:00')
            for index, mode, value in ((1, 'real', '2026-11-18T10:05:00+00:00'),
                    (2, 'real', '2026-11-18T10:00:00+00:00'), (3, 'replay', '2020-01-01T00:00:00+00:00')):
                self.controller.store.attempt(rt, wf['id'], {'id': 'legacy-' + str(index), 'nodeId': wf['nodes'][0]['id'],
                    'number': index, 'mode': mode, 'startedAt': value})
            self.controller.store.save(rt, current, 'fixture_legacy')
            original = rt.db.execute('SELECT payload FROM platform_workflows WHERE id=?', (wf['id'],)).fetchone()[0]
            seq = rt.db.execute('SELECT max(seq) FROM events').fetchone()[0]
        with patch('backend.controller.now', return_value='2026-11-18T10:30:00+00:00'):
            versions = [self.controller.get(wf['id']), self.controller.list()[0], self.controller.snapshot()['workflows'][0]]
        for derived in versions:
            self.assertEqual(derived['firstAcquisitionAt'], '2026-11-18T10:00:00+00:00')
            self.assertIsNone(derived['finishedAt'])
            self.assertEqual(derived['elapsed'], 1800)
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT payload FROM platform_workflows WHERE id=?', (wf['id'],)).fetchone()[0], original)
            self.assertEqual(rt.db.execute('SELECT max(seq) FROM events').fetchone()[0], seq)

    def test_legacy_terminal_timer_freezes_and_replay_has_no_acquisition_start(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current.pop('firstAcquisitionAt')
            current.pop('finishedAt')
            current['status'] = 'failed'
            self.controller.store.attempt(rt, wf['id'], {'id': 'legacy-terminal', 'nodeId': wf['nodes'][0]['id'],
                'number': 1, 'mode': 'real', 'startedAt': '2026-11-18T10:00:00+00:00'})
            with patch('backend.store.now', return_value='2026-11-18T10:01:00+00:00'):
                self.controller.store.save(rt, current, 'legacy_terminal_without_event')
        with patch('backend.controller.now', return_value='2026-11-19T10:00:00+00:00'):
            result = self.controller.get(wf['id'])
        self.assertEqual(result['finishedAt'], '2026-11-18T10:01:00+00:00')
        self.assertEqual(result['elapsed'], 60)
        replay = self.create(mode='replay')
        self.controller._complete(replay['id'])
        replay = self.controller.get(replay['id'])
        self.assertIsNone(replay['firstAcquisitionAt'])
        self.assertEqual(replay['elapsed'], 0)

    def test_chat_exposes_only_registered_workflow_evidence_summaries(self):
        wf = self.create()
        self.assertTrue(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            result = current['nodes'][0]['result']
            result.update(parameters={'drive_amp': .8, 'password': 'credential-sentinel'},
                provenance={'freshAcquisition': True, 'operationId': 'actual-operation',
                            'api_key': 'secret-sentinel', 'fileContents': 'raw-file-body-sentinel'})
            self.controller.store.save(rt, current, 'fixture_chat_metadata')
        calls = []
        self.controller._brain = lambda path, request: calls.append((path, deepcopy(request))) or {'status': 'ok', 'message': 'Summary only'}
        with patch.object(self.controller, 'artifact', side_effect=AssertionError('chat must not open raw files')):
            self.controller.chat({'workflowId': wf['id'], 'message': 'Inspect this acquisition'})
        context = calls[0][1]['context']
        evidence_id = result['artifacts'][0]['id']
        found = next(item for item in context['observations'] if item['id'] == evidence_id)
        self.assertEqual(found['nodeId'], wf['nodes'][0]['id'])
        self.assertEqual(found['procedureId'], wf['nodes'][0]['procedureId'])
        self.assertEqual(found['dataKind'], 'real')
        self.assertEqual(found['acquiredAt'], result['acquiredAt'])
        self.assertEqual(found['metrics'], result['metrics'])
        self.assertEqual(found['quality']['status'], result['quality']['status'])
        self.assertEqual(found['parameters'], {'drive_amp': .8})
        self.assertEqual(found['provenance'], {'freshAcquisition': True, 'operationId': 'actual-operation'})
        self.assertFalse(found['rawFileRead'])
        self.assertIn('不读取原始文件', context['notice'])
        serialized = dumps(calls[0][1])
        for secret in ('credential-sentinel', 'secret-sentinel', 'raw-file-body-sentinel', 'localPath', 'sourcePath'):
            self.assertNotIn(secret, serialized)
        self.assertLess(len(serialized), 200000)
        self.assertEqual(context['catalog']['parameterBounds'], self.controller.catalog.describe(self.adapter.capabilities(), self.controller._bounds())['parameterBounds'])
        observations, omitted = self.controller._chat_observations(self.controller.get(wf['id']), 1)
        self.assertEqual(observations, [])
        self.assertGreater(omitted, 0)

    def test_health_exposes_actual_agent_port_without_url_credentials(self):
        self.controller.brain_url = 'http://user:secret-value@127.0.0.1:8767'
        health = self.controller.health()
        self.assertEqual(health['agentPort'], 8767)
        self.assertNotIn('secret-value', dumps(health))
        self.assertNotIn('brain_url', health)

    def test_operator_budget_increase_is_audited_idempotent_and_does_not_start(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current.update(status='attention', engineState='budget_exhausted', reservedShots=1000,
                           iterations=2, currentNodeId=current['nodes'][0]['id'])
            current['nodes'][0].update(attempt=2, engineState='budget_exhausted')
            self.controller.store.save(rt, current, 'fixture_budget_exhausted')
        before = self.controller.get(wf['id'])
        payload = {'operationId': 'operator-budget-1', 'expectedRevision': before['revision'],
                   'budget': {'maxTotalShots': 60000000, 'maxAttemptsPerNode': 6, 'maxWorkflowAttempts': 100},
                   'reason': 'Allow independent readout rebuild and remaining RB captures'}
        after = self.controller.set_budget(wf['id'], payload)
        self.assertEqual(after['status'], 'attention')
        self.assertEqual(after['engineState'], 'budget_updated')
        self.assertEqual(after['nodes'][0]['engineState'], 'budget_updated')
        self.assertEqual(after['lastBudgetUpdate']['before']['maxTotalShots'], before['maxTotalShots'])
        self.assertEqual(after['lastBudgetUpdate']['after'], payload['budget'])
        self.assertEqual(after['lastBudgetUpdate']['source'], 'operator')
        self.assertEqual(self.controller.set_budget(wf['id'], payload), after)
        self.assertEqual(self.controller.get(wf['id'])['revision'], after['revision'])
        for key in ('reservedShots', 'iterations', 'gates', 'gatesVersion', 'adapterProfileHash'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after['nodes'][0]['attempt'], before['nodes'][0]['attempt'])
        self.assertEqual(after['nodes'][0]['parameters'], before['nodes'][0]['parameters'])
        self.assertEqual(self.adapter.calls, [])
        events = [e for e in self.controller.store.events(wf['id']) if e['event'] == 'budget_updated']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['payload']['reason'], payload['reason'])
        self.assertEqual(events[0]['payload']['operationId'], payload['operationId'])
        self.assert_code('IDEMPOTENCY_CONFLICT', lambda: self.controller.set_budget(wf['id'], {**payload, 'reason': 'different request'}))
        self.assert_code('STALE_REVISION', lambda: self.controller.set_budget(wf['id'], {**payload, 'operationId': 'stale-budget'}))

    def test_budget_is_integer_monotonic_bounded_and_requires_reason(self):
        wf = self.create()
        before = self.controller.get(wf['id'])
        bad = ({'maxTotalShots': 100000001}, {'maxAttemptsPerNode': 11}, {'maxWorkflowAttempts': 201},
               {'maxTotalShots': wf['maxTotalShots'] - 1}, {'maxAttemptsPerNode': True}, {'maxWorkflowAttempts': 41.5},
               {'maxTotalShots': float('nan')}, {'maxTotalShots': wf['maxTotalShots']}, {}, {'arbitrary': 5})
        for index, budget in enumerate(bad):
            with self.subTest(budget=budget):
                payload = {'operationId': 'bad-budget-' + str(index), 'expectedRevision': wf['revision'], 'budget': budget, 'reason': 'test'}
                self.assert_code('INVALID_BUDGET', lambda: self.controller.set_budget(wf['id'], payload))
        self.assert_code('INVALID_BUDGET_REASON', lambda: self.controller.set_budget(wf['id'], {
            'operationId': 'missing-reason', 'expectedRevision': wf['revision'], 'budget': {'maxTotalShots': 60000000}}))
        self.assertEqual(self.controller.get(wf['id']), before)
        self.assertEqual(self.adapter.calls, [])

    def test_budget_change_rejects_replay_running_and_uncertain_effects(self):
        for mode, status, effect, lease in (('replay', 'draft', None, False), ('real', 'running', None, False),
                ('real', 'queued', None, False), ('real', 'completed', None, False),
                ('real', 'attention', 'pending', False), ('real', 'attention', 'unknown', False),
                ('real', 'paused', None, True)):
            with self.subTest(mode=mode, status=status, effect=effect, lease=lease):
                wf = self.create(mode=mode)
                with self.controller.store.transaction() as rt:
                    current = self.controller.get(wf['id'], rt)
                    current['status'] = status
                    if effect:
                        rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,NULL)',
                            ('budget-effect-' + wf['id'], wf['id'], 'budget-test', effect, '{}'))
                    if lease:
                        rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)',
                            ('budget-test-bus', wf['id'], 'budget-test', 'active', 'budget-test-token'))
                    self.controller.store.save(rt, current, 'fixture_budget_guard')
                current = self.controller.get(wf['id'])
                expected = 'RECONCILIATION_REQUIRED' if effect or lease else 'INVALID_STATE'
                self.assert_code(expected, lambda: self.controller.set_budget(wf['id'], {
                    'operationId': 'budget-' + wf['id'], 'expectedRevision': current['revision'],
                    'budget': {'maxTotalShots': 60000000}, 'reason': 'Must remain unchanged'}))
                self.assertEqual(self.controller.get(wf['id'])['maxTotalShots'], wf['maxTotalShots'])
        self.assertEqual(self.adapter.calls, [])

    def test_budget_increase_keeps_other_attention_reason_and_model_cannot_raise_limits(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current.update(status='attention', engineState='waiting_model')
            self.controller.store.save(rt, current, 'fixture_model_wait')
        current = self.controller.get(wf['id'])
        changed = self.controller.set_budget(wf['id'], {'operationId': 'waiting-budget', 'expectedRevision': current['revision'],
            'budget': {'maxWorkflowAttempts': 60}, 'reason': 'Operator adds time for later retries'})
        self.assertEqual(changed['engineState'], 'waiting_model')
        node = changed['nodes'][0]
        node['result'] = {'quality': {'status': 'failed'}, 'artifacts': []}
        self.assert_code('PARAMETER_OUT_OF_RANGE', lambda: validate_proposal(
            {'action': 'retry', 'reason': 'model budget request', 'evidenceIds': [], 'parameterChanges': {'maxTotalShots': 100000000}},
            changed, node, self.controller._parameter_schema(node['procedureId'])))

    def test_budget_updated_failed_result_requests_fresh_model_decision_without_replaying_old_changes(self):
        wf = self.create(policy='autopilot')
        node_id = next(n['id'] for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current.update(status='attention', engineState='quality_attention', currentNodeId=node_id)
            node = self.controller._node(current, node_id)
            node.update(attempt=current['maxAttemptsPerNode'], result={'artifacts': [], 'quality': {'status': 'failed'}})
            self.controller.store.save(rt, current, 'fixture_budget_decision')
        calls = []
        def brain(path, payload):
            calls.append(deepcopy(payload))
            return {'status': 'ok', 'decision': {'action': 'retry', 'reason': 'New bounded offline proposal',
                'evidenceIds': [], 'parameterChanges': {'drive_amp': .7 if len(calls) == 1 else .6}}}
        self.controller._brain = brain
        self.assertFalse(self.controller._decide(wf['id'], node_id))
        blocked = self.controller.get(wf['id'])
        self.assertEqual(blocked['engineState'], 'budget_exhausted')
        self.assertNotIn('drive_amp', self.controller._node(blocked, node_id).get('parameterOverrides', {}))
        changed = self.controller.set_budget(wf['id'], {'operationId': 'budget-before-fresh-decision',
            'expectedRevision': blocked['revision'], 'budget': {'maxAttemptsPerNode': 6}, 'reason': 'Operator permits more trials'})
        queued = self.command(changed, 'decide', 'decide-after-budget', nodeId=node_id)
        self.assertEqual(queued['engineState'], 'decision_queued')
        self.assertEqual(self.controller._node(queued, node_id)['attempt'], self.controller._node(blocked, node_id)['attempt'])
        self.assertNotIn('drive_amp', self.controller._node(queued, node_id).get('parameterOverrides', {}))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.adapter.calls, [])
        self.assertTrue(self.controller._decide(wf['id'], node_id))
        current = self.controller.get(wf['id'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.controller._node(current, node_id)['parameterOverrides']['drive_amp'], .6)
        self.assertEqual(self.controller._node(current, node_id)['attempt'], self.controller._node(blocked, node_id)['attempt'])
        self.assertEqual(current['iterations'], blocked['iterations'])
        self.assertEqual(current['reservedShots'], blocked['reservedShots'])
        self.assertEqual(self.adapter.calls, [])

    def test_budget_updated_decide_requires_current_failed_or_unverified_nonstale_result(self):
        for quality, stale, wrong_node in (('passed', False, False), ('failed', True, False),
                                          ('unverified', False, True), ('unverified', False, False)):
            with self.subTest(quality=quality, stale=stale, wrong_node=wrong_node):
                wf = self.create(policy='autopilot')
                with self.controller.store.transaction() as rt:
                    current = self.controller.get(wf['id'], rt)
                    current.update(status='attention', engineState='budget_updated', currentNodeId=current['nodes'][0]['id'])
                    for node in current['nodes'][:2]:
                        node['result'] = {'artifacts': [], 'quality': {'status': quality}, 'stale': stale}
                    self.controller.store.save(rt, current, 'fixture_budget_decide_guard')
                current = self.controller.get(wf['id'])
                node_id = current['nodes'][1 if wrong_node else 0]['id']
                command = lambda: self.command(current, 'decide', 'guard-' + wf['id'], nodeId=node_id)
                if quality == 'unverified' and not stale and not wrong_node:
                    self.assertEqual(command()['engineState'], 'decision_queued')
                else:
                    self.assert_code('CURRENT_EVIDENCE_REQUIRED', command)
        self.assertEqual(self.adapter.calls, [])

    def test_global_budget_stop_targets_pending_node_and_resume_keeps_counters(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            for node in current['nodes']:
                if node['procedureId'] in ('ro_resonator_survey', 'ro_power_shift', 'sq_spectroscopy'):
                    node.update(engineState='verified', status='success')
            prior = next(n for n in current['nodes'] if n['procedureId'] == 'sq_spectroscopy')
            target = next(n for n in current['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
            current.update(status='queued', engineState='queued', currentNodeId=prior['id'], iterations=current['maxWorkflowAttempts'])
            self.controller.store.save(rt, current, 'fixture_global_budget')
        self.controller.run_workflow(wf['id'])
        blocked = self.controller.get(wf['id'])
        self.assertEqual(blocked['currentNodeId'], target['id'])
        self.assertEqual(next(n for n in blocked['nodes'] if n['id'] == prior['id'])['engineState'], 'verified')
        changed = self.controller.set_budget(wf['id'], {'operationId': 'budget-global-resume', 'expectedRevision': blocked['revision'],
            'budget': {'maxWorkflowAttempts': 60}, 'reason': 'Continue the pending Rabi step'})
        resumed = self.command(changed, 'resume', 'resume-budget-global')
        self.assertEqual(resumed['iterations'], blocked['iterations'])
        self.assertEqual(next(n for n in resumed['nodes'] if n['id'] == target['id'])['attempt'], 0)
        self.assertEqual(self.adapter.calls, [])
        self.assertTrue(self.controller._execute_node(wf['id'], target['id']))
        current = self.controller.get(wf['id'])
        self.assertEqual(current['iterations'], blocked['iterations'] + 1)
        self.assertEqual(next(n for n in current['nodes'] if n['id'] == target['id'])['attempt'], 1)
        self.assertEqual(next(n for n in current['nodes'] if n['id'] == prior['id'])['engineState'], 'verified')

    def test_native_analysis_rejection_survives_passing_numeric_gates(self):
        result = {'dataKind': 'real', 'acquiredAt': now(), 'metrics': {'peak_snr': 100},
                  'artifacts': [{'kind': 'raw'}], 'analysisQuality': {'accepted': False, 'reasons': ['Native contrast check failed']}}
        quality = evaluate(result, 'sq_spectroscopy', 'real', independent=True)['quality']
        self.assertEqual(quality['status'], 'failed')
        self.assertIn('Native contrast check failed', quality['reasons'])

    def test_full_real_coverage_is_explicit_distinct_and_dependency_ordered(self):
        wf = self.create()
        self.assertEqual(wf['coverage'], 'full')
        self.assertEqual(len(wf['nodes']), 13)
        by_procedure = {node['procedureId']: node for node in wf['nodes']}
        for procedure in ('ro_power_shift', 'sq_rabi_time', 'sq_allxy', 'sq_ramsey', 'sq_echo'):
            self.assertIn(procedure, by_procedure)
            self.assertTrue(by_procedure[procedure]['id'].startswith('Q11:' + by_procedure[procedure]['ownerId'] + ':'))
        self.assertIn(by_procedure['ro_power_shift']['id'], by_procedure['sq_spectroscopy']['dependsOn'])
        self.assertIn(by_procedure['sq_rabi_time']['id'], by_procedure['ro_iq01']['dependsOn'])
        self.assertIn(by_procedure['sq_echo']['id'], by_procedure['sq_cpmg']['dependsOn'])
        self.assertIn(by_procedure['sq_allxy']['id'], by_procedure['sq_rb']['dependsOn'])
        self.assertEqual(by_procedure['ro_power_shift']['capability'], 'unavailable')
        # A topological traversal must include every measurement exactly once.
        pending = {n['id']: set(n['dependsOn']) for n in wf['nodes']}
        accepted = set()
        while pending:
            ready = [key for key, deps in pending.items() if deps <= accepted]
            self.assertTrue(ready, 'full blueprint must not contain dependency cycles')
            for key in ready:
                accepted.add(key)
                del pending[key]
        self.assertEqual(len(self.create(mode='replay')['nodes']), 8)
        self.assertEqual(len(self.create(coverage='main')['nodes']), 8)

    def test_only_eight_main_results_do_not_complete_full_workflow(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            for node in current['nodes'][:8]:
                node.update(engineState='verified', status='success')
            self.controller.store.save(rt, current, 'fixture_main_pass')
        self.controller._complete(wf['id'])
        result = self.controller.get(wf['id'])
        self.assertFalse(result['workflowComplete'])
        self.assertEqual(result['resultSummary']['verifiedNodes'], 8)
        self.assertEqual(result['resultSummary']['totalNodes'], 13)

    def test_duplicate_command_is_idempotent_and_conflicting_body_is_rejected(self):
        wf = self.create()
        payload = {'action': 'start', 'operationId': 'start-once', 'expectedRevision': wf['revision']}
        started = self.controller.action(wf['id'], payload)
        count = len(self.controller.store.events(wf['id']))
        self.assertEqual(self.controller.action(wf['id'], payload), started)
        self.assertEqual(len(self.controller.store.events(wf['id'])), count)
        self.assert_code('IDEMPOTENCY_CONFLICT', lambda: self.controller.action(
            wf['id'], {**payload, 'action': 'abort'}))
        self.assertEqual(self.adapter.calls, [])

    def test_stale_revision_and_stale_attempt_cannot_mutate_workflow(self):
        wf = self.create()
        started = self.command(wf, 'start')
        self.assert_code('STALE_REVISION', lambda: self.command(wf, 'pause', 'stale'))
        self.assertEqual(self.controller.get(wf['id'])['revision'], started['revision'])
        self.assert_code('STALE_ATTEMPT', lambda: self.command(started, 'pause', 'old-attempt',
                         nodeId=started['nodes'][0]['id'], attemptId='not-current'))

    def test_upstream_backtrack_invalidates_downstream_and_keeps_attempt_evidence(self):
        wf = self.create()
        upstream, downstream = wf['nodes'][0], wf['nodes'][1]
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current['status'] = 'paused'
            for node in current['nodes']:
                node.update(status='success', engineState='verified', mainProcedureComplete=True,
                            result={'dataKind': 'real', 'metrics': {}, 'quality': {'status': 'passed'}})
            for n, node in enumerate((upstream, downstream), 1):
                self.controller.store.attempt(rt, wf['id'], {'id': f'old-{n}', 'nodeId': node['id'],
                    'number': 1, 'state': 'verified', 'result': {'quality': {'status': 'passed'}}})
            self.controller.store.save(rt, current, 'fixture_seed')
        updated = self.command(self.controller.get(wf['id']), 'backtrack', targetNodeId=upstream['id'])
        for node in updated['nodes'][:2]:
            self.assertEqual(node['engineState'], 'stale')
            self.assertFalse(node['mainProcedureComplete'])
            self.assertTrue(node['result']['stale'])
        attempts = self.controller.detail(wf['id'], downstream['id'])['attempts']
        self.assertEqual(attempts[0]['id'], 'old-2')
        self.assertEqual(attempts[0]['result']['quality']['status'], 'passed')

    def test_real_run_rejects_historical_result_even_if_adapter_claims_passed(self):
        wf = self.create()
        self.adapter.data_kind = 'historical'
        self.assertFalse(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
        current = self.controller.get(wf['id'])
        self.assertEqual(current['engineState'], 'execution_failed')
        self.assertNotEqual(current['nodes'][0]['engineState'], 'verified')
        self.assertEqual(len(self.adapter.calls), 1)
        detail = self.controller.detail(wf['id'], wf['nodes'][0]['id'])
        self.assertIn('历史', detail['attempts'][0]['error'])

    def test_acceptance_requires_two_distinct_acquisitions_and_operation_receipts(self):
        wf = self.create()
        self.assertTrue(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
        self.assertEqual(len(self.adapter.calls), 2)
        a, b = self.adapter.calls
        self.assertNotEqual(a['operationId'], b['operationId'])
        self.assertEqual(a['attemptId'], b['attemptId'])
        detail = self.controller.detail(wf['id'], wf['nodes'][0]['id'])
        self.assertEqual(detail['node']['result']['quality']['status'], 'passed')
        self.assertEqual(detail['attempts'][0]['result']['selectionResult']['quality']['status'], 'unverified')
        with self.controller.store.connect() as rt:
            rows = rt.db.execute('SELECT state FROM platform_effects').fetchall()
            self.assertEqual([r[0] for r in rows], ['completed', 'completed'])
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0], 0)

    def test_same_raw_data_cannot_count_as_independent_verification(self):
        wf = self.create()
        self.adapter.same_bytes = True
        self.assertFalse(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
        self.assertEqual(len(self.adapter.calls), 2)
        node = self.controller.get(wf['id'])['nodes'][0]
        self.assertEqual(node['result']['quality']['status'], 'unverified')
        self.assertIn('需要独立采集复测', node['result']['quality']['reasons'])

    def test_candidate_is_verified_with_new_parameters_before_version_commit(self):
        wf = self.create()
        self.adapter.candidate = {'readout_hz': 6.9e9}
        self.assertTrue(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
        self.assertEqual(self.adapter.calls[0]['parameters']['readout_hz'], 6.8e9)
        self.assertEqual(self.adapter.calls[1]['parameters']['readout_hz'], 6.9e9)
        with self.controller.store.connect() as rt:
            row = rt.db.execute('SELECT value,revision FROM parameters WHERE name=?',
                                (wf['id'] + ':Q11:readout_hz',)).fetchone()
        self.assertEqual(json.loads(row['value']), 6.9e9)
        self.assertEqual(row['revision'], 1)
        self.assertEqual(self.controller.get(wf['id'])['nodes'][1]['engineState'], 'stale')

    def test_candidate_repeatability_uses_actual_selected_scan_parameters(self):
        wf = self.create()
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_spectroscopy')
        self.adapter.candidates_by_call = [{'drive_hz': 4.4e9}, {'drive_hz': 4.4003e9}]
        original_execute = self.adapter.execute
        def with_actual_scan(request, emit, cancel):
            result = original_execute(request, emit, cancel)
            result['parameters'] = {**request['parameters'], 'span_hz': 10e6, 'points': 81}
            return result
        self.adapter.execute = with_actual_scan
        self.assertFalse(self.controller._execute_node(wf['id'], node['id']))
        current = self.controller.get(wf['id'])
        result = next(n for n in current['nodes'] if n['id'] == node['id'])['result']
        self.assertEqual(result['quality']['status'], 'failed')
        self.assertTrue(any('容差 250000' in r for r in result['quality']['reasons']))
        self.assertNotIn('span_hz', self.adapter.calls[0]['parameters'])
        with self.controller.store.connect() as rt:
            self.assertIsNone(rt.db.execute('SELECT value FROM parameters WHERE name=?',
                (wf['id'] + ':Q11:drive_hz',)).fetchone())

    def test_out_of_range_fit_retains_evidence_without_verification_or_commit(self):
        wf = self.create()
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
        self.adapter.candidate = {'drive_amp': 1.2}
        self.assertFalse(self.controller._execute_node(wf['id'], node['id']))
        detail = self.controller.detail(wf['id'], node['id'])
        result, attempt = detail['node']['result'], detail['attempts'][0]
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.controller.get(wf['id'])['engineState'], 'quality_attention')
        self.assertEqual(detail['node']['engineState'], 'quality_attention')
        self.assertEqual(result['quality']['status'], 'failed')
        self.assertEqual(result['candidateParameters'], {'drive_amp': 1.2})
        self.assertEqual(result['candidateValidation']['raw'], {'drive_amp': 1.2})
        self.assertEqual(result['candidateValidation']['stage'], 'selection')
        self.assertEqual(result['metrics']['r_squared'], .95)
        self.assertEqual(attempt['result'], result)
        self.assertIsNone(attempt['verificationOperationId'])
        artifact = result['artifacts'][0]
        self.assertTrue(self.controller.artifact(artifact['id'])[0].is_file())
        with self.controller.store.connect() as rt:
            effects = rt.db.execute('SELECT state,result FROM platform_effects').fetchall()
            self.assertEqual([row['state'] for row in effects], ['completed'])
            self.assertEqual(json.loads(effects[0]['result'])['candidateParameters'], {'drive_amp': 1.2})
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0], 0)
            self.assertIsNone(rt.db.execute('SELECT value FROM parameters WHERE name=?',
                (wf['id'] + ':Q11:drive_amp',)).fetchone())

    def test_out_of_range_fit_reaches_model_with_original_result(self):
        wf = self.create(policy='autopilot')
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
        self.adapter.candidate = {'drive_amp': 1.2}
        observed = []
        def brain(path, payload):
            observed.append(deepcopy(payload))
            return {'status': 'ok', 'decision': {'action': 'retry', 'reason': 'Adjust scan and repeat',
                'evidenceIds': [payload['observations']['result']['artifacts'][0]['id']],
                'parameterChanges': {'drive_amp': .8}}}
        self.controller._brain = brain
        self.assertTrue(self.controller._execute_node(wf['id'], node['id']))
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]['observations']['result']['candidateParameters'], {'drive_amp': 1.2})
        self.assertEqual(observed[0]['observations']['result']['quality']['status'], 'failed')
        current = self.controller.detail(wf['id'], node['id'])['node']
        self.assertEqual(current['engineState'], 'retry_queued')
        self.assertEqual(current['parameterOverrides']['drive_amp'], .8)
        with self.controller.store.connect() as rt:
            self.assertIsNone(rt.db.execute('SELECT value FROM parameters WHERE name=?',
                (wf['id'] + ':Q11:drive_amp',)).fetchone())

    def test_verification_candidate_above_bound_fails_even_within_repeatability_tolerance(self):
        wf = self.create()
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
        self.adapter.candidates_by_call = [{'drive_amp': 1.0}, {'drive_amp': 1.1}]
        self.assertFalse(self.controller._execute_node(wf['id'], node['id']))
        detail = self.controller.detail(wf['id'], node['id'])
        result = detail['node']['result']
        self.assertEqual(len(self.adapter.calls), 2)
        self.assertEqual(self.adapter.calls[1]['parameters']['drive_amp'], 1.0)
        self.assertEqual(result['quality']['status'], 'failed')
        self.assertEqual(result['candidateValidation']['stage'], 'verification')
        self.assertEqual(result['candidateValidation']['raw'], {'drive_amp': 1.1})
        self.assertEqual(result['verificationCandidateParameters'], {'drive_amp': 1.1})
        self.assertEqual(result['selectionResult']['candidateParameters'], {'drive_amp': 1.0})
        self.assertEqual(result['metrics']['r_squared'], .95)
        self.assertIsNotNone(detail['attempts'][0]['verificationOperationId'])
        self.assertTrue(self.controller.artifact(result['selectionResult']['artifacts'][0]['id'])[0].is_file())
        self.assertTrue(self.controller.artifact(result['artifacts'][0]['id'])[0].is_file())
        with self.controller.store.connect() as rt:
            self.assertEqual([r[0] for r in rt.db.execute('SELECT state FROM platform_effects')], ['completed', 'completed'])
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0], 0)
            self.assertIsNone(rt.db.execute('SELECT value FROM parameters WHERE name=?',
                (wf['id'] + ':Q11:drive_amp',)).fetchone())

    def test_candidate_at_inclusive_bound_can_verify_and_commit(self):
        wf = self.create()
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
        self.adapter.candidate = {'drive_amp': 1.0}
        self.assertTrue(self.controller._execute_node(wf['id'], node['id']))
        self.assertEqual(len(self.adapter.calls), 2)
        with self.controller.store.connect() as rt:
            row = rt.db.execute('SELECT value FROM parameters WHERE name=?',
                (wf['id'] + ':Q11:drive_amp',)).fetchone()
        self.assertEqual(json.loads(row['value']), 1.0)

    def test_malformed_or_unknown_fit_candidate_is_quality_failure(self):
        for candidate in (None, [], 'bad', {'drive_amp': None}, {'drive_amp': True}, {'unknown_fit': .5}):
            with self.subTest(candidate=candidate):
                wf = self.create()
                self.adapter.candidate = candidate
                count = len(self.adapter.calls)
                self.assertFalse(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
                result = self.controller.detail(wf['id'], wf['nodes'][0]['id'])['node']['result']
                self.assertEqual(len(self.adapter.calls) - count, 1)
                self.assertEqual(result['quality']['status'], 'failed')
                self.assertEqual(result['candidateValidation']['raw'], candidate)
                self.assertEqual(result['candidateParameters'], candidate)

    def test_normalized_candidate_is_also_validated_and_preserved(self):
        for normalized in ({'drive_amp': 1.2}, []):
            with self.subTest(normalized=normalized):
                wf = self.create()
                self.adapter.candidate = {'drive_amp': .8}
                def normalizer(procedure, raw):
                    raw['drive_amp'] = 1.2
                    return deepcopy(normalized)
                self.adapter.normalize_candidate = normalizer
                count = len(self.adapter.calls)
                self.assertFalse(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
                result = self.controller.detail(wf['id'], wf['nodes'][0]['id'])['node']['result']
                self.assertEqual(len(self.adapter.calls) - count, 1)
                self.assertEqual(result['quality']['status'], 'failed')
                self.assertEqual(result['candidateValidation']['raw'], {'drive_amp': .8})
                self.assertEqual(result['candidateValidation']['normalized'], normalized)

    def test_unrelated_normalization_errors_remain_execution_failures(self):
        for error in (RuntimeError('normalizer bug'), APIError('ADAPTER_BUG', 'adapter bug')):
            with self.subTest(error=type(error).__name__):
                wf = self.create()
                self.adapter.candidate = {'drive_amp': .8}
                def broken_normalizer(procedure, raw):
                    raise error
                self.adapter.normalize_candidate = broken_normalizer
                count = len(self.adapter.calls)
                self.assertFalse(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
                self.assertEqual(len(self.adapter.calls) - count, 1)
                self.assertEqual(self.controller.get(wf['id'])['engineState'], 'execution_failed')
                detail = self.controller.detail(wf['id'], wf['nodes'][0]['id'])
                self.assertIn(str(error), detail['attempts'][0]['error'])

    def test_repeatability_two_steps_uses_intervals_between_points(self):
        selection = {'drive_hz': 4.4e9}
        actual = {'span_hz': 10e6, 'points': 81}
        self.assertEqual(self.controller._repeatability(selection, {'drive_hz': 4.40025e9}, actual), [])
        self.assertTrue(self.controller._repeatability(selection, {'drive_hz': 4.400250001e9}, actual))

    def test_operator_change_while_model_runs_discards_stale_model_response(self):
        wf = self.create(policy='autopilot')
        node = wf['nodes'][0]
        def stale_response(path, payload):
            current = self.controller.get(wf['id'])
            self.controller.set_policy(wf['id'], {'operationId': 'operator-supersedes-model',
                'expectedRevision': current['revision'], 'policy': 'strict'})
            return {'status': 'ok', 'binding': {k: payload[k] for k in ('runId', 'nodeId', 'revision')},
                    'decision': {'action': 'retry', 'reason': 'outdated response', 'evidenceIds': []}}
        self.controller._brain = stale_response
        self.assertFalse(self.controller._decide(wf['id'], node['id']))
        current = self.controller.get(wf['id'])
        self.assertEqual(current['policy'], 'strict')
        self.assertNotEqual(current['status'], 'queued')
        self.assertEqual(self.controller.detail(wf['id'], node['id'])['decisions'], [])
        self.assertEqual(self.adapter.calls, [])

    def test_pause_waits_for_checkpoint_and_does_not_launch_verification(self):
        wf = self.create()
        started, release = threading.Event(), threading.Event()
        def hold(request, emit, cancel):
            started.set()
            self.assertTrue(release.wait(5), 'test did not release fake acquisition')
        self.adapter.before_return = hold
        worker = threading.Thread(target=self.controller._execute_node, args=(wf['id'], wf['nodes'][0]['id']))
        worker.start()
        try:
            self.assertTrue(started.wait(5))
            active = self.controller.get(wf['id'])
            stopping = self.command(active, 'pause', 'pause-active')
            self.assertEqual(stopping['engineState'], 'pausing_at_checkpoint')
            self.assertEqual(stopping['status'], 'running')
            release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            paused = self.controller.get(wf['id'])
            self.assertEqual(paused['status'], 'paused')
            self.assertTrue(self.adapter.cancel_observed)
            self.assertEqual(len(self.adapter.calls), 1)
            self.assertEqual(len(self.controller.detail(wf['id'], wf['nodes'][0]['id'])['attempts']), 1)
        finally:
            release.set()
            worker.join(5)

    def test_restart_quarantines_pending_effect_and_does_not_redispatch(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current.update(status='running', engineState='acquiring')
            rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)',
                          ('fake-shared-bus', wf['id'], 'interrupted', 'active', 'token'))
            rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,NULL)',
                          ('interrupted/measure', wf['id'], 'interrupted', 'pending', '{}'))
            self.controller.store.save(rt, current, 'fixture_started')
        self.controller.close()
        self.controller = Controller(self.root, adapter=self.adapter, start_worker=False)
        current = self.controller.get(wf['id'])
        self.assertEqual(current['engineState'], 'needs_reconciliation')
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT state FROM platform_leases').fetchone()[0], 'quarantined')
            self.assertEqual(rt.db.execute('SELECT state FROM platform_effects').fetchone()[0], 'unknown')
        self.assert_code('RECONCILIATION_REQUIRED', lambda: self.command(current, 'resume'))
        self.assertEqual(self.adapter.calls, [])

    def test_unknown_dispatch_retains_exclusive_lease(self):
        wf = self.create()
        self.adapter.fail = True
        self.assertFalse(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
        self.assertEqual(self.controller.get(wf['id'])['engineState'], 'needs_reconciliation')
        other = self.create()
        self.assertFalse(self.controller._execute_node(other['id'], other['nodes'][0]['id']))
        self.assertEqual(self.controller.get(other['id'])['engineState'], 'resource_busy')
        self.assertEqual(len(self.adapter.calls), 1)

    def test_model_cannot_advance_failed_gate_or_propose_out_of_range_values(self):
        wf = self.create()
        node = wf['nodes'][0]
        node['result'] = {'quality': {'status': 'failed'}, 'artifacts': [{'id': 'known'}]}
        proposal = {'action': 'continue', 'reason': 'model says good', 'evidenceIds': ['known']}
        self.assert_code('GATE_NOT_PASSED', lambda: validate_proposal(proposal, wf, node, self.controller._bounds()))
        for changes in ({'drive_amp': 1.5}, {'shots': 2.5}, {'shell': 0}, {'drive_amp': float('nan')}):
            self.assert_code('PARAMETER_OUT_OF_RANGE', lambda: validate_proposal(
                {**proposal, 'action': 'retry', 'parameterChanges': changes}, wf, node, self.controller._bounds()))
        self.assert_code('BAD_EVIDENCE', lambda: validate_proposal(
            {**proposal, 'action': 'wait', 'evidenceIds': ['invented']}, wf, node, self.controller._bounds()))

    def test_model_cannot_override_decision_audit_fields(self):
        wf = self.create()
        node = wf['nodes'][0]
        node['result'] = {'quality': {'status': 'failed'}, 'artifacts': []}
        for override in ({'source': 'operator'}, {'nodeId': 'other-node'}, {'id': 'forged'}):
            with self.subTest(override=override):
                self.assert_code('BAD_DECISION', lambda: validate_proposal(
                    {'action': 'wait', 'reason': 'wait', 'evidenceIds': [], **override}, wf, node, {}))

    def test_independent_evaluation_recomputes_quality_not_adapter_label(self):
        result = {'dataKind': 'real', 'acquiredAt': now(), 'artifacts': [{'id': 'x'}],
                  'metrics': {'peak_snr': .5}, 'quality': {'status': 'passed'}}
        self.assertEqual(evaluate(result, 'sq_spectroscopy', 'real', independent=True)['quality']['status'], 'failed')
        self.assert_code('SOURCE_MISMATCH', lambda: evaluate({**result, 'dataKind': 'historical'},
                         'sq_spectroscopy', 'real', independent=True))

    def test_artifact_read_detects_post_registration_mutation(self):
        wf = self.create()
        self.controller._execute_node(wf['id'], wf['nodes'][0]['id'])
        artifact = self.controller.get(wf['id'])['nodes'][0]['result']['artifacts'][0]
        path, _ = self.controller.artifact(artifact['id'])
        path.write_text('mutated-after-capture', encoding='utf-8')
        self.assert_code('ARTIFACT_HASH_MISMATCH', lambda: self.controller.artifact(artifact['id']))

    def test_disabled_profile_confirmation_is_task_scoped_and_cannot_bypass_ownership(self):
        self.adapter.profile['enabled'] = False
        self.adapter.profile.pop('connectionConfirmedAt', None)
        wf = self.command(self.create(), 'start', 'start-disabled')
        self.controller.run_workflow(wf['id'])
        waiting = self.controller.get(wf['id'])
        self.assertEqual(waiting['engineState'], 'waiting_hardware')
        for owner in ({'verified': False, 'owners': []}, {'verified': True, 'owners': [{'pid': 100}]}):
            with patch.object(self.controller, 'hardware_ownership', return_value=owner):
                self.assert_code('DEVICE_IN_USE', lambda: self.command(waiting, 'confirm_hardware', 'blocked-confirm'))
        with patch.object(self.controller, 'hardware_ownership', return_value={'verified': True, 'owners': []}):
            confirmed = self.command(waiting, 'confirm_hardware', 'confirmed-once')
        self.assertTrue(confirmed['hardwareConfirmed'])
        self.assertFalse(self.adapter.profile['enabled'])
        with patch.object(self.controller, 'hardware_ownership', return_value={'verified': True, 'owners': [{'pid': 101}]}):
            self.controller.run_workflow(wf['id'])
        self.assertEqual(self.controller.get(wf['id'])['engineState'], 'waiting_hardware')
        self.assertEqual(self.adapter.calls, [])
        other = self.command(self.create(), 'start', 'start-new-task')
        self.controller.run_workflow(other['id'])
        self.assertFalse(self.controller.get(other['id'])['hardwareConfirmed'])
        self.assertEqual(self.controller.get(other['id'])['engineState'], 'waiting_hardware')

    def test_confirmed_run_enables_only_scoped_execution_adapter(self):
        self.adapter.profile['enabled'] = False
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current.update(hardwareConfirmed=True, status='queued')
            self.controller.store.save(rt, current, 'fixture_confirmation')
        self.controller._physical_adapter = True
        scoped = deepcopy(self.adapter)
        scoped.physical_hardware = True
        scoped.profile['enabled'] = True
        scoped.calls = self.adapter.calls
        original_execute = scoped.execute
        def actual_context_receipt(request, emit, cancel):
            result = original_execute(request, emit, cancel)
            result['parameters'] = deepcopy(request['parameters'])
            return result
        scoped.execute = actual_context_receipt
        with patch.object(self.controller, 'hardware_ownership', return_value={'verified': True, 'owners': []}), \
             patch.object(self.adapter, 'validation_parameter_keys', return_value=('readout_hz', 'drive_hz'), create=True), \
             patch.object(self.adapter, 'with_execution_enabled', return_value=scoped, create=True) as factory:
            self.assertTrue(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
        self.assertEqual(factory.call_count, 2)
        self.assertTrue(all(not call.args for call in factory.call_args_list))
        self.assertFalse(self.adapter.profile['enabled'])
        self.assertEqual(len(self.adapter.calls), 2)

    def test_latest_committed_parameters_win_over_snapshot_but_explicit_overrides_win(self):
        wf = self.create()
        node = wf['nodes'][1]
        node['parameters'] = {'readout_hz': 6.8e9, 'drive_amp': .2}
        with self.controller.store.transaction() as rt:
            rt.db.execute('INSERT INTO parameters VALUES(?,?,?)', (wf['id'] + ':Q11:readout_hz', dumps(6.95e9), 3))
            values, versions = self.controller._parameters(rt, wf, node)
            self.assertEqual(values['readout_hz'], 6.95e9)
            self.assertNotIn('drive_amp', values)
            self.assertEqual(versions['readout_hz'], 3)
            node['parameterOverrides'] = {'readout_hz': 6.96e9, 'drive_amp': .3}
            values, versions = self.controller._parameters(rt, wf, node)
            self.assertEqual(values['readout_hz'], 6.96e9)
            self.assertEqual(values['drive_amp'], .3)
            self.assertEqual(versions['readout_hz'], 3)

    def seed_unknown_receipt(self):
        wf = self.create()
        self.adapter.fail = True
        self.controller._execute_node(wf['id'], wf['nodes'][0]['id'])
        return self.controller.get(wf['id'])

    def test_reconcile_unknown_retains_quarantine_not_started_releases_it(self):
        wf = self.seed_unknown_receipt()
        unknown = self.command(wf, 'reconcile', 'reconcile-unknown')
        self.assertEqual(unknown['engineState'], 'needs_reconciliation')
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT state FROM platform_leases').fetchone()[0], 'quarantined')
        self.adapter.reconcile = lambda op: {'status': 'not_started', 'operationId': op}
        recovered = self.command(unknown, 'reconcile', 'reconcile-not-started')
        self.assertEqual((recovered['status'], recovered['engineState']), ('paused', 'reconciled'))
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0], 0)
            self.assertEqual(rt.db.execute('SELECT state FROM platform_effects').fetchone()[0], 'not_started')
        self.assertEqual(len(self.adapter.calls), 1)

    def test_reconcile_completed_preserves_evidence_but_does_not_accept_single_receipt(self):
        wf = self.seed_unknown_receipt()
        path = self.root / 'recovered-raw.json'
        path.write_text('recovered evidence', encoding='utf-8')
        result = {'dataKind': 'real', 'acquiredAt': now(), 'metrics': {'contrast_snr': 9},
            'quality': {'status': 'passed', 'reasons': ['receipt self claim']},
            'artifacts': [{'id': 'recovered-raw', 'kind': 'raw', 'name': path.name, 'localPath': str(path),
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}
        self.adapter.reconcile = lambda op: {'status': 'completed', 'operationId': op, 'result': deepcopy(result)}
        recovered = self.command(wf, 'reconcile', 'reconcile-completed')
        self.assertEqual((recovered['status'], recovered['engineState']), ('paused', 'reconciled'))
        node = recovered['nodes'][0]
        self.assertEqual(node['engineState'], 'reconciled_unverified')
        self.assertEqual(node['result']['quality']['status'], 'unverified')
        self.assert_code('GATE_NOT_PASSED', lambda: self.command(recovered, 'approve', 'no-approval-shortcut', nodeId=node['id']))
        self.assertEqual(self.controller.artifact('recovered-raw')[0], path)
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0], 0)
            self.assertEqual(rt.db.execute('SELECT state FROM platform_effects').fetchone()[0], 'completed')
        self.assertEqual(len(self.adapter.calls), 1)

    def test_readout_optimization_invalidates_readout_reference_and_preserves_evidence(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            reference = next(n for n in current['nodes'] if n['ownerId'] == 's04_readout_fidelity')
            optimizer = next(n for n in current['nodes'] if n['procedureId'] == 'ro_optimize')
            reference.update(engineState='verified', status='success', mainProcedureComplete=True,
                             result={'quality': {'status': 'passed'}, 'artifacts': [{'id': 'kept-reference'}]})
            original_attempt = {'id': 'readout-reference', 'nodeId': reference['id'], 'number': 1,
                                'state': 'verified', 'result': deepcopy(reference['result'])}
            self.controller.store.attempt(rt, wf['id'], original_attempt)
            optimizer['result'] = {'artifacts': [{'id': 'optimization-evidence'}]}
            self.controller._commit_candidate(rt, current, optimizer, {'readout_hz': 6.91e9}, {'readout_hz': 0})
            self.controller.store.save(rt, current, 'fixture_commit')
        details = self.controller.detail(wf['id'], reference['id'])
        self.assertEqual(details['node']['engineState'], 'stale')
        self.assertTrue(details['node']['result']['stale'])
        self.assertEqual(details['attempts'][0], original_attempt)
        self.assertFalse(details['node']['mainProcedureComplete'])

    def _readout_rebuild_fixture(self, policy='threshold', selected=.4):
        wf = self.create(policy=policy)
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            for node in current['nodes']:
                node.update(engineState='verified', status='success', progress=100, mainProcedureComplete=True,
                            result={'quality': {'status': 'passed'}, 'artifacts': []})
                if node['procedureId'] == 'sq_rabi_amplitude':
                    old = {'id': 'old-rabi-' + wf['id'], 'nodeId': node['id'], 'number': 1,
                           'mode': 'real', 'state': 'verified', 'result': deepcopy(node['result'])}
                    self.controller.store.attempt(rt, wf['id'], old)
                    node.update(attempt=1, latestAttemptId=old['id'])
                    node['parameterOverrides'] = {'readout_amp': .2}
            self.controller.store.save(rt, current, 'fixture_pre_readout_change')
        optimizer = next(n for n in current['nodes'] if n['procedureId'] == 'ro_optimize')
        self.adapter.candidate = {'readout_amp': selected}
        self.assertEqual(self.controller._execute_node(wf['id'], optimizer['id']), policy != 'strict')
        return self.controller.get(wf['id']), optimizer['id'], old

    def test_readout_change_rebuilds_rabi_chain_then_independently_verifies_fixed_candidate(self):
        wf, optimizer_id, old_rabi = self._readout_rebuild_fixture()
        marker = wf['readoutRevalidations']['Q11']
        self.assertEqual(wf['currentNodeId'], marker['rabiNodeId'])
        self.assertEqual(wf['engineState'], 'backtrack_queued')
        self.assertLess(wf['progress'], 100)
        self.assertEqual(marker['values'], {'readout_hz': 6.8e9, 'readout_amp': .4})
        self.assertEqual(marker['parameterVersions']['readout_amp'], 1)
        for procedure in ('sq_rabi_amplitude', 'sq_rabi_time', 'ro_iq01', 'ro_optimize', 'sq_allxy', 'sq_rb'):
            self.assertEqual(next(n for n in wf['nodes'] if n['procedureId'] == procedure)['engineState'], 'stale')
        self.assert_code('GATE_NOT_PASSED', lambda: self.command(wf, 'approve', 'no-stale-approve', nodeId=optimizer_id))
        self.adapter.candidate = {}
        for procedure in ('sq_rabi_amplitude', 'sq_rabi_time', 'ro_iq01'):
            node = next(n for n in wf['nodes'] if n['procedureId'] == procedure)
            before = len(self.adapter.calls)
            self.assertTrue(self.controller._execute_node(wf['id'], node['id']))
            self.assertEqual(len(self.adapter.calls) - before, 2)
            for request in self.adapter.calls[before:]:
                self.assertEqual(request['parameters']['readout_amp'], .4)
                self.assertNotIn('amplitudes', request['parameters'], 'Readout pin must not collapse the XY Rabi scan')
        self.adapter.candidate = {'readout_amp': .4}
        before = len(self.adapter.calls)
        self.assertTrue(self.controller._execute_node(wf['id'], optimizer_id))
        fixed_calls = self.adapter.calls[before:]
        self.assertEqual(len(fixed_calls), 2)
        self.assertNotEqual(fixed_calls[0]['operationId'], fixed_calls[1]['operationId'])
        self.assertTrue(all(request['parameters']['amplitudes'] == [.4] for request in fixed_calls))
        current = self.controller.get(wf['id'])
        self.assertEqual(current['readoutRevalidations']['Q11']['state'], 'completed')
        self.assertEqual(next(n for n in current['nodes'] if n['id'] == marker['rabiNodeId'])['engineState'], 'verified')
        self.assertEqual(next(n for n in current['nodes'] if n['id'] == optimizer_id)['engineState'], 'verified')
        self.assertEqual(self.controller.detail(wf['id'], marker['rabiNodeId'])['attempts'][0], old_rabi)
        self.assertEqual(current['gates'], wf['gates'])
        self.assertEqual(current['gatesVersion'], wf['gatesVersion'])
        self.assertEqual(current['maxAttemptsPerNode'], 3)
        with self.controller.store.connect() as rt:
            row = rt.db.execute('SELECT revision FROM parameters WHERE name=?', (wf['id'] + ':Q11:readout_amp',)).fetchone()
            self.assertEqual(row['revision'], 1, 'Fixed confirmation must not create a new winner/version')

    def test_equal_initial_readout_candidate_does_not_start_rebuild(self):
        wf, optimizer_id, _ = self._readout_rebuild_fixture(selected=.2)
        self.assertNotIn('readoutRevalidations', wf)
        self.assertEqual(next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')['engineState'], 'verified')

    def test_readout_rebuild_strict_policy_pauses_at_real_rabi_target(self):
        wf, _, _ = self._readout_rebuild_fixture(policy='strict')
        self.assertEqual((wf['status'], wf['engineState']), ('paused', 'readout_revalidation_pending'))
        self.assertEqual(wf['currentNodeId'], wf['readoutRevalidations']['Q11']['rabiNodeId'])
        self.assertEqual(len(self.adapter.calls), 2)

    def test_upstream_readout_change_invalidates_old_pin_without_reapplying_it(self):
        wf, optimizer_id, _ = self._readout_rebuild_fixture()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            survey = next(n for n in current['nodes'] if n['procedureId'] == 'ro_resonator_survey')
            self.controller._commit_candidate(rt, current, survey, {'readout_hz': 6.91e9}, {'readout_hz': 0})
            self.controller.store.save(rt, current, 'fixture_upstream_changed')
        current = self.controller.get(wf['id'])
        self.assertEqual(current['readoutRevalidations']['Q11']['state'], 'invalidated')
        self.assertIn('readout_hz', current['readoutRevalidations']['Q11']['reason'])
        self.adapter.candidate = {}
        before = len(self.adapter.calls)
        self.assertTrue(self.controller._execute_node(wf['id'], optimizer_id))
        request = self.adapter.calls[before]
        self.assertEqual(request['parameters']['readout_hz'], 6.91e9)
        self.assertNotIn('amplitudes', request['parameters'])
        self.assertNotIn('readoutRevalidation', request)

    def test_readout_version_only_change_invalidates_pin_before_dispatch(self):
        wf, optimizer_id, _ = self._readout_rebuild_fixture()
        with self.controller.store.transaction() as rt:
            rt.db.execute('UPDATE parameters SET revision=revision+1 WHERE name=?', (wf['id'] + ':Q11:readout_amp',))
        self.adapter.candidate = {}
        before = len(self.adapter.calls)
        self.assertTrue(self.controller._execute_node(wf['id'], optimizer_id))
        self.assertNotIn('amplitudes', self.adapter.calls[before]['parameters'])
        self.assertEqual(self.controller.get(wf['id'])['readoutRevalidations']['Q11']['state'], 'invalidated')

    def test_operator_override_cannot_release_pin_or_move_rebuild_to_uncommitted_readout(self):
        wf, optimizer_id, _ = self._readout_rebuild_fixture(policy='strict')
        marker = wf['readoutRevalidations']['Q11']
        for node_id in (optimizer_id, marker['rabiNodeId']):
            self.assert_code('READOUT_FIXED_WORKING_POINT', lambda: self.controller.set_parameters(wf['id'], node_id, {
                'operationId': 'conflicting-readout-' + node_id, 'expectedRevision': wf['revision'], 'parameters': {'readout_amp': .6}}))
        self.assertEqual(self.controller.get(wf['id'])['readoutRevalidations']['Q11'], marker)
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            self.controller._node(current, marker['rabiNodeId']).setdefault('parameterOverrides', {})['readout_amp'] = .6
            self.controller.store.save(rt, current, 'fixture_legacy_conflicting_override')
        self.assert_code('READOUT_FIXED_WORKING_POINT', lambda: self.controller._execute_node(wf['id'], marker['rabiNodeId']))
        self.assertEqual(self.controller.get(wf['id'])['readoutRevalidations']['Q11']['state'], 'active')
        self.assertEqual(len(self.adapter.calls), 2)
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0], 0)

    def test_readout_rebuild_rejects_main_blueprint_without_time_rabi(self):
        wf = self.create(coverage='main')
        def unsupported_commit():
            with self.controller.store.transaction() as rt:
                current = self.controller.get(wf['id'], rt)
                optimizer = next(n for n in current['nodes'] if n['procedureId'] == 'ro_optimize')
                optimizer['result'] = {'artifacts': [], 'quality': {'status': 'passed'}}
                self.controller._commit_candidate(rt, current, optimizer, {'readout_amp': .4}, {'readout_amp': 0})
        self.assert_code('READOUT_REVALIDATION_UNSUPPORTED', unsupported_commit)
        self.assertNotIn('readoutRevalidations', self.controller.get(wf['id']))
        with self.controller.store.connect() as rt:
            self.assertIsNone(rt.db.execute('SELECT value FROM parameters WHERE name=?', (wf['id'] + ':Q11:readout_amp',)).fetchone())
        self.assertEqual(self.adapter.calls, [])

    def _failed_fixed_readout(self):
        wf, optimizer_id, _ = self._readout_rebuild_fixture(policy='autopilot')
        # A nearby different winner would fit the old 15% repeatability rule,
        # but it cannot satisfy this fixed candidate's identity contract.
        self.adapter.candidate = {'readout_amp': .45}
        before = len(self.adapter.calls)
        self.assertFalse(self.controller._execute_node(wf['id'], optimizer_id))
        self.assertEqual(len(self.adapter.calls) - before, 1)
        current = self.controller.get(wf['id'])
        self.assertEqual(current['readoutRevalidations']['Q11']['state'], 'fixed_failed')
        self.assertEqual(next(n for n in current['nodes'] if n['id'] == optimizer_id)['result']['quality']['status'], 'failed')
        return current, optimizer_id

    def test_failed_fixed_readout_shots_retry_stays_pinned(self):
        wf, optimizer_id = self._failed_fixed_readout()
        self.controller._brain = lambda *args: {'status': 'ok', 'decision': {
            'action': 'retry', 'reason': 'More samples at the selected point', 'evidenceIds': [],
            'parameterChanges': {'shots': 1500}}}
        self.assertTrue(self.controller._decide(wf['id'], optimizer_id))
        self.assertEqual(self.controller.get(wf['id'])['readoutRevalidations']['Q11']['state'], 'fixed_failed')
        self.adapter.candidate = {'readout_amp': .4}
        before = len(self.adapter.calls)
        self.assertTrue(self.controller._execute_node(wf['id'], optimizer_id))
        self.assertTrue(all(r['parameters']['amplitudes'] == [.4] and r['parameters']['shots'] == 1500 for r in self.adapter.calls[before:]))

    def test_fixed_failure_requires_explicit_validated_range_to_release_pin(self):
        wf, optimizer_id = self._failed_fixed_readout()
        plans = []
        def plan(request):
            plans.append(deepcopy(request))
            p = request['parameters']
            if p['amp_stop'] > .9:
                raise APIError('GRID_OUT_OF_BOUNDS', 'Emitted scan axis exceeds test hardware limit')
            return {'estimated_total_shots': 1}
        self.adapter.build_plan = plan
        changes = {'amp_start': .1, 'amp_stop': .98}
        self.controller._brain = lambda *args: {'status': 'ok', 'decision': {
            'action': 'retry', 'reason': 'Selected fixed point failed; scan a new range', 'evidenceIds': [],
            'parameterChanges': changes}}
        self.assertFalse(self.controller._decide(wf['id'], optimizer_id))
        rejected = self.controller._node(self.controller.get(wf['id']), optimizer_id)
        self.assertEqual(rejected['engineState'], 'waiting_model')
        self.assertEqual(rejected['lastDecisionError']['code'], 'GRID_OUT_OF_BOUNDS')
        self.assertEqual(self.controller.get(wf['id'])['readoutRevalidations']['Q11']['state'], 'fixed_failed')
        changes['amp_stop'] = .8
        self.assertTrue(self.controller._decide(wf['id'], optimizer_id))
        current = self.controller.get(wf['id'])
        marker = current['readoutRevalidations']['Q11']
        self.assertEqual(marker['state'], 'released')
        self.assertEqual(marker['rescanRequest']['validatedRange'], changes)
        self.assertNotIn('amplitudes', plans[-1]['parameters'])
        self.assertEqual(len(self.adapter.calls), 3, 'Model range validation must not acquire')
        self.assertEqual(self.controller.detail(wf['id'], optimizer_id)['decisions'][-1]['readoutRevalidation']['mode'], 'full_scan')

    def test_readout_rebuild_and_rescan_do_not_extend_attempt_budget(self):
        wf, optimizer_id, _ = self._readout_rebuild_fixture()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            rabi = self.controller._node(current)
            rabi['attempt'] = current['maxAttemptsPerNode']
            self.controller.store.save(rt, current, 'fixture_budget_used')
        before = len(self.adapter.calls)
        self.controller.run_workflow(wf['id'])
        current = self.controller.get(wf['id'])
        self.assertEqual(current['engineState'], 'budget_exhausted')
        self.assertEqual(len(self.adapter.calls), before)
        self.assertEqual(current['maxAttemptsPerNode'], wf['maxAttemptsPerNode'])
        self.assertEqual(current['maxWorkflowAttempts'], wf['maxWorkflowAttempts'])
        self.assertEqual(current['maxTotalShots'], wf['maxTotalShots'])
        self.assertEqual(current['budget'], wf['budget'])

    def test_range_rescan_requires_failed_fixed_point_and_keeps_pin_when_budget_exhausted(self):
        wf, optimizer_id, _ = self._readout_rebuild_fixture(policy='autopilot')
        self.adapter.build_plan = lambda request: {'estimated_total_shots': 1}
        self.controller._brain = lambda *args: {'status': 'ok', 'decision': {
            'action': 'retry', 'reason': 'Explicit range request', 'evidenceIds': [],
            'parameterChanges': {'amp_start': .1, 'amp_stop': .8}}}
        self.assertFalse(self.controller._decide(wf['id'], optimizer_id))
        rejected = self.controller._node(self.controller.get(wf['id']), optimizer_id)
        self.assertEqual(rejected['engineState'], 'waiting_model')
        self.assertEqual(rejected['lastDecisionError']['code'], 'READOUT_FIXED_VALIDATION_REQUIRED')
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            optimizer = self.controller._node(current, optimizer_id)
            optimizer.update(attempt=current['maxAttemptsPerNode'], latestAttemptId='failed-fixed',
                result={'quality': {'status': 'failed'}, 'artifacts': []})
            current['readoutRevalidations']['Q11'].update(state='fixed_failed', lastFixedAttemptId='failed-fixed')
            self.controller.store.save(rt, current, 'fixture_fixed_budget')
        self.assertFalse(self.controller._decide(wf['id'], optimizer_id))
        current = self.controller.get(wf['id'])
        self.assertEqual(current['engineState'], 'budget_exhausted')
        self.assertEqual(current['readoutRevalidations']['Q11']['state'], 'fixed_failed')
        self.assertEqual(current['maxAttemptsPerNode'], 3)
        self.assertEqual(len(self.adapter.calls), 2)

    def test_good_fits_with_inconsistent_candidates_cannot_pass_or_commit(self):
        wf = self.create()
        self.adapter.candidates_by_call = [{'readout_hz': 6.9e9}, {'readout_hz': 6.92e9}]
        self.assertFalse(self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
        node = self.controller.get(wf['id'])['nodes'][0]
        self.assertEqual(len(self.adapter.calls), 2)
        self.assertEqual(node['result']['metrics']['contrast_snr'], 8)
        self.assertEqual(node['result']['quality']['status'], 'failed')
        self.assertTrue(any('重复性不足' in reason for reason in node['result']['quality']['reasons']))
        with self.controller.store.connect() as rt:
            self.assertIsNone(rt.db.execute('SELECT value FROM parameters WHERE name=?', (wf['id'] + ':Q11:readout_hz',)).fetchone())


if __name__ == '__main__':
    unittest.main()
