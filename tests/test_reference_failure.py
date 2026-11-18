"""Offline receipt-contract fixtures; numerical arrays are NOT measured IQ.

The pure classifier checks evidence structure; it does not perform new fitting.
No controller, service, equipment, credentials, or original workspace is used.
"""
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path, PureWindowsPath
import statistics
import struct
import unittest
import zipfile

from backend.reference_failure import classify_reference_failure, AUDITED_SOURCES, IDENTITY


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


def npy(values, shape, dtype='<f8'):
    header = repr({'descr': dtype, 'fortran_order': False, 'shape': shape})
    header += ' ' * ((64 - (10 + len(header) + 1) % 64) % 64) + '\n'
    fmt = 'd' if dtype == '<f8' else 'h'
    return b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header.encode() + struct.pack('<' + fmt * len(values), *values)


def iq_npz(i, q, shape, shots=2000, block=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('i.npy', npy(i, shape))
        archive.writestr('q.npy', npy(q, shape))
        blocks = [0] * shots + [1] * shots if block is None else [block] * shots
        archive.writestr('acquisition_block_index.npy', npy(blocks, (len(blocks),), '<i2'))
    return out.getvalue()


def fixture(procedure='sq_allxy'):
    operation = 'OFFLINE_REFERENCE_FAILURE_FIXTURE/measure'
    root = PureWindowsPath('D:/offline-contract-fixture/data/runs/arclight_operations') / hashlib.sha256(operation.encode()).hexdigest()
    name = 'op_fixture_reference' if procedure == 'sq_allxy' else 'op_fixture_pre'
    folder = root / 'results' / ('20261118_000000_000000_live_' + name)
    request = {'runId': 'OFFLINE-RUN', 'nodeId': 'OFFLINE-NODE', 'attemptId': 'OFFLINE-ATTEMPT',
               'operationId': operation, 'resourceId': 'Q11', 'procedureId': procedure,
               'parameters': {}, 'parameterVersions': {'drive_amp': 1}}
    request_hash = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    job = {'name': name, 'kind': 'readout', 'target': 'Q11', 'captures': 6, 'acquisitions_per_point': 2,
           'rb_role': 'independent_reference' if procedure == 'sq_allxy' else 'pre',
           'spec': {'meta': {'shots': 2000}}, 'testFixtureOnly': True}
    plan = {'adapter': 'arclight', 'operationId': operation, 'resourceId': 'Q11', 'procedureId': procedure,
            'requestIdentity': {k: v for k, v in request.items() if k != 'parameters'},
            'jobs': [job] + [{'testFixtureOnly': True}] * (1 if procedure == 'sq_allxy' else 3),
            'expectedJobRows': [12] + ([42] if procedure == 'sq_allxy' else [160, 160, 12])}
    context = {'implementationId': IDENTITY[0], 'profileId': IDENTITY[1], 'operationId': operation,
               'requestHash': request_hash, 'operationDirectory': str(root), 'runtimeSourceHashes': deepcopy(AUDITED_SOURCES)}
    for k in ('adapterBindingVerified', 'receiptProvenanceVerified', 'expectedPlanVerified',
              'completeResultInventoryVerified', 'artifactPathsVerified', 'noPendingOrUnknownEffects', 'cleanupAndOwnershipVerified'):
        context[k] = True
    status = {'mode': 'real_hardware', 'state': 'acquired', 'job': name, 'target': 'Q11', 'folder': str(folder),
              'hardware_connected': False, 'shutdown_ok': True, 'points_completed': 12, 'points_expected': 12,
              'shots_per_acquisition': 2000, 'acquisitions_per_point': 2, 'effective_shots_per_point': 4000}
    axes = [{'capture': c, 'base_index': b, 'axes': {'exp.cir.default': label}}
            for c in range(6) for b, label in enumerate(('ground', 'excited'))]
    raw_i = [index * .001 + sample * .00000001 for index in range(12) for sample in range(4000)]
    raw_q = [-v for v in raw_i]
    content = {'job.json': encoded(job), 'status.json': encoded(status), 'axes.json': encoded(axes),
               'raw_scan.npz': iq_npz(raw_i, raw_q, (12, 1, 4000)),
               'entry_source_hashes.json': encoded({k: AUDITED_SOURCES[k] for k in ('run_single_qubit.py', 'repeated_iq_acquisition.py')})}
    for i in range(12):
        content[f'point_{i:04d}.npz'] = iq_npz(raw_i[i * 4000:(i + 1) * 4000], raw_q[i * 4000:(i + 1) * 4000], (1, 4000))
        block = {'schema': 'arclight_actual_repeated_iq_v1', 'point_index': i, 'state': 'complete',
                 'acquisitions_per_point': 2, 'shots_per_acquisition': 2000, 'blocks_completed': 2,
                 'effective_shots_completed': 4000, 'exact_duplicate_blocks_detected': False, 'blocks': []}
        for b in range(2):
            n = f'point_{i:04d}_block_{b:03d}.npz'
            start, stop = i * 4000 + b * 2000, i * 4000 + (b + 1) * 2000
            content[n] = iq_npz(raw_i[start:stop], raw_q[start:stop], (1, 2000), block=b)
            block['blocks'].append({'point_index': i, 'block_index': b, 'block_file': n,
                                    'sha256': hashlib.sha256(content[n]).hexdigest(),
                                    'exact_duplicate_of_blocks': [], 'shots_in_block': 2000})
        content[f'point_{i:04d}_acquisition_blocks.json'] = encoded(block)
    ds = [.001, .0012, .0009, -.0001, .0002, .0001]
    mean, sem = statistics.mean(ds[3:]), statistics.stdev(ds[3:]) / math.sqrt(3)
    report = {'target': 'Q11', 'projection': {'independent_reference': True, 'reference_contrast_repeated': False,
        'source': str(folder / 'independent_projection.json'), 'reference_raw': str(folder / 'raw_scan.npz'),
        'reference_raw_sha256': hashlib.sha256(content['raw_scan.npz']).hexdigest(),
        'training_captures': [0, 1, 2], 'validation_captures': [3, 4, 5]},
        'held_out_contrast_positive_and_repeat_consistent': False,
        'held_out_capture_mean_contrast': mean, 'held_out_capture_contrast_sem': sem,
        'held_out_capture_contrast_ci95_t': [mean - 4.302652729911275 * sem, mean + 4.302652729911275 * sem],
        'capture_results': [{'capture': c, 'role': 'training' if c < 3 else 'held_out_capture',
                            'ground_mean': 0, 'excited_mean': d, 'contrast': d} for c, d in enumerate(ds)],
        'sources': [{'path': str(folder / n), 'sha256': hashlib.sha256(content[n]).hexdigest()}
                    for n in ('job.json', 'axes.json', 'raw_scan.npz', 'status.json')]}
    content['independent_projection.json'] = encoded(report)
    receipt = {'operationId': operation, 'requestHash': request_hash, 'status': 'failed_after_shutdown',
               'shutdownConfirmed': True, 'pointsCompleted': 12, 'completionPath': str(root / 'completion.json'),
               'reason': 'ValueError: Fresh held-out reference contrast failed; no downstream pulse sequence executed',
               'partialArtifacts': []}
    args = {'context': context, 'request': request, 'expected_plan': plan, 'receipt': receipt,
            'registered_artifacts': {}, 'artifact_bytes': {}}
    for name, data in content.items():
        add_artifact(args, folder / name, data)
    return args


def add_artifact(args, path, content):
    path = PureWindowsPath(path)
    sha = hashlib.sha256(content).hexdigest()
    aid = 'real-' + sha[:24]
    args['receipt']['partialArtifacts'].append({'id': aid, 'name': path.name, 'sourcePath': str(path),
                                               'sha256': sha, 'kind': 'raw' if path.suffix == '.npz' else 'json'})
    args['registered_artifacts'][aid] = {'path': str(path), 'sha256': sha}
    args['artifact_bytes'][aid] = content


def change_json(args, name, change):
    a = next(a for a in args['receipt']['partialArtifacts'] if a['name'] == name)
    value = json.loads(args['artifact_bytes'][a['id']])
    change(value)
    args['receipt']['partialArtifacts'].remove(a)
    add_artifact(args, a['sourcePath'], encoded(value))


class ReferenceFailureTests(unittest.TestCase):
    def test_recognizes_only_audited_reference_stage_for_both_procedures(self):
        for procedure in ('sq_allxy', 'sq_rb'):
            with self.subTest(procedure=procedure):
                args = fixture(procedure)
                before = deepcopy(args)
                r = classify_reference_failure(**args)
                self.assertTrue(r['recognized'], r)
                self.assertTrue(r['eligibleForModelDecision'])
                self.assertFalse(r['isExecutionApproval'])
                self.assertFalse(r['mainAcquisitionStarted'])
                self.assertEqual(r['quality']['status'], 'failed')
                self.assertEqual(r['samplingSemantics']['referenceAcquisitionsPerPoint'], 2)
                self.assertFalse(r['samplingSemantics']['ordinaryCaptureAndAcquisitionOverridesAffectReference'])
                self.assertEqual(args, before)

    def test_every_caller_attestation_is_required(self):
        for field in [k for k, v in fixture()['context'].items() if v is True]:
            with self.subTest(field=field):
                args = fixture(); args['context'][field] = False
                self.assertFalse(classify_reference_failure(**args)['recognized'])

    def test_unaudited_identity_and_source_fail_closed(self):
        for key, value in [('implementationId', 'adapters.f8.F8Adapter'), ('profileId', 'foreign'),
                           ('operationId', 'wrong'), ('requestHash', '0' * 64),
                           ('runtimeSourceHashes', {**AUDITED_SOURCES, 'platform_worker.py': '0' * 64})]:
            with self.subTest(key=key):
                args = fixture(); args['context'][key] = value
                self.assertFalse(classify_reference_failure(**args)['recognized'])

    def test_unknown_error_cleanup_and_receipt_fail_closed(self):
        for key, value in [('status', 'unknown'), ('shutdownConfirmed', False), ('pointsCompleted', 13),
                           ('reason', 'ValueError: unrelated error'), ('operationId', 'foreign'),
                           ('requestHash', '0' * 64), ('completionPath', 'D:/elsewhere/completion.json')]:
            with self.subTest(key=key):
                args = fixture(); args['receipt'][key] = value
                self.assertFalse(classify_reference_failure(**args)['recognized'])

    def test_missing_and_changed_registered_bytes_fail_closed(self):
        for mode in ('registry', 'content', 'missing', 'path', 'hash'):
            with self.subTest(mode=mode):
                args = fixture(); a = args['receipt']['partialArtifacts'][0]
                if mode == 'registry': args['registered_artifacts'].pop(a['id'])
                if mode == 'content': args['artifact_bytes'][a['id']] += b'changed'
                if mode == 'missing': args['artifact_bytes'].pop(a['id'])
                if mode == 'path': args['registered_artifacts'][a['id']]['path'] = 'D:/elsewhere/job.json'
                if mode == 'hash': args['registered_artifacts'][a['id']]['sha256'] = '0' * 64
                self.assertFalse(classify_reference_failure(**args)['recognized'])

    def test_missing_required_reference_artifacts_fail_closed(self):
        for name in ('raw_scan.npz', 'axes.json', 'status.json', 'job.json', 'independent_projection.json',
                     'entry_source_hashes.json', 'point_0003.npz', 'point_0003_acquisition_blocks.json', 'point_0003_block_001.npz'):
            with self.subTest(name=name):
                args = fixture(); args['receipt']['partialArtifacts'] = [a for a in args['receipt']['partialArtifacts'] if a['name'] != name]
                self.assertFalse(classify_reference_failure(**args)['recognized'])

    def test_any_later_job_even_zero_points_rejects(self):
        for name in ('job.json', 'status.json', 'config.json'):
            args = fixture()
            root = PureWindowsPath(args['context']['operationDirectory'])
            add_artifact(args, root / 'results/20261118_live_main_started' / name, encoded({'state': 'preflight', 'points_completed': 0}))
            self.assertEqual(classify_reference_failure(**args)['rejection'], 'LATER_JOB_OR_EXTRA_FOLDER_PRESENT')

    def test_partial_and_downstream_artifacts_reject(self):
        for name in ('partial_raw_scan.npz', 'platform_measurement.json', 'paired_rb_projection_export.json'):
            args = fixture(); folder = PureWindowsPath(args['receipt']['partialArtifacts'][0]['sourcePath']).parent
            add_artifact(args, folder / name, b'OFFLINE EXTRA STAGE FIXTURE')
            self.assertFalse(classify_reference_failure(**args)['recognized'])

    def test_resigned_reference_report_cannot_claim_success_or_wrong_source(self):
        edits = [lambda r: r['projection'].update(reference_contrast_repeated=True),
                 lambda r: r.update(held_out_contrast_positive_and_repeat_consistent=True),
                 lambda r: r['projection'].update(validation_captures=[0, 1, 2]),
                 lambda r: r['projection'].update(reference_raw_sha256='0' * 64),
                 lambda r: r.update(held_out_capture_contrast_ci95_t=[1, 2]),
                 lambda r: r.update(capture_results=r['capture_results'][:-1]),
                 lambda r: r['sources'][0].update(sha256='0' * 64)]
        for i, edit in enumerate(edits):
            with self.subTest(edit=i):
                args = fixture(); change_json(args, 'independent_projection.json', edit)
                self.assertFalse(classify_reference_failure(**args)['recognized'])

    def test_status_labels_sources_and_block_errors_reject(self):
        edits = [('status.json', lambda r: r.update(points_completed=11)),
                 ('status.json', lambda r: r.update(shutdown_ok=False)),
                 ('axes.json', lambda r: r[0]['axes'].update({'exp.cir.default': 'on'})),
                 ('axes.json', lambda r: r[0].update(capture=5)),
                 ('entry_source_hashes.json', lambda r: r.update({'run_single_qubit.py': '0' * 64})),
                 ('point_0000_acquisition_blocks.json', lambda r: r.update(exact_duplicate_blocks_detected=True)),
                 ('point_0000_acquisition_blocks.json', lambda r: r['blocks'][0].update(sha256='0' * 64))]
        for name, edit in edits:
            with self.subTest(name=name):
                args = fixture(); change_json(args, name, edit)
                self.assertFalse(classify_reference_failure(**args)['recognized'])

    def test_plan_changes_and_foreign_paths_reject(self):
        for mode in ('identity', 'job', 'pre_sampling', 'outside', 'parent', 'duplicate'):
            args = fixture()
            if mode == 'identity': args['expected_plan']['requestIdentity']['runId'] = 'foreign'
            if mode == 'job': args['expected_plan']['jobs'][0]['name'] = 'foreign'
            if mode == 'pre_sampling': args['expected_plan']['jobs'][0]['acquisitions_per_point'] = 4
            if mode == 'outside': args['receipt']['partialArtifacts'][0]['sourcePath'] = 'D:/elsewhere/job.json'
            if mode == 'parent': args['receipt']['partialArtifacts'][0]['sourcePath'] = str(PureWindowsPath(args['context']['operationDirectory']) / 'results/../job.json')
            if mode == 'duplicate': args['receipt']['partialArtifacts'].append(deepcopy(args['receipt']['partialArtifacts'][0]))
            self.assertFalse(classify_reference_failure(**args)['recognized'], mode)

    def test_malformed_inputs_do_not_raise_or_recognize(self):
        for key in fixture():
            args = fixture(); args[key] = None
            self.assertFalse(classify_reference_failure(**args)['recognized'])
        args = fixture(); change_json(args, 'independent_projection.json', lambda r: r.update(capture_results=[None] * 6))
        self.assertFalse(classify_reference_failure(**args)['recognized'])

    def test_audited_source_constants_match_current_private_manifest(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / 'adapters/arclight_runtime/source_manifest.json').read_text(encoding='utf8'))
        actual = {PureWindowsPath(row['path']).name: row['sha256'] for row in manifest}
        for name, sha in AUDITED_SOURCES.items():
            self.assertEqual(actual[name], sha)

    def test_invalid_raw_and_mismatched_point_are_not_native_evidence(self):
        for data in (b'NOT AN NPZ', iq_npz([1.] * 4000, [2.] * 4000, (1, 4000)),
                     iq_npz([float('nan')] * 4000, [2.] * 4000, (1, 4000))):
            args = fixture()
            artifact = next(a for a in args['receipt']['partialArtifacts'] if a['name'] == 'point_0000.npz')
            args['receipt']['partialArtifacts'].remove(artifact)
            add_artifact(args, artifact['sourcePath'], data)
            self.assertFalse(classify_reference_failure(**args)['recognized'])


if __name__ == '__main__':
    unittest.main()
