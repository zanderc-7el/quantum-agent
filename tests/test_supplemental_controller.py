"""Explicit offline protocol fixtures; no real measurements or device imports.

The .npz-named fixtures contain labelled test bytes, not fabricated experimental
arrays. These tests exercise controller evidence identity and acceptance wiring.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.common import APIError, digest, dumps, now
from backend.controller import Controller
try:
    from test_controller import make_fixture
    from test_supplemental_quality import allxy_report, t2_report
except ImportError:
    from tests.test_controller import make_fixture
    from tests.test_supplemental_quality import allxy_report, t2_report


class SupplementalControllerContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-supplemental-offline-')
        self.root = Path(self.directory.name)
        controller, self.adapter = make_fixture(self.root)
        controller.close()
        native = self.root / 'native'
        native.mkdir()
        self.analysis_source = native / 'platform_analysis.py'
        self.analysis_source.write_text('# Explicit offline fixture source, never imported.\n', encoding='utf-8')
        self.native_sha = hashlib.sha256(self.analysis_source.read_bytes()).hexdigest()
        (native / 'source_manifest.json').write_text(json.dumps([
            {'path': 'native/platform_analysis.py', 'sha256': self.native_sha}]), encoding='utf-8')
        self.adapter.profile.update(sourceManifest='native/source_manifest.json', runtimeRoot='native')
        self.adapter.validation_parameter_keys = lambda: ('readout_hz', 'readout_amp', 'drive_hz')
        self.controller = Controller(self.root, adapter=self.adapter, start_worker=False)
        self.controller._brain = lambda *args, **kwargs: {'status': 'waiting_model', 'message': 'OFFLINE TEST ONLY'}
        self.report_factory = None
        self.damage = None
        self.adapter.execute = self.execute_fixture

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def create(self, mode='real'):
        return self.controller.create({'name': 'OFFLINE supplemental controller contract', 'mode': mode,
                                       'resourceIds': ['Q11'], 'policy': 'threshold'})

    def install(self, wf, operation='install-supplemental'):
        return self.controller.install_supplemental_acceptance(wf['id'], {
            'operationId': operation, 'expectedRevision': wf['revision'],
            'policyVersion': 'capture-consistency/1', 'reason': 'Explicit offline installation test'})

    def assert_code(self, code, callback):
        with self.assertRaises(APIError) as error:
            callback()
        self.assertEqual(error.exception.code, code)

    def execute_fixture(self, request, emit, cancel):
        self.adapter.calls.append(deepcopy(request))
        operation = request['operationId']
        directory = self.root / 'data/runs/arclight_operations' / hashlib.sha256(operation.encode()).hexdigest()
        folder = directory / 'results' / 'offline_fixture_job'
        folder.mkdir(parents=True)
        raw = folder / 'raw_scan.npz'
        raw.write_bytes(('EXPLICIT OFFLINE CONTRACT FIXTURE, NOT MEASURED IQ: ' + operation).encode())
        raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
        procedure = request['procedureId']
        role = 'verification' if operation.endswith('/verify') else 'selection'
        if self.report_factory:
            report = self.report_factory(procedure, role, raw_sha)
        elif procedure == 'sq_allxy':
            report = allxy_report([.05, -.04], raw_sha)
        else:
            report = t2_report(procedure, [1e-6, 1.02e-6], raw_sha,
                               'exponential' if procedure == 'sq_cpmg' else 'damped_sinusoid')
        report['analysis']['source_sha256'] = self.native_sha
        parameters = deepcopy(request['parameters'])
        job = {'name': 'offline-job-' + hashlib.sha256(operation.encode()).hexdigest()[:12],
               'target': 'Q11', 'parameters': parameters, 'spec': {'fixture_only': True},
               'config': {'pulse_fixture': {'amplitude': .2}}, 'xy_physical_channel': 0,
               'xy_filter_mhz': 500, 'xy_attenuation_db': 0, 'xy_delay_s': 0.,
               'captures': 2, 'acquisitions_per_point': 1, 'randomize': True, 'order_seed': 123}
        rows = 42 if procedure == 'sq_allxy' else 162
        plan = {'adapter': 'arclight', 'operationId': operation, 'resourceId': request['resourceId'],
                'procedureId': procedure, 'parameters': parameters, 'jobs': [deepcopy(job)], 'expectedJobRows': [rows],
                'requestIdentity': {k: request.get(k) for k in ('runId', 'nodeId', 'attemptId', 'operationId',
                                                              'resourceId', 'procedureId', 'parameterVersions')}}
        state = {'mode': 'real_hardware', 'state': 'acquired', 'job': job['name'], 'target': 'Q11',
                 'shutdown_ok': True, 'points_completed': rows}
        result = {'dataKind': 'real', 'acquiredAt': now(), 'parameters': parameters,
                  'metrics': deepcopy(report['metrics']), 'analysisQuality': deepcopy(report['analysis']),
                  'candidateParameters': {}, 'provenance': {'freshAcquisition': True, 'operationId': operation}}
        if self.damage:
            self.damage(role, plan, job, state, report, result)
        contents = [(directory / 'plan.json', plan), (folder / 'job.json', job),
                    (folder / 'status.json', state), (folder / 'platform_analysis.json', report)]
        for path, value in contents:
            path.write_text(dumps(value), encoding='utf-8')
        # A shared unrelated source-hash artifact is deliberately re-registered
        # by verification; cross-checking selection must not mistake it for raw
        # or analysis evidence from the wrong operation.
        common = folder / 'source_hashes.json'
        common.write_text('{"offline_shared_source":true}', encoding='utf-8')
        paths = [raw] + [path for path, _ in contents] + [common]
        result['artifacts'] = [{'id': 'offline-' + hashlib.sha256(path.read_bytes()).hexdigest()[:24],
            'name': path.name, 'kind': 'raw' if path == raw else 'json', 'localPath': str(path),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]
        return result

    def test_explicit_install_freezes_policy_without_rewriting_original_acceptance(self):
        wf = self.create()
        with self.controller.store.connect() as rt:
            steps_before = [tuple(r) for r in rt.db.execute('SELECT id,spec FROM steps ORDER BY id')]
        installed = self.install(wf)
        snapshot = installed['supplementalAcceptance']
        self.assertEqual(snapshot['policyVersion'], 'capture-consistency/1')
        self.assertEqual(snapshot['policyHash'], digest(snapshot['policySnapshot']))
        self.assertEqual(snapshot['nativeAnalysisSha256'], self.native_sha)
        self.assertEqual(set(snapshot['applicableProcedures']), {'sq_allxy', 'sq_ramsey', 'sq_echo', 'sq_cpmg'})
        self.assertEqual(installed['status'], 'draft')
        for key in ('gates', 'gatesVersion', 'adapterProfileHash', 'iterations', 'reservedShots', 'budget'):
            self.assertEqual(installed[key], wf[key])
        with self.controller.store.connect() as rt:
            self.assertEqual([tuple(r) for r in rt.db.execute('SELECT id,spec FROM steps ORDER BY id')], steps_before)
        self.assertEqual(self.install(wf), installed)
        self.assert_code('SUPPLEMENTAL_ALREADY_INSTALLED', lambda: self.install(installed, 'second-install'))
        events = [e for e in self.controller.store.events(wf['id']) if e['event'] == 'supplemental_acceptance_installed']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['payload']['source'], 'operator')
        self.assertEqual(self.adapter.calls, [])

    def test_install_rejects_prior_applicable_attempt_even_if_counter_was_zeroed(self):
        for ledger in (False, True):
            wf = self.create()
            with self.controller.store.transaction() as rt:
                current = self.controller.get(wf['id'], rt)
                node = next(n for n in current['nodes'] if n['procedureId'] == 'sq_ramsey')
                if ledger:
                    self.controller.store.attempt(rt, wf['id'], {'id': 'prior-' + wf['id'], 'nodeId': node['id'], 'number': 1})
                else:
                    node['attempt'] = 1
                self.controller.store.save(rt, current, 'fixture_prior_attempt')
            current = self.controller.get(wf['id'])
            self.assert_code('SUPPLEMENTAL_ALREADY_STARTED', lambda: self.install(current, 'attempt-install-' + wf['id']))
            self.assertNotIn('supplementalAcceptance', self.controller.get(wf['id']))

    def test_install_requires_real_idle_nonterminal_checkpoint(self):
        cases = [('replay', 'draft', None), ('real', 'running', None), ('real', 'queued', None),
                 ('real', 'completed', None), ('real', 'attention', 'unknown'), ('real', 'paused', 'pending')]
        for mode, status, effect in cases:
            wf = self.create(mode)
            with self.controller.store.transaction() as rt:
                current = self.controller.get(wf['id'], rt); current['status'] = status
                if effect:
                    rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,NULL)',
                        ('pending-' + wf['id'], wf['id'], 'fixture', effect, '{}'))
                self.controller.store.save(rt, current, 'fixture_install_guard')
            current = self.controller.get(wf['id'])
            self.assert_code('RECONCILIATION_REQUIRED' if effect else 'INVALID_STATE',
                             lambda: self.install(current, 'guard-' + wf['id']))
        self.assertEqual(self.adapter.calls, [])

    def test_uninstalled_workflow_retains_original_gate_semantics(self):
        wf = self.create()
        self.report_factory = lambda p, r, raw: allxy_report([.3, -.3], raw)
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_allxy')
        with patch.object(self.controller, '_loaded_supplemental_module', side_effect=AssertionError('No implicit policy import')):
            self.assertTrue(self.controller._execute_node(wf['id'], node['id']))
        result = self.controller.detail(wf['id'], node['id'])['node']['result']
        self.assertEqual(result['quality']['status'], 'passed')
        self.assertNotIn('supplementalQuality', result)

    def test_good_native_reports_pass_all_three_receipts_without_changing_files(self):
        for procedure in ('sq_allxy', 'sq_ramsey', 'sq_echo', 'sq_cpmg'):
            wf = self.install(self.create(), 'install-' + procedure)
            node = next(n for n in wf['nodes'] if n['procedureId'] == procedure)
            before = len(self.adapter.calls)
            self.assertTrue(self.controller._execute_node(wf['id'], node['id']))
            result = self.controller.detail(wf['id'], node['id'])['node']['result']
            self.assertEqual(len(self.adapter.calls) - before, 2)
            self.assertEqual(result['quality']['ruleVersion'], 'diagnostic-gates/1')
            bundle = result['supplementalQuality']
            self.assertTrue(bundle['accepted'])
            self.assertEqual(set(bundle['receipts']), {'selection', 'verification', 'cross'})
            self.assertTrue(all(v['accepted'] for v in bundle['receipts'].values()))
            self.assertFalse(result['selectionResult']['supplementalQuality']['accepted'])
            self.assertEqual(set(result['selectionResult']['supplementalQuality']['receipts']), {'selection'})
            for role in ('selection', 'verification'):
                evidence = bundle['receipts']['cross']['registeredEvidence'][role]
                path, metadata = self.controller.artifact(evidence['analysisArtifactId'])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), metadata['sha256'])

    def test_bad_allxy_capture_blocks_verification_and_preserves_acquisition(self):
        wf = self.install(self.create())
        self.report_factory = lambda p, r, raw: allxy_report([.3, -.3], raw)
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_allxy')
        self.assertFalse(self.controller._execute_node(wf['id'], node['id']))
        detail = self.controller.detail(wf['id'], node['id'])
        result = detail['node']['result']
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(result['metrics']['allxy_rms'], 0)
        self.assertEqual(result['quality']['status'], 'failed')
        self.assertEqual(result['supplementalQuality']['receipts']['selection']['status'], 'failed')
        self.assertIsNone(detail['attempts'][0]['verificationOperationId'])
        self.assertTrue(self.controller.artifact(result['artifacts'][0]['id'])[0].is_file())

    def test_different_independent_decay_times_fail_cross_receipt(self):
        wf = self.install(self.create())
        self.report_factory = lambda p, role, raw: t2_report(p, [1e-6, 1.02e-6] if role == 'selection' else [2e-6, 2.02e-6], raw)
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_ramsey')
        self.assertFalse(self.controller._execute_node(wf['id'], node['id']))
        result = self.controller.detail(wf['id'], node['id'])['node']['result']
        receipts = result['supplementalQuality']['receipts']
        self.assertTrue(receipts['selection']['accepted'])
        self.assertTrue(receipts['verification']['accepted'])
        self.assertFalse(receipts['cross']['accepted'])
        self.assertEqual(len(self.adapter.calls), 2)
        self.assertEqual(result['quality']['status'], 'failed')

    def test_wrong_run_resource_operation_context_or_source_is_not_accepted(self):
        for damage in ('run', 'resource', 'operation', 'parameters', 'source', 'status'):
            with self.subTest(damage=damage):
                wf = self.install(self.create(), 'install-damage-' + damage)
                def corrupt(role, plan, job, state, report, result):
                    if damage == 'run': plan['requestIdentity']['runId'] = 'another-run'
                    if damage == 'resource': job['target'] = 'Q18'
                    if damage == 'operation': result['provenance']['operationId'] = 'old-operation'
                    if damage == 'parameters': result['parameters'] = {**result['parameters'], 'readout_amp': .9}
                    if damage == 'source': report['analysis']['source_sha256'] = '0'*64
                    if damage == 'status': state['mode'] = 'offline_compile_only'
                self.damage = corrupt
                node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_echo')
                before = len(self.adapter.calls)
                self.assertFalse(self.controller._execute_node(wf['id'], node['id']))
                self.assertEqual(len(self.adapter.calls) - before, 1)
                result = self.controller.detail(wf['id'], node['id'])['node']['result']
                self.assertEqual(result['supplementalQuality']['receipts']['selection']['status'], 'unverified')
                self.assertNotEqual(result['quality']['status'], 'passed')

    def test_actual_native_configuration_and_routing_must_match_plan(self):
        for field in ('config', 'xy_physical_channel', 'xy_filter_mhz', 'xy_attenuation_db', 'xy_delay_s',
                      'captures', 'acquisitions_per_point', 'randomize', 'order_seed'):
            with self.subTest(field=field):
                wf = self.install(self.create(), 'install-config-' + field)
                def corrupt(role, plan, job, state, report, result):
                    if field == 'config':
                        job[field]['pulse_fixture']['amplitude'] = .7
                    elif field == 'randomize':
                        job[field] = not job[field]
                    else:
                        job[field] += 1
                self.damage = corrupt
                node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_ramsey')
                before = len(self.adapter.calls)
                self.assertFalse(self.controller._execute_node(wf['id'], node['id']))
                result = self.controller.detail(wf['id'], node['id'])['node']['result']
                self.assertEqual(len(self.adapter.calls) - before, 1)
                self.assertEqual(result['supplementalQuality']['receipts']['selection']['bindingError'],
                                 'SUPPLEMENTAL_EVIDENCE_BINDING')

    def test_native_worker_may_inject_independent_projection_without_config_change(self):
        wf = self.install(self.create())
        def inject(role, plan, job, state, report, result):
            job['calibration'] = {'projection': {'independent_reference': True, 'source': 'offline-reference'},
                                  'paired_rb_projection': {'source': 'offline-reference'}}
        self.damage = inject
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_allxy')
        self.assertTrue(self.controller._execute_node(wf['id'], node['id']))
        self.assertTrue(self.controller.detail(wf['id'], node['id'])['node']['result']['supplementalQuality']['accepted'])

    def test_registered_analysis_modified_after_capture_is_not_accepted(self):
        wf = self.install(self.create())
        call = self.controller._call_adapter
        def after_registration(request, token):
            result = call(request, token)
            artifact = next(a for a in result['artifacts'] if a['name'] == 'platform_analysis.json')
            path, _ = self.controller.artifact(artifact['id'])
            path.write_text('{}', encoding='utf-8')
            return result
        self.controller._call_adapter = after_registration
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_ramsey')
        self.assertFalse(self.controller._execute_node(wf['id'], node['id']))
        result = self.controller.detail(wf['id'], node['id'])['node']['result']
        receipt = result['supplementalQuality']['receipts']['selection']
        self.assertEqual(receipt['status'], 'unverified')
        self.assertEqual(receipt['bindingError'], 'SUPPLEMENTAL_EVIDENCE_HASH')
        self.assertEqual(len(self.adapter.calls), 1)

    def test_changed_frozen_native_source_stops_before_dispatch(self):
        wf = self.install(self.create())
        self.analysis_source.write_text('# changed fixture source\n', encoding='utf-8')
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_ramsey')
        self.assertFalse(self.controller._execute_node(wf['id'], node['id']))
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.controller.get(wf['id'])['engineState'], 'supplemental_acceptance_mismatch')

    def test_loaded_module_hash_cannot_be_relabelled_after_source_change(self):
        module = self.controller._loaded_supplemental_module()
        path = Path(module.__file__)
        original_read = Path.read_bytes
        def changed_source(value):
            content = original_read(value)
            return content + b'\n# changed disk bytes for test only\n' if value == path else content
        with patch.object(Path, 'read_bytes', changed_source):
            self.assert_code('SUPPLEMENTAL_SOURCE_CHANGED', self.controller._loaded_supplemental_module)
        self.assertEqual(self.controller._loaded_supplemental_module()._qcal_source_sha256,
                         hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
