"""Read-only report labels from frozen parameters, never execution or calibration."""
from datetime import datetime, timedelta, timezone
import math

MISSING = '采集参数来源不足，未推断工作点'
FIELDS = {
    'readout_hz': ('读出频率', 1e-9, 'GHz'),
    'readout_amp': ('读出幅度', 1, 'a.u.'),
    'readout_width_s': ('读出时长', 1e6, 'μs'),
    'drive_hz': ('驱动频率', 1e-9, 'GHz'),
    'drive_amp': ('驱动幅度', 1, 'a.u.'),
    'drive_length_s': ('门长', 1e9, 'ns'),
}


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    except (AttributeError, TypeError, ValueError):
        pass
    return None


def parameter_text(key, value):
    label, scale, unit = FIELDS[key]
    return f'{label} {value * scale:.9g} {unit}'


def committed_workpoints(report):
    snapshot = report.get('committedParameters') or {}
    if snapshot.get('schema') != 'qcal-report-committed-parameters/1':
        return []
    result = []
    for resource, values in snapshot.get('resources', {}).items():
        selected = {k: v for k, v in values.items() if k in FIELDS and isinstance(v, dict)
                    and finite(v.get('value')) and type(v.get('revision')) is int and v['revision'] >= 0}
        if selected:
            result.append({'resourceId': resource, 'parameters': selected,
                           'summary': '；'.join(parameter_text(k, selected[k]['value']) for k in FIELDS if k in selected)})
    return result


def acquisition_context(node, attempt, result):
    """Actual stage parameters only. Scan axes override unused profile defaults."""
    attempt_id = attempt.get('id')
    operation = result.get('sourceRunId')
    p = result.get('parameters')
    acquired = stamp(result.get('acquiredAt'))
    if (not isinstance(attempt_id, str) or attempt.get('nodeId') != node.get('id')
            or operation not in (attempt_id+'/measure', attempt_id+'/verify')
            or operation != attempt.get('verificationOperationId' if operation.endswith('/verify') else 'operationId')
            or not isinstance(p, dict) or not p or not acquired or result.get('dataKind') != 'real'):
        return {'workpoint': MISSING, 'acquiredAt': result.get('acquiredAt'), 'time': acquired or '时间未记录',
                'operationId': None, 'parameterSource': None}
    proc = node.get('procedureId')
    keys = ['readout_hz', 'readout_amp', 'readout_width_s']
    details = []
    def axis(label, start, stop, scale, unit, array_key=None):
        values = p.get(array_key) if array_key else None
        if isinstance(values, list) and values and all(finite(v) for v in values):
            details.append(f'{label} {min(values)*scale:.9g}–{max(values)*scale:.9g} {unit}，{len(values)} 点')
        elif array_key and array_key in p:
            details.append(label+'（显式扫描点来源不足）')
        elif finite(p.get(start)) and finite(p.get(stop)):
            points = f'，{p["points"]} 点' if type(p.get('points')) is int else ''
            details.append(f'{label} {p[start]*scale:.9g}–{p[stop]*scale:.9g} {unit}{points}')
        else:
            details.append(label+'（范围未记录）')
    def frequency(label, key, endpoints=False):
        if 'frequencies_hz' in p or (endpoints and 'start_hz' in p and 'stop_hz' in p):
            axis(label+'扫描', 'start_hz', 'stop_hz', 1e-9, 'GHz', 'frequencies_hz')
        elif finite(p.get(key)):
            # These are archived reference parameters; never infer a center from an overriding axis.
            details.append(f'{label}配置参考 {p[key]*1e-9:.9g} GHz')
    if proc in ('ro_resonator_survey', 'ro_power_shift'):
        keys.remove('readout_hz')
        frequency('腔频率', 'readout_hz')
        if proc == 'ro_power_shift':
            keys.remove('readout_amp')
            if 'powers' in p:
                axis('读出幅度扫描', 'unused_start', 'unused_stop', 1, 'a.u.', 'powers')
            else:
                details.append('读出幅度扫描（各档见原图）')
    elif proc == 'ro_optimize':
        keys.remove('readout_amp')
        values = p.get('amplitudes')
        if isinstance(values, list) and values and all(finite(v) for v in values):
            details.append('读出幅度 '+f'{values[0]:.9g} a.u.' if len(values) == 1 else
                           f'读出幅度扫描 {min(values):.9g}–{max(values):.9g} a.u.，{len(values)} 档')
        else:
            axis('读出幅度扫描', 'amp_start', 'amp_stop', 1, 'a.u.')
        keys += ['drive_hz', 'drive_amp', 'drive_length_s']
    elif proc == 'sq_spectroscopy':
        keys += ['drive_amp']
        frequency('驱动频率', 'drive_hz', endpoints=True)
        if finite(p.get('spectroscopy_width_s')):
            details.append(f'谱激励时长 {p["spectroscopy_width_s"]*1e6:.9g} μs')
    elif proc == 'sq_rabi_amplitude':
        keys += ['drive_hz', 'drive_length_s']
        axis('驱动幅度扫描', 'amp_start', 'amp_stop', 1, 'a.u.', 'amplitudes')
    elif proc == 'sq_rabi_time':
        keys += ['drive_hz', 'drive_amp']
        axis('门长扫描', 'start_s', 'stop_s', 1e6, 'μs')
    elif proc in ('ro_iq01', 'sq_t1', 'sq_ramsey', 'sq_echo', 'sq_cpmg', 'sq_allxy', 'sq_rb'):
        keys += ['drive_hz', 'drive_amp', 'drive_length_s']
    else:
        details.append('其余控制参数见本次归档')
    parts = [parameter_text(k, p[k]) for k in keys if finite(p.get(k))] + details
    brief = [parameter_text(k, p[k]) for k in keys if k in ('readout_amp', 'drive_length_s') and finite(p.get(k))]
    if proc in ('ro_optimize', 'ro_power_shift'):
        brief += [v for v in details if v.startswith('读出幅度')]
    if proc == 'sq_rabi_time':
        brief.append('门长扫描，范围见采集轮次表')
    return {'workpoint': '；'.join(parts) or MISSING, 'acquiredAt': result['acquiredAt'], 'time': acquired,
            'brief': '；'.join(brief),
            'operationId': operation, 'parameterSource': 'result.parameters',
            'parameters': {k: p[k] for k in keys if finite(p.get(k))}}


def failed_rabi_summary(evidence):
    valid = [c['index'] for c in evidence['captures'] if c['valid']]
    invalid = [c['index'] for c in evidence['captures'] if not c['valid']]
    role = '独立复测' if evidence['stage'] == 'verify' else '首测'
    failed = '、'.join(f'第{i}次' for i in invalid)
    return (f'{role}中 {len(valid)}/{len(evidence["captures"])} 次重复拟合有效；{failed}未获得合格拟合。'
            f'本次未通过，需至少 {evidence["requiredValid"]} 次有效且全部重复一致，并完成独立复测。')


def qualify_fit_metrics(rows, evidence):
    valid = [c['index'] for c in evidence['captures'] if c['valid']]
    prefix = f'第{valid[0]}次重复拟合：' if len(valid) == 1 else '部分有效重复的汇总：'
    # Native summary metrics exclude failed fits. They cannot stand for all repeats.
    return [{**r, 'label': prefix+r['label']} for r in rows]
