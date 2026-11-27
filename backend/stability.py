"""Bounded periodic frequency/T1 rounds owned by the existing controller loop."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from .common import APIError, digest, dumps, ident, now, number
from . import stability_clock

RECOVERY_POLICY = 'bounded_spectrum_window_expansion_v1'


def _recovery_policy(value, span):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value)-{'maxExpansions','maxSpanHz'}:
        raise APIError('INVALID_FREQUENCY_RECOVERY', '扩窗只允许指定最大次数和最大频窗')
    count, maximum = value.get('maxExpansions', 2), value.get('maxSpanHz', 100e6)
    if type(count) is not int or not 0 <= count <= 2 or not number(maximum) or not span <= maximum <= 100e6:
        raise APIError('INVALID_FREQUENCY_RECOVERY', '扩窗最多两次，最大频窗须介于初始窗口和100MHz之间')
    return {'policy':RECOVERY_POLICY,'maxExpansions':count,'factor':2,
            'maxSpanHz':maximum,'edgeGuardSteps':2,'maximumFrequencyAttempts':count+1}


def _frequency_window(result):
    parameters = result.get('parameters') or {}
    points = parameters.get('points')
    frequencies = parameters.get('frequencies_hz')
    if isinstance(frequencies,list) and len(frequencies)>=3 and all(number(v) for v in frequencies):
        low, high, points = min(frequencies), max(frequencies), len(set(frequencies))
    else:
        center, span = parameters.get('drive_hz'), parameters.get('span_hz')
        if not number(center) or not number(span):return None
        low = parameters.get('start_hz',center-span/2)
        high = parameters.get('stop_hz',center+span/2)
    if not number(low) or not number(high) or high<=low or type(points) is not int or not 3<=points<=161:
        return None
    return {'startHz':low,'stopHz':high,'spanHz':high-low,'points':points}


def _frequency_problem(node, binding):
    """Use the latest actual independent scan, not the committed selection alias."""
    policy = binding.get('frequencyRecovery') or {}
    if policy.get('policy') != RECOVERY_POLICY:return None
    result = node.get('result') or {}; analysis = result.get('analysisQuality') or {}
    if (result.get('dataKind')!='real' or result.get('partial') or result.get('stale')
            or result.get('provenance',{}).get('freshAcquisition') is not True
            or analysis.get('kind')!='spectrum' or not node.get('latestAttemptId')):return None
    window = _frequency_window(result)
    if window is None:return {'code':'frequency_window_unverified','message':'本轮实际频谱窗口不完整，不能自动扩窗或执行 T1'}
    candidate = result.get('verificationCandidateParameters',result.get('candidateParameters',{})).get('drive_hz')
    reason = {'originAttemptId':node['latestAttemptId'],'resultSha256':digest(result),'window':window}
    if not number(candidate) or analysis.get('accepted') is not True:
        return {**reason,'code':'frequency_peak_missing','message':'本轮没有独立可信的内部响应峰，扩大频窗复核'}
    guard = policy['edgeGuardSteps']*window['spanHz']/(window['points']-1)
    if not window['startHz']+guard < candidate < window['stopHz']-guard:
        return {**reason,'code':'frequency_peak_at_edge','candidateHz':candidate,
                'message':'本轮响应峰距频窗边界不超过两个采样步长，扩大频窗复核'}
    return None


def _accepted(node):
    result = node.get('result') or {}
    return (node.get('engineState') == 'verified' and result.get('dataKind') == 'real'
            and result.get('quality', {}).get('status') == 'passed'
            and not result.get('stale') and not result.get('partial'))


def admit_t1(controller, workflow, node):
    """A passing spectral SNR without a new frequency is insufficient."""
    if not workflow.get('stabilityBinding') or node['procedureId'] != 'sq_t1':
        return True
    frequency = next(n for n in workflow['nodes'] if n['procedureId'] == 'sq_spectroscopy')
    result = frequency.get('result') or {}
    candidate = result.get('candidateParameters', {}).get('drive_hz')
    problem = _frequency_problem(frequency,workflow['stabilityBinding'])
    if problem:
        controller._attention(workflow['id'], 'stability_frequency_edge' if problem['code']=='frequency_peak_at_edge' else 'stability_frequency_required',
            problem['message']+'；T1 未执行', node_id=node['id'])
        return False
    if not _accepted(frequency) or not number(candidate):
        controller._attention(workflow['id'], 'stability_frequency_required',
            '本轮未获得独立验证的比特频率，T1 未执行', node_id=node['id'])
        return False
    with controller.store.connect() as rt:
        actual, versions = controller._parameters(rt, workflow, node)
    if not controller._same_parameter(actual.get('drive_hz'), candidate) or versions.get('drive_hz', 0) < 1:
        controller._attention(workflow['id'], 'stability_frequency_required',
            '本轮频率尚未提交到 T1 驱动配置，T1 未执行', node_id=node['id'])
        return False
    return True


class StabilityService:
    def __init__(self, controller):
        self.c, self.store = controller, controller.store
        with self.store.connect() as rt:
            rt.db.executescript('''
                CREATE TABLE IF NOT EXISTS platform_stability(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS platform_stability_commands(id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,response TEXT NOT NULL);
            ''')
        # A service restart never silently renews periodic execution permission.
        with self.store.transaction() as rt:
            for schedule in self.list(rt):
                if schedule['state'] == 'running':
                    schedule.update(state='paused', error='服务重启，周期许可已暂停；已有采集按回执核查')
                    for row in schedule['rounds']:
                        if row['state'] not in ('queued', 'running'):
                            continue
                        child = self.c.get(row['workflowId'], rt)
                        if child['status'] not in ('completed', 'aborted', 'failed'):
                            self.c.action(child['id'], {'operationId': ident('stability-recovery'),
                                'expectedRevision': child['revision'], 'action': 'pause'}, _rt=rt)
                    self._save(rt, schedule)

    def list(self, rt=None):
        if rt is None:
            with self.store.connect() as current:
                return self.list(current)
        return [json.loads(row[0]) for row in rt.db.execute('SELECT payload FROM platform_stability ORDER BY rowid DESC')]

    def get(self, identity, rt=None):
        if rt is None:
            with self.store.connect() as current:
                return self.get(identity, current)
        row = rt.db.execute('SELECT payload FROM platform_stability WHERE id=?', (identity,)).fetchone()
        if not row:
            raise APIError('NOT_FOUND', '周期任务不存在', 404)
        return json.loads(row[0])

    def _save(self, rt, schedule):
        schedule['revision'] += 1
        schedule['updatedAt'] = now()
        rt.db.execute('INSERT OR REPLACE INTO platform_stability VALUES(?,?)', (schedule['id'], dumps(schedule)))

    @staticmethod
    def _command(rt, request):
        operation = request.get('operationId')
        if not isinstance(operation, str) or not 1 <= len(operation) <= 256:
            raise APIError('OPERATION_ID_REQUIRED', '请提供操作编号')
        fingerprint = digest(request)
        prior = rt.db.execute('SELECT request_hash,response FROM platform_stability_commands WHERE id=?', (operation,)).fetchone()
        if prior and prior[0] != fingerprint:
            raise APIError('IDEMPOTENCY_CONFLICT', '同一操作编号对应不同请求', 409)
        return operation, fingerprint, json.loads(prior[1]) if prior else None

    @staticmethod
    def _receipt(rt, operation, fingerprint, result):
        rt.db.execute('INSERT INTO platform_stability_commands VALUES(?,?,?)', (operation, fingerprint, dumps(result)))
        return result

    def _source(self, rt, workflow_id, resource_id):
        wf = self.c.get(workflow_id, rt)
        scoped = self.c.for_workflow(workflow_id, rt)
        if wf['mode'] != 'real' or resource_id not in wf['resourceIds']:
            raise APIError('STABILITY_CALIBRATION_REQUIRED', '请选择同一比特的真机标定任务', 409)
        if wf['status'] in ('running', 'queued') or scoped._has_lease(rt, wf['id']):
            raise APIError('SOURCE_BUSY', '请先完成或暂停标定任务', 409)
        scoped._assert_adapter_binding(wf)
        nodes = [n for n in wf['nodes'] if n['resourceId'] == resource_id and _accepted(n)]
        rabi = next((n for n in nodes if n['procedureId'] == 'sq_rabi_time'), None) or next((n for n in nodes if n['procedureId'] == 'sq_rabi_amplitude'), None)
        readout = next((n for n in nodes if n['procedureId'] == 'ro_optimize'), None) or next((n for n in nodes if n['procedureId'] == 'ro_iq01'), None)
        if not rabi or not readout:
            raise APIError('STABILITY_CALIBRATION_REQUIRED', '需要同一工作点已验收的 Rabi 与读出标定', 409)
        evidence = []
        for node in (rabi, readout):
            row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=?', (node.get('latestAttemptId'), wf['id'])).fetchone()
            attempt = json.loads(row[0]) if row else {}
            if attempt.get('executionBinding'):
                # This new amplitude-only sampling extension is not the legacy
                # stability fallback contract. The normal full workflow has an
                # independent time-Rabi source, selected preferentially above.
                raise APIError('STABILITY_TIME_RABI_REQUIRED', '此幅度采集采用单次扩展版本；请先完成普通时间 Rabi 及读出验收，再建立稳定性任务', 409)
            if attempt.get('state') != 'verified' or not attempt.get('verificationOperationId') or attempt.get('result') != node['result']:
                raise APIError('CURRENT_EVIDENCE_REQUIRED', '标定缺少匹配的独立复测记录', 409)
            for artifact in node['result']['artifacts']:
                scoped._artifact(rt, artifact)
            evidence.append({'nodeId': node['id'], 'attemptId': attempt['id'], 'sha256': digest(node['result'])})
        # A readout scan's points/span/explicit frequency axis is not a
        # calibration seed for the next spectroscopy or T1 experiment.
        parameters, versions = scoped._parameters(rt, wf, {**readout, 'parameterOverrides': {}})
        keys = getattr(scoped.adapter, 'validation_parameter_keys', lambda: (
            'readout_hz','readout_amp','drive_hz','drive_amp','drive_length_s','half_pi_amp',
            'xy_delay_s','xy_attenuation_db','xy_filter_mhz','readout_width_s','pulse_compatibility'))()
        parameters = {key: value for key, value in parameters.items() if key in keys}
        versions = {key: value for key, value in versions.items() if key in keys}
        proof = {'workflowId': wf['id'], 'resourceId': resource_id, 'adapterProfileHash': wf['adapterProfileHash'],
                 'chipVersionId': wf.get('chipVersionId'), 'deploymentVersionId': wf.get('deploymentVersionId'),
                 'parameters': parameters, 'parameterVersions': versions, 'evidence': evidence}
        return wf, scoped, proof

    def create(self, body):
        interval, count, span, points = (body.get(key) for key in ('intervalSeconds','maxRounds','spanHz','points'))
        if (not number(interval) or not 60 <= interval <= 30*86400 or type(count) is not int or not 1 <= count <= 1000
                or not number(span) or not 1e3 <= span <= 100e6 or type(points) is not int or not 3 <= points <= 161):
            raise APIError('INVALID_SCHEDULE', '请检查间隔（至少60秒）、轮数、频窗与扫描点数')
        recovery = _recovery_policy(body.get('frequencyRecovery'), span)
        with self.store.transaction() as rt:
            op, fingerprint, prior = self._command(rt, {'kind': 'create', **body})
            if prior:
                return prior
            source, _, proof = self._source(rt, body.get('sourceWorkflowId'), body.get('resourceId'))
            schedule = {'id': ident('stability'), 'revision': 0, 'state': 'draft',
                'name': body.get('name') or body['resourceId'] + ' 频率与 T1 稳定性',
                'sourceWorkflowId': source['id'], 'resourceId': body['resourceId'],
                'chipVersionId': source.get('chipVersionId'), 'deploymentVersionId': source.get('deploymentVersionId'),
                'intervalSeconds': interval, 'maxRounds': count, 'spanHz': span, 'points': points,
                'clockPolicy': stability_clock.POLICY,
                'frequencyRecovery': recovery,
                'maximumFrequencyOperations':count*2*recovery['maximumFrequencyAttempts'],
                'createdAt': now(), 'nextDueAt': None, 'rounds': [], 'calibration': proof, 'calibrationHash': digest(proof)}
            self._save(rt, schedule)
            return self._receipt(rt, op, fingerprint, schedule)

    def action(self, identity, body):
        with self.store.transaction() as rt:
            op, fingerprint, prior = self._command(rt, {'kind': 'action', 'id': identity, **body})
            if prior:
                return prior
            schedule = self.get(identity, rt)
            action = body.get('action')
            expected = body.get('expectedRevision')
            older_stop = action in ('pause','abort') and type(expected) is int and 1 <= expected <= schedule['revision']
            if not older_stop and expected != schedule['revision']:
                raise APIError('STALE_REVISION', '周期任务已更新，请刷新', 409)
            if action == 'start':
                if getattr(self.c, '_stability_error', None):
                    raise APIError('STABILITY_RECONCILIATION_REQUIRED', '周期调度已暂停，请核查本轮记录并重启控制器', 409)
                if schedule['state'] not in ('draft','paused'):
                    raise APIError('INVALID_STATE', '当前周期任务不能开始', 409)
                _, _, proof = self._source(rt, schedule['sourceWorkflowId'], schedule['resourceId'])
                if digest(proof) != schedule['calibrationHash']:
                    raise APIError('CALIBRATION_CHANGED', '来源标定已变化，请建立新周期任务', 409)
                if any(row['state'] in ('queued','running') for row in schedule['rounds']):
                    raise APIError('ROUND_UNRESOLVED', '上一轮尚未核查完成，请先处理该工作流', 409)
                due = (stability_clock.recheck(self.c, rt, schedule)
                       if stability_clock.enabled(schedule) and schedule['rounds'] else now())
                schedule.update(state='running', nextDueAt=due, error=None)
            elif action in ('pause', 'abort'):
                if schedule['state'] not in ('completed','aborted'):
                    schedule.update(state='paused' if action=='pause' else 'aborted')
                    for row in schedule['rounds']:
                        if row['state'] not in ('queued','running'):
                            continue
                        child = self.c.get(row['workflowId'], rt)
                        self.c.action(child['id'], {'operationId': op + '/child', 'expectedRevision': child['revision'], 'action': action}, _rt=rt)
            else:
                raise APIError('INVALID_ACTION', '未知周期操作')
            self._save(rt, schedule)
            self.c.wake.set()
            return self._receipt(rt, op, fingerprint, schedule)

    def _recover_frequency(self, rt, schedule, row, child, spectrum, t1, problem):
        policy = schedule.get('frequencyRecovery') or {}
        if (schedule['state']!='running' or policy.get('policy')!=RECOVERY_POLICY
                or child['status'] not in ('attention','failed') or child.get('cancelRequested')
                or child.get('sharedDeviceFault') or child.get('engineState')=='needs_reconciliation'
                or t1.get('attempt',0)>0 or problem.get('code') not in ('frequency_peak_missing','frequency_peak_at_edge')):
            return False
        events = row.setdefault('recoveryEvents',[])
        origin = problem['originAttemptId']
        if any(e['originAttemptId']==origin for e in events):return False
        def stop(code,message):
            row['recoveryStop']={'code':code,'message':message,'originAttemptId':origin,'at':now()}
            return False
        if len(events)>=policy['maxExpansions']:
            return stop('FREQUENCY_RECOVERY_LIMIT','本轮频窗复核次数已耗尽，未执行 T1')
        if (spectrum.get('result') or {}).get('parameters',{}).get('frequencies_hz'):
            return stop('FREQUENCY_WINDOW_FIXED_AXIS','本轮使用显式频率数组；保留原数组，未自动改窗或执行 T1')
        if child.get('stabilityBinding',{}).get('frequencyRecovery')!=policy:
            return stop('FREQUENCY_RECOVERY_BINDING','扩窗许可与本轮冻结配置不一致')
        scoped = self.c.for_workflow(child['id'],rt)
        if scoped._has_lease(rt,child['id']) or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')",(child['id'],)).fetchone():
            return stop('RECONCILIATION_REQUIRED','上次采集回执尚未核查，未重试')
        window = problem['window'];schema = scoped._parameter_schema('sq_spectroscopy')
        span_rule = schema.get('span_hz') or {};lo_rule = schema.get('start_hz') or {};hi_rule = schema.get('stop_hz') or {}
        if any(not number(rule.get(k)) for rule in (span_rule,lo_rule,hi_rule) for k in ('min','max')):
            return stop('FREQUENCY_WINDOW_LIMIT','执行器缺少明确的频窗参数上下限')
        span = min(window['spanHz']*policy['factor'],policy['maxSpanHz'],span_rule['max'])
        center = (window['startHz']+window['stopHz'])/2
        changes = {'span_hz':span,'start_hz':center-span/2,'stop_hz':center+span/2}
        if span<=window['spanHz'] or not lo_rule['min']<=changes['start_hz']<=lo_rule['max'] or not hi_rule['min']<=changes['stop_hz']<=hi_rule['max']:
            return stop('FREQUENCY_WINDOW_LIMIT','扩窗已达频率或窗口上限，未执行 T1')
        operation = schedule['id']+'/round/'+str(row['number'])+'/expand/'+origin
        hook = getattr(scoped,'_stability_retry_frequency',None)
        if not callable(hook):return stop('STABILITY_RECOVERY_UNAVAILABLE','控制器未提供受限扩窗重试接口')
        try:
            _,_,proof = self._source(rt,schedule['sourceWorkflowId'],schedule['resourceId'])
            if digest(proof)!=schedule['calibrationHash']:raise APIError('CALIBRATION_CHANGED','周期来源标定已变化')
            updated = hook(rt,child,spectrum,operation_id=operation,changes=changes,reason=problem)
        except APIError as exc:
            return stop(exc.code,exc.message)
        events.append({'operationId':operation,'originAttemptId':origin,'at':now(),'reason':deepcopy(problem),
            'before':{'span_hz':window['spanHz'],'start_hz':window['startHz'],'stop_hz':window['stopHz']},
            'after':changes,'workflowRevision':updated['revision']})
        row.update(state='queued',frequencyHz=None,driftHz=None,t1Seconds=None,frequencyAttemptId=None,t1AttemptId=None)
        row.pop('finishedAt',None);row.pop('error',None)
        return True

    def _update_round(self, rt, schedule, row):
        child = self.c.get(row['workflowId'], rt)
        if stability_clock.enabled(schedule):
            try:
                stability_clock.observe(self.c, rt, schedule, row, child)
            except (APIError, OSError, ValueError, TypeError) as exc:
                schedule.update(state='paused', error=getattr(exc, 'message', '定频采集时间证据无法核验，后续轮次已暂停'))
        spectrum, t1 = child['nodes']
        frequency = (spectrum.get('result') or {}).get('candidateParameters', {}).get('drive_hz')
        problem = _frequency_problem(spectrum,child.get('stabilityBinding') or {})
        if (spectrum.get('latestAttemptId') and spectrum.get('result')
                and not spectrum['result'].get('partial') and not spectrum['result'].get('verificationPending')):
            attempts = row.setdefault('frequencyAttempts',[])
            if not any(a['attemptId']==spectrum['latestAttemptId'] for a in attempts):
                attempts.append({'attemptId':spectrum['latestAttemptId'],'resultSha256':digest(spectrum['result']),
                    'window':_frequency_window(spectrum['result']),'accepted':_accepted(spectrum) and problem is None,'problem':deepcopy(problem)})
        if problem:
            row.update(frequencyHz=None,driftHz=None,t1Seconds=None,frequencyAttemptId=None,t1AttemptId=None)
            if self._recover_frequency(rt,schedule,row,child,spectrum,t1,problem):return
        if _accepted(spectrum) and number(frequency) and problem is None:
            row.update(frequencyHz=frequency, driftHz=frequency-row['referenceFrequencyHz'], frequencyAttemptId=spectrum.get('latestAttemptId'))
        if _accepted(t1) and row['frequencyHz'] is not None:
            result = t1['result']
            if result.get('parameters', {}).get('drive_hz') == row['frequencyHz']:
                row.update(t1Seconds=result.get('metrics', {}).get('t1_s'), t1AttemptId=t1.get('latestAttemptId'))
        if child['status'] == 'completed' and row['frequencyHz'] is not None and row['t1Seconds'] is not None:
            row.update(state='completed', finishedAt=child.get('finishedAt') or now())
        elif child['status'] in ('attention','failed','aborted','paused'):
            row.update(state='interrupted' if child['status'] in ('paused','aborted') else 'failed',
                       finishedAt=now(), error=(child.get('events') or [{}])[-1].get('message') or child['engineState'])
            if child.get('sharedDeviceFault') or child['engineState'] == 'needs_reconciliation':
                schedule.update(state='paused', error='共用设备待核查，周期队列已暂停')
        else:
            row['state'] = 'running' if child['status'] == 'running' else 'queued'

    def tick(self, timestamp=None):
        current = timestamp or datetime.now(timezone.utc)
        with self.store.transaction() as rt:
            for schedule in self.list(rt):
                before = dumps(schedule)
                for row in schedule['rounds']:
                    if row['state'] in ('queued','running'):
                        self._update_round(rt, schedule, row)
                active = any(row['state'] in ('queued','running') for row in schedule['rounds'])
                if schedule['state'] == 'running' and not active:
                    if len(schedule['rounds']) >= schedule['maxRounds']:
                        schedule['state'] = 'completed'
                        if stability_clock.enabled(schedule):schedule['nextDueAt'] = None
                    elif stability_clock.enabled(schedule) and schedule['rounds'] and not schedule['rounds'][-1].get('intervalAnchor'):
                        schedule.update(state='paused', nextDueAt=None,
                            error='本轮尚无可核验的定频首测采集完成时间，请核查本轮后再继续')
                    elif schedule['nextDueAt'] and datetime.fromisoformat(schedule['nextDueAt']) <= current:
                        try:
                            if stability_clock.enabled(schedule) and schedule['rounds']:
                                due = stability_clock.recheck(self.c, rt, schedule)
                                if schedule['nextDueAt'] != due:
                                    raise APIError('STABILITY_CLOCK_EVIDENCE', '周期到期时间与首测采集时间不一致')
                            source, scoped, proof = self._source(rt, schedule['sourceWorkflowId'], schedule['resourceId'])
                            if digest(proof) != schedule['calibrationHash']:
                                raise APIError('CALIBRATION_CHANGED', '来源标定已变化')
                            valid = [r for r in schedule['rounds'] if r['frequencyHz'] is not None]
                            reference = valid[-1]['frequencyHz'] if valid else proof['parameters']['drive_hz']
                            number_ = len(schedule['rounds'])+1
                            binding = {'scheduleId': schedule['id'], 'round': number_, 'calibrationHash': schedule['calibrationHash'],
                                       'parameters': {**proof['parameters'], 'drive_hz': reference}, 'referenceFrequencyHz': reference,
                                       'spanHz': schedule['spanHz'], 'points': schedule['points']}
                            if schedule.get('frequencyRecovery'):
                                binding['frequencyRecovery']=deepcopy(schedule['frequencyRecovery'])
                            child = scoped.create({'name': schedule['name']+' · 第'+str(number_)+'轮', 'mode':'real',
                                'resourceIds':[schedule['resourceId']], 'policy':'threshold', 'coverage':'main'},
                                _rt=rt, _binding=source.get('configurationBinding'), _stability=binding)
                            child['stabilityBinding'].update(
                                frequencyNodeId=next(n['id'] for n in child['nodes'] if n['procedureId']=='sq_spectroscopy'),
                                t1NodeId=next(n['id'] for n in child['nodes'] if n['procedureId']=='sq_t1'))
                            child.update(hardwareConfirmed=True, hardwareConfirmedAt=now())
                            self.store.save(rt, child, 'stability_permission', {'message':'本轮许可来自已启动的有界周期任务'})
                            self.c.action(child['id'], {'operationId':schedule['id']+'/round/'+str(number_),
                                'expectedRevision':child['revision'], 'action':'start'}, _rt=rt)
                            schedule['rounds'].append({'number':number_, 'state':'queued', 'workflowId':child['id'],
                                'startedAt':now(), 'frequencyHz':None, 'referenceFrequencyHz':reference,
                                'driftHz':None, 't1Seconds':None,'frequencyAttempts':[],'recoveryEvents':[]})
                            if stability_clock.enabled(schedule):
                                schedule['rounds'][-1]['intervalAnchor'] = None
                                schedule['nextDueAt'] = None
                            else:
                                schedule['nextDueAt'] = (current+timedelta(seconds=schedule['intervalSeconds'])).isoformat()
                        except APIError as exc:
                            schedule.update(state='paused', error=exc.message)
                if dumps(schedule) != before:
                    self._save(rt, schedule)

    def halt_after_error(self, error_type):
        """Close periodic permits without stopping unrelated controller work."""
        with self.store.transaction() as rt:
            for schedule in self.list(rt):
                if schedule['state'] != 'running':
                    continue
                schedule.update(state='paused', error='周期调度异常：'+error_type+'；请核查本轮记录')
                for row in schedule['rounds']:
                    if row['state'] not in ('queued','running'):
                        continue
                    child = self.c.get(row['workflowId'], rt)
                    self.c.action(child['id'], {'operationId':ident('stability-fault'),
                        'expectedRevision':child['revision'], 'action':'pause'}, _rt=rt)
                self._save(rt, schedule)
