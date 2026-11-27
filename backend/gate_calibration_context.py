"""Admission of native fixed gates calibrated by amplitude Rabi alone.

This checks existing receipts; it neither publishes node overrides nor changes
historical acceptance. Independent time Rabi supplies the width when it is part
of the actual dependency graph; amplitude-only chains keep their fixed width.
"""
from copy import deepcopy
import json

from .common import APIError, checked_path, digest, file_hash, number

VERSION = 'native-rabi-fixed-gate-context/1'
CONSUMERS = frozenset(('ro_iq01', 'ro_optimize', 'ro_optimize_frequency', 'ro_s21_ge',
    'sq_t1', 'sq_ramsey', 'sq_echo', 'sq_cpmg', 'sq_allxy', 'sq_rb'))
# These are the inputs actually applied to U_gates/XY by platform_specs.
# Readout settings and the unused half_pi_amp metadata are not gate identity.
GATE_KEYS = ('drive_hz','drive_amp','drive_length_s','xy_attenuation_db','xy_filter_mhz','pulse_compatibility')
GATE_LABELS = {'drive_hz':'驱动频率','drive_amp':'驱动幅度','drive_length_s':'门长',
    'xy_attenuation_db':'XY 衰减','xy_filter_mhz':'XY 滤波带宽','pulse_compatibility':'脉冲策略'}
ADAPTERS = frozenset(('adapters.arclight.ArcLightAdapter',
    'adapters.arclight_dynamic.DynamicArcLightAdapter'))
REMEDY = '请按当前工作点重新完成 Rabi 标定及独立复测；若需改变门长，请使用包含独立时间 Rabi 的完整流程。'


def _require(condition, reason):
    if not condition:
        raise APIError('GATE_CALIBRATION_EVIDENCE_REQUIRED', 'Rabi 标定的原生门条件无法核对：' + reason + '。' + REMEDY, 409)


def _ancestors(wf, node):
    by_id = {n['id']: n for n in wf['nodes']}
    seen, pending = set(), list(node.get('dependsOn', []))
    while pending:
        key = pending.pop()
        _require(key in by_id and key != node['id'], '前置图不完整')
        if key not in seen:
            seen.add(key); pending.extend(by_id[key].get('dependsOn', []))
    return [by_id[key] for key in seen if by_id[key].get('resourceId') == node['resourceId']]


def _load(path):
    path = checked_path(path)
    _require(path.is_file() and path.stat().st_size <= 32 * 1024 * 1024, '缺少有界原生回执文件')
    return json.loads(path.read_text(encoding='utf-8'))


def _projection(result):
    keys = ('dataKind','sourceRunId','acquiredAt','parameters','metrics','candidateParameters',
            'series','analysisQuality','provenance','methodLabel')
    value = {key: deepcopy(result.get(key)) for key in keys}
    value['artifacts'] = sorted(({key:a.get(key) for key in ('id','name','kind','sha256','sourcePath')}
        for a in result.get('artifacts', [])), key=lambda a:(a['id'],a['name']))
    return value


def _operation(controller, rt, wf, prior, attempt, op, stage):
    if attempt.get('executionBinding'):
        controller = controller.for_attempt(wf['id'], attempt, rt)
        wf = {**wf, 'adapterId':controller.adapter_id, 'adapterProfileHash':controller.adapter_profile_hash}
    row = rt.db.execute("SELECT request,result FROM platform_effects WHERE workflow_id=? AND attempt_id=? AND operation_id=? AND state='completed'",
        (wf['id'], attempt['id'], op)).fetchone()
    _require(row is not None, '缺少已完成的独立 ' + stage + ' effect')
    request, result = json.loads(row['request']), json.loads(row['result'])
    if attempt.get('executionBinding'):
        from .attempt_execution import validate_request
        validate_request(controller, rt, request)
    identity = {'runId':wf['id'],'nodeId':prior['id'],'attemptId':attempt['id'],
        'resourceId':prior.get('resourceId'),'procedureId':prior['procedureId'],'operationId':op,
        'adapterId':wf['adapterId'],'adapterProfileHash':wf['adapterProfileHash']}
    _require(all(request.get(k) == v for k,v in identity.items()), 'effect 身份或执行配置不一致')
    directory = checked_path(controller.adapter._operation_dir(op))
    intent, receipt = _load(directory/'adapter_intent.json'), _load(directory/'adapter_receipt.json')
    plan, completion = _load(directory/'plan.json'), _load(directory/'completion.json')
    from adapters.f8 import _digest as native_digest
    canonical = {k:v for k,v in request.items() if k != 'outputDir'}
    _require(intent.get('operationId') == op and intent.get('request') == canonical
        and intent.get('requestHash') == native_digest(canonical)
        and intent.get('profileHash') == native_digest({**controller.profile, 'enabled':True}), '原生 intent 与冻结 effect 不一致')
    _require(receipt.get('status') == 'completed' and receipt.get('operationId') == op
        and receipt.get('requestHash') == native_digest(canonical), '原生完成回执身份不一致')
    file_limit = 4096
    if attempt.get('executionBinding'):
        from adapters.arclight_rabi16_runtime.policy import MAX_REGISTERED_ARTIFACTS
        file_limit = MAX_REGISTERED_ARTIFACTS
    _require(completion.get('state') == 'completed' and completion.get('shutdown_ok') is True
        and isinstance(completion.get('files'), list) and len(completion['files']) <= file_limit, '原生结束回执不完整')
    _require(plan == controller.adapter.build_plan(request), '原生实际计划与原请求不一致')
    native = receipt.get('result', {})
    _require(_projection(native) == _projection(result), '原生采集产物与平台 effect 不一致')
    if attempt.get('executionBinding'):
        from .attempt_execution import sampling_evidence
        sampling_evidence(controller,rt,request,result,plan=plan,completion=completion)
    analysis = completion.get('analysis', {})
    expected = {'parameters':plan['parameters'],'acquiredAt':completion.get('acquiredAt'),
        'metrics':analysis.get('metrics',{}),'candidateParameters':analysis.get('candidateParameters',{}),
        'series':analysis.get('series',[]),'analysisQuality':analysis.get('analysis',{})}
    _require(all(native.get(key) == value for key,value in expected.items())
        and native.get('provenance',{}).get('freshAcquisition') is True
        and native['provenance'].get('operationId') == op, '原生完成记录与实际参数不一致')
    raw = set(); plan_bound = False
    for artifact in result.get('artifacts', []):
        if artifact.get('kind') != 'raw' and artifact.get('name') != 'plan.json': continue
        registered = rt.db.execute('SELECT path,sha256,payload FROM platform_artifacts WHERE id=?', (artifact.get('id'),)).fetchone()
        _require(registered is not None and registered['sha256'] == artifact.get('sha256'), '原始文件或计划未登记')
        path = checked_path(registered['path'], parent=directory)
        _require(file_hash(path) == artifact['sha256'], '原始文件或计划摘要改变')
        metadata = json.loads(registered['payload'])
        _require(all(metadata.get(k) == artifact.get(k) for k in ('id','name','kind','sha256')), '登记产物身份改变')
        if artifact.get('kind') == 'raw': raw.add(artifact['sha256'])
        elif path == checked_path(directory/'plan.json'): plan_bound = True
    _require(raw and plan_bound and result.get('dataKind') == 'real' and result.get('acquiredAt')
        and result.get('sourceRunId') == op and not result.get('partial'), '缺少独立原始数据或实际计划证据')
    return result, raw


def require_current_gate(controller, rt, wf, node, parameters):
    adapter_type = type(controller.adapter)
    implementation = adapter_type.__module__ + '.' + adapter_type.__qualname__
    if wf.get('mode') != 'real' or implementation not in ADAPTERS or node.get('procedureId') not in CONSUMERS:
        return
    ancestors = _ancestors(wf, node)
    amplitude = [n for n in ancestors if n.get('procedureId') == 'sq_rabi_amplitude']
    # Ordinary prerequisite admission handles pending nodes. Pure budget previews
    # do not turn an unexecuted graph into a claim of accepted calibration.
    if not amplitude or not any(n.get('engineState') == 'verified' for n in amplitude): return
    timing = [n for n in ancestors if n.get('procedureId') == 'sq_rabi_time']
    sources = timing or amplitude
    try:
        _require(len(sources) == 1, '幅度校准来源不唯一')
        prior = sources[0]; procedure = prior['procedureId']; result = prior.get('result') or {}
        row = rt.db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=? AND node_id=? AND id=?',
            (wf['id'], prior['id'], prior.get('latestAttemptId'))).fetchone()
        _require(row is not None, '缺少当前幅度标定尝试')
        attempt = json.loads(row[0])
        _require(attempt.get('mode') == 'real' and attempt.get('procedureId') == procedure and prior.get('engineState') == 'verified'
            and attempt.get('nodeId') == prior['id'] and attempt.get('state') == 'verified'
            and attempt.get('endedAt') and attempt.get('result') == result
            and result.get('quality', {}).get('status') == 'passed'
            and result['quality'].get('criteria') == wf['gates'][procedure]
            and result['quality'].get('ruleVersion') == wf['gatesVersion']
            and not any(result.get(k) for k in ('partial','stale','verificationPending')), '幅度标定不是当前独立验收结果')
        operations = (attempt.get('operationId'), attempt.get('verificationOperationId'))
        _require(operations == (attempt['id']+'/measure', attempt['id']+'/verify'), '缺少不同的首测和独立复测')
        # Reject an already inconsistent saved gate before reading potentially
        # large ADC files. This early refusal does not grant source eligibility.
        selection_saved = result.get('selectionResult', {})
        candidate = controller._normalize_candidate(procedure, selection_saved.get('candidateParameters', {}))
        _require(candidate == result.get('candidateParameters') and number(candidate.get('drive_length_s' if timing else 'drive_amp')), '已提交候选与首测记录不一致')
        expected = deepcopy(selection_saved.get('parameters', {}))
        outputs = ('drive_length_s',) if timing else ('drive_amp',)
        for key in GATE_KEYS:
            if key in outputs: continue
            _require(key in expected and controller._same_parameter(expected[key], result.get('parameters', {}).get(key)), '首复测固定门条件不一致：'+GATE_LABELS[key])
        expected.update(candidate)
        mismatches = [key for key in GATE_KEYS
            if key not in expected or key not in parameters or not controller._same_parameter(expected[key], parameters[key])]
        if mismatches:
            source_width, target_width = expected.get('drive_length_s'), parameters.get('drive_length_s')
            label = '时间 Rabi' if timing else '幅度 Rabi'
            if 'drive_length_s' in mismatches and number(source_width) and number(target_width):
                message = f'{label} 验证门长 {source_width*1e9:g} ns，本步设置 {target_width*1e9:g} ns。'
                remaining = [GATE_LABELS[key] for key in mismatches if key != 'drive_length_s']
                if remaining: message += '另有门条件不一致：'+'、'.join(remaining)+'。'
            else:
                message = label+'与本步的'+'、'.join(GATE_LABELS[key] for key in mismatches)+'不一致。'
            raise APIError('GATE_CALIBRATION_CONTEXT_MISMATCH', message+REMEDY, 409)
        selection, raw1 = _operation(controller, rt, wf, prior, attempt, operations[0], '首测')
        verification, raw2 = _operation(controller, rt, wf, prior, attempt, operations[1], '复测')
        _require(not raw1 & raw2, '首测与复测重复使用原始文件')
        for stored, original in ((selection_saved, selection), (result, verification)):
            _require(all(stored.get(k) == original.get(k) for k in ('sourceRunId','acquiredAt','parameters','metrics','series','artifacts')), '验收结果与原始采集不一致')
        _require(candidate == controller._normalize_candidate(procedure, selection.get('candidateParameters', {})), '候选与原生首测不一致')
        _require(result.get('verificationCandidateParameters') == verification.get('candidateParameters'), '独立复测候选记录不一致')
        return {'policyVersion':VERSION,'sourceProcedureId':procedure,'sourceAttemptId':attempt['id'],'selectionOperationId':operations[0],
            'verificationOperationId':operations[1],'parametersSha256':digest({k:expected[k] for k in GATE_KEYS}),'rawSha256':sorted(raw1|raw2)}
    except APIError as exc:
        if exc.code.startswith('GATE_CALIBRATION_'): raise
        _require(False, exc.code+'：'+exc.message)
    except Exception as exc:
        _require(False, type(exc).__name__+'：'+str(exc))
