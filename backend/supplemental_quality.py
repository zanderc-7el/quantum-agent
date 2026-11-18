"""Pure, opt-in audit of immutable native analysis reports.

No file/device I/O and no mutation of reports, original gates, or native results.
The controller must first verify file hashes, operation identities, acquisition
receipts and the complete calibration context. A receipt here is derived
validation, never a new acquisition or an independent replacement for one.
"""
from copy import deepcopy
import hashlib
from itertools import combinations
import json
import math
from statistics import median

POLICY_VERSION = 'capture-consistency/1'
SUPPORTED_PROCEDURES = ('sq_allxy', 'sq_ramsey', 'sq_echo', 'sq_cpmg')
SCHEMA = 'qcal-supplemental-quality/1'


def hash_payload(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def policy_snapshot():
    return {'version': POLICY_VERSION, 'procedures': list(SUPPORTED_PROCEDURES),
            'allxy': {'rule': 'every_capture_uses_original_allxy_rms_gate', 'completeCircuits': 21,
                      'minimumCaptures': 2, 'newBetweenOperationPhysicalThreshold': False},
            't2': {'rule': 'apply_native_parameter_quality_and_repeat_agreement_to_decay_time',
                   'minimumCaptures': 2, 'normal95Multiplier': 1.96,
                   'nativeMax95HalfWidthFraction': .5, 'nativeRepeatRelativeFloor': .15,
                   'nativeRepeatSigmaMultiplier': 3,
                   'relativeStderrGate': 'unchanged frozen procedure relative_stderr gate',
                   'comparison': 'every pair within each group, including independent operations'},
            'numericComparisonRelativeTolerance': 1e-12,
            'sources': ['adapters/arclight_runtime/analyze_single_qubit.py:fit_signal/_repeat_agreement',
                        'adapters/arclight_runtime/analyze_allxy.py:analyze_arrays',
                        'backend/quality.py:GATES'],
            'semantics': 'New explicit applicability of existing numerical rules; model-conditional diagnostics, not gate fidelity or a simultaneous confidence guarantee.'}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _close(left, right):
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=8*max(math.ulp(left), math.ulp(right)))


class _Checks:
    def __init__(self):
        self.items = []

    def add(self, key, passed, reason, *, missing=False, observed=None, criterion=None):
        item = {'id': key, 'status': 'passed' if passed else 'unverified' if missing else 'failed'}
        if not passed:
            item['reason'] = reason
        if observed is not None:
            item['observed'] = observed
        if criterion is not None:
            item['criterion'] = criterion
        self.items.append(item)
        return passed

    def need(self, key, condition, reason):
        return self.add(key, bool(condition), reason, missing=True)


def _within_gate(checks, key, value, gate):
    if not checks.need(key + '.available', _number(value) and isinstance(gate, dict) and
                       bool(gate) and set(gate) <= {'min', 'max'} and all(_number(v) for v in gate.values()),
                       '缺少有限指标或原冻结数值门槛：' + key):
        return False
    passed = all(value >= bound or _close(value, bound) for name, bound in gate.items() if name == 'min')
    passed &= all(value <= bound or _close(value, bound) for name, bound in gate.items() if name == 'max')
    return checks.add(key, passed, '指标不满足原冻结门槛：' + key, observed=value, criterion=deepcopy(gate))


def _allxy(checks, role, analysis, criteria):
    captures, rows = analysis.get('captures'), analysis.get('points')
    rms = analysis.get('rms_by_capture')
    if not checks.need(role + '.allxy.structure', isinstance(captures, list) and len(captures) >= 2 and
                       all(type(c) is int and c >= 0 for c in captures) and len(set(captures)) == len(captures) and
                       isinstance(rows, list) and isinstance(rms, dict), 'AllXY 缺少至少两个完整 capture 及逐次 RMS'):
        return
    reference, normalization = analysis.get('independent_reference'), analysis.get('normalization')
    checks.need(role + '.allxy.fixed_reference', isinstance(reference, dict) and reference.get('independent_reference') is True and
                bool(reference.get('source')) and isinstance(normalization, dict) and normalization.get('allxy_used_to_train_axis_or_scale') is False,
                'AllXY 未提供独立冻结参考或使用本轮 AllXY 重训投影')
    checks.need(role + '.allxy.capture_keys', set(rms) == {str(c) for c in captures}, '逐 capture RMS 的编号与原始点表不一致')
    checks.need(role + '.allxy.point_count', len(rows) == 21*len(captures), 'AllXY 点表不是每个 capture 各 21 点')
    for capture in captures:
        key = f'{role}.allxy.capture_{capture}'
        points = [p for p in rows if isinstance(p, dict) and p.get('capture') == capture]
        if not checks.need(key + '.complete', len(points) == 21 and
                           {p.get('native_index') for p in points if type(p.get('native_index')) is int} == set(range(21)) and
                           all(_number(p.get('prepared_reference_iq_mean_unclipped')) and _number(p.get('ideal_p1')) for p in points),
                           'AllXY capture 缺少完整 21 个 native_index 或有限未裁剪点值'):
            continue
        computed = math.sqrt(math.fsum((p['prepared_reference_iq_mean_unclipped']-p['ideal_p1'])**2 for p in points)/21)
        reported = rms.get(str(capture))
        checks.need(key + '.reported_rms', _number(reported), '缺少原生逐 capture RMS')
        if _number(reported):
            checks.add(key + '.point_consistency', _close(computed, reported), '逐 capture RMS 与原生点表不一致',
                       observed={'fromPoints': computed, 'reported': reported})
        _within_gate(checks, key + '.rms', computed, criteria.get('allxy_rms'))


def _ci(checks, key, value, interval):
    if not checks.need(key + '.available', _number(value) and value > 0 and isinstance(interval, list) and len(interval) == 2 and
                       all(_number(x) for x in interval) and interval[0] <= value <= interval[1],
                       '缺少正的拟合参数或包含该参数的有限 95% 模型区间'):
        return None
    lower, upper = interval
    if not checks.add(key + '.centered', _close(value-lower, upper-value), '95% 模型区间与原生对称协方差格式不符'):
        return None
    half_width = (upper-lower)/2
    checks.add(key + '.native_width', half_width < .5*value, '95% 模型区间过宽；沿用原生参数半宽小于估计值一半的规则',
               observed={'halfWidth': half_width, 'estimate': value}, criterion={'strictMaxFraction': .5})
    return half_width/1.96


def _t2(checks, role, analysis, criteria):
    found = {}
    groups = analysis.get('groups')
    if not checks.need(role + '.t2.groups', isinstance(groups, list) and bool(groups) and isinstance(analysis.get('x_axis'), dict) and analysis['x_axis'].get('unit') == 's',
                       'T2 缺少按秒计的原生分组拟合'):
        return found
    for index, group in enumerate(groups):
        prefix = f'{role}.t2.group_{index}'
        if not checks.need(prefix + '.structure', isinstance(group, dict) and isinstance(group.get('group_axes'), dict) and
                           isinstance(group.get('repeats'), list) and len(group['repeats']) >= 2,
                           'T2 分组缺少至少两个 capture 及 group_axes'):
            continue
        token = hash_payload(group['group_axes'])
        if not checks.need(prefix + '.unique', token not in found, 'T2 存在重复分组，不能匹配独立复测'):
            continue
        found[token] = []
        checks.need(prefix + '.native_repeats', group.get('repeat_validated') is True, '原生频率／时间重复性尚未通过')
        ids = set()
        for position, repeat in enumerate(group['repeats']):
            key = prefix + f'.repeat_{position}'
            if not checks.need(key + '.structure', isinstance(repeat, dict) and type(repeat.get('repeat')) is int and repeat['repeat'] > 0 and
                               repeat['repeat'] not in ids and isinstance(repeat.get('fit'), dict), 'T2 capture 编号不完整、重复或拟合缺失'):
                continue
            ids.add(repeat['repeat'])
            fit = repeat['fit']
            checks.need(key + '.native_fit', isinstance(repeat.get('diagnostics'), dict) and repeat['diagnostics'].get('rejection_reasons') == [],
                        '原生拟合未给出无拒绝项的有效诊断')
            primary_sigma = _ci(checks, key + '.primary_ci', fit.get('value'), fit.get('ci95_model'))
            stderr = fit.get('standard_error_model')
            checks.need(key + '.primary_stderr', _number(stderr) and stderr >= 0, '缺少有限原生模型标准误')
            if primary_sigma is not None and _number(stderr):
                checks.add(key + '.primary_ci_consistency', _close(primary_sigma, stderr), '原生标准误与 95% 模型区间不一致')
            model = fit.get('model')
            if model == 'damped_sinusoid':
                value, interval = fit.get('decay_time'), fit.get('decay_time_ci95_model')
            elif model == 'exponential':
                value, interval = fit.get('value'), fit.get('ci95_model')
                checks.need(key + '.exponential_parameter', fit.get('parameter') == 'decay_time_in_x_units', '指数拟合没有标明衰减时间参数')
                checks.add(key + '.exponential_decay', _number(value) and _number(fit.get('decay_time')) and _close(fit['decay_time'], value),
                           '指数拟合的 decay_time 与原生主参数不一致')
            else:
                checks.need(key + '.decay_model', False, '该 capture 没有可识别的衰减模型，不能将频率或无衰减振荡当作 T2')
                continue
            sigma = _ci(checks, key + '.decay_ci', value, interval)
            if sigma is None:
                continue
            _within_gate(checks, key + '.decay_relative_stderr', sigma/value, criteria.get('relative_stderr'))
            found[token].append({'role': role, 'repeat': repeat['repeat'], 'value': value, 'stderr': sigma})
        checks.need(prefix + '.complete_fits', len(found[token]) == len(group['repeats']), 'T2 未能为每个 capture 建立衰减时间与误差')
    return found


def _compare_t2(checks, group_key, fits, *, cross=False):
    for index, (left, right) in enumerate(combinations(fits, 2)):
        if cross and left['role'] == right['role']:
            continue
        floor = .15*median([left['value'], right['value']])
        statistical = 3*math.hypot(left['stderr'], right['stderr'])
        tolerance = max(floor, statistical)
        difference = abs(left['value']-right['value'])
        checks.add(f't2.{group_key}.pair_{index}', difference <= tolerance or _close(difference, tolerance),
                   '独立 capture 的 T2 不符合原生 15%／3σ 一致性规则的新衰减时间适用范围',
                   observed={'left': deepcopy(left), 'right': deepcopy(right), 'spread_s': difference},
                   criterion={'tolerance_s': tolerance, 'relativeFloor': .15, 'sigmaMultiplier': 3})


def evaluate_supplemental_quality(procedure_id, stage, reports, *, policy, base_gates, evidence, context):
    """Return a derived receipt. Selection never requires verification data.

    Reports are parsed, integrity-checked platform_analysis.json objects keyed
    by selection/verification. Evidence carries operationId, rawSha256s,
    analysisArtifactSha256 and calibrationContextSha256 for each supplied role.
    Context binds run/resource/base gates/source manifest/module/policy hashes.
    """
    checks = _Checks()
    roles = {'selection': ('selection',), 'verification': ('verification',), 'cross': ('selection', 'verification')}.get(stage, ())
    checks.need('procedure', procedure_id in SUPPORTED_PROCEDURES, '该方法没有已定义的追加验收规则')
    checks.need('stage', bool(roles), '追加验收阶段必须为 selection、verification 或 cross')
    policy_hash = hash_payload(policy_snapshot())
    checks.need('policy', policy == policy_snapshot(), '追加规则快照不是已实现的冻结版本')
    context = context if isinstance(context, dict) else {}
    checks.need('context.identity', all(isinstance(context.get(k), str) and context[k] for k in ('runId', 'resourceId', 'baseGatesVersion')),
                '缺少任务、资源或原规则版本标识')
    for key in ('baseGatesSha256', 'sourceManifestSha256', 'moduleSha256', 'policySha256'):
        checks.need('context.' + key, _sha(context.get(key)), '缺少有效的冻结哈希：' + key)
    checks.add('policy.hash', context.get('policySha256') == policy_hash, '追加规则哈希与已实现规则不一致')
    try:
        gate_hash = hash_payload(base_gates)
    except (TypeError, ValueError):
        gate_hash = None
    checks.need('base_gates.hash', gate_hash is not None and gate_hash == context.get('baseGatesSha256'), '原冻结门槛内容与哈希不一致')
    criteria = base_gates.get(procedure_id, {}) if isinstance(base_gates, dict) else {}
    checks.need('base_gates.criteria', isinstance(criteria, dict) and bool(criteria), '该方法缺少原冻结门槛')
    reports = reports if isinstance(reports, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else {}
    bound, fitted = {}, {}
    for role in roles:
        report, proof = reports.get(role), evidence.get(role)
        if not checks.need(role + '.input', isinstance(report, dict) and isinstance(proof, dict), '缺少本阶段报告或证据引用：' + role):
            continue
        try:
            content_hash = hash_payload(report)
        except (TypeError, ValueError):
            checks.need(role + '.json', False, '原生报告不是有限、可序列化的 JSON 数据')
            continue
        raws = proof.get('rawSha256s')
        proof_ok = isinstance(proof.get('operationId'), str) and bool(proof['operationId']) and isinstance(raws, list) and bool(raws) and all(_sha(v) for v in raws)
        proof_ok &= _sha(proof.get('analysisArtifactSha256')) and _sha(proof.get('calibrationContextSha256'))
        if not checks.need(role + '.evidence', proof_ok, '缺少 operation、原始数据、分析产物或实际校准上下文哈希'):
            continue
        bound[role] = {k: deepcopy(proof[k]) for k in ('operationId', 'rawSha256s', 'analysisArtifactSha256', 'calibrationContextSha256')}
        bound[role]['reportContentSha256'] = content_hash
        analysis, metrics = report.get('analysis'), report.get('metrics')
        if not checks.need(role + '.analysis', isinstance(analysis, dict) and isinstance(metrics, dict), '缺少完整原生 analysis 或 metrics'):
            continue
        checks.need(role + '.raw_binding', analysis.get('raw_sha256') in raws, '原生分析未绑定本 operation 的原始数据哈希')
        checks.need(role + '.source_binding', _sha(analysis.get('source_sha256')), '原生分析缺少源文件哈希；应由控制器进一步核对冻结清单')
        checks.add(role + '.procedure_binding', analysis.get('procedureId') == procedure_id, '原生分析方法与追加验收方法不一致')
        checks.need(role + '.native_accepted', analysis.get('accepted') is True, '原生分析尚未通过，追加验收不能替代它')
        if isinstance(criteria, dict):
            for metric, gate in criteria.items():
                _within_gate(checks, role + '.base.' + metric, metrics.get(metric), gate)
        if procedure_id == 'sq_allxy':
            _allxy(checks, role, analysis, criteria)
        elif procedure_id in SUPPORTED_PROCEDURES:
            fitted[role] = _t2(checks, role, analysis, criteria)
            for group, fits in fitted[role].items():
                _compare_t2(checks, role + '.' + group[:12], fits)
    if stage == 'cross' and checks.need('cross.evidence', set(bound) == {'selection', 'verification'}, '跨轮追加验收需要两个实际 operation 的完整证据'):
        left, right = bound['selection'], bound['verification']
        checks.add('cross.operations', left['operationId'] != right['operationId'], '选择与验证复用了同一 operation')
        checks.add('cross.raw_independence', set(left['rawSha256s']).isdisjoint(right['rawSha256s']), '选择与独立验证复用了原始数据')
        checks.add('cross.calibration_context', left['calibrationContextSha256'] == right['calibrationContextSha256'], '两次采集的实际校准上下文不同，不能比较一致性')
        if procedure_id != 'sq_allxy':
            selected, verified = fitted.get('selection', {}), fitted.get('verification', {})
            if checks.need('cross.t2.groups', bool(selected) and set(selected) == set(verified), '独立复测没有相同的原生 T2 分组'):
                for group in selected:
                    _compare_t2(checks, 'cross.' + group[:12], selected[group] + verified[group], cross=True)
    status = 'failed' if any(c['status'] == 'failed' for c in checks.items) else 'unverified' if any(c['status'] == 'unverified' for c in checks.items) else 'passed'
    binding = {'runId': context.get('runId'), 'resourceId': context.get('resourceId'), 'procedureId': procedure_id,
               'stage': stage, 'policySha256': policy_hash, 'baseGatesSha256': context.get('baseGatesSha256'),
               'sourceManifestSha256': context.get('sourceManifestSha256'), 'moduleSha256': context.get('moduleSha256'), 'evidence': bound}
    return {'schema': SCHEMA, 'ruleVersion': POLICY_VERSION, 'stage': stage, 'procedureId': procedure_id,
            'status': status, 'accepted': status == 'passed', 'baseGatesVersion': context.get('baseGatesVersion'),
            'policyHash': policy_hash, 'evidenceHash': hash_payload(binding), 'binding': binding,
            'checks': checks.items, 'reasons': [c['reason'] for c in checks.items if c['status'] != 'passed'],
            'independenceScope': 'Cross checks require two distinct raw acquisitions; running this pure analysis again is not a new acquisition.',
            'integrityScope': 'Controller must verify file hashes, frozen sources and physical receipts; this function performs no filesystem or device I/O.',
            'acquisitionClaim': 'derived_validation_only', 'requiresIndependentAcquisition': stage != 'cross'}
