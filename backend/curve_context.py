"""Bounded navigation summaries of already-supplied curves, never acceptance.

No raw file, model, instrument, calibration, or quality state is accessed here.
Original result.series and native fit diagnostics are preserved verbatim.
"""
from copy import deepcopy
import json
import math

from .prior_failure_context import javascript_json_units_upper_bound

MAX_TRACES = 16
MAX_SAMPLES = 17
MAX_TRACE_POINTS = 20000
MAX_SUMMARY_BYTES = 32768
MAX_INPUT_UNITS = 198000
RABI_PROCEDURES = frozenset(('sq_rabi_amplitude', 'sq_rabi_time', 'sq_rabi_frequency'))


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _text(value, maximum):
    return value[:maximum] if isinstance(value, str) else ''


def _trace(trace, role, index):
    output = {'resultRole': role, 'seriesIndex': index, 'status': 'unavailable'}
    if not isinstance(trace, dict):
        return {**output, 'reason': 'trace_not_object'}
    output.update(name=_text(trace.get('name'), 160), xUnit=_text(trace.get('xUnit'), 40),
                  yUnit=_text(trace.get('yUnit'), 40))
    x, y = trace.get('x'), trace.get('y')
    if not isinstance(x, list) or not isinstance(y, list):
        return {**output, 'reason': 'paired_arrays_required'}
    output.update(xPointCount=len(x), yPointCount=len(y))
    if len(x) != len(y):
        return {**output, 'reason': 'paired_array_length_mismatch'}
    size = len(x)
    if not size or size > MAX_TRACE_POINTS:
        return {**output, 'reason': 'empty_or_point_limit_exceeded'}
    if not all(_finite(v) for v in x) or not all(_finite(v) for v in y):
        return {**output, 'reason': 'nonfinite_or_nonnumeric_pair'}
    if any(b <= a for a, b in zip(x, x[1:])):
        return {**output, 'reason': 'axis_not_strictly_increasing'}
    sample_count = min(MAX_SAMPLES, size)
    indices = [0] if sample_count == 1 else [k * (size - 1) // (sample_count - 1) for k in range(sample_count)]
    pair = lambda i: {'index': i, 'x': x[i], 'y': y[i]}
    minimum, maximum = min(range(size), key=y.__getitem__), max(range(size), key=y.__getitem__)
    output.update(status='available', pointCount=size, xRange=[x[0], x[-1]],
                  globalMinimum=pair(minimum), globalMaximum=pair(maximum),
                  samples=[pair(i) for i in indices], sampleCount=sample_count,
                  coverage={'sourcePointsExamined': size, 'fullTraceRangeCovered': True,
                            'includesFirstAndLast': True, 'samplesAreSubset': sample_count < size,
                            'sampling': 'uniform_original_index_paired_xy', 'extremaFrom': 'all_original_points'})
    return output


def curve_summary(result):
    """Fixed-schema, non-scientific summary of current/direct selection curves."""
    output = {'schema': 'qcal-curve-navigation/1', 'scope': 'navigation_only_not_acceptance',
        'source': 'Already supplied result.series and direct selectionResult.series; no file was read.',
        'notice': '以下均匀配对点覆盖完整扫描首尾，不是只截开头；全量极值来自全部原始点。'
                  '抽样可能漏掉细节或产生混叠，不能据此计数可信周期、外推未扫描区间或判定通过。'
                  '原生拒绝原因中的周期不足描述未通过的拟合，不是实测波形没有周期或单调的定论；'
                  '缺失 metrics.cycles 不等于零周期。原曲线仍在 result.series 中，原生质量与控制器验收不变。'
                  'inspect_observation 返回上下文登记记录，不等于读取了文件内容或看过图像。',
        'sourceTraceCount': 0, 'fitTracesExcluded': 0, 'eligibleTraceCount': 0,
        'providedTraceCount': 0, 'omittedTraceCount': 0, 'traces': []}
    sources = [('result', result)]
    if isinstance(result, dict) and isinstance(result.get('selectionResult'), dict):
        sources.append(('selectionResult', result['selectionResult']))
    for role, source in sources:
        series = source.get('series') if isinstance(source, dict) else None
        if not isinstance(series, list):
            continue
        output['sourceTraceCount'] += len(series)
        for index, trace in enumerate(series):
            if isinstance(trace, dict) and trace.get('fit') is True:
                output['fitTracesExcluded'] += 1
                continue
            output['eligibleTraceCount'] += 1
            if len(output['traces']) < MAX_TRACES:
                output['traces'].append(_trace(trace, role, index))
    return _bound_summary(output)


def _bound_summary(summary):
    while True:
        summary['providedTraceCount'] = len(summary['traces'])
        summary['omittedTraceCount'] = summary['eligibleTraceCount'] - len(summary['traces'])
        valid = sum(item['status'] == 'available' for item in summary['traces'])
        summary['status'] = ('unavailable' if not valid else
                             'partial' if summary['omittedTraceCount'] or valid != len(summary['traces']) else 'available')
        if len(json.dumps(summary, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')) <= MAX_SUMMARY_BYTES:
            return summary
        summary['traces'].pop()
        summary['omittedForSummaryBudget'] = True


def attach_curve_summary(payload, *, procedure_id=None):
    """Add optional navigation last; discard only new summary if input is full."""
    output = deepcopy(payload)
    if procedure_id not in RABI_PROCEDURES or not isinstance(output.get('observations'), dict):
        return output
    observations = output['observations']
    summary = curve_summary(observations.get('result'))
    observations['curveNavigation'] = summary
    while javascript_json_units_upper_bound(output) > MAX_INPUT_UNITS:
        if summary['traces']:
            summary['traces'].pop()
            summary['omittedForInputBudget'] = True
            _bound_summary(summary)
        else:
            del observations['curveNavigation']
            break
    return output
