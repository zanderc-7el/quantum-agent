"""Frozen diagnostic gates. Measurement completion is not scientific acceptance."""
from copy import deepcopy
from .common import APIError, number, digest
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

SCIENCE_GATES_VERSION = 'science-diagnostic-gates/3'
SCIENCE_GATES = {
    'sq_shape_rabi_amplitude': {'r_squared': {'min': .8}, 'contrast_snr': {'min': 5}, 'cycles': {'min': 1}},
    'sq_shape_half_verify': {'half_rotation_rms': {'min': 0, 'max': .15}, 'contrast_snr': {'min': 5}},
    'sq_drag_scan': {'diagnostic_improvement': {'min': 0}, 'r_squared': {'min': .8}, 'curvature_z': {'min': 3}},
    'sq_shelve_spectrum12': {'peak_snr': {'min': 5}, 'r_squared': {'min': .8}, 'center_stderr_hz': {'min': 0}},
    'sq_shelve_rabi12': {'r_squared': {'min': .8}, 'contrast_snr': {'min': 5}, 'cycles': {'min': 1}},
    'sq_shelve_readout_compare': {'assignment_fidelity': {'min': .5, 'max': 1},
        'assignment_gain_pp': {'min': -100, 'max': 100}, 'heldout_captures': {'min': 2},
        'paired_capture_gain_stderr_pp': {'min': 0}, 'conditional_shift_snr': {'min': 5},
        'ground_disturbance_ratio': {'min': 0, 'max': .2}},
}
SCIENCE_CANDIDATE_METHODS = frozenset(('sq_shape_rabi_amplitude_repeat5','sq_shape_rabi_amplitude', 'sq_drag_scan',
                                     'sq_shelve_spectrum12', 'sq_shelve_rabi12', 'sq_drag_half_scan'))
SCIENCE_IRB_GATES_VERSION = 'science-drag-xhalf-irb-gates/1'
SCIENCE_IRB_GATES = {
    'sq_drag_half_scan':deepcopy(SCIENCE_GATES['sq_drag_scan']),
    'sq_drag_irb':{'r_squared':{'min':.95},'gate_fidelity':{'min':.5,'max':1},
        'clifford_fidelity':{'min':.5,'max':1},'reference_drift_fraction':{'min':0,'max':.1},
        'reference_drift_upper_diagnostic':{'min':0,'max':.1},
        'independent_sequences_per_length':{'min':20},'rb_captures':{'min':2}},
}
SCIENCE_FLUX_GATES_VERSION = 'science-flux-measured-restore-gates/1'
_FLUX_COVERAGE={'scan_coverage':{'min':1,'max':1},'bias_readback_coverage':{'min':1,'max':1},'restoration_verified':{'min':1,'max':1}}
SCIENCE_FLUX_GATES = {'ro_flux_scan':{**deepcopy(_FLUX_COVERAGE),'resolved_bias_points':{'min':1}},
    'sq_flux_point_recheck':{**deepcopy(_FLUX_COVERAGE),'local_cavity_contrast_snr':{'min':5}}}
SCIENCE_FLUX_COMMAND_GATES_VERSION = 'science-flux-commanded-return-gates/1'
_FLUX_COMMAND_COVERAGE={'scan_coverage':{'min':1,'max':1},'bias_command_coverage':{'min':1,'max':1},'declared_end_completed':{'min':1,'max':1}}
SCIENCE_FLUX_COMMAND_GATES = {'ro_flux_scan':{**deepcopy(_FLUX_COMMAND_COVERAGE),'resolved_bias_points':{'min':1}},
    'sq_flux_point_recheck':{**deepcopy(_FLUX_COMMAND_COVERAGE),'local_cavity_contrast_snr':{'min':5}}}


SCIENCE_REPEAT5_GATES_VERSION='science-shape-repeat5-chain-gates/1'
SCIENCE_REPEAT5_GATES={**deepcopy(SCIENCE_GATES),**deepcopy(SCIENCE_IRB_GATES),
    'sq_shape_rabi_amplitude_repeat5':{'r_squared':{'min':.8},'contrast_snr':{'min':5},'cycles':{'min':1.5},
        'gate_repeats':{'min':5,'max':5},'relative_stderr':{'min':0,'max':.1}}}

def science_rules(profile):
    if profile.get('scienceRepeat5Policy'):return SCIENCE_REPEAT5_GATES_VERSION,SCIENCE_REPEAT5_GATES
    if profile.get('scienceFluxPolicy'):
        modes={route.get('schema')=='qcal-bias-command-route/1' for route in profile.get('biasRoutes',{}).values()}
        if len(modes)>1:raise APIError('FLUX_POLICY_SCOPE','偏置任务须冻结单一资源及其执行策略',409)
        if modes=={True}:return SCIENCE_FLUX_COMMAND_GATES_VERSION,SCIENCE_FLUX_COMMAND_GATES
        return SCIENCE_FLUX_GATES_VERSION,SCIENCE_FLUX_GATES
    return ((SCIENCE_IRB_GATES_VERSION,SCIENCE_IRB_GATES) if profile.get('scienceIRBPolicy')
            else (SCIENCE_GATES_VERSION,SCIENCE_GATES))


def criteria_for(procedure, gates=None, *, independent=False):
    """Reconstruct exactly the frozen criteria used by a controller result."""
    rules = deepcopy((GATES if gates is None else gates).get(procedure, {}))
    if independent and procedure in SCIENCE_CANDIDATE_METHODS:
        rules.update(candidate_agreement_fraction={'min': 0, 'max': 1},
                     candidate_signal_agreement={'min': 0, 'max': 1},
                     candidate_capture_agreement={'min': 0, 'max': 1},
                     candidate_points_acquired={'min': 1})
    if independent and procedure=='sq_drag_half_scan':
        rules.update(candidate_improvement_upper_5sem={'min':0},candidate_improvement_sem={'min':0})
    return rules


def evaluate(result, procedure, mode, gates=None, independent=False, *, rule_version=None):
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
    frozen = gates if gates is not None else GATES
    rules = criteria_for(procedure, frozen, independent=independent)
    if procedure=='sq_shape_rabi_amplitude_repeat5' or procedure in SCIENCE_GATES or procedure in SCIENCE_IRB_GATES or procedure in SCIENCE_FLUX_GATES:
        if not isinstance(native, dict) or native.get('accepted') is not True:
            missing.append('缺少原生科学分析通过回执')
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
    if procedure=='sq_shape_rabi_amplitude_repeat5':
        from .science_repeat5_quality import check
        reasons.extend(check(result,independent=independent))
    if procedure in SCIENCE_FLUX_GATES:
        from .science_flux_quality import check
        reasons.extend(check(result,procedure))
    if procedure in SCIENCE_IRB_GATES:
        from .science_irb_quality import check
        integrity_reasons=check(result,procedure,independent=independent)
        reasons.extend(integrity_reasons)
        if procedure=='sq_drag_irb' and (reasons or missing):
            result['diagnosticMetrics']={k:result['metrics'].pop(k) for k in ('gate_fidelity','clifford_fidelity') if k in result['metrics']}
    result['quality'] = {'status': 'failed' if reasons else ('unverified' if missing else 'passed'),
                         'reasons': reasons + missing,
                         'ruleVersion': rule_version or ('diagnostic-gates/1' if frozen == GATES else 'custom-gates/' + digest(frozen)[:16]),
                         'rulesSha256': digest(frozen), 'criteriaSha256': digest(rules),
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
