"""Workflow-wide accounting is independent of a native method's hard limits."""
from .common import APIError

MODE = 'accounting_only'
CEILINGS = {'maxTotalShots': 100000000, 'maxAttemptsPerNode': 10, 'maxWorkflowAttempts': 200}
SMART_SCHEMA = 'q11-full-same-gate/1'
BASE_ADAPTER = 'arclight-private-f8-q11-v3'


def only_accounting(workflow):
    return (workflow.get('budget') or {}).get('mode') == MODE


def limits_for_accounting(mode, resources, experiment, policy, is_stability, adapter_id):
    if (mode != 'real' or resources != ['Q11'] or experiment != 'single' or policy != 'autopilot'
            or is_stability or adapter_id != BASE_ADAPTER):
        raise APIError('INVALID_BUDGET_SCOPE', '仅八号机 Q11 真机单比特自主任务可选择仅记账模式', 409)
    return dict.fromkeys(CEILINGS)


def inherit_smart_budget(controller, rt, parent_id, data, binding):
    """Called only by an in-process controller creating a science child in its transaction."""
    if rt is None or not rt.db.in_transaction:
        raise APIError('INVALID_BUDGET_SCOPE', '子任务计量授权缺少父任务事务', 409)
    parent = controller.store.get(rt, parent_id)
    observed = (binding or {}).get('observedResourceBinding') or {}
    if (not parent or parent.get('adapterId') != BASE_ADAPTER or parent.get('mode') != 'real'
            or parent.get('resourceIds') != ['Q11'] or parent.get('policy') != 'autopilot'
            or parent.get('experiment') != 'single' or parent.get('configurationBinding')
            or (parent.get('smartRun') or {}).get('schema') != SMART_SCHEMA or not only_accounting(parent)
            or parent.get('cancelRequested') or parent.get('status') not in ('queued','running')
            or data.get('mode') != 'real' or data.get('resourceIds') != ['Q11']
            or data.get('experiment','single') != 'single' or data.get('policy') != 'strict'
            or data.get('executionProfile') not in ('science-shape-repeat5','science-v3','science-v3-irb')
            or observed.get('sourceWorkflowId') != parent_id or observed.get('resourceId') != 'Q11'
            or observed.get('sourceAdapterProfileHash') != parent.get('adapterProfileHash')):
        raise APIError('INVALID_BUDGET_SCOPE', '科学子任务无法核对 Q11 智能父任务与当前实测来源', 409)
    controller.for_workflow(parent_id,rt)._assert_adapter_binding(parent)
    return {'parentWorkflowId':parent_id, 'parentSchema':SMART_SCHEMA, 'sourceAdapterProfileHash':parent['adapterProfileHash']}


def remaining(workflow, key, used):
    maximum = workflow.get(key)
    if type(used) is not int or used < 0:
        raise APIError('INVALID_BUDGET', '已用数量缺少有效记录：' + key, 409)
    if only_accounting(workflow):
        if maximum is not None or key not in workflow:
            raise APIError('INVALID_BUDGET', '仅记账模式的上限配置不一致：' + key, 409)
        return None
    if type(maximum) is not int or maximum < 1:
        raise APIError('INVALID_BUDGET', '任务缺少有效上限：' + key, 409)
    return maximum - used


def exhausted(workflow, key, used):
    available = remaining(workflow, key, used)
    return available is not None and available <= 0


def revised_limits(workflow, proposed):
    if proposed == {'mode': MODE}:
        if only_accounting(workflow):
            raise APIError('INVALID_BUDGET', '本任务已采用仅记账模式')
        return limits_for_accounting(workflow['mode'], workflow['resourceIds'], workflow['experiment'],
                                     workflow['policy'], bool(workflow.get('stabilityBinding')), workflow.get('adapterId'))
    if only_accounting(workflow):
        raise APIError('INVALID_BUDGET', '本任务采用仅记账模式，无需增加额度')
    if not isinstance(proposed, dict) or not proposed or set(proposed) - CEILINGS.keys():
        raise APIError('INVALID_BUDGET', '请选择仅记账模式，或提供有效的任务额度')
    updated = {key: proposed.get(key, workflow[key]) for key in CEILINGS}
    for key, val in updated.items():
        if type(val) is not int or not workflow[key] <= val <= CEILINGS[key]:
            raise APIError('INVALID_BUDGET', f'{key} 必须为不小于当前值且不大于 {CEILINGS[key]} 的整数')
    if all(workflow[key] == updated[key] for key in CEILINGS):
        raise APIError('INVALID_BUDGET', '至少增加一项预算')
    if (updated['maxTotalShots'] < workflow.get('reservedShots', 0) or updated['maxWorkflowAttempts'] < workflow['iterations']
            or updated['maxAttemptsPerNode'] < max((n['attempt'] for n in workflow['nodes']), default=0)):
        raise APIError('INVALID_BUDGET', '预算不得小于已记账的采集或尝试次数')
    return updated
