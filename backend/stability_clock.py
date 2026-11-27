"""Acquisition-completion clock for new bounded stability schedules only."""
from datetime import datetime, timedelta
import json
from .common import APIError, digest, file_hash, checked_path

POLICY = 'first_frequency_acquisition_completed_v1'


def enabled(schedule):
    return schedule.get('clockPolicy') == POLICY


def _require(ok, message):
    if not ok:
        raise APIError('STABILITY_CLOCK_EVIDENCE', message, 409)


def stamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.utcoffset() is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _receipt(controller, rt, child, node, effect):
    request, result = json.loads(effect['request']), json.loads(effect['result'])
    identity = effect['attempt_id']
    saved = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
        (identity, child['id'], node['id'])).fetchone()
    attempt = json.loads(saved[0]) if saved else {}
    operation = identity + '/measure'
    expected = {'runId': child['id'], 'nodeId': node['id'], 'attemptId': identity,
                'operationId': operation, 'resourceId': node['resourceId'],
                'procedureId': 'sq_spectroscopy', 'adapterId': child['adapterId'],
                'adapterProfileHash': child['adapterProfileHash']}
    _require(effect['operation_id'] == operation and attempt.get('operationId') == operation
        and attempt.get('mode') == 'real' and attempt.get('procedureId') == 'sq_spectroscopy'
        and all(request.get(k) == v for k, v in expected.items()),
        '定频首测采集回执与本轮身份不一致，后续轮次已暂停')
    acquired = stamp(result.get('acquiredAt'))
    started = stamp(attempt.get('startedAt'))
    provenance = result.get('provenance') or {}
    _require(result.get('dataKind') == 'real' and acquired is not None and started is not None
        and acquired >= started and not any(result.get(k) for k in ('partial', 'stale', 'verificationPending'))
        and result.get('sourceRunId', operation) == operation
        and provenance.get('operationId', operation) == operation
        and provenance.get('freshAcquisition', True) is True,
        '定频首测缺少完整真实采集时间，后续轮次已暂停')
    raw = [a for a in result.get('artifacts', []) if a.get('kind') == 'raw']
    _require(1 <= len(raw) <= 64, '定频首测缺少已登记原始数据，后续轮次已暂停')
    files = []
    for artifact in raw:
        entry = rt.db.execute('SELECT path,sha256,payload FROM platform_artifacts WHERE id=?',
            (artifact.get('id'),)).fetchone()
        _require(entry is not None and entry['sha256'] == artifact.get('sha256'),
            '定频首测原始数据登记不一致，后续轮次已暂停')
        metadata = json.loads(entry['payload'])
        path = checked_path(entry['path'], parent=controller.root)
        _require(all(metadata.get(k) == artifact.get(k) for k in ('id','name','kind','sha256'))
            and path.is_file() and file_hash(path) == artifact['sha256'],
            '定频首测原始数据摘要改变，后续轮次已暂停')
        files.append({'id': artifact['id'], 'sha256': artifact['sha256']})
    return {'schema': 'qcal-stability-interval-anchor/1', 'basis': POLICY,
            'workflowId': child['id'], 'nodeId': node['id'], 'attemptId': identity,
            'operationId': operation, 'acquiredAt': result['acquiredAt'],
            'resultSha256': digest(result), 'rawArtifacts': files}


def first_receipt(controller, rt, child):
    node = next((n for n in child['nodes'] if n['procedureId'] == 'sq_spectroscopy'), None)
    _require(node is not None and child.get('mode') == 'real', '本轮定频节点缺失')
    # The first completed selection, including a quality-rejected spectrum, is
    # the first actual acquisition. Verification/T1/recovery scans do not reset it.
    for effect in rt.db.execute("SELECT * FROM platform_effects WHERE workflow_id=? AND state='completed' ORDER BY rowid", (child['id'],)):
        request = json.loads(effect['request'])
        if request.get('procedureId') == 'sq_spectroscopy' and effect['operation_id'].endswith('/measure'):
            return _receipt(controller, rt, child, node, effect)
    return None


def deadline(schedule, anchor):
    acquired = stamp((anchor or {}).get('acquiredAt'))
    _require(acquired is not None, '本轮尚无可核验的定频首测采集完成时间，请核查本轮后再继续')
    return (acquired + timedelta(seconds=schedule['intervalSeconds'])).isoformat()


def observe(controller, rt, schedule, row, child):
    if not enabled(schedule) or row.get('intervalAnchor'):
        return
    _scope(schedule, row, child)
    anchor = first_receipt(controller, rt, child)
    if anchor is not None:
        row['intervalAnchor'] = anchor
        schedule['nextDueAt'] = deadline(schedule, anchor)


def recheck(controller, rt, schedule):
    row = schedule['rounds'][-1]
    anchor = row.get('intervalAnchor')
    due = deadline(schedule, anchor)
    child = controller.get(row['workflowId'], rt)
    _scope(schedule, row, child)
    if (child.get('status') in ('queued','running') or child.get('cancelRequested')
            or child.get('sharedDeviceFault') or child.get('engineState') == 'needs_reconciliation'
            or controller._has_lease(rt, child['id'])
            or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')",
                             (child['id'],)).fetchone()):
        raise APIError('ROUND_UNRESOLVED', '本轮设备动作尚未结束或仍待核查，未安排下一轮', 409)
    try:
        actual = first_receipt(controller, rt, child)
    except (OSError, ValueError, TypeError, KeyError):
        raise APIError('STABILITY_CLOCK_EVIDENCE', '定频首测时间证据无法核验，后续轮次已暂停', 409) from None
    _require(actual is not None and actual == anchor, '定频首测时间来源已变化，后续轮次已暂停')
    return due


def _scope(schedule, row, child):
    binding = child.get('stabilityBinding') or {}
    _require(binding.get('scheduleId') == schedule['id'] and binding.get('round') == row['number']
        and binding.get('calibrationHash') == schedule['calibrationHash'], '周期采集时间的来源绑定改变')
