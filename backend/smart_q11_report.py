"""Reader-facing view of the linked shaped X/2 calibration and paired interleaved RB."""
from math import isfinite

METHODS = {
    'sq_shape_rabi_amplitude_repeat5': '五脉冲整形 Rabi',
    'sq_shape_half_verify': '整形 X/2 幅度验证',
    'sq_drag_half_scan': 'X/2 的 DRAG 系数',
    'sq_drag_irb': 'X/2 条件交错 RB',
}


def fidelity_measurement(child, leaf):
    if not leaf.get('accepted') or child['workflow'].get('workflowComplete') is not True:
        return None
    item = child['nodes'][0]
    node = item['node']
    attempt = next((a for a in item.get('attempts', []) if a.get('id') == node.get('latestAttemptId')), {})
    if attempt.get('state') != 'verified' or not attempt.get('verificationOperationId'):
        return None
    result = attempt.get('result') or {}
    if result != node.get('result') or not result.get('selectionResult'):
        return None
    parts = []
    for role, op, capture in [('首测', attempt.get('operationId'), result['selectionResult']),
                              ('独立复测', attempt['verificationOperationId'], result)]:
        irb = capture.get('provenance', {}).get('scienceIRB', {}).get('irb', {})
        boot = irb.get('bootstrap', {})
        interval = boot.get('intervals_unclipped', {}).get('F_X_half_conditional')
        value = irb.get('F_X_half_conditional')
        if (not op or irb.get('reportable') is not True or irb.get('target_gate') != 'X/2'
                or irb.get('reasons') != [] or boot.get('confidence') != .95
                or boot.get('systematic_model_error_included') is not False
                or not isinstance(interval, (list,tuple)) or len(interval) != 2
                or not all(type(x) in (int,float) and isfinite(x) and 0 <= x <= 1 for x in [value,*interval])
                or interval[0] > interval[1] or capture.get('metrics',{}).get('gate_fidelity') != value):
            return None
        parts.append({'role': role, 'operationId': op, 'value': value, 'interval95': list(interval),
                      'sourceWorkflowId': child['workflow']['id'], 'attemptId': attempt['id']})
    return parts if parts[0]['operationId'] != parts[1]['operationId'] else None


def add_gate_results(model, report, reader):
    smart = report['workflow'].get('smartRun') or {}
    if smart.get('schema') != 'q11-full-same-gate/1':
        return model
    for measured, source in zip(model['nodes'], report.get('nodes', [])):
        if source['node'].get('procedureId') == 'sq_rb':
            measured['label'] = '主流程 X/2 交错 RB'
            for metric in measured['metrics']:
                if metric.get('key') == 'gate_fidelity':
                    metric['label'] = metric['label'].replace('单门保真度', '主流程 X/2 条件保真度')
    states = {method: '待测' for method in METHODS}
    fidelity = None
    for entry in report.get('smartGateReports', []):
        method, child = entry['procedureId'], entry['report']
        sub = reader(child)
        leaf = sub['nodes'][0]
        label = METHODS[method] if entry['current'] else METHODS[method] + '（历史任务）'
        leaf.update(label=label, sourceWorkflowId=child['workflow']['id'])
        if entry['current']:
            states[method] = leaf['status']
            if method == 'sq_drag_irb' and smart.get('gate') == 'verified':
                evidence = smart.get('gateEvidence') or {}
                pair = fidelity_measurement(child, leaf)
                if (pair and evidence.get('reportable') is True and evidence.get('recordId')
                        and evidence.get('childWorkflowId') == child['workflow']['id']
                        and evidence.get('attemptId') == pair[1]['attemptId']
                        and evidence.get('metrics', {}).get('gate_fidelity') == pair[1]['value']):
                    fidelity = pair
        else:
            leaf['accepted'] = False
            leaf['status'] = '历史任务 · ' + leaf['status']
        model['nodes'].append(leaf)
        model['claimLedger'].extend(sub['claimLedger'])
        model['acquisitionHistory'].extend(sub['acquisitionHistory'])
    model['acquisitionHistory'].sort(key=lambda row: row.get('acquiredAt') or '')
    model['smartGate'] = {'steps': [{'label': label, 'status': states[key]} for key,label in METHODS.items()],
                          'fidelity': fidelity or [], 'verified': bool(fidelity)}
    model['summary'] = f"Q11 主流程通过 {model['verified']}/{model['total']} 项。" + (
        '同一 X/2 门已完成首测和独立复测。' if fidelity else '同一 X/2 门保真度待验收。')
    model['scope'] = '主流程和整形门子任务均归属于本次启动。'
    return model
