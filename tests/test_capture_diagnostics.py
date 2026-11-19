"""Synthetic offline fixtures in temp directories; no devices, model or live DB."""
from copy import deepcopy
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from backend.common import ROOT, file_hash
from backend.capture_diagnostics import (PROCEDURES, VERSION, MAX_INPUT_UNITS, resolve_sources,
    calculate_diagnostics, attach_diagnostics, unavailable)
from backend.decision_context import build_decision_context
from backend.prior_failure_context import (PROFILE_FILES, ENTRY_FILES, audited_bindings,
    javascript_json_units_upper_bound)

IMPLEMENTATION = 'adapters.arclight.ArcLightAdapter'
PYTHON = json.loads((ROOT / 'adapters/arclight_profile_v2.json').read_text(encoding='utf-8'))['python']


def numeric_fixture(root, procedure, captures=2):
    """Deliberately synthetic test IQ, never written to the real artifact store."""
    import numpy as np
    spec = importlib.util.spec_from_file_location('fixture_native', root / 'adapters/arclight_runtime/analyze_single_qubit.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    kind = PROCEDURES[procedure]
    points, shots, blocks = 13, 8, 4
    t = np.linspace(0, 6e-6, points)
    multiplier = 2 if kind == 'echo' else 8 if kind == 'cpmg' else 1
    key = 'exp.cir.main.wait_a.params' if kind in ('echo', 'cpmg') else 'exp.cir.main.wait.params'
    sweeps = [{'bindings': key, 'values': (t / multiplier).tolist()}]
    if kind == 'echo':
        sweeps.append({'bindings': 'exp.cir.main.wait_b.params', 'values': (t / 2).tolist()})
    if kind in ('ramsey', 'echo'):
        sweeps.append({'bindings': 'exp.cir.main.phase.params', 'values': (2*np.pi*3e5*t).tolist()})
    if kind == 'cpmg':
        # Native N=4: first/last half gaps T/8, three middle gaps T/4.
        sweeps += [{'bindings': 'exp.cir.main.wait_' + str(n) + '.params',
                    'values': (t / (8 if n == 3 else 4)).tolist()} for n in range(4)]
    records = []
    rng = np.random.default_rng(382019)
    for capture in range(captures):
        for base in rng.permutation(points):
            records.append({'capture': capture, 'base_index': int(base),
                'axes': {s['bindings']: s['values'][base] for s in sweeps}})
    x = np.array([r['axes'][key] for r in records]) * multiplier
    z = x / t[-1]
    oscillatory = kind in ('ramsey', 'echo')
    params = [0., 0., 1e-3, 2., .2, 1.5] if oscillatory else [0., 1e-3, .8]
    prediction = 1e-3*np.exp(-z/1.5)*np.cos(2*np.pi*2*z+.2) if oscillatory else 1e-3*np.exp(-z/.8)
    ordinal_trend = (np.arange(len(records)) - np.mean(np.arange(len(records)))) * 1e-6
    # Exact centered per-shot variation; block offsets make effective SEM differ
    # from shot IID SEM. Randomized x keeps ordinal and scan coordinates distinct.
    noise = np.tile(np.arange(shots) - (shots-1)/2, blocks) * 1e-6
    noise += np.repeat(np.array([-1.5, -.5, .5, 1.5]), shots) * 2e-6
    i = (prediction[:, None] + ordinal_trend[:, None] + noise[None, :])[:, None, :]
    q = np.zeros_like(i)
    block_index = np.tile(np.repeat(np.arange(blocks), shots), (len(records), 1))
    iq = native.validate_arrays(records, i, q)
    projected, sem, metadata = native.projection(iq, {'projection': {'direction': [1., 0.], 'origin': [0., 0.]}}, blocks, block_index)
    stats = metadata.pop('point_statistics')
    axes = native.normalize_axes(records)
    _, batches = native.repeat_blocks(x, list(range(len(x))), axes)
    repeats, series, expected = [], [], []
    for capture, indices in enumerate(batches):
        residual = projected[indices] - prediction[indices]
        rms = float(np.sqrt(np.mean(residual**2)))
        r2 = float(1 - np.sum(residual**2) / np.sum((projected[indices]-projected[indices].mean())**2))
        repeats.append({'repeat': capture+1, 'fit': {'model': 'damped_sinusoid' if oscillatory else 'exponential',
            'fit_parameters_normalized_x': params, 'x_origin': float(x[indices][0]), 'x_span': float(np.ptp(x[indices])),
            'residual_rms': rms, 'decay_time': float(t[-1] * (1.5 if oscillatory else .8))},
            'diagnostics': {'rejection_reasons': []}})
        series.append({'fit': False, 'x': x[indices].tolist(), 'y': projected[indices].tolist()})
        expected.append({'residualRms': rms, 'rSquaredFromUnchangedFit': r2,
            'medianEffectiveSem': float(np.median(sem[indices])),
            'residualToEffectiveSemRms': float(np.sqrt(np.mean((residual/sem[indices])**2))),
            'residualAcquisitionOrdinalPearsonR': float(np.corrcoef(residual, indices)[0, 1]),
            'scanCoordinateAcquisitionOrdinalPearsonR': float(np.corrcoef(x[indices], indices)[0, 1])})
    analysis = {'kind': kind, 'procedureId': procedure, 'accepted': False, 'reasons': ['SYNTHETIC OFFLINE FIXTURE'],
        'projection': metadata, 'point_statistics': stats, 'points': len(records), 'shots_per_point': shots*blocks,
        'x_axis': {'native_key': key, 'unit': 's', 'echo_multiplier': multiplier},
        'groups': [{'group_axes': {}, 'repeats': repeats}]}
    np.savez_compressed(root / 'synthetic-fixture.npz', i=i, q=q, acquisition_block_index=block_index)
    return {'records': records, 'spec': {'meta': {'expand_mode': 'zip' if len(sweeps)>1 else 'cartesian', 'shots': shots}, 'sweeps': sweeps},
        'analysis': analysis, 'series': series, 'metrics': {'r_squared': min(r['rSquaredFromUnchangedFit'] for r in expected)},
        'expected': expected, 'parameters': {'points': points, 'shots': shots, 'capture_repeats': captures,
            'acquisitions_per_point': blocks, 'pi_pulses': 4}, 'multiplier': multiplier}


class CaptureDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='qcal-capture-diagnostic-tests-')
        cls.root = Path(cls.temp.name)
        for name in [v[0] for v in PROFILE_FILES.values()] + ['adapters/arclight_runtime/' + n for n in
                ('source_manifest.json', 'platform_analysis.py', 'analyze_single_qubit.py')]:
            dst = cls.root / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((ROOT / name).read_bytes())
        cls.bindings = audited_bindings(IMPLEMENTATION,
            {key: (cls.root / file).read_bytes() for key, (file, _) in PROFILE_FILES.items()},
            (cls.root / 'adapters/arclight_runtime/source_manifest.json').read_bytes())
        cls.fixtures = {}
        for procedure in PROCEDURES:
            run = subprocess.run([PYTHON, '-X', 'utf8', '-m', 'tests.test_capture_diagnostics', '--fixture', str(cls.root), procedure],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, encoding='utf8', check=True, timeout=15)
            cls.fixtures[procedure] = (json.loads(run.stdout), (cls.root / 'synthetic-fixture.npz').read_bytes())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def fixture(self, procedure='sq_echo', independent=False, null_captures=()):
        self.db_path = self.root / 'fixture.sqlite'
        db = sqlite3.connect(self.db_path)
        db.executescript('DROP TABLE IF EXISTS platform_workflows; DROP TABLE IF EXISTS platform_attempts; DROP TABLE IF EXISTS platform_effects; DROP TABLE IF EXISTS platform_artifacts;'
            'CREATE TABLE platform_workflows(id TEXT, payload TEXT); CREATE TABLE platform_attempts(id TEXT,workflow_id TEXT,node_id TEXT,payload TEXT);'
            'CREATE TABLE platform_effects(operation_id TEXT,workflow_id TEXT,attempt_id TEXT,state TEXT,request TEXT,result TEXT);'
            'CREATE TABLE platform_artifacts(id TEXT PRIMARY KEY,path TEXT,sha256 TEXT,payload TEXT);')
        binding = self.bindings['arclight-private-f8-q11-v2']
        numeric, raw_bytes = deepcopy(self.fixtures[procedure])
        for index in null_captures:
            numeric['analysis']['groups'][0]['repeats'][index].update(fit=None,
                diagnostics={'rejection_reasons': ['SYNTHETIC native fit rejected: wide interval']})
        if null_captures:
            valid = [value for index, value in enumerate(numeric['expected']) if index not in null_captures]
            numeric['metrics'] = {'r_squared': min(v['rSquaredFromUnchangedFit'] for v in valid)} if valid else {}
        parameters = {k: v for k, v in binding['profile']['resources']['Q11'].items() if k not in ('q', 'calibrationStatus')}
        versions = {k: 0 for k in parameters}
        parameters.update(numeric['parameters'])
        node = {'id': 'Q11:offline:' + procedure, 'procedureId': procedure, 'resourceId': 'Q11', 'latestAttemptId': 'synthetic-attempt'}
        workflow = {'id': 'synthetic-workflow', 'revision': 17, 'mode': 'real', 'adapterId': 'arclight-private-f8-q11-v2',
            'adapterProfileHash': binding['fingerprint'], 'nodes': [node]}
        attempt = {'id': 'synthetic-attempt', 'nodeId': node['id'], 'procedureId': procedure, 'state': 'attention',
            'mode': 'real', 'endedAt': 'SYNTHETIC TEST ONLY', 'parameterVersions': versions,
            'operationId': 'synthetic-attempt/measure'}
        if independent:
            attempt['verificationOperationId'] = 'synthetic-attempt/verify'
        results = []
        for role in ['measure', 'verify'] if independent else ['measure']:
            operation = attempt['id'] + '/' + role
            op_root = self.root / 'data/runs/arclight_operations' / hashlib.sha256(operation.encode()).hexdigest()
            folder = op_root / 'results' / 'offline-fixture'
            folder.mkdir(parents=True, exist_ok=True)
            # Different raw archive bytes without changing numeric values permits
            # the receipt distinction test; never claims statistical independence.
            raw = raw_bytes if role == 'measure' else raw_bytes + b'OFFLINE ZIP COMMENT'
            raw_sha = hashlib.sha256(raw).hexdigest()
            request = {'runId': workflow['id'], 'nodeId': node['id'], 'attemptId': attempt['id'], 'operationId': operation,
                'resourceId': 'Q11', 'procedureId': procedure, 'parameterVersions': versions,
                'adapterId': workflow['adapterId'], 'adapterProfileHash': workflow['adapterProfileHash']}
            job = {'name': 'SYNTHETIC_OFFLINE', 'kind': PROCEDURES[procedure], 'parameters': parameters,
                'captures': parameters['capture_repeats'], 'acquisitions_per_point': parameters['acquisitions_per_point'], 'spec': numeric['spec']}
            if procedure == 'sq_cpmg':
                job['total_delay_multiplier'] = numeric['multiplier']
            count = len(numeric['records'])
            plan = {'operationId': operation, 'procedureId': procedure, 'resourceId': 'Q11', 'parameters': parameters,
                'requestIdentity': request, 'jobs': [job], 'expectedJobRows': [count], 'totalRows': count}
            native = deepcopy(numeric['analysis'])
            native.update(raw_sha256=raw_sha, source_sha256=binding['manifest']['platform_analysis.py'])
            analysis = {'analysis': native, 'metrics': numeric['metrics'], 'series': numeric['series'], 'candidateParameters': {}}
            docs = {'axes.json': numeric['records'], 'job.json': job,
                'status.json': {'state': 'acquired', 'mode': 'real_hardware', 'job': job['name'], 'points_completed': count, 'points_expected': count},
                'platform_analysis.json': analysis,
                'source_hashes.json': {k: v for k, v in binding['manifest'].items() if k.startswith('work/server/core/') and k.endswith('.py')},
                'entry_source_hashes.json': {k: binding['manifest'][k] for k in ENTRY_FILES}}
            inventory = []
            for name, value in {'raw_scan.npz': raw, 'plan.json': plan, **docs}.items():
                path = (op_root if name == 'plan.json' else folder) / name
                data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf8')
                path.write_bytes(data)
                sha = hashlib.sha256(data).hexdigest()
                entry = {'id': 'real-' + sha[:24], 'name': name, 'sha256': sha,
                    'kind': 'raw' if name == 'raw_scan.npz' else 'json', 'sourcePath': str(path)}
                db.execute('INSERT OR REPLACE INTO platform_artifacts VALUES(?,?,?,?)', (entry['id'], str(path), sha, json.dumps(entry)))
                inventory.append(entry)
            result = {**analysis, 'analysisQuality': native, 'parameters': parameters, 'artifacts': inventory,
                'dataKind': 'real', 'acquiredAt': 'SYNTHETIC TEST ONLY', 'sourceRunId': operation,
                'quality': {'status': 'failed', 'reasons': ['SYNTHETIC TEST ONLY']},
                'provenance': {'freshAcquisition': True, 'shutdownCommandsCompleted': True, 'operationId': operation,
                    'runnerSha256': binding['profile']['runnerSha256'], 'profileHash': binding['executionProfileHashes'][0],
                    'sourceManifest': binding['profile']['sourceManifest'], 'parameterVersions': versions}}
            del result['analysis']
            db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)', (operation, workflow['id'], attempt['id'], 'completed', json.dumps(request), json.dumps(result)))
            results.append(result)
        result = deepcopy(results[-1])
        if independent:
            result['selectionResult'] = results[0]
        attempt['result'] = node['result'] = result
        db.execute('INSERT INTO platform_workflows VALUES(?,?)', (workflow['id'], json.dumps(workflow)))
        db.execute('INSERT INTO platform_attempts VALUES(?,?,?,?)', (attempt['id'], workflow['id'], node['id'], json.dumps(attempt)))
        db.commit(); db.close()
        self.workflow, self.node, self.attempt, self.expected = workflow, node, attempt, numeric['expected']
        return workflow, node

    def resolve(self):
        return resolve_sources(self.root, self.db_path, self.workflow, self.node, implementation_id=IMPLEMENTATION)

    def test_all_four_audited_methods_preserve_raw_order_and_saved_fit(self):
        for procedure in PROCEDURES:
            with self.subTest(procedure=procedure):
                self.fixture(procedure)
                before = deepcopy(self.workflow)
                sources = self.resolve()
                self.assertEqual(sources['status'], 'resolved', sources)
                output = calculate_diagnostics(sources)
                self.assertEqual(output['status'], 'available', output)
                operation = output['operations'][0]
                self.assertTrue(operation['allRecordedPointsCoveredExactlyOnce'])
                self.assertFalse(operation['wallClockTimeAvailable'])
                self.assertFalse(operation['nativeFitsRefitted'])
                self.assertFalse(operation['unregisteredTimestampFilesRead'])
                for actual, expected in zip(operation['captures'], self.expected):
                    for key, value in expected.items():
                        self.assertAlmostEqual(actual[key], value, places=10)
                    self.assertGreater(actual['residualAcquisitionOrdinalPearsonR'], .99)
                    self.assertLess(abs(actual['scanCoordinateAcquisitionOrdinalPearsonR']), .7)
                self.assertEqual(before, self.workflow)

    def test_selection_and_independent_verification_are_separate_operations(self):
        self.fixture(independent=True)
        output = calculate_diagnostics(self.resolve())
        self.assertEqual(output['status'], 'available', output)
        self.assertEqual([x['resultRole'] for x in output['operations']], ['selection', 'independent_verification'])
        self.assertNotEqual(*[x['registeredSourceSha256']['raw_scan.npz'] for x in output['operations']])

    def test_partial_or_cancelled_latest_attempt_never_falls_back(self):
        for change in ({'state': 'cancelled'}, {'error': 'worker failed'}, {'endedAt': None}, {'state': 'running'}):
            self.fixture(); self.attempt.update(change)
            with closing(sqlite3.connect(self.db_path)) as db, db:
                db.execute('UPDATE platform_attempts SET payload=?', (json.dumps(self.attempt),))
            self.assertEqual(self.resolve()['status'], 'unavailable')
        self.fixture(); self.attempt['result']['partial'] = True
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute('UPDATE platform_attempts SET payload=?', (json.dumps(self.attempt),))
            db.execute('UPDATE platform_workflows SET payload=?', (json.dumps(self.workflow),))
        self.assertEqual(self.resolve()['reasonCode'], 'partial_or_stale_result')

    def test_revision_profile_identity_and_effect_state_fail_closed(self):
        self.fixture()
        self.workflow['revision'] += 1
        self.assertEqual(self.resolve()['reasonCode'], 'stale_workflow_snapshot')
        self.fixture()
        self.assertEqual(resolve_sources(self.root, self.db_path, self.workflow, self.node,
            implementation_id='spoof.ArcLightAdapter')['reasonCode'], 'unaudited_binding')
        self.workflow['adapterProfileHash'] = 'tampered'
        self.assertEqual(self.resolve()['reasonCode'], 'unaudited_binding')
        for state in ('pending', 'unknown', 'cancelled'):
            self.fixture()
            with closing(sqlite3.connect(self.db_path)) as db, db:
                db.execute('UPDATE platform_effects SET state=?', (state,))
            self.assertEqual(self.resolve()['reasonCode'], 'incomplete_effect')

    def test_real_controller_get_timing_projection_resolves_without_mocking(self):
        from backend.controller import Controller
        from backend.store import Store
        self.fixture()
        self.workflow.update(status='attention', firstAcquisitionAt='2026-11-18T18:00:00+00:00',
                             finishedAt=None, elapsed=1)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute('UPDATE platform_workflows SET payload=?', (json.dumps(self.workflow),))
        # Real Store + real Controller.get/_with_timing; __init__ is intentionally
        # not run because this test never starts workers or instantiates adapters.
        controller = Controller.__new__(Controller)
        controller.store = Store(self.db_path)
        presented = controller.get(self.workflow['id'])
        self.assertNotEqual(presented['elapsed'], self.workflow['elapsed'])
        self.assertEqual(presented['revision'], self.workflow['revision'])
        self.assertEqual(presented['nodes'], self.workflow['nodes'])
        resolve = lambda wf, node=None: resolve_sources(self.root, self.db_path, wf,
            node or wf['nodes'][0], implementation_id=IMPLEMENTATION)
        before = deepcopy(presented)
        sources = resolve(presented)
        self.assertEqual(sources['status'], 'resolved', sources)
        diagnostic = calculate_diagnostics(sources)
        self.assertEqual(diagnostic['status'], 'available', diagnostic)
        self.assertEqual(diagnostic['binding']['revision'], presented['revision'])
        self.assertEqual(presented, before)
        # Only the explicit display projection is exempt. Scientific/dispatch
        # state cannot change under the same presentation snapshot.
        for field in ('revision', 'status', 'gates', 'adapterProfileHash', 'nodes', 'nodes_elapsed'):
            changed = deepcopy(presented)
            if field == 'revision': changed[field] += 1
            elif field == 'nodes': changed['nodes'][0]['result']['metrics']['r_squared'] = .99
            elif field == 'nodes_elapsed': changed['nodes'][0]['elapsed'] = 999
            elif field == 'gates': changed[field] = {'sq_echo': {'r_squared': {'min': .1}}}
            else: changed[field] = 'modified-offline-test'
            with self.subTest(field=field):
                self.assertEqual(resolve(changed)['status'], 'unavailable')
        bad_node = deepcopy(presented['nodes'][0]); bad_node['latestAttemptId'] = 'another-attempt'
        self.assertEqual(resolve(presented, bad_node)['reasonCode'], 'current_node_mismatch')
        # An actual DB revision advance after get must still fail, even though
        # the caller has a completely valid display-enriched object.
        advanced = deepcopy(self.workflow); advanced['revision'] += 1
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute('UPDATE platform_workflows SET payload=?', (json.dumps(advanced),))
        self.assertEqual(resolve(presented)['reasonCode'], 'stale_workflow_snapshot')

    def test_unregistered_tampered_and_outside_operation_paths_unavailable(self):
        for mode in ('unregister', 'tamper', 'path'):
            self.fixture()
            artifact = next(a for a in self.node['result']['artifacts'] if a['name'] == 'axes.json')
            if mode == 'unregister':
                with closing(sqlite3.connect(self.db_path)) as db, db:
                    db.execute('DELETE FROM platform_artifacts WHERE id=?', (artifact['id'],))
            elif mode == 'tamper':
                Path(artifact['sourcePath']).write_text('[]', encoding='utf8')
            else:
                artifact['sourcePath'] = str(self.root / 'outside-operation.json')
                with closing(sqlite3.connect(self.db_path)) as db, db:
                    db.execute('UPDATE platform_workflows SET payload=?', (json.dumps(self.workflow),))
                    db.execute('UPDATE platform_attempts SET payload=?', (json.dumps(self.attempt),))
                    effect = json.loads(db.execute('SELECT result FROM platform_effects').fetchone()[0])
                    effect['artifacts'] = self.node['result']['artifacts']
                    db.execute('UPDATE platform_effects SET result=?', (json.dumps(effect),))
            self.assertEqual(self.resolve()['status'], 'unavailable', mode)

    def test_worker_rechecks_files_after_resolver_and_never_refits_missing_fit(self):
        for change, reason in [('axis', 'axis_mapping_mismatch'), ('fit', 'native_fit_null_without_failure_reasons'),
                               ('sem', 'stored_sem_mismatch'), ('metric', 'stored_metric_mismatch')]:
            self.fixture(); sources = self.resolve()
            name = 'axes.json' if change == 'axis' else 'platform_analysis.json'
            artifact = next(f for f in sources['operations'][0]['files'] if f['name'] == name)
            path = Path(artifact['path']); data = json.loads(path.read_text(encoding='utf8'))
            if change == 'axis': data[0]['base_index'] = data[1]['base_index']
            elif change == 'fit': data['analysis']['groups'][0]['repeats'][0]['fit'] = None
            elif change == 'sem': data['analysis']['point_statistics']['effective_sem'][0] *= 2
            else: data['metrics']['r_squared'] = .1
            path.write_text(json.dumps(data), encoding='utf8')
            self.assertEqual(calculate_diagnostics(sources)['reasonCode'], 'artifact_changed_after_snapshot')
            # Direct worker schema boundary, with a deliberately updated test
            # manifest only; production resolver never permits this change.
            artifact['sha256'] = file_hash(path)
            self.assertEqual(calculate_diagnostics(sources)['reasonCode'], reason)

    def test_explicit_rejected_fit_keeps_raw_sem_and_other_capture_fit(self):
        self.fixture(null_captures=(0,))
        before = deepcopy(self.workflow)
        sources = self.resolve()
        self.assertEqual(sources['status'], 'resolved', sources)
        output = calculate_diagnostics(sources)
        self.assertEqual(output['status'], 'partial_fit_diagnostics', output)
        operation = output['operations'][0]
        self.assertFalse(operation['nativeAcceptedUnchanged'])
        self.assertTrue(operation['allRecordedPointsCoveredExactlyOnce'])
        self.assertEqual(operation['fitDiagnosticCoverage']['captureCount'], 2)
        self.assertEqual(operation['fitDiagnosticCoverage']['validFitCaptureCount'], 1)
        missing, valid = operation['captures']
        self.assertEqual(missing['fitDiagnostics']['reasonCode'], 'native_fit_rejected')
        self.assertEqual(missing['fitDiagnostics']['nativeRejectionReasons'], ['SYNTHETIC native fit rejected: wide interval'])
        for field in missing['fitDiagnostics']['unavailableFields']:
            self.assertIsNone(missing[field])
        self.assertGreater(missing['medianEffectiveSem'], 0)
        self.assertEqual(missing['pointCount'], 13)
        self.assertIsInstance(missing['rawProjectedMeanAcquisitionOrdinalPearsonR'], float)
        self.assertEqual(valid['fitDiagnostics']['status'], 'available')
        self.assertAlmostEqual(valid['rSquaredFromUnchangedFit'], self.expected[1]['rSquaredFromUnchangedFit'])
        self.assertEqual(valid['nativeDecayTimeSeconds'], 9e-6)
        self.assertEqual(self.workflow, before)
        payload = {'runId': output['binding']['runId'], 'nodeId': output['binding']['nodeId'], 'revision': output['binding']['revision'],
                   'allowedEvidenceIds': ['original'], 'observations': {'quality': {'status': 'failed'}}}
        self.assertEqual(attach_diagnostics(payload, output)['captureDiagnostics']['status'], 'partial_fit_diagnostics')
        payload['revision'] += 1
        self.assertEqual(attach_diagnostics(payload, output)['captureDiagnostics']['reasonCode'], 'stale_diagnostic_binding')

    def test_all_native_fits_rejected_does_not_invent_metrics(self):
        self.fixture(null_captures=(0, 1))
        output = calculate_diagnostics(self.resolve())
        self.assertEqual(output['status'], 'partial_fit_diagnostics', output)
        self.assertEqual(output['operations'][0]['fitDiagnosticCoverage']['validFitCaptureCount'], 0)
        self.assertTrue(all(c['rSquaredFromUnchangedFit'] is None for c in output['operations'][0]['captures']))

    def test_missing_fit_key_unknown_model_and_unexplained_null_stay_unavailable(self):
        for mode in ('absent', 'unknown', 'malformed', 'empty_reason', 'wrong_reason_type', 'decay_mismatch'):
            self.fixture(null_captures=(0,)); sources = self.resolve()
            item = next(f for f in sources['operations'][0]['files'] if f['name'] == 'platform_analysis.json')
            path = Path(item['path']); data = json.loads(path.read_text(encoding='utf8'))
            repeat = data['analysis']['groups'][0]['repeats'][0]
            if mode == 'absent': del repeat['fit']
            elif mode == 'unknown': repeat['fit'] = {'model': 'unknown'}
            elif mode == 'malformed': repeat['fit'] = []
            elif mode == 'empty_reason': repeat['diagnostics']['rejection_reasons'] = []
            elif mode == 'decay_mismatch':
                repeat['fit'] = deepcopy(self.fixtures['sq_echo'][0]['analysis']['groups'][0]['repeats'][0]['fit'])
                repeat['fit']['decay_time'] *= 2
            else: repeat['diagnostics']['rejection_reasons'] = [None]
            path.write_text(json.dumps(data), encoding='utf8'); item['sha256'] = file_hash(path)
            with self.subTest(mode=mode):
                self.assertEqual(calculate_diagnostics(sources)['status'], 'unavailable')

    def test_optional_context_never_modifies_evidence_or_exceeds_input_cap(self):
        payload = build_decision_context({'runId': 'run', 'nodeId': 'node', 'revision': 1,
            'observations': {'result': {'artifacts': [{'id': 'real-current'}], 'quality': {'status': 'failed'}}}})
        before = deepcopy(payload)
        diagnostic = {'schema': VERSION, 'status': 'available', 'binding': {'runId': 'run', 'nodeId': 'node', 'revision': 1},
            'operations': [{'sourceOperation': 'same-attempt/measure'}]}
        output = attach_diagnostics(payload, diagnostic)
        self.assertEqual(output['observations'], before['observations'])
        self.assertEqual(output['allowedEvidenceIds'], before['allowedEvidenceIds'])
        self.assertEqual(payload, before)
        diagnostic['binding']['revision'] = 0
        self.assertEqual(attach_diagnostics(payload, diagnostic)['captureDiagnostics']['reasonCode'], 'stale_diagnostic_binding')
        payload['extra'] = 'x' * (MAX_INPUT_UNITS - javascript_json_units_upper_bound(payload) - 2)
        output = attach_diagnostics(payload, unavailable('synthetic'))
        self.assertEqual(output, payload)  # Optional context alone is dropped.

    def test_worker_timeout_is_optional_and_unsupported_method_reads_nothing(self):
        self.fixture(); sources = self.resolve()
        with patch('backend.capture_diagnostics.subprocess.run', side_effect=subprocess.TimeoutExpired('offline-reader', 45)):
            self.assertEqual(calculate_diagnostics(sources)['reasonCode'], 'diagnostic_worker_unavailable')
        with patch('backend.capture_diagnostics.read_chat_snapshot', side_effect=AssertionError('no read')):
            self.assertEqual(resolve_sources(self.root, self.db_path, {}, {'procedureId': 'sq_rb'},
                implementation_id=IMPLEMENTATION)['reasonCode'], 'unsupported_procedure')

    def test_frozen_analysis_tamper_is_rejected_before_import(self):
        self.fixture()
        path = self.root / 'adapters/arclight_runtime/analyze_single_qubit.py'
        original = path.read_bytes()
        try:
            path.write_bytes(original + b'\n# OFFLINE TAMPER TEST\n')
            self.assertEqual(self.resolve()['reasonCode'], 'analysis_source_changed')
        finally:
            path.write_bytes(original)

    def test_axes_cannot_be_relocated_into_another_job_of_the_same_operation(self):
        self.fixture(); sources = self.resolve()
        artifact = next(a for a in self.node['result']['artifacts'] if a['name'] == 'axes.json')
        path = Path(artifact['sourcePath']); other = path.parent.parent / 'another-job' / 'axes.json'
        other.parent.mkdir(exist_ok=True); other.write_bytes(path.read_bytes())
        artifact['sourcePath'] = str(other)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute('UPDATE platform_workflows SET payload=?', (json.dumps(self.workflow),))
            db.execute('UPDATE platform_attempts SET payload=?', (json.dumps(self.attempt),))
            result = json.loads(db.execute('SELECT result FROM platform_effects').fetchone()[0])
            result['artifacts'] = self.node['result']['artifacts']
            db.execute('UPDATE platform_effects SET result=?', (json.dumps(result),))
            db.execute('UPDATE platform_artifacts SET path=? WHERE id=?', (str(other), artifact['id']))
        self.assertEqual(self.resolve()['reasonCode'], 'axes_raw_job_folder_mismatch')
        entry = next(f for f in sources['operations'][0]['files'] if f['name'] == 'axes.json')
        entry['path'] = str(other)
        self.assertEqual(calculate_diagnostics(sources)['reasonCode'], 'axes_raw_job_folder_mismatch')

    def test_forged_npy_header_cannot_trigger_unbounded_allocation(self):
        self.fixture(); sources = self.resolve()
        file = next(f for f in sources['operations'][0]['files'] if f['name'] == 'raw_scan.npz')
        path = Path(file['path'])
        with zipfile.ZipFile(path) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        content = members['i.npy']
        # NPY1 headers carry a Python-literal shape, independent of payload size.
        length = int.from_bytes(content[8:10], 'little')
        header = content[10:10+length].decode('latin1')
        header = header.replace('(26, 1, 32)', '(999999999, 1, 32)')
        replacement = header.encode('latin1')
        members['i.npy'] = content[:8] + len(replacement).to_bytes(2, 'little') + replacement + content[10+length:]
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in members.items():
                archive.writestr(name, content)
        file['sha256'] = file_hash(path)  # Worker boundary only, not a valid registry change.
        self.assertEqual(calculate_diagnostics(sources)['reasonCode'], 'raw_header_shape_mismatch')

    def test_model_hook_preserves_quality_evidence_and_never_executes(self):
        from tests.test_controller import make_fixture
        from tests.test_prior_failure_context import node_json_check
        with tempfile.TemporaryDirectory(prefix='capture-diagnostic-controller-') as folder:
            controller, adapter = make_fixture(Path(folder))
            try:
                wf = controller.create({'name': 'OFFLINE diagnostics hook', 'resourceIds': ['Q11'], 'mode': 'real', 'policy': 'autopilot'})
                with controller.store.transaction() as rt:
                    wf = controller.get(wf['id'], rt)
                    node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_echo')
                    node.update(attempt=1, status='attention', engineState='quality_attention', result={
                        'metrics': {'r_squared': .78}, 'quality': {'status': 'failed', 'reasons': ['OFFLINE']},
                        'artifacts': [{'id': 'real-current-offline', 'name': 'OFFLINE fixture', 'sha256': 'offline-only'}]})
                    original_result = deepcopy(node['result'])
                    wf.update(currentNodeId=node['id'], status='attention', engineState='quality_attention')
                    controller.store.save(rt, wf, 'offline_fixture')
                calls = []
                controller._brain = lambda path, body: calls.append(deepcopy(body)) or {
                    'status': 'ok', 'decision': {'action': 'wait', 'reason': 'OFFLINE wait', 'evidenceIds': []}}
                def resolved(root, db, workflow, current, **kwargs):
                    return {'schema': VERSION, 'status': 'available', 'binding': {
                        'runId': workflow['id'], 'nodeId': current['id'], 'revision': workflow['revision']},
                        'notice': 'SYNTHETIC DIAGNOSTIC FOR HOOK TEST ONLY', 'operations': [{'sourceOperation': 'current/measure'}]}
                with patch('backend.controller.resolve_sources', side_effect=resolved), patch('backend.controller.calculate_diagnostics', side_effect=lambda x: x):
                    self.assertFalse(controller._decide(wf['id'], node['id']))
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]['observations']['result']['quality'], original_result['quality'])
                self.assertEqual(calls[0]['allowedEvidenceIds'], ['real-current-offline'])
                checked = node_json_check(calls, decisions=True)[0]
                self.assertEqual(checked['status'], 'passed')
                self.assertEqual(checked['evidence'], ['real-current-offline'])
                self.assertEqual(adapter.calls, [])
                current = controller.get(wf['id'])
                self.assertEqual(next(n for n in current['nodes'] if n['id'] == node['id'])['result'], original_result)
                self.assertNotIn('captureDiagnostics', current)
            finally:
                controller.close()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--fixture':
        print(json.dumps(numeric_fixture(Path(sys.argv[2]), sys.argv[3])))
    else:
        unittest.main()
