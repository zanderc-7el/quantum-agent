"""Pure admission lookahead, not a reservation or scientific acceptance rule.

The controller must attest the real adapter implementation and frozen profile.
Plans come from that adapter's hardware-free build_plan, never model estimates.
Candidate values are unknown before selection: only audited count-preserving
calibration changes and ArcLight's single-setting readout verification are used.
The actual candidate's verification plan is checked again before its dispatch.
"""
from copy import deepcopy

from .common import APIError
from . import workflow_budget as task_budget

VERSION = 'selection-verification-budget/1'
ARC_IDENTITIES = frozenset((
    ('adapters.arclight.ArcLightAdapter', 'arclight-private-f8-q11-v1'),
    ('adapters.arclight.ArcLightAdapter', 'arclight-private-f8-q11-v2'),
    ('adapters.arclight.ArcLightAdapter', 'arclight-private-f8-q11-v3')))
F8_IDENTITY = ('adapters.f8.F8Adapter', 'f8-native-cy8')
RABI16_IDENTITY = ('adapters.arclight_rabi16_adapter.ArcLightRabi16Adapter', 'arclight-private-f8-q11-rabi16-v1')
SMART_RABI16_IDENTITY = ('adapters.arclight_smart_rabi16_adapter.ArcLightSmartRabi16Adapter', 'arclight-private-f8-q11-smart-rabi16-v1')
PROCEDURES = frozenset(('ro_resonator_survey', 'ro_power_shift', 'sq_spectroscopy',
    'sq_rabi_amplitude', 'sq_rabi_time', 'sq_rabi_frequency', 'sq_allxy', 'ro_iq01',
    'ro_optimize', 'ro_optimize_frequency', 'ro_s21_ge', 'sq_t1', 'sq_ramsey',
    'sq_echo', 'sq_cpmg', 'sq_rb'))
FIT_PROCEDURES = frozenset(('sq_rabi_amplitude', 'sq_rabi_time', 'sq_rabi_frequency',
    'sq_t1', 'sq_ramsey', 'sq_echo', 'sq_cpmg'))


def _integer(value, name, *, zero=False):
    if type(value) is not int or value < (0 if zero else 1):
        raise APIError('INVALID_BUDGET', name + ' 缺少可信非负整数' if zero else name + ' 缺少可信正整数')
    return value


def _remaining(workflow):
    available = task_budget.remaining(workflow, 'maxTotalShots', workflow.get('reservedShots'))
    if available is not None and available < 0:
        raise APIError('INVALID_BUDGET', '累计预留已超任务上限；不能假定剩余额度')
    return available


def plan_shots(plan):
    if not isinstance(plan, dict):
        raise APIError('INVALID_BUDGET', '执行器未提供采集计划')
    return _integer(plan.get('estimated_total_shots'), 'estimated_total_shots')


def verification_probe(*, implementation_id, profile_id, procedure_id, request_parameters, selection_plan):
    """Return budget-only parameters; never a measured or submitted candidate."""
    identity = (implementation_id, profile_id)
    if identity not in ARC_IDENTITIES | {F8_IDENTITY, RABI16_IDENTITY, SMART_RABI16_IDENTITY} or procedure_id not in PROCEDURES or (identity in {RABI16_IDENTITY, SMART_RABI16_IDENTITY} and procedure_id != 'sq_rabi_amplitude'):
        raise APIError('VERIFICATION_BUDGET_UNKNOWN', '此执行器/方法的独立复测预算语义尚未核验')
    if identity == SMART_RABI16_IDENTITY and (selection_plan.get('executionPolicy') != 'q11-smart-rabi-independent-blocks16/1'
            or selection_plan.get('samplingPolicy') != 'arclight-rabi-independent-blocks16/1'
            or not isinstance(selection_plan.get('executionBinding'), dict)
            or selection_plan['executionBinding'].get('schema') != 'qcal-smart-attempt-execution-binding/1'):
        raise APIError('VERIFICATION_BUDGET_UNKNOWN', '智能 16 块计划缺少本版本的授权与分块策略')
    parameters = deepcopy(request_parameters)
    basis = 'same_sampling_counts_after_calibration_only'
    if identity in ARC_IDENTITIES:
        actual = selection_plan.get('parameters')
        if not isinstance(actual, dict):
            raise APIError('INVALID_BUDGET', 'ArcLight 计划缺少实际参数')
        parameters = deepcopy(actual)
        key = ('readout_amp' if procedure_id == 'ro_optimize' else
               'readout_hz' if procedure_id in ('ro_optimize_frequency', 'ro_s21_ge') else None)
        if key:
            value = actual.get(key)
            if type(value) not in (int, float):
                raise APIError('INVALID_BUDGET', '固定候选复测预算缺少读出参考参数')
            parameters['amplitudes' if key == 'readout_amp' else 'frequencies_hz'] = [value]
            basis = 'arclight_single_setting_same_shots_captures_blocks'
    return parameters, basis


def selection_lookahead(workflow, selection_plan, verification_plan, *, basis):
    selection, verification = plan_shots(selection_plan), plan_shots(verification_plan)
    remaining = _remaining(workflow)
    if remaining is not None and selection + verification > remaining:
        raise APIError('JOINT_SHOTS_BUDGET',
            f'首测 {selection} + 独立复测 {verification} = {selection + verification} shots，'
            f'超过任务剩余 {remaining}；未启动首测，已有预留不退还')
    return {'version': VERSION, 'selectionShots': selection, 'verificationShotsUpperBound': verification,
        'jointShots': selection + verification, 'remainingShotsAtCheck': remaining,
        **({'budgetMode': task_budget.MODE} if remaining is None else {}),
        'verificationBasis': basis, 'reservation': False,
        'candidateKnown': False, 'actualVerificationPlanRechecked': True}


def verify_admission(workflow, actual_plan, lookahead):
    """Check this operation only; selection is already reserved, never charged twice."""
    shots = plan_shots(actual_plan)
    if not isinstance(lookahead, dict) or lookahead.get('version') != VERSION:
        raise APIError('VERIFICATION_BUDGET_UNKNOWN', '缺少本次首测的联合预算预检')
    maximum = _integer(lookahead.get('verificationShotsUpperBound'), 'verificationShotsUpperBound')
    if shots > maximum:
        raise APIError('VERIFICATION_BUDGET_CHANGED', '实际候选改变了复测采样规模，超过首测前已核验容量；未下发复测')
    remaining = _remaining(workflow)
    if remaining is not None and shots > remaining:
        raise APIError('TOTAL_SHOTS_BUDGET', '独立复测所需 shots 超过当前剩余额度；首测数据及预留保留')
    return {'version': VERSION, 'actualVerificationShots': shots, 'remainingShotsAtCheck': remaining,
        **({'budgetMode': task_budget.MODE} if remaining is None else {}),
        'reservation': False, 'selectionChargedAgain': False}


def sampling_context(*, implementation_id, profile_id, procedure_id):
    """Static native semantics, never new measurement evidence or a duration estimate."""
    identity = (implementation_id, profile_id)
    if identity not in ARC_IDENTITIES | {RABI16_IDENTITY, SMART_RABI16_IDENTITY} or procedure_id not in FIT_PROCEDURES or (identity in {RABI16_IDENTITY, SMART_RABI16_IDENTITY} and procedure_id != 'sq_rabi_amplitude'):
        return None
    return {'source': 'audited_ArcLight_platform_analysis._fit_metrics',
        'fitUnit': 'one_capture', 'rSquaredAggregation': 'minimum_across_capture_fits',
        'captureRepeats': '每个 group/capture 单独拟合，平台 r_squared 取这些拟合的最小值；不同条件不会合并成一次拟合。增加 capture 不会降低单个 capture 的点噪声或漂移，不能用平均 R² 代替最差 R²。',
        'acquisitionsPerPoint': '每点新增独立 IQ block；增加 blocks 可增加点内样本量，但是否改善 SEM 需实测，不能保证消除慢漂移或通过验收。',
        'wallClock': '比较 points × captures × blocks 及既有真实采集耗时；更多 capture 延长整次测量，更多点/blocks 延长每个 capture。需权衡采样覆盖与扫描期间漂移；不能将脉冲时间窗当作实际采集耗时，不能宣称未测方案已改善。',
        'gatePolicy': '原生分析及冻结平台门槛不变；首测通过后仍需新的独立 verification 操作。'}
