"""Pure classification of one audited ArcLight PRE-reference quality failure.

Not connected to Controller. This is NOT execution permission or acceptance.
The caller must authenticate the actual adapter class/profile/operation, obtain
the trusted adapter receipt, verify the complete result-file inventory (including
absence of later job folders), resolve registered artifact paths without links,
and verify current frozen runtime sources. Do not build ``context`` from a model,
request body, or the contents of a measurement file. No file, DB or hardware I/O
occurs here. All supplied bytes are checked against both receipt and registry.

The current partial receipt registers results JSON/NPZ, but not root plan or
completion. The caller must independently attest that trusted receipt provenance
and the expected immutable plan were checked; missing attestation fails closed.
"""
from copy import deepcopy
import ast
import hashlib
import io
import json
import math
from pathlib import PureWindowsPath
import statistics
import struct
import zipfile


IDENTITY = ('adapters.arclight.ArcLightAdapter', 'arclight-private-f8-q11-v1')
AUDITED_SOURCES = {
    'platform_worker.py': 'd62d6453cad6d6fdf3a324611b549a2aa875dcc6911a208b208f3919f65fed59',
    'run_single_qubit.py': '483d35d7efaffef9e5353827dcdc1c342702d1e3da5b32f2481a930bbaaa982a',
    'freeze_historical_point_reference.py': 'b97cb026ebe4ad08adc5473558d5024800fdec67b9066a01321e273c7fdce959',
    'repeated_iq_acquisition.py': '9e0fac6c39e196487c973770bb2b84a687bb51a749164d48618be661c65916db',
}
_MESSAGE = 'ValueError: Fresh held-out reference contrast failed; no downstream pulse sequence executed'
_ATTESTATIONS = ('adapterBindingVerified', 'receiptProvenanceVerified', 'expectedPlanVerified',
                 'completeResultInventoryVerified', 'artifactPathsVerified',
                 'noPendingOrUnknownEffects', 'cleanupAndOwnershipVerified')


class _Reject(Exception):
    pass


def _need(ok, code):
    if not ok:
        raise _Reject(code)


def _finite(x):
    return type(x) in (int, float) and math.isfinite(x)


def _equal_number(a, b):
    return _finite(a) and _finite(b) and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-20)


def _path(value):
    _need(isinstance(value, str) and value, 'INVALID_PATH')
    p = PureWindowsPath(value)
    _need(p.is_absolute() and '..' not in p.parts and not str(p).startswith('\\\\'), 'INVALID_PATH')
    return p


def _load_json(data):
    def pairs(items):
        value = {}
        for key, item in items:
            _need(key not in value, 'DUPLICATE_JSON_KEY')
            value[key] = item
        return value
    return json.loads(data.decode('utf-8-sig'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(_Reject('NONFINITE_JSON')))


def _npy_member(archive, name, shape, kind):
    """Read bounded numeric NPY bytes without NumPy, pickle or code execution."""
    info = archive.getinfo(name + '.npy')
    _need(info.file_size <= 8 * math.prod(shape) + 4096, 'RAW_ARRAY_TOO_LARGE')
    data = archive.read(info)
    _need(data[:6] == b'\x93NUMPY' and data[6:8] in (b'\x01\x00', b'\x02\x00'), 'RAW_NPY_INVALID')
    header_start = 10 if data[6] == 1 else 12
    size = int.from_bytes(data[8:header_start], 'little')
    _need(size <= 4096, 'RAW_NPY_HEADER_TOO_LARGE')
    header = ast.literal_eval(data[header_start:header_start + size].decode('latin1'))
    formats = {'float': {'<f8': ('d', 8), '<f4': ('f', 4)}, 'blocks': {'<i2': ('h', 2)}}[kind]
    _need(isinstance(header, dict) and header.get('shape') == shape and header.get('fortran_order') is False
          and header.get('descr') in formats, 'RAW_ARRAY_CONTRACT_INVALID')
    fmt, width = formats[header['descr']]
    payload = data[header_start + size:]
    _need(len(payload) == width * math.prod(shape), 'RAW_ARRAY_LENGTH_INVALID')
    values = [v[0] for v in struct.iter_unpack('<' + fmt, payload)]
    _need(all(math.isfinite(v) for v in values), 'RAW_ARRAY_NONFINITE')
    return values


def _iq_arrays(data, shape, shots, block=None):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        _need(len(names) == len(set(names)) and len(names) <= 12
              and all('/' not in n and '\\' not in n for n in names), 'RAW_ARCHIVE_INVALID')
        i = _npy_member(archive, 'i', shape, 'float')
        q = _npy_member(archive, 'q', shape, 'float')
        expected = [0] * shots + [1] * shots if block is None else [block] * shots
        blocks = _npy_member(archive, 'acquisition_block_index', (len(expected),), 'blocks')
        _need(blocks == expected, 'RAW_BLOCK_AXIS_INVALID')
        return i, q


def classify_reference_failure(*, context, request, expected_plan, receipt,
                               registered_artifacts, artifact_bytes):
    """Return recognized=False for ANY incomplete/foreign/changed evidence.

    registered_artifacts is an id -> {path, sha256} map from the current trusted
    registry, artifact_bytes is id -> bytes loaded by the caller. Raw NPZ shape,
    finiteness, block axes and aggregate/point equality are checked without NumPy;
    no new LDA or fit is performed. This classifies a frozen native failure.
    """
    base = {'schema': 'qcal-reference-failure/1', 'recognized': False,
            'eligibleForModelDecision': False, 'isExecutionApproval': False}
    try:
        _need(all(isinstance(x, dict) for x in (context, request, expected_plan, receipt,
                                               registered_artifacts, artifact_bytes)), 'INVALID_INPUT')
        _need((context.get('implementationId'), context.get('profileId')) == IDENTITY, 'UNSUPPORTED_IDENTITY')
        _need(all(context.get(k) is True for k in _ATTESTATIONS), 'CALLER_VERIFICATION_REQUIRED')
        _need(all(context.get('runtimeSourceHashes', {}).get(k) == v for k, v in AUDITED_SOURCES.items()), 'UNAUDITED_RUNTIME')
        operation = request.get('operationId')
        procedure = request.get('procedureId')
        _need(isinstance(operation, str) and operation and procedure in ('sq_allxy', 'sq_rb')
              and request.get('resourceId') == 'Q11', 'UNSUPPORTED_OPERATION')
        _need(context.get('operationId') == operation, 'OPERATION_MISMATCH')
        canonical = {k: v for k, v in request.items() if k != 'outputDir'}
        request_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                                                allow_nan=False).encode()).hexdigest()
        _need(receipt.get('operationId') == operation and receipt.get('requestHash') == request_hash
              and context.get('requestHash') == request_hash, 'REQUEST_IDENTITY_MISMATCH')
        _need(receipt.get('status') == 'failed_after_shutdown' and receipt.get('shutdownConfirmed') is True
              and receipt.get('reason') == _MESSAGE and receipt.get('pointsCompleted') == 12,
              'NOT_KNOWN_CLEAN_REFERENCE_FAILURE')
        root = _path(context.get('operationDirectory'))
        _need(root.name == hashlib.sha256(operation.encode()).hexdigest(), 'OPERATION_DIRECTORY_MISMATCH')
        _need(_path(receipt.get('completionPath')) == root / 'completion.json', 'COMPLETION_IDENTITY_MISMATCH')
        _need(expected_plan.get('adapter') == 'arclight' and expected_plan.get('operationId') == operation
              and expected_plan.get('resourceId') == 'Q11' and expected_plan.get('procedureId') == procedure,
              'PLAN_IDENTITY_MISMATCH')
        identity = expected_plan.get('requestIdentity', {})
        _need(all(identity.get(k) == request.get(k) and request.get(k) is not None for k in
                  ('runId', 'nodeId', 'attemptId', 'operationId', 'resourceId', 'procedureId', 'parameterVersions')),
              'PLAN_REQUEST_IDENTITY_MISMATCH')
        jobs = expected_plan.get('jobs')
        _need(isinstance(jobs, list) and len(jobs) == (2 if procedure == 'sq_allxy' else 4)
              and expected_plan.get('expectedJobRows', [None])[0] == 12, 'UNSUPPORTED_PLAN')
        first = jobs[0]
        _need(first.get('rb_role') == ('independent_reference' if procedure == 'sq_allxy' else 'pre')
              and first.get('kind') == 'readout' and first.get('target') == 'Q11'
              and first.get('captures') == 6 and first.get('acquisitions_per_point') == 2,
              'REFERENCE_PLAN_MISMATCH')
        shots = first.get('spec', {}).get('meta', {}).get('shots')
        _need(type(shots) is int and 100 <= shots <= 2047, 'REFERENCE_SHOTS_INVALID')
        artifacts = receipt.get('partialArtifacts')
        _need(isinstance(artifacts, list) and 1 <= len(artifacts) <= 256, 'ARTIFACT_INVENTORY_INVALID')
        files, ids, total_bytes = {}, set(), 0
        for a in artifacts:
            _need(isinstance(a, dict), 'ARTIFACT_INVALID')
            aid, sha = a.get('id'), a.get('sha256')
            p = _path(a.get('sourcePath'))
            _need(p.is_relative_to(root / 'results') and len(p.relative_to(root / 'results').parts) == 2,
                  'ARTIFACT_OUTSIDE_REFERENCE_FOLDER')
            _need(p not in files and a.get('name') == p.name and p.suffix in ('.json', '.npz'), 'ARTIFACT_PATH_INVALID')
            registered = registered_artifacts.get(aid)
            _need(isinstance(registered, dict) and _path(registered.get('path')) == p
                  and registered.get('sha256') == sha, 'ARTIFACT_NOT_REGISTERED')
            content = artifact_bytes.get(aid)
            _need(isinstance(content, bytes), 'ARTIFACT_BYTES_MISSING')
            total_bytes += len(content)
            _need(total_bytes <= 128 * 1024 * 1024 and hashlib.sha256(content).hexdigest() == sha,
                  'ARTIFACT_HASH_OR_SIZE_INVALID')
            _need(aid == 'real-' + sha[:24], 'ARTIFACT_ID_INVALID')
            files[p] = (a, content)
            ids.add(aid)
        folders = {p.parent for p in files}
        _need(len(folders) == 1, 'LATER_JOB_OR_EXTRA_FOLDER_PRESENT')
        folder = next(iter(folders))
        _need(folder.name.endswith('_live_' + first['name']), 'REFERENCE_FOLDER_IDENTITY_MISMATCH')

        def file(name):
            _need(folder / name in files, 'REQUIRED_ARTIFACT_MISSING:' + name)
            return files[folder / name]

        def read(name):
            return _load_json(file(name)[1])

        _need(not any(p.name.startswith(('partial_', 'platform_', 'paired_rb_')) for p in files), 'LATER_OR_PARTIAL_RESULT_PRESENT')
        job, status, axes, report = read('job.json'), read('status.json'), read('axes.json'), read('independent_projection.json')
        _need(job == first, 'REFERENCE_JOB_CHANGED')
        _need(status.get('mode') == 'real_hardware' and status.get('state') == 'acquired'
              and status.get('job') == first['name'] and status.get('target') == 'Q11'
              and _path(status.get('folder')) == folder and status.get('hardware_connected') is False
              and status.get('shutdown_ok') is True and status.get('points_completed') == 12
              and status.get('points_expected') == 12 and status.get('shots_per_acquisition') == shots
              and status.get('acquisitions_per_point') == 2 and status.get('effective_shots_per_point') == 2 * shots,
              'REFERENCE_ACQUISITION_INCOMPLETE')
        _need(isinstance(axes, list) and len(axes) == 12, 'REFERENCE_AXES_INVALID')
        keys = []
        for row in axes:
            _need(isinstance(row, dict) and type(row.get('capture')) is int and 0 <= row['capture'] < 6
                  and isinstance(row.get('axes'), dict)
                  and set(row['axes']) == {'exp.cir.default'}, 'REFERENCE_AXES_INVALID')
            label = row['axes']['exp.cir.default']
            _need(label in ('ground', 'excited') and row.get('base_index') == (0 if label == 'ground' else 1),
                  'REFERENCE_LABELS_INVALID')
            keys.append((row['capture'], label))
        _need(set(keys) == {(c, s) for c in range(6) for s in ('ground', 'excited')}, 'REFERENCE_LABELS_INCOMPLETE')
        entry = read('entry_source_hashes.json')
        _need(all(entry.get(k) == AUDITED_SOURCES[k] for k in ('run_single_qubit.py', 'repeated_iq_acquisition.py')),
              'EXECUTED_SOURCE_MISMATCH')
        sources = report.get('sources')
        _need(isinstance(sources, list) and len(sources) == 4, 'REFERENCE_SOURCES_INVALID')
        source_paths = set()
        for source in sources:
            p = _path(source.get('path'))
            _need(p in files and files[p][0]['sha256'] == source.get('sha256') and p not in source_paths,
                  'REFERENCE_SOURCE_HASH_MISMATCH')
            source_paths.add(p)
        _need(source_paths == {folder / name for name in ('job.json', 'axes.json', 'raw_scan.npz', 'status.json')},
              'REFERENCE_SOURCES_INCOMPLETE')
        projection = report.get('projection', {})
        _need(report.get('target') == 'Q11' and projection.get('independent_reference') is True
              and projection.get('reference_contrast_repeated') is False
              and report.get('held_out_contrast_positive_and_repeat_consistent') is False
              and _path(projection.get('source')) == folder / 'independent_projection.json'
              and _path(projection.get('reference_raw')) == folder / 'raw_scan.npz'
              and projection.get('reference_raw_sha256') == file('raw_scan.npz')[0]['sha256']
              and projection.get('training_captures') == [0, 1, 2]
              and projection.get('validation_captures') == [3, 4, 5], 'REFERENCE_FAILURE_NOT_ESTABLISHED')
        rows = report.get('capture_results')
        _need(isinstance(rows, list) and len(rows) == 6 and [r.get('capture') for r in rows] == list(range(6)),
              'REFERENCE_DIAGNOSTICS_INCOMPLETE')
        for r in rows:
            _need(r.get('role') == ('training' if r['capture'] < 3 else 'held_out_capture')
                  and all(_finite(r.get(k)) for k in ('ground_mean', 'excited_mean', 'contrast'))
                  and _equal_number(r['contrast'], r['excited_mean'] - r['ground_mean']), 'REFERENCE_DIAGNOSTICS_INVALID')
        held = [r['contrast'] for r in rows[3:]]
        mean, sem = statistics.mean(held), statistics.stdev(held) / math.sqrt(3)
        ci = report.get('held_out_capture_contrast_ci95_t')
        _need(isinstance(ci, list) and len(ci) == 2 and all(_finite(v) for v in ci)
              and _equal_number(mean, report.get('held_out_capture_mean_contrast'))
              and _equal_number(sem, report.get('held_out_capture_contrast_sem'))
              and _equal_number(ci[0], mean - 4.302652729911275 * sem)
              and _equal_number(ci[1], mean + 4.302652729911275 * sem)
              and not (ci[0] > 0 and all(d > 0 for d in held)), 'REFERENCE_FAILURE_DIAGNOSTICS_DISAGREE')
        raw_i, raw_q = _iq_arrays(file('raw_scan.npz')[1], (12, 1, 2 * shots), shots)
        for index in range(12):
            point_i, point_q = _iq_arrays(file(f'point_{index:04d}.npz')[1], (1, 2 * shots), shots)
            start, stop = index * 2 * shots, (index + 1) * 2 * shots
            _need(point_i == raw_i[start:stop] and point_q == raw_q[start:stop], 'RAW_AGGREGATE_POINT_MISMATCH')
            _need(point_i[:shots] != point_i[shots:] or point_q[:shots] != point_q[shots:], 'RAW_BLOCKS_EXACTLY_DUPLICATED')
            block = read(f'point_{index:04d}_acquisition_blocks.json')
            _need(block.get('schema') == 'arclight_actual_repeated_iq_v1' and block.get('state') == 'complete'
                  and block.get('point_index') == index and block.get('acquisitions_per_point') == 2
                  and block.get('shots_per_acquisition') == shots and block.get('blocks_completed') == 2
                  and block.get('effective_shots_completed') == 2 * shots
                  and block.get('exact_duplicate_blocks_detected') is False, 'REFERENCE_BLOCKS_INCOMPLETE')
            blocks = block.get('blocks')
            _need(isinstance(blocks, list) and len(blocks) == 2, 'REFERENCE_BLOCKS_INCOMPLETE')
            for b, record in enumerate(blocks):
                name = f'point_{index:04d}_block_{b:03d}.npz'
                _need(record.get('point_index') == index and record.get('block_index') == b
                      and record.get('block_file') == name and record.get('sha256') == file(name)[0]['sha256']
                      and record.get('exact_duplicate_of_blocks') == [] and record.get('shots_in_block') == shots,
                      'REFERENCE_BLOCK_EVIDENCE_INVALID')
                bi, bq = _iq_arrays(file(name)[1], (1, shots), shots, block=b)
                _need(bi == point_i[b * shots:(b + 1) * shots] and bq == point_q[b * shots:(b + 1) * shots],
                      'RAW_POINT_BLOCK_MISMATCH')
        return {**base, 'recognized': True, 'eligibleForModelDecision': True,
                'code': 'FRESH_REFERENCE_CONTRAST_FAILED', 'stage': 'fresh_reference',
                'procedureId': procedure, 'operationId': operation, 'mainAcquisitionStarted': False,
                'quality': {'status': 'failed', 'reasons': ['独立参考的留出采集对比度未通过原生重复性检查；主测未执行。']},
                'evidenceIds': sorted(ids), 'referenceReportArtifactId': file('independent_projection.json')[0]['id'],
                'diagnostics': {'heldOutContrasts': held, 'heldOutContrastCi95': deepcopy(ci)},
                'samplingSemantics': {'referenceCaptureRepeats': 6, 'referenceAcquisitionsPerPoint': 2,
                    'shotsPerAcquisition': shots, 'ordinaryCaptureAndAcquisitionOverridesAffectReference': False,
                    'notice': '普通 capture_repeats/acquisitions_per_point 只改变后续主测，不能增加此固定 PRE 参考采样；可根据证据回读出或脉冲校准。'},
                'preservation': 'Original receipt, failed reference and raw data must be retained; this is a derived diagnosis, not a completed main measurement.'}
    except _Reject as exc:
        return {**base, 'rejection': str(exc)}
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, UnicodeError,
            IndexError, SyntaxError, EOFError, RuntimeError, struct.error, zipfile.BadZipFile):
        return {**base, 'rejection': 'MALFORMED_EVIDENCE'}
