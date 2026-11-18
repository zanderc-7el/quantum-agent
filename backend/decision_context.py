"""Pure model-context projection; stored results/history are never modified.

The caller supplies the current node's registered, current-attempt result. This
module does not authenticate files or acquire data. Only its direct artifacts
and its direct selectionResult artifacts are evidence for this decision. Keep
controller proposal validation on the same current_attempt_evidence_ids helper.

Legacy agents recursively treat generic ``id`` fields as evidence. Strip those
fields from observations/history before restoring IDs at the two trusted artifact
locations, so their existing collector and inspect_observation agree exactly.
"""
from ast import literal_eval
from copy import deepcopy
import math
import re
from statistics import median


_EVIDENCE_KEYS = frozenset(('id', 'artifactId', 'evidenceId', 'attemptId', 'evidenceIds', 'allowedEvidenceIds'))
_UNSAFE_KEYS = frozenset(('__proto__', 'prototype', 'constructor'))
_HISTORY_FIELDS = frozenset(('action', 'reason', 'parameterChanges', 'requestedParameterChanges',
                             'normalization', 'ruleId', 'rule'))
_CAPTURE_LABEL = re.compile(r'^capture\s+([1-9][0-9]*)\s+(\{.*\})$')


def _without_evidence_keys(value):
    if isinstance(value, dict):
        return {key: _without_evidence_keys(item) for key, item in value.items()
                if key not in _EVIDENCE_KEYS and key not in _UNSAFE_KEYS}
    if isinstance(value, list):
        return [_without_evidence_keys(item) for item in value]
    return deepcopy(value)


def _current_results(result):
    if not isinstance(result, dict):
        return []
    values = [('result', result)]
    if isinstance(result.get('selectionResult'), dict):
        values.append(('selectionResult', result['selectionResult']))
    return values


def _artifacts(result):
    items = result.get('artifacts')
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def current_attempt_evidence_ids(result):
    """Sorted current/selection artifact IDs; never recursively collect metadata."""
    return sorted({item['id'] for _, value in _current_results(result) for item in _artifacts(value)
                   if isinstance(item.get('id'), str) and item['id'].strip()})


def _restore_artifact_ids(original, sanitized):
    artifacts = []
    for item in _artifacts(original):
        projected = _without_evidence_keys(item)
        if isinstance(item.get('id'), str) and item['id'].strip():
            projected['id'] = item['id']
        artifacts.append(projected)
    if 'artifacts' in original:
        sanitized['artifacts'] = artifacts


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _power_diagnostics(result):
    """Explain saved native series only; do not replace or change acceptance."""
    output = {
        'schema': 'qcal-power-series-explanation/1',
        'scope': 'derived_diagnostic_only_not_acceptance',
        'source': 'current_result.series; no new acquisition or raw-file read',
        'sourceEvidenceIds': current_attempt_evidence_ids({
            'artifacts': result.get('artifacts', [])}),
        'repeatCentersMeaning': ('analysisQuality.repeat_centers_hz is one mean cavity center per power group, '
                                 'aligned with powers_waveform_scale; it is not one center per capture.'),
        'method': 'Native edge-median linear baseline minus |mean IQ|; first maximum depth, no smoothing.',
        'acceptanceUnchanged': True,
        'groups': [],
        'issues': [],
    }
    grouped = {}
    series = result.get('series', [])
    if not isinstance(series, list):
        series = []
    for trace in series:
        if not isinstance(trace, dict) or trace.get('fit') is True:
            continue
        label = trace.get('name', trace.get('label'))
        match = _CAPTURE_LABEL.fullmatch(label) if isinstance(label, str) and len(label) <= 4096 else None
        if not match:
            output['issues'].append('Saved series lacks the native capture/power label.')
            continue
        try:
            axes = literal_eval(match.group(2))
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            axes = None
        powers = [value for key, value in axes.items() if isinstance(key, str) and key.endswith('.para.probCarryScale')] if isinstance(axes, dict) else []
        x, y = trace.get('x'), trace.get('y')
        if (len(powers) != 1 or len(axes) != 1 or not _finite(powers[0]) or not isinstance(x, list) or not isinstance(y, list)
                or len(x) != len(y) or len(x) < 3 or not all(_finite(v) for v in x + y)
                or any(b <= a for a, b in zip(x, x[1:])) or trace.get('xUnit', 'Hz') != 'Hz'):
            output['issues'].append('Saved series has invalid power, frequency grid, or finite IQ values.')
            continue
        edge = max(2, len(y)//8)
        left, right = median(y[:edge]), median(y[-edge:])
        step = (right-left)/(len(y)-1)
        baseline = [left + i*step for i in range(len(y))]
        baseline[-1] = right
        depth = [b-v for b, v in zip(baseline, y)]
        peak = max(range(len(depth)), key=depth.__getitem__)
        capture = int(match.group(1))
        rows = grouped.setdefault(powers[0], [])
        if any(row['capture'] == capture for row in rows):
            output['issues'].append('Duplicate capture label in a power group; group is not a unique capture set.')
        rows.append({'capture': capture, 'centerHz': x[peak], 'dipIndex': peak,
                     'dipDepth': depth[peak], 'onScanBoundary': peak in (0, len(x)-1),
                     'frequencyStepHz': median(b-a for a, b in zip(x, x[1:])),
                     '_grid': x})
    for power, rows in sorted(grouped.items()):
        same_grid = all(row['_grid'] == rows[0]['_grid'] for row in rows)
        centers = [row['centerHz'] for row in rows]
        spread = max(centers)-min(centers)
        unique_captures = len({row['capture'] for row in rows}) == len(rows)
        step = rows[0]['frequencyStepHz'] if same_grid else None
        threshold = max(5e4, 2*step) if step is not None else None
        if not same_grid:
            output['issues'].append('Captures within one power group use different frequency grids.')
        output['groups'].append({
            'powerWaveformScale': power, 'captureCount': len(rows),
            'captures': [{k: v for k, v in row.items() if k != '_grid'} for row in sorted(rows, key=lambda row: row['capture'])],
            'meanCenterHz': sum(centers)/len(centers), 'spreadHz': spread,
            'sameFrequencyGrid': same_grid, 'uniqueCaptureLabels': unique_captures,
            'frequencyStepHz': step, 'nativeRepeatThresholdHz': threshold,
            'exceedsNativeRepeatThreshold': spread > threshold if threshold is not None and unique_captures else None,
        })
    output['status'] = 'unavailable' if not output['groups'] else ('partial' if output['issues'] else 'available')
    output['limitations'] = ('Uses saved amplitude curves only. Does not reconstruct noise/SEM or prove acquisition identity. '
                             'The native recorded quality/reasons remain authoritative. Differences between power-group '
                             'means are not within-power capture drift and alone do not justify invalidating S21.')
    return output


def build_decision_context(payload, *, procedure_id=None):
    """Return a new payload whose legacy-agent evidence set equals the allowlist.

    Top-level rules, parameter bounds and run/revision binding stay intact.
    Current result data and direct selection data remain inspectable, but only
    direct artifact IDs are valid references. History is explanatory only.
    """
    if not isinstance(payload, dict):
        raise TypeError('Decision payload must be a dict')
    output = deepcopy(payload)
    observations = payload.get('observations')
    observations = observations if isinstance(observations, dict) else {}
    clean = _without_evidence_keys(observations)
    result = observations.get('result')
    for role, original in _current_results(result):
        destination = clean['result'] if role == 'result' else clean['result']['selectionResult']
        _restore_artifact_ids(original, destination)
    history = payload.get('history', [])
    output['history'] = [_without_evidence_keys({key: value for key, value in item.items() if key in _HISTORY_FIELDS})
                         for item in history if isinstance(item, dict)] if isinstance(history, list) else []
    output['allowedEvidenceIds'] = current_attempt_evidence_ids(result)
    clean['evidenceScope'] = {
        'schema': 'qcal-current-attempt-evidence/1',
        'source': 'Current result.artifacts plus direct current result.selectionResult.artifacts only.',
        'history': 'Prior decisions are explanatory, not usable evidence IDs for this decision.',
        'notice': 'Only allowedEvidenceIds may be cited. inspect_observation resolves their artifact records. '
                  'Current curves/metrics belong to the enclosing result or selectionResult respectively.',
    }
    if procedure_id == 'ro_power_shift' and isinstance(result, dict):
        clean['powerSeriesDiagnostics'] = _power_diagnostics(result)
        if isinstance(result.get('selectionResult'), dict):
            clean['selectionPowerSeriesDiagnostics'] = _power_diagnostics(result['selectionResult'])
    output['observations'] = clean
    return output
