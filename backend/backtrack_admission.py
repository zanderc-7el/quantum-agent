"""Pure attempt admission before a queued backtrack invalidates any evidence.

This is a lookahead, not an attempt/shot reservation or a quality decision.
Shot-plan admission remains a separate controller check for physical adapters.
"""
from .common import APIError

VERSION = 'backtrack-attempt-admission/1'


def _integer(value, name, minimum):
    if type(value) is not int or value < minimum:
        raise APIError('INVALID_BUDGET', name + ' 缺少可信整数；未回退或使证据失效', 409)
    return value


def admit_backtrack(workflow, target):
    attempts = _integer(target.get('attempt'), 'target.attempt', 0)
    maximum = _integer(workflow.get('maxAttemptsPerNode'), 'maxAttemptsPerNode', 1)
    iterations = _integer(workflow.get('iterations'), 'iterations', 0)
    total = _integer(workflow.get('maxWorkflowAttempts'), 'maxWorkflowAttempts', 1)
    if attempts >= maximum:
        raise APIError('ATTEMPT_BUDGET', '回退目标已达到本轮尝试次数上限；未回退或使证据失效', 409)
    if iterations >= total:
        raise APIError('WORKFLOW_ATTEMPT_BUDGET', '任务已达到本轮总尝试次数上限；未回退或使证据失效', 409)
    return {'version': VERSION, 'targetNodeId': target['id'],
            'remainingTargetAttempts': maximum - attempts,
            'remainingWorkflowAttempts': total - iterations, 'reservation': False}


def eligible_backtracks(workflow, ancestor_ids):
    nodes = {n['id']: n for n in workflow['nodes']}
    allowed, excluded = [], []
    for node_id in ancestor_ids:
        try:
            if node_id not in nodes:
                raise APIError('BAD_BACKTRACK', '回退目标不存在', 409)
            admit_backtrack(workflow, nodes[node_id])
            allowed.append(node_id)
        except APIError as error:
            excluded.append({'nodeId': node_id, 'reasonCode': error.code, 'reason': error.message})
    return {'version': VERSION, 'allowed': allowed, 'excluded': excluded,
            'meaning': '仅尝试次数预检；实际回退仍须当前版本、共享设备状态和首测加独立复测 shots 准入。未预留或退还预算。'}
