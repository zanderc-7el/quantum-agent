"""Bound only model views of known numerical arrays; never alter stored evidence."""
from copy import deepcopy
import math

from .prior_failure_context import javascript_json_units_upper_bound

MAX_INPUT_UNITS = 198000
STAT_ARRAYS = ('shot_iid_sem', 'acquisition_block_sem', 'effective_sem')


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _indices(size, cap):
    count = min(size, cap)
    return [0] if count == 1 else [i * (size - 1) // (count - 1) for i in range(count)] if count else []


def _vector_info(values, indices):
    low, high = min(range(len(values)), key=values.__getitem__), max(range(len(values)), key=values.__getitem__)
    return {'originalPointCount': len(values), 'providedPointCount': len(indices), 'originalIndices': indices,
            'partial': len(indices) < len(values), 'globalMinimum': {'index': low, 'value': values[low]},
            'globalMaximum': {'index': high, 'value': values[high]}, 'includesFirstAndLast': True}


def _project_result(result, cap, *, include_series):
    changed = 0
    native = result.get('analysisQuality')
    stats = native.get('point_statistics') if isinstance(native, dict) else None
    if isinstance(stats, dict):
        details = {}
        for key in STAT_ARRAYS:
            values = stats.get(key)
            if isinstance(values, list) and len(values) > cap and all(_finite(v) for v in values):
                indices = _indices(len(values), cap)
                details[key] = _vector_info(values, indices)
                stats[key] = [values[i] for i in indices]
                changed += 1
        if details:
            stats['modelArrayProjection'] = {'schema': 'qcal-model-array-projection/1', 'partial': True,
                'order': 'Original acquisition order from axes.json/raw_scan.npz; NOT the sorted series order.',
                'notice': 'Arrays contain sampled original values only. Do not pair them with series by position or fit these summaries.',
                'arrays': details}
    series = result.get('series')
    if include_series and isinstance(series, list):
        for index, trace in enumerate(series):
            if not isinstance(trace, dict):
                continue
            x, y = trace.get('x'), trace.get('y')
            if (not isinstance(x, list) or not isinstance(y, list) or len(x) != len(y) or len(x) <= cap
                    or not all(_finite(v) for v in x + y)):
                continue
            # Unknown/misaligned uncertainty fields are left untouched. They
            # must never look aligned with a newly sampled x/y grid.
            companions = [key for key in ('sem', 'yerr') if key in trace]
            if any(not isinstance(trace[k], list) or len(trace[k]) != len(x)
                   or not all(_finite(v) for v in trace[k]) for k in companions):
                continue
            indices = _indices(len(x), cap)
            details = _vector_info(y, indices)
            details.update(seriesIndex=index, sampling='uniform_original_index_paired_xy',
                originalXEndpoints=[x[0], x[-1]], sourceXRange=[min(x), max(x)],
                globalMinimum={'index': details['globalMinimum']['index'], 'x': x[details['globalMinimum']['index']],
                               'y': details['globalMinimum']['value']},
                globalMaximum={'index': details['globalMaximum']['index'], 'x': x[details['globalMaximum']['index']],
                               'y': details['globalMaximum']['value']},
                notice='Model-view subset only; endpoints/extrema examined across all points. No sorting, smoothing, interpolation, or new measurement. Sampling may miss structure or alias oscillations; do not refit/count cycles or infer missing peaks from it.')
            trace['x'], trace['y'] = [x[i] for i in indices], [y[i] for i in indices]
            for key in companions:
                trace[key] = [trace[key][i] for i in indices]
            trace['modelArrayProjection'] = details
            changed += 2 + len(companions)
    return changed


def project_model_input(payload):
    """Return a copy. Unknown/invalid arrays and all protected fields stay exact.

    If known-array projection cannot fit, return an explicitly over-budget view;
    the controller must reject it before HTTP, without truncating other content.
    """
    original_units = javascript_json_units_upper_bound(payload)
    if original_units <= MAX_INPUT_UNITS:
        return deepcopy(payload)
    output = deepcopy(payload)
    # Preserve complete curves whenever sampling only the much larger SEM
    # vectors already resolves the input limit (the observed Spectrum case).
    for include_series, cap in ((False, 33), (True, 33), (True, 17), (True, 9)):
        output = deepcopy(payload)
        observations = output.get('observations', {})
        result = observations.get('result') if isinstance(observations, dict) else None
        changed = 0
        if isinstance(result, dict):
            changed += _project_result(result, cap, include_series=include_series)
            if isinstance(result.get('selectionResult'), dict):
                changed += _project_result(result['selectionResult'], cap, include_series=include_series)
        output['modelInputProjection'] = {'schema': 'qcal-model-input-projection/1',
            'scope': 'model_view_only_not_acquisition_or_acceptance', 'partialView': bool(changed),
            'projectionStage': 'statistics_and_paired_series' if include_series else 'statistics_only',
            'maximumSamplesPerArray': cap, 'projectedArrayCount': changed,
            'originalEstimatedUtf16Units': original_units if math.isfinite(original_units) else None,
            'notice': 'This is not the full numerical input. Stored raw files, SHA-bound artifacts, metrics, native decisions and controller gates are unchanged. Array counts/indices describe omissions; use registered original evidence for full analysis.'}
        navigation = observations.get('curveNavigation') if isinstance(observations, dict) else None
        if isinstance(navigation, dict) and changed and include_series:
            navigation['notice'] = navigation.get('notice', '').replace(
                '原曲线仍在 result.series 中，', '本次 result.series 也可能已抽样，完整曲线保留在未修改的采集产物中，')
        if javascript_json_units_upper_bound(output) <= MAX_INPUT_UNITS:
            return output
    return output
