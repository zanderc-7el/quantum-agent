"""Offline bounded numerical reader. Never call native fitting or hardware APIs."""
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import sys
import zipfile

from .capture_diagnostics import (VERSION, PROCEDURES, MAX_JSON_BYTES, MAX_RAW_BYTES,
    MAX_CONTEXT_BYTES, MAX_CAPTURES, NOTICE, _require, unavailable)
from .common import checked_path, file_hash
from .prior_failure_context import MANIFEST_SHA


def _read(path):
    with path.open('rb') as stream:
        data = stream.read(MAX_JSON_BYTES + 1)
    _require(0 < len(data) <= MAX_JSON_BYTES, 'json_size_limit')
    return json.loads(data.decode('utf-8-sig'))


def _sources(root, expected):
    manifest_path = checked_path(root / 'adapters/arclight_runtime/source_manifest.json', parent=root)
    _require(file_hash(manifest_path) == MANIFEST_SHA, 'source_manifest_changed')
    manifest = {v['path'].replace('\\', '/'): v['sha256'] for v in _read(manifest_path)}
    for name in ('platform_analysis.py', 'analyze_single_qubit.py'):
        rel = 'adapters/arclight_runtime/' + name
        _require(expected.get(name) == manifest[rel] and file_hash(checked_path(root / rel, parent=root)) == manifest[rel],
                 'analysis_source_changed')


def _axis_inventory(records, job):
    """Validate raw order against the frozen job's exact generated axis tuples."""
    sweeps, meta = job['spec']['sweeps'], job['spec']['meta']
    _require(isinstance(sweeps, list) and 1 <= len(sweeps) <= 32, 'unsupported_axis_schema')
    keys = [s['bindings'] for s in sweeps]
    values = [s['values'] for s in sweeps]
    _require(all(isinstance(k, str) for k in keys) and len(set(keys)) == len(keys)
             and all(isinstance(v, list) and 1 <= len(v) <= 20001 for v in values), 'unsupported_axis_schema')
    if meta['expand_mode'] == 'zip':
        _require(len({len(v) for v in values}) == 1, 'axis_length_mismatch')
        rows = list(zip(*values))
    else:
        _require(meta['expand_mode'] == 'cartesian', 'unsupported_axis_schema')
        count = 1
        for v in values:
            count *= len(v)
        _require(count <= 20001, 'axis_size_limit')
        rows = list(itertools.product(*values))
    seen = set()
    for record in records:
        capture, base = record.get('capture'), record.get('base_index')
        _require(type(capture) is int and 0 <= capture < job['captures']
                 and type(base) is int and 0 <= base < len(rows), 'capture_axis_identity_mismatch')
        _require((capture, base) not in seen and record.get('axes') == dict(zip(keys, rows[base])), 'axis_mapping_mismatch')
        seen.add((capture, base))
    _require(len(seen) == len(rows) * job['captures'], 'incomplete_axis_coverage')


def _operation(root, item, binding, native, platform):
    import numpy as np
    operation = item['operationId']
    op_root = checked_path(root / 'data/runs/arclight_operations' / hashlib.sha256(operation.encode()).hexdigest(), parent=root)
    files = item['files']
    _require(len({f['name'] for f in files}) == len(files), 'artifact_inventory_mismatch')
    paths = {}
    for file in files:
        path = checked_path(file['path'], parent=op_root)
        _require(path.name == file['name'] and 0 < path.stat().st_size <= (MAX_RAW_BYTES if path.name == 'raw_scan.npz' else MAX_JSON_BYTES)
                 and file_hash(path) == file['sha256'], 'artifact_changed_after_snapshot')
        paths[file['name']] = path
    _require(paths['axes.json'].parent == paths['raw_scan.npz'].parent, 'axes_raw_job_folder_mismatch')
    records, job, status, saved, plan = (_read(paths[name]) for name in
        ('axes.json', 'job.json', 'status.json', 'platform_analysis.json', 'plan.json'))
    _require(plan['operationId'] == operation and plan['procedureId'] == binding['procedureId']
             and plan['requestIdentity']['runId'] == binding['runId']
             and plan['requestIdentity']['nodeId'] == binding['nodeId']
             and plan['requestIdentity']['attemptId'] == binding['sourceAttempt']
             and plan['jobs'] == [job] and job['kind'] == PROCEDURES[binding['procedureId']], 'operation_plan_mismatch')
    _require(status['mode'] == 'real_hardware' and status['state'] == 'acquired'
             and status['job'] == job['name'] and job['parameters'] == plan['parameters']
             and len(records) == status['points_completed'] == status['points_expected'] == plan['totalRows'] == plan['expectedJobRows'][0],
             'incomplete_native_job')
    count, shots = job['acquisitions_per_point'], job['spec']['meta']['shots']
    _require(type(count) is int and 2 <= count <= 8 and type(shots) is int and 2 <= shots <= 2047
             and type(job['captures']) is int and 1 <= job['captures'] <= MAX_CAPTURES, 'unsupported_sampling_schema')
    _axis_inventory(records, job)
    with zipfile.ZipFile(paths['raw_scan.npz']) as archive:
        info = archive.infolist()
        _require(len(info) == 3 and {i.filename for i in info} == {'i.npy', 'q.npy', 'acquisition_block_index.npy'}
                 and sum(i.file_size for i in info) <= MAX_RAW_BYTES, 'raw_archive_limit')
        # Reject forged NPY shapes before NumPy allocates any advertised array.
        for member in info:
            with archive.open(member) as stream:
                version = np.lib.format.read_magic(stream)
                _require(version in ((1, 0), (2, 0)), 'unsupported_npy_version')
                reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
                shape, _, dtype = reader(stream, max_header_size=4096)
                shapes = {(len(records), 1, shots * count)} if member.filename in ('i.npy', 'q.npy') else {
                    (shots * count,), (len(records), shots * count), (len(records), 1, shots * count)}
                _require(shape in shapes and dtype.kind in ('iuf' if member.filename != 'acquisition_block_index.npy' else 'iu')
                         and int(np.prod(shape)) * dtype.itemsize == member.file_size - stream.tell(), 'raw_header_shape_mismatch')
    with np.load(paths['raw_scan.npz'], allow_pickle=False) as archive:
        i, q, block_ids = archive['i'], archive['q'], archive['acquisition_block_index']
    iq = native.validate_arrays(records, i, q)
    _require(iq.shape == (len(records), shots * count), 'raw_shape_mismatch')
    axes = native.normalize_axes(records)
    analysis = saved['analysis']
    raw_sha = next(f['sha256'] for f in files if f['name'] == 'raw_scan.npz')
    _require(analysis['raw_sha256'] == raw_sha and analysis['procedureId'] == binding['procedureId']
             and analysis['kind'] == job['kind'] and analysis['points'] == len(records)
             and analysis['shots_per_point'] == shots * count, 'analysis_identity_mismatch')
    projection = analysis['projection']
    _require(isinstance(projection, dict) and 'direction' in projection and 'origin' in projection, 'saved_projection_missing')
    y, sem, metadata = native.projection(iq, {'projection': projection}, count, block_ids)
    statistics = metadata['point_statistics']
    for name in ('shot_iid_sem', 'acquisition_block_sem', 'effective_sem'):
        actual, stored = np.asarray(statistics[name]), np.asarray(analysis['point_statistics'][name])
        _require(actual.shape == stored.shape == (len(records),) and np.isfinite(stored).all()
                 and np.allclose(actual, stored, rtol=1e-12, atol=1e-20), 'stored_sem_mismatch')
    kind = job['kind']
    multiplier = 2 if kind == 'echo' else 2 * job['parameters']['pi_pulses'] if kind == 'cpmg' else 1
    if kind == 'cpmg':
        _require(job.get('total_delay_multiplier') == multiplier, 'cpmg_delay_multiplier_mismatch')
    key = native.axis_key(axes, '.wait_a.params' if kind in ('echo', 'cpmg') else '.wait.params', job.get('axis_key'))
    _require(analysis['x_axis'] == {'native_key': key, 'unit': 's', 'echo_multiplier': multiplier}, 'analysis_axis_mismatch')
    if kind == 'echo':
        _require(key == 'exp.cir.main.wait_a.params' and all(a.get('exp.cir.main.wait_b.params') == a[key] for a in axes),
                 'echo_linked_delay_mismatch')
    if kind == 'cpmg':
        pulses = job['parameters']['pi_pulses']
        _require(type(pulses) is int and 1 <= pulses <= 16 and key == 'exp.cir.main.wait_a.params', 'cpmg_native_delay_schema')
        waits = {key, *(f'exp.cir.main.wait_{n}.params' for n in range(pulses))}
        _require(all({k for k in a if '.wait_' in k} == waits and
            all(a[f'exp.cir.main.wait_{n}.params'] == a[key] * (1 if n == pulses-1 else 2) for n in range(pulses)) for a in axes),
            'cpmg_linked_delay_mismatch')
    x = np.asarray([a[key] for a in axes], dtype=float) * multiplier
    _require(np.isfinite(x).all(), 'nonfinite_axis')
    excluded = {key, 'exp.cir.default'}
    excluded.update(k for a in axes for k in a if k.endswith('.phase.params') or
        (kind == 'echo' and k.endswith('.wait_b.params')) or (kind == 'cpmg' and '.wait_' in k))
    groups = native.split_groups(axes, excluded, job.get('group_by'))
    _require(len(groups) == len(analysis['groups']) and len(groups) * job['captures'] <= MAX_CAPTURES, 'group_coverage_limit')
    originals = [s for s in saved['series'] if s.get('fit') is False]
    rows, ordinals = [], []
    def correlation(a, b):
        return float(np.corrcoef(a, b)[0, 1]) if len(a) >= 3 and np.std(a) > 0 and np.std(b) > 0 else None
    for group_index, (group, stored_group) in enumerate(zip(groups, analysis['groups'])):
        _require(group['axes'] == stored_group['group_axes'], 'group_axes_mismatch')
        _, batches = native.repeat_blocks(x, group['indices'], axes)
        _require(len(batches) == len(stored_group['repeats']) == job['captures'], 'capture_count_mismatch')
        for repeat, indices in zip(stored_group['repeats'], batches):
            xx, yy, sigma = x[indices], y[indices], sem[indices]
            _require(len(indices) == job['parameters']['points'] and np.all(sigma > 0), 'capture_points_or_sem_mismatch')
            captures = {records[int(index)]['capture'] for index in indices}
            _require(len(captures) == 1 and next(iter(captures)) + 1 == repeat['repeat'], 'capture_label_mismatch')
            trace = originals[len(rows)]
            _require(np.array_equal(trace['x'], xx) and len(trace['y']) == len(yy)
                     and np.allclose(trace['y'], yy, rtol=1e-12, atol=1e-18), 'stored_series_mismatch')
            raw_indices = indices.astype(int)
            ordinals.extend(raw_indices.tolist())
            row = {'groupIndex': group_index, 'groupAxes': group['axes'], 'captureIndex': next(iter(captures)),
                'nativeRepeatLabel': repeat['repeat'], 'sortedSeriesIndex': len(rows), 'pointCount': len(indices),
                'rawOrdinalRange': [int(raw_indices.min()), int(raw_indices.max())],
                'medianEffectiveSem': float(np.median(sigma)),
                'medianShotIidSem': float(np.median(np.asarray(statistics['shot_iid_sem'])[indices])),
                'medianBlockSem': float(np.median(np.asarray(statistics['acquisition_block_sem'])[indices])),
                'scanCoordinateAcquisitionOrdinalPearsonR': correlation(xx, raw_indices),
                'rawProjectedMeanRange': [float(np.min(yy)), float(np.max(yy))],
                'rawProjectedMeanMedian': float(np.median(yy)),
                'rawProjectedMeanAcquisitionOrdinalPearsonR': correlation(yy, raw_indices),
                'rawProjectedMeanScanCoordinatePearsonR': correlation(yy, xx)}
            _require('fit' in repeat, 'native_fit_missing_or_unknown')
            fit = repeat['fit']
            if fit is None:
                reasons = (repeat.get('diagnostics') or {}).get('rejection_reasons')
                _require(isinstance(reasons, list) and 1 <= len(reasons) <= 16 and
                    all(isinstance(reason, str) and reason.strip() and len(reason) <= 2048 for reason in reasons)
                    and analysis.get('accepted') is False, 'native_fit_null_without_failure_reasons')
                unavailable_fields = ('residualRms', 'residualToEffectiveSemRms', 'residualAcquisitionOrdinalPearsonR',
                                      'rSquaredFromUnchangedFit', 'nativeDecayTimeSeconds')
                row.update({key: None for key in unavailable_fields})
                row['fitDiagnostics'] = {'status': 'unavailable', 'reasonCode': 'native_fit_rejected',
                    'unavailableFields': list(unavailable_fields), 'nativeRejectionReasons': reasons}
            else:
                _require(isinstance(fit, dict) and fit.get('model') in ('exponential', 'damped_sinusoid', 'sinusoid_decay_unresolved'), 'native_fit_missing_or_unknown')
                parameters = np.asarray(fit['fit_parameters_normalized_x'], float)
                expected_parameters = {'exponential': 3, 'sinusoid_decay_unresolved': 5, 'damped_sinusoid': 6}[fit['model']]
                _require(parameters.shape == (expected_parameters,) and np.isfinite(parameters).all()
                         and np.isfinite([fit['x_origin'], fit['x_span'], fit['residual_rms']]).all()
                         and fit['x_span'] > 0, 'invalid_saved_fit')
                _require(fit['x_origin'] == float(xx[0]) and fit['x_span'] == float(xx[-1] - xx[0]), 'fit_window_or_sem_mismatch')
                prediction = platform._prediction(fit, xx)  # Evaluation only, never fitting.
                _require(prediction.shape == xx.shape and np.isfinite(prediction).all(), 'nonfinite_saved_prediction')
                residual = yy - prediction
                rms = float(np.sqrt(np.mean(residual**2)))
                _require(np.isclose(rms, fit['residual_rms'], rtol=1e-10, atol=1e-15), 'stored_fit_residual_mismatch')
                decay = fit.get('decay_time')
                _require((fit['model'] == 'sinusoid_decay_unresolved' and decay is None) or
                         (type(decay) in (float, int) and np.isfinite(decay) and decay > 0), 'invalid_saved_decay_time')
                if decay is not None:
                    _require(np.isclose(decay, fit['x_span'] * parameters[2 if fit['model'] == 'exponential' else 5],
                             rtol=1e-12, atol=1e-18), 'saved_decay_parameter_mismatch')
                row.update(fitDiagnostics={'status': 'available', 'model': fit['model'],
                    'decayTimeStatus': 'unresolved_by_native_fit' if decay is None else 'saved_native_value'},
                    nativeDecayTimeSeconds=decay, residualRms=rms,
                    residualToEffectiveSemRms=float(np.sqrt(np.mean((residual / sigma)**2))),
                    residualAcquisitionOrdinalPearsonR=correlation(residual, raw_indices),
                    rSquaredFromUnchangedFit=float(1 - np.sum(residual**2) / max(float(np.sum((yy - yy.mean())**2)), 1e-30)))
            rows.append(row)
    _require(len(originals) == len(rows) and sorted(ordinals) == list(range(len(records))), 'raw_point_coverage_mismatch')
    valid = [row for row in rows if row['fitDiagnostics']['status'] == 'available']
    _require(np.isclose(min(row['rSquaredFromUnchangedFit'] for row in valid), saved['metrics'].get('r_squared'), rtol=1e-12, atol=1e-12)
             if valid else saved['metrics'].get('r_squared') is None, 'stored_metric_mismatch')
    for file in files:
        _require(file_hash(paths[file['name']]) == file['sha256'], 'artifact_changed_during_calculation')
    return {'resultRole': item['resultRole'], 'sourceOperation': operation,
        'registeredSourceSha256': {f['name']: f['sha256'] for f in files if f['name'] in ('raw_scan.npz', 'axes.json', 'platform_analysis.json', 'plan.json')},
        'nativeAcceptedUnchanged': analysis['accepted'], 'pointCount': len(records), 'shotsPerBlock': shots,
        'fitDiagnosticCoverage': {'status': 'complete' if len(valid) == len(rows) else 'partial_fit_diagnostics',
            'captureCount': len(rows), 'validFitCaptureCount': len(valid), 'missingFitCaptureCount': len(rows)-len(valid),
            'nativeRSquaredScope': 'minimum_over_non_null_native_fits_only_not_all_captures'},
        'blocksPerPoint': count, 'allRecordedPointsCoveredExactlyOnce': True,
        'savedProjectionAndSemCrossChecked': True, 'nativeFitsRefitted': False,
        'wallClockTimeAvailable': False, 'unregisteredTimestampFilesRead': False, 'captures': rows}


def calculate(resolved):
    try:
        root = checked_path(resolved['root'])
        binding = resolved['binding']
        _require(binding['procedureId'] in PROCEDURES and 1 <= len(resolved['operations']) <= 2, 'unsupported_scope')
        _sources(root, resolved['sourceHashes'])
        path = root / 'adapters/arclight_runtime/platform_analysis.py'
        spec = importlib.util.spec_from_file_location('_qcal_offline_capture_analysis', path)
        platform = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(platform)
        native = platform.native_module('analyze_single_qubit')
        operations = [_operation(root, item, binding, native, platform) for item in resolved['operations']]
        _sources(root, resolved['sourceHashes'])
        partial = any(o['fitDiagnosticCoverage']['missingFitCaptureCount'] for o in operations)
        output = {'schema': VERSION, 'status': 'partial_fit_diagnostics' if partial else 'available', 'scope': 'current_attempt_diagnostic_only_not_acceptance',
                  'binding': binding, 'sourceHashes': resolved['sourceHashes'], 'notice': NOTICE,
                  'operations': operations}
        _require(len(json.dumps(output, ensure_ascii=False, allow_nan=False).encode('utf-8')) <= MAX_CONTEXT_BYTES, 'diagnostic_size_limit')
        return output
    except Exception as exc:
        # Deliberately optional. No raw exception text/path is sent to a model.
        code = str(exc) if type(exc) is ValueError and str(exc).replace('_', '').isalnum() and len(str(exc)) < 80 else 'numerical_validation_failed'
        return unavailable(code)


if __name__ == '__main__':
    try:
        raw = sys.stdin.read(256 * 1024 + 1)
        _require(len(raw) <= 256 * 1024, 'worker_input_limit')
        output = calculate(json.loads(raw))
    except Exception:
        output = unavailable('invalid_worker_input')
    print(json.dumps(output, ensure_ascii=False, allow_nan=False))
