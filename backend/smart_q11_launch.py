"""Atomically authorize one fresh, full Q11 run using the fixed station."""
from __future__ import annotations

import json
from contextlib import nullcontext

from .common import APIError, checked_path, digest, dumps, file_hash, now, number


PROFILE = 'arclight-private-f8-q11-v3'
START_SCOPE = 'q11-full-same-gate/1'


def _previous_rabi_captures(controller, rt, capture, raw_path):
    """Summarize only the registered native analysis next to its authenticated raw scan."""
    artifact = next((a for a in capture.get('artifacts', []) if a.get('name') == 'platform_analysis.json'
                     and a.get('kind') == 'json'), None)
    if not artifact:
        return None
    registered = rt.db.execute('SELECT path,sha256 FROM platform_artifacts WHERE id=?', (artifact['id'],)).fetchone()
    if not registered or registered['sha256'] != artifact.get('sha256'):
        return None
    try:
        path = checked_path(registered['path'], parent=controller.root / 'data')
        if path.parent != raw_path.parent or path.stat().st_size > 2_000_000 or file_hash(path) != registered['sha256']:
            return None
        native = json.loads(path.read_bytes())['analysis']
        if native['kind'] != 'rabi_amplitude' or len(native['groups']) != 1:
            return None
        repeats = native['groups'][0]['repeats']
        if not 2 <= len(repeats) <= 8:
            return None
        captures = []
        for index, repeat in enumerate(repeats, 1):
            fit = repeat['fit']
            if repeat['repeat'] != index or (fit is not None and not isinstance(fit, dict)):
                return None
            entry = {'capture': index, 'fitAvailable': fit is not None}
            if fit and number(fit.get('cycles_in_window')):
                entry['cyclesInWindow'] = fit['cycles_in_window']
            captures.append(entry)
        return {'captures': captures, 'nativeAccepted': native['accepted'] is True,
                'analysisSha256': registered['sha256']}
    except (APIError, OSError, TypeError, KeyError, ValueError):
        return None


def rabi_grid_hint(controller, rt, source, initial):
    """Reuse the grid of a recorded scan at this readout/drive condition, with its outcome visible."""
    node = next((n for n in source['nodes'] if n['procedureId']=='sq_rabi_amplitude'),{})
    if not node.get('latestAttemptId'):
        return None
    row = rt.db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=? AND id=? AND node_id=?',
                        (source['id'],node['latestAttemptId'],node['id'])).fetchone()
    attempt = json.loads(row[0]) if row else {}
    result = attempt.get('result') or {}
    capture = result.get('selectionResult') or result
    p = attempt.get('parameters') or {}
    if (node.get('engineState') in ('stale','acquiring','verifying') or result.get('stale') or result.get('partial')
            or capture.get('dataKind')!='real' or capture.get('provenance',{}).get('freshAcquisition') is not True
            or capture.get('quality',{}).get('status') not in ('passed','failed','unverified')
            or any(p.get(k)!=v for k,v in initial.items()) or not attempt.get('operationId')):
        return None
    effect = rt.db.execute('SELECT state FROM platform_effects WHERE workflow_id=? AND attempt_id=? AND operation_id=?',
                           (source['id'],attempt['id'],attempt['operationId'])).fetchone()
    raw = next((a for a in capture.get('artifacts',[]) if a.get('kind')=='raw' and a.get('name')=='raw_scan.npz'),None)
    if not raw or not effect or effect[0]!='completed':
        return None
    reg = rt.db.execute('SELECT path,sha256 FROM platform_artifacts WHERE id=?',(raw['id'],)).fetchone()
    if not reg or reg['sha256']!=raw.get('sha256'):
        return None
    try:
        raw_path = checked_path(reg['path'],parent=controller.root/'data')
        if file_hash(raw_path)!=reg['sha256']:
            return None
    except (APIError,OSError):
        return None
    keys = ('acquisitions_per_point','shots','points','capture_repeats','amp_start','amp_stop','drive_length_s')
    if not all(number(p.get(k)) for k in keys):
        return None
    grid = {k:p[k] for k in keys}
    # The new workflow starts on the base profile until its Agent authorizes a larger capture.
    limits = controller.adapter.parameter_schema('sq_rabi_amplitude')['acquisitions_per_point']
    grid['acquisitions_per_point'] = min(grid['acquisitions_per_point'], limits['max'])
    hint = {'grid':grid,'sourceWorkflowId':source['id'],'sourceAttemptId':attempt['id'],
            'sourceOperationId':attempt['operationId'],'sourceRawSha256':reg['sha256'],
            'sourceQuality':result.get('quality',{}).get('status'),'sourceBlocks':p['acquisitions_per_point']}
    previous = _previous_rabi_captures(controller, rt, capture, raw_path)
    if previous:
        hint['previousAnalysis'] = previous
    return hint


def attach_rabi_history(payload, workflow, procedure):
    if procedure != 'sq_rabi_amplitude' or workflow.get('smartRun', {}).get('schema') != START_SCOPE:
        return payload
    hint = workflow['smartRun'].get('initialization', {}).get('rabiGridSource', {})
    if 'previousAnalysis' in hint:
        payload['historicalRabiGrid'] = {'blocksPerPoint': hint['sourceBlocks'], 'parameters': {
            k: hint['grid'][k] for k in ('points','shots','amp_start','amp_stop','drive_length_s')},
            'previousOutcome': hint['sourceQuality'], 'native': hint['previousAnalysis'],
            'scope': '历史同工况扫描。用于比较加块、调门长、复核定频等方案。每项判断和复测以当前任务新采集为准。'}
    return payload


SHORT_HALF_POLICY = 'f8_validated_short_half_dc_buff2ns_v1'


def spectroscopy_initial_hints(wf, node, initialization, base):
    """Recover the derived companion only in an authenticated, legacy f01 seed."""
    hints = initialization.get('procedureHints', {}).get(node['procedureId'], {})
    if ('drive_amp' not in hints or 'half_pi_amp' in hints or node['procedureId'] != 'sq_spectroscopy'
            or base.get('pulse_compatibility') != SHORT_HALF_POLICY or node.get('resourceId') != 'Q11'
            or wf.get('smartRun', {}).get('schema') != START_SCOPE or wf.get('policy') != 'autopilot'
            or wf.get('mode') != 'real' or wf.get('adapterId') != PROFILE or wf.get('configurationBinding')
            or not wf.get('adapterProfileHash') or wf['smartRun'].get('adapterProfileHash') != wf['adapterProfileHash']
            or initialization.get('acceptedCalibrationInherited') is not False
            or not initialization.get('sourceWorkflowId') or not initialization.get('sourceAttemptId')
            or not number(hints['drive_amp'])):
        return hints
    refs = initialization.get('rawReferences') or []
    if (len(refs) != 2 or not all(isinstance(r, dict) and r.get('id') and r.get('operationId')
                                  and r.get('sha256') for r in refs)
            or refs[0]['id'] == refs[1]['id'] or refs[0]['operationId'] == refs[1]['operationId']):
        return hints
    return {**hints, 'half_pi_amp': hints['drive_amp']}


def spectroscopy_seed(controller, rt, wf):
    """Use a previously verified frequency and its readout setting as numeric initial values."""
    node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_spectroscopy')
    rows = rt.db.execute('SELECT workflow_id,id,payload FROM platform_attempts WHERE node_id=? ORDER BY rowid DESC',
                         (node['id'],))
    for row in rows:
        attempt = json.loads(row['payload'])
        if attempt.get('state') != 'verified' or not attempt.get('verificationOperationId'):
            continue
        source = controller.get(row['workflow_id'], rt)
        if (source.get('mode') != 'real' or source.get('resourceIds') != ['Q11'] or source.get('configurationBinding')
                or source.get('adapterProfileHash') != wf['adapterProfileHash'] or source.get('adapterId') != PROFILE):
            continue
        source_node = next((n for n in source['nodes'] if n['id'] == node['id']), {})
        if source_node.get('engineState') != 'verified' or source_node.get('latestAttemptId') != row['id']:
            continue
        result = attempt.get('result') or {}
        if (result.get('dataKind') != 'real' or result.get('stale') or result.get('partial')
                or result.get('quality', {}).get('status') != 'passed'):
            continue
        values = {k: attempt.get('parameters', {}).get(k) for k in ('readout_hz','readout_amp','readout_width_s')}
        values['drive_hz'] = result.get('candidateParameters', {}).get('drive_hz')
        if not all(number(v) for v in values.values()):
            continue
        proof = []
        for op, capture in [(attempt['operationId'], result.get('selectionResult', {})),
                            (attempt['verificationOperationId'], result)]:
            effect = rt.db.execute('SELECT state FROM platform_effects WHERE operation_id=? AND workflow_id=? AND attempt_id=?',
                                   (op, source['id'], row['id'])).fetchone()
            artifacts = [a for a in capture.get('artifacts', []) if a.get('kind') == 'raw' and a.get('name') == 'raw_scan.npz']
            if not effect or effect[0] != 'completed' or len(artifacts) != 1:
                break
            raw = artifacts[0]
            registered = rt.db.execute('SELECT path,sha256 FROM platform_artifacts WHERE id=?', (raw['id'],)).fetchone()
            if not registered or registered['sha256'] != raw.get('sha256'):
                break
            try:
                path = checked_path(registered['path'], parent=controller.root / 'data')
                if file_hash(path) != raw['sha256']:
                    break
            except (OSError, APIError):
                break
            proof.append({'operationId': op, 'id': raw['id'], 'sha256': raw['sha256']})
        if len(proof) == 2 and proof[0]['operationId'] != proof[1]['operationId'] and proof[0]['id'] != proof[1]['id']:
            settings = attempt.get('parameters', {})
            keys = ('acquisitions_per_point','capture_repeats','points','shots','span_hz','spectroscopy_width_s','drive_amp','half_pi_amp')
            scan = {k:settings[k] for k in keys if number(settings.get(k))}
            seed = {'sourceWorkflowId': source['id'], 'sourceAttemptId': row['id'], 'parameters': values,
                    'procedureHints': {'sq_spectroscopy': scan},
                    'rawReferences': proof, 'acceptedCalibrationInherited': False}
            seed['procedureHints']['sq_spectroscopy'] = spectroscopy_initial_hints(
                wf, node, seed, controller.profile['resources'][node['resourceId']])
            rabi = rabi_grid_hint(controller,rt,source,values)
            if rabi:
                seed['procedureHints']['sq_rabi_amplitude'] = rabi['grid']
                seed['rabiGridSource'] = rabi
            return seed
    return None


def launch(controller, body, *, _rt=None, _confirmed_source=None):
    fields = {'operationId', 'name', 'resourceId', 'adapterId', 'scope', 'stationConfirmed'}
    if not isinstance(body, dict) or set(body) != fields:
        raise APIError('SMART_START_REQUEST', '请从完整单比特智能测量入口启动')
    operation = body['operationId']
    if not isinstance(operation, str) or not 1 <= len(operation) <= 128:
        raise APIError('OPERATION_ID_REQUIRED', '启动操作编号无效')
    if (body['resourceId'] != 'Q11' or body['adapterId'] != PROFILE
            or body['scope'] != START_SCOPE or body['stationConfirmed'] is not True):
        raise APIError('SMART_START_SCOPE', '本入口需要确认八号机 Q11 的主机读出、XY 和采集接线', 409)
    if not isinstance(body['name'], str) or not 1 <= len(body['name'].strip()) <= 120:
        raise APIError('INVALID_NAME', '请填写 1 至 120 字的任务名称')
    request_hash = digest({'kind': 'smart_q11_launch', 'body': body})
    with (controller.store.transaction() if _rt is None else nullcontext(_rt)) as rt:
        existing = rt.db.execute(
            'SELECT request_hash,response FROM platform_commands WHERE id=?', (operation,)
        ).fetchone()
        if existing:
            if existing['request_hash'] != request_hash:
                raise APIError('IDEMPOTENCY_CONFLICT', '本次启动编号已用于其他请求', 409)
            return controller.get(json.loads(existing['response'])['id'], rt)
        if not controller._physical_adapter or controller.adapter_id != PROFILE:
            raise APIError('SMART_START_PROFILE', '当前机台的执行器与八号机 Q11 配置不一致', 409)
        wf = controller.create({'mode': 'real', 'policy': 'autopilot', 'resourceIds': ['Q11'],
                                'coverage': 'full', 'experiment': 'single', 'name': body['name'].strip(),
                                'budget': {'mode': 'accounting_only'}}, _rt=rt)
        controller._assert_adapter_binding(wf)
        if len(wf['nodes']) != 13 or any(n.get('capability') != 'available' for n in wf['nodes']):
            raise APIError('SMART_START_INCOMPLETE', '当前执行器未覆盖完整的 Q11 主流程', 409)
        wf['smartRun'] = {'schema': START_SCOPE, 'operationId': operation,
                          'stationConfirmedAt': now(), 'adapterProfileHash': wf['adapterProfileHash'],
                          'main': 'pending', 'gate': 'pending', 'report': 'pending'}
        seed = spectroscopy_seed(controller, rt, wf)
        if seed:
            wf['smartRun']['initialization'] = seed
        if _confirmed_source:
            prior = controller.get(_confirmed_source,rt)
            if (prior.get('mode') != 'real' or prior.get('hardwareConfirmed') is not True
                    or prior.get('adapterId') != PROFILE or prior.get('adapterProfileHash') != wf['adapterProfileHash']
                    or prior.get('configurationBinding') or prior.get('resourceIds') != ['Q11']):
                raise APIError('SMART_HANDOVER_REQUIRED','缺少同一机台、接线版本与 Q11 的现场确认',409)
            wf['hardwareConfirmationSource'] = prior['id']
            wf['smartRun']['stationConfirmedAt'] = prior.get('hardwareConfirmedAt')
        wf.update(hardwareConfirmed=True, hardwareConfirmedAt=wf['smartRun']['stationConfirmedAt'])
        controller.store.save(rt, wf, 'smart_start_authorized', {'message': '已确认八号机 Q11 接线，开始本轮智能测量'})
        started = controller.action(wf['id'], {'operationId': 'smart-start/' + digest(operation),
                                               'action': 'start', 'expectedRevision': wf['revision']}, _rt=rt)
        rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (operation, request_hash, dumps({'id': started['id']})))
    controller.wake.set()
    return started
