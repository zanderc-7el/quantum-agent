"""Frozen diagnostic gates. Measurement completion is not scientific acceptance."""
from copy import deepcopy
from .common import APIError, number
from .decision_context import current_attempt_evidence_ids

GATES = {
    'ro_resonator_survey': {'contrast_snr': {'min': 5}},
    'ro_power_shift': {'contrast_snr': {'min': 5}, 'scan_coverage': {'min': 1, 'max': 1}},
    'sq_spectroscopy': {'peak_snr': {'min': 5}},
    'sq_rabi_amplitude': {'r_squared': {'min': .8}, 'contrast_snr': {'min': 5}, 'cycles': {'min': 1}},
    'sq_rabi_time': {'r_squared': {'min': .8}, 'contrast_snr': {'min': 5}, 'cycles': {'min': 1}},
    'sq_allxy': {'allxy_rms': {'min': 0, 'max': .2}},
    'ro_iq01': {'assignment_fidelity': {'min': .5, 'max': 1}},
    'ro_optimize': {'assignment_fidelity': {'min': .5, 'max': 1}},
    'sq_t1': {'r_squared': {'min': .8}, 'relative_stderr': {'max': .3}, 't1_s': {'min': 1e-9}},
    'sq_cpmg': {'r_squared': {'min': .8}, 'relative_stderr': {'max': .3}},
    'sq_ramsey': {'r_squared': {'min': .8}, 'relative_stderr': {'max': .3}},
    'sq_echo': {'r_squared': {'min': .8}, 'relative_stderr': {'max': .3}},
    'sq_rb': {'r_squared': {'min': .8}, 'gate_fidelity': {'min': .5, 'max': 1}, 'reference_drift_fraction': {'max': .1}},
}

def evaluate(result, procedure, mode, gates=None, independent=False):
    result = deepcopy(result)
    if mode == 'replay':
        result['dataKind'] = 'historical'
        result.setdefault('quality', {'status': 'unverified', 'reasons': ['历史记录，仅用于回放，不是本轮独立验收']})
        return result
    reasons, missing = [], []
    if result.get('dataKind') != 'real':
        raise APIError('SOURCE_MISMATCH', '真机任务拒绝仿真或历史数据')
    if not result.get('artifacts'):
        missing.append('缺少原始采集产物')
    if not result.get('acquiredAt'):
        missing.append('缺少采集时间')
    metrics = result.get('metrics', {})
    native = result.get('analysisQuality')
    if isinstance(native, dict) and native.get('accepted') is False:
        details = native.get('reasons', [])
        reasons.extend(str(reason) for reason in details if isinstance(reason, str))
        if not details:
            reasons.append('原生分析未接受本次采集')
    rules = (gates or GATES).get(procedure, {})
    if not rules:
        missing.append('该方法尚未定义可执行验收指标')
    for name, rule in rules.items():
        value = metrics.get(name)
        if not number(value):
            missing.append('缺少指标 ' + name)
        elif value < rule.get('min', -float('inf')) or value > rule.get('max', float('inf')):
            reasons.append(f'{name}={value:g} 不满足 {rule}')
    if not independent:
        missing.append('需要独立采集复测')
    result['quality'] = {'status': 'failed' if reasons else ('unverified' if missing else 'passed'),
                         'reasons': reasons + missing, 'ruleVersion': 'diagnostic-gates/1',
                         'criteria': rules, 'note': '这些是诊断门槛；不代表用户目标保真度已达标'}
    return result

def validate_proposal(proposal, workflow, node, bounds):
    if not isinstance(proposal, dict):
        raise APIError('BAD_DECISION', '模型决策必须是结构化对象')
    if set(proposal) - {'action', 'reason', 'evidenceIds', 'parameterChanges', 'targetNodeId', 'ruleId'}:
        raise APIError('BAD_DECISION', '模型决策包含未允许字段')
    if not isinstance(proposal.get('evidenceIds', []), list) or any(not isinstance(e, str) for e in proposal.get('evidenceIds', [])):
        raise APIError('BAD_EVIDENCE', '证据引用必须为编号列表')
    if proposal.get('action') not in ('continue', 'retry', 'backtrack', 'wait', 'stop'):
        raise APIError('BAD_DECISION', '未知决策动作')
    if not isinstance(proposal.get('reason'), str) or not proposal['reason'].strip():
        raise APIError('BAD_DECISION', '决策缺少证据说明')
    known = set(current_attempt_evidence_ids(node.get('result', {})))
    if any(e not in known for e in proposal.get('evidenceIds', [])):
        raise APIError('BAD_EVIDENCE', '决策引用了当前节点之外的证据')
    changes = proposal.get('parameterChanges', {})
    if not isinstance(changes, dict):
        raise APIError('BAD_PARAMETERS', '参数修改必须是键值对象')
    for name, value in changes.items():
        if isinstance(value, dict):
            value = value.get('to')
        rule = bounds.get(name)
        if rule and 'enum' in rule:
            if isinstance(value, (dict, list)) or value not in rule['enum']:
                raise APIError('PARAMETER_OUT_OF_RANGE', f'{name} 不在该方法允许选项内')
            continue
        if not rule or not number(value) or not rule['min'] <= value <= rule['max']:
            raise APIError('PARAMETER_OUT_OF_RANGE', f'{name} 超出该方法允许范围')
        if rule.get('integer') and int(value) != value:
            raise APIError('PARAMETER_OUT_OF_RANGE', f'{name} 必须为整数')
    if proposal['action'] == 'continue' and node.get('result', {}).get('quality', {}).get('status') != 'passed':
        raise APIError('GATE_NOT_PASSED', '指标未通过，模型不能直接推进')
    if proposal['action'] == 'backtrack':
        ids = {n['id'] for n in workflow['nodes']}
        target = proposal.get('targetNodeId')
        if target not in ids or target == node['id']:
            raise APIError('BAD_BACKTRACK', '回退目标不是有效前置节点')
        ancestry = set(node['dependsOn'])
        while True:
            expanded = ancestry | {d for n in workflow['nodes'] if n['id'] in ancestry for d in n['dependsOn']}
            if expanded == ancestry:
                break
            ancestry = expanded
        if target not in ancestry:
            raise APIError('BAD_BACKTRACK', '仅允许回退到当前节点的前置依赖')
    return proposal
