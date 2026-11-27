"""Evidence-bound reader view, shared by HTML and the local PowerPoint exporter."""
from __future__ import annotations
import base64
import hashlib
import html
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .receipt_presentation import report_view
from .rabi_attention import rabi_fit_evidence
from .report_workpoints import acquisition_context, committed_workpoints, failed_rabi_summary, qualify_fit_metrics

POLICY_VERSION = 'defensive-language-audit/report-reader-v1'
METHOD_LABELS = {'sq_cpmg': 'CPMG 相干测试', 'sq_t1': 'T1 能量弛豫', 'sq_ramsey': 'Ramsey 相干测试',
                 'sq_echo': 'Spin Echo 相干测试', 'ro_iq01': '读出状态区分', 'ro_optimize': '读出参数优化',
                 'sq_rb': '单比特 RB', 'sq_rabi_amplitude': '幅度 Rabi', 'sq_rabi_time': '时间 Rabi'}
METRICS = {
    't1_s': ('T1 能量弛豫时间', 1e6, 'μs'),
    't2_s': ('相干衰减时间', 1e6, 'μs'),
    'rabi_frequency_hz': ('Rabi 振荡频率', 1e-6, 'MHz'),
    'r_squared': ('拟合 R²', 1, ''),
    'contrast_snr': ('信噪比', 1, ''),
    'peak_snr': ('谱峰信噪比', 1, ''),
    'assignment_fidelity': ('读出指派准确率', 100, '%'),
    'readout_snr_dprime': ('读出区分度', 1, ''),
    'allxy_rms': ('AllXY 均方根偏差', 1, ''),
    'gate_fidelity': ('单门保真度', 100, '%'),
    'clifford_fidelity': ('Clifford 保真度', 100, '%'),
    'reference_drift_fraction': ('读出参考漂移', 100, '%'),
    'relative_stderr': ('相对标准误差', 100, '%'),
    'resonance_hz': ('共振频率', 1e-9, 'GHz'),
    'frequency_hz': ('频率', 1e-9, 'GHz'),
}


def fresh(result):
    return result.get('dataKind') == 'real' and result.get('provenance', {}).get('freshAcquisition') is True


def accepted(node, workflow):
    result = node.get('result') or {}
    return (workflow.get('mode') == 'real' and fresh(result)
            and node.get('engineState') == 'verified' and result.get('quality', {}).get('status') == 'passed'
            and not result.get('partial') and not result.get('stale') and not result.get('verificationPending'))


def status_label(node, workflow, has_attempts=False):
    result = node.get('result') or {}
    if node.get('engineState') == 'stale' or result.get('stale'):
        if not (result or has_attempts or node.get('latestAttemptId') or node.get('attempt')):
            return '待测（前置参数已更新）'
        return '前置参数已变化，需复测'
    if accepted(node, workflow):
        return '独立复测通过'
    if workflow.get('mode') == 'replay':
        return '历史回放'
    if result.get('quality', {}).get('status') == 'failed':
        return '未通过'
    if result.get('verificationPending'):
        return '独立复测尚未完成'
    if result.get('partial'):
        return partial_status(result)
    return {'acquiring': '正在采集', 'verifying': '正在独立复测', 'waiting_hardware': '等待接线'}.get(node.get('engineState'), '待测' if not result else '待验收')


def partial_status(result):
    return {'none': '未获得测量数据', 'unconfirmed': '原始数据留存待核查'}.get(
        result.get('executionSummary', {}).get('measurementData'), '部分采集')


def metric_rows(result, is_accepted=False):
    rows = []
    for key, value in result.get('metrics', {}).items():
        if key not in METRICS or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        label, scale, unit = METRICS[key]
        qualifier = '（未验收候选）' if key in ('gate_fidelity', 'clifford_fidelity', 't1_s', 't2_s') and not is_accepted else ''
        rows.append({'key': key, 'label': label + qualifier, 'value': f'{value * scale:.4g} {unit}'.strip()})
    priority = ['t1_s', 't2_s', 'rabi_frequency_hz', 'assignment_fidelity', 'allxy_rms', 'reference_drift_fraction', 'gate_fidelity', 'clifford_fidelity', 'r_squared', 'contrast_snr']
    return sorted(rows, key=lambda row: priority.index(row['key']) if row['key'] in priority else len(priority))


_CALIBRATION_FIELDS = {
    'ro_resonator_survey': ('readout_hz', '读取腔频率', 'valueHz'),
    'sq_spectroscopy': ('drive_hz', '比特频率', 'valueHz'),
    'sq_rabi_amplitude': ('drive_amp', 'π 脉冲幅度', 'valueAu'),
}


def _controller_commit(snapshot, decisions, workflow, node, attempt, key, value):
    if (workflow.get('policy') != 'autopilot' or (workflow.get('smartRun') or {}).get('schema') != 'q11-full-same-gate/1'
            or node.get('humanAcceptedAt') or attempt.get('humanAcceptedAt') or not isinstance(snapshot, dict)
            or snapshot.get('schema') != 'qcal-report-committed-parameters/1'):
        return None
    resource = node.get('resourceId')
    if not resource or resource != workflow.get('target'):
        return None
    resources = snapshot.get('resources')
    values = resources.get(resource) if isinstance(resources, dict) else None
    entry = values.get(key) if isinstance(values, dict) else None
    revision = node.get('parameterVersions', {}).get(key)
    if (not isinstance(entry, dict) or type(revision) is not int or revision <= 0
            or type(entry.get('revision')) is not int or entry['revision'] != revision
            or type(entry.get('value')) not in (int, float) or entry['value'] != value):
        return None
    artifacts = {a['id'] for a in node['result'].get('artifacts', []) if isinstance(a, dict) and isinstance(a.get('id'), str)}
    if not artifacts:
        return None
    commits = [d for d in decisions if isinstance(d, dict) and d.get('action') == 'commit' and d.get('source') == 'rule'
               and d.get('nodeId') == node['id'] and d.get('attemptId') == attempt['id']
               and isinstance(d.get('parameterChanges'), dict) and d['parameterChanges'].get(key) == value
               and d.get('id') and d.get('createdAt') and isinstance(d.get('evidenceIds'), list)
               and all(isinstance(e, str) for e in d['evidenceIds']) and set(d['evidenceIds']) == artifacts]
    if len(commits) == 1:
        return {'revision': revision, 'value': value, 'attemptId': attempt['id'],
                'decisionId': commits[0]['id'], 'committedAt': commits[0]['createdAt']}
    return None


def report_calibration(node, workflow, latest, decisions=(), committed=None):
    """Bind measured, independently verified and accepted values to their attempt."""
    spec = _CALIBRATION_FIELDS.get(node.get('procedureId'))
    if spec is None:
        return None
    key, label, value_field = spec
    result = node.get('result') or {}
    selection_result = result.get('selectionResult') or result
    def value(parameters):
        candidate = (parameters or {}).get(key)
        return candidate if (type(candidate) in (int, float) and math.isfinite(candidate) and candidate > 0) else None
    def source(parameters, stage, field):
        measured = value(parameters)
        return None if measured is None else {value_field: measured, 'sourceField': f'{field}.{key}',
            'operationId': stage.get('provenance', {}).get('operationId'), 'acquiredAt': stage.get('acquiredAt')}
    selected = source(selection_result.get('candidateParameters'), selection_result,
                      'node.result.selectionResult.candidateParameters' if result.get('selectionResult') else 'node.result.candidateParameters')
    verified = source(result.get('verificationCandidateParameters'), result,
                      'node.result.verificationCandidateParameters') if result.get('selectionResult') else None
    accepted_value = value(result.get('candidateParameters'))
    stamp = node.get('humanAcceptedAt')
    try:
        valid_stamp = isinstance(stamp, str) and datetime.fromisoformat(stamp.replace('Z', '+00:00')).tzinfo is not None
    except ValueError:
        valid_stamp = False
    attempt_result = (latest or {}).get('result') or {}
    accepted_source = None
    target = workflow.get('target')
    node_target = str(node.get('id', '')).partition(':')[0]
    same_target = not (node_target.startswith('Q') and node_target[1:].isdigit() and target and node_target != target)
    linked_pair = (accepted(node, workflow) and selected and verified and accepted_value is not None and latest and same_target
            and latest.get('id') == node.get('latestAttemptId')
            and latest.get('nodeId') == node.get('id') and latest.get('number') == node.get('attempt')
            and latest.get('state') == 'verified'
            and selected['operationId'] == latest.get('operationId') and selected['operationId']
            and verified['operationId'] == latest.get('verificationOperationId') and verified['operationId']
            and selected['operationId'] != verified['operationId'] and fresh(selection_result)
            and not any(selection_result.get(key) or attempt_result.get(key) for key in ('partial', 'stale', 'verificationPending'))
            and value(attempt_result.get('candidateParameters')) == accepted_value
            and value(attempt_result.get('verificationCandidateParameters')) == verified[value_field]
            and value((attempt_result.get('selectionResult') or {}).get('candidateParameters')) == selected[value_field]
            and attempt_result.get('quality', {}).get('status') == 'passed')
    if (linked_pair and valid_stamp and latest.get('humanAcceptedAt') == stamp
            and latest.get('id') == node.get('humanAcceptedAttemptId')):
        accepted_source = {value_field: accepted_value, 'attemptId': latest['id'], 'acceptedAt': stamp,
                           'sourceField': f'node.result.candidateParameters.{key}',
                           'meaning': 'human_accepted_measurement_value_not_instrument_readback'}
    def fmt(number):
        return f'{number / 1e9:.9g} GHz' if value_field == 'valueHz' else f'{number:.9g} a.u.'
    notes = [('首测候选 ' + fmt(selected[value_field])) if selected else '首测候选未记录',
             ('独立复测候选 ' + fmt(verified[value_field])) if verified else '独立复测候选未记录',
             ('人工接受 ' + fmt(accepted_value)) if accepted_source else '未记录本次人工接受']
    commit = _controller_commit(committed, decisions, workflow, node, latest, key, accepted_value) if linked_pair else None
    if commit:
        notes[-1] = '控制器已提交 ' + fmt(accepted_value)
    elif (not accepted_source and workflow.get('policy') == 'autopilot'
          and (workflow.get('smartRun') or {}).get('schema') == 'q11-full-same-gate/1'):
        notes.pop()
    primary = accepted_source or ({value_field: accepted_value} if commit else None) or verified or selected
    if primary is None and not result:
        return None
    title = ('已接受' + (' ' if key == 'drive_amp' else '') + label if accepted_source
             else label + ('（独立复测候选）' if verified else '（首测候选）'))
    if commit:
        title = '已提交' + (' ' if key == 'drive_amp' else '') + label
    return {'selection': selected, 'verification': verified, 'accepted': accepted_source, 'controllerCommit': commit,
            'summary': '；'.join(notes) + '。',
            'metric': {'key': key, 'label': title, 'value': fmt(primary[value_field])} if primary else None}


def reader_reasons(result):
    original = ' '.join(str(s) for s in result.get('quality', {}).get('reasons', []))
    rules = [('reference changed', '读出参考在测量前后发生漂移'), ('reference_drift_fraction', '读出参考在测量前后发生漂移'),
             ('model interval is too wide', '拟合置信区间过宽'), ('valid repeat fits', '有效重复拟合数量不足'),
             ('BIC improvement', '衰减拟合未充分优于趋势模型'), ('r_squared', '拟合质量未达到当前门槛'),
             ('contrast_snr', '响应信噪比未达到当前门槛')]
    translated = list(dict.fromkeys(text for fragment, text in rules if fragment.lower() in original.lower()))
    if not translated and original:
        translated = [original] if all(not ('a' <= char.lower() <= 'z' or char in '{}_') for char in original) else ['采集结果未满足本次验收规则']
    return '。'.join(text.rstrip('。') for text in translated) + ('。' if translated else '')


def _stages(attempt):
    result = attempt.get('result') or {}
    if not result:
        return []
    if isinstance(result.get('selectionResult'), dict):
        return [('首测', result['selectionResult']), ('独立复测', result)]
    return [('本次采集', result)]


def no_data_attempt_summary(node, attempt, result):
    """Present frozen, attempt-bound evidence; never infer totals from old data."""
    summary = result.get('executionSummary', {})
    if summary.get('measurementData') != 'none':
        return None
    number = attempt.get('number') if attempt else None
    prefix = f'第{number}次尝试' if type(number) is int and number > 0 else '本次尝试'
    completed = summary.get('pointsCompleted')
    total = None
    telemetry = node.get('telemetry') or {}
    if (attempt and telemetry.get('jobCount') == 1
            and telemetry.get('controllerAttemptId') == attempt.get('id')
            and telemetry.get('controllerOperationId') in summary.get('receiptOperationIds', [])):
        candidate = telemetry.get('totalRows')
        if type(candidate) is int and candidate > 0 and type(completed) is int and 0 <= completed <= candidate:
            total = candidate
    progress = (f'，已采集{completed}/{total}点' if total is not None else
                f'，已采集{completed}点' if type(completed) is int and completed >= 0 else '')
    reason = '设备连接失败' if summary.get('reason') == 'connection_failed' else '采集已停止'
    text = f'{prefix}：{reason}{progress}，本次未获得测量数据。'
    if summary.get('selectionRetained'):
        text += '此前候选采集单独保留，独立复测尚未验收。'
    retained = '日志与诊断记录已保留；' if '已保留执行日志和诊断文件' in summary.get('message', '') else '退出回执已保留；'
    return text + retained + '仪器输出状态未核验。'


def reader_model(report):
    from .smart_q11_report import add_gate_results
    return add_gate_results(_single_reader_model(report), report, _single_reader_model)


def _single_reader_model(report):
    report = report_view(report)
    wf = report['workflow']
    nodes, ledger, acquisition_history = [], [], []
    for item in report.get('nodes', []):
        node = item['node']
        ok = accepted(node, wf)
        attempts = item.get('attempts', [])
        status = status_label(node, wf, has_attempts=bool(attempts))
        latest = next((a for a in attempts if a.get('id') == node.get('latestAttemptId')), None)
        result = node.get('result') or {}
        calibration = report_calibration(node, wf, latest, item.get('decisions', []), report.get('committedParameters'))
        label = METHOD_LABELS.get(node.get('procedureId'), node.get('label', '测量'))
        summary = f'{label}：{status}。'
        if not ok:
            summary = no_data_attempt_summary(node, latest, result) or summary + reader_reasons(result)
        if node.get('procedureId') in ('ro_iq01', 'ro_optimize'):
            summary += '此处衡量读出状态的区分能力。'
        elif node.get('procedureId') == 'sq_allxy':
            summary += '此处检查脉冲组合的响应偏差。'
        elif node.get('procedureId') == 'sq_rb' and not ok:
            summary += '当前单门保真度尚未通过验收。'
        if calibration:
            summary += calibration['summary']
        fit_evidence = rabi_fit_evidence(node) if wf.get('mode') == 'real' else None
        if fit_evidence:
            summary = f'{label}：{status}。' + failed_rabi_summary(fit_evidence)
        history = []
        for attempt in attempts:
            stages = []
            current = attempt.get('id') == node.get('latestAttemptId')
            for role, stage in _stages(attempt):
                stage_ok = current and ok and role == '独立复测'
                stage_label = '历史回放' if wf.get('mode') == 'replay' or (not fresh(stage) and not stage.get('executionSummary')) else role
                if not current:
                    stage_label += '，历次尝试'
                stage_status = (partial_status(stage) if stage.get('partial') else
                                '独立复测通过' if stage_ok else
                                '未通过' if stage.get('quality', {}).get('status') == 'failed' else '候选结果')
                context = acquisition_context(node, attempt, stage)
                historical = wf.get('mode') == 'replay' or not current
                history_status = ('历史轮次 · ' if historical else '') + (status if current else stage_status)
                acquisition_history.append({'nodeId': node['id'], 'label': label,
                    'resourceId': node.get('resourceId', node.get('qubits', '')), 'attemptId': attempt.get('id'),
                    'number': attempt.get('number'), 'role': role, 'current': current, 'status': history_status, **context})
                figures = [{'artifactId': a['id'], 'caption': f'{label} · 第{attempt.get("number", "?")}次 · {stage_label} · {stage_status}',
                            'role': role, 'status': stage_status}
                           for a in stage.get('artifacts', []) if a.get('kind') == 'image']
                stage_metrics = metric_rows(stage, stage_ok)
                stage_fit = rabi_fit_evidence({'procedureId': node.get('procedureId'), 'latestAttemptId': attempt.get('id'), 'result': stage}) if wf.get('mode') == 'real' else None
                if stage_fit:
                    stage_metrics = qualify_fit_metrics(stage_metrics, stage_fit)
                stages.append({'label': stage_label, 'status': stage_status, 'metrics': stage_metrics, 'figures': figures, **context})
            history.append({'attemptId': attempt.get('id'), 'number': attempt.get('number'), 'current': current, 'stages': stages})
        figures = [f for a in history if a['current'] for s in a['stages'] for f in s['figures']]
        # Persisted current result may exist before its attempt row is visible; never silently substitute older figures.
        row = {'id': node['id'], 'label': label, 'qubits': node.get('qubits', wf.get('target', '')),
               'status': status, 'summary': summary, 'accepted': ok, 'metrics': metric_rows(result, ok),
               'figures': figures, 'history': history, 'decisions': [
                   {'reason': d.get('reason', ''), 'action': {'retry': '调整后重测', 'backtrack': '回到前置步骤', 'continue': '继续', 'wait': '等待', 'stop': '停止'}.get(d.get('action'), '记录'),
                    'time': d.get('createdAt', '')} for d in item.get('decisions', [])]}
        if calibration:
            row['calibration'] = {key: calibration[key] for key in ('selection', 'verification', 'accepted')}
            if calibration['controllerCommit']:
                row['calibration']['controllerCommit'] = calibration['controllerCommit']
            row['calibrationSummary'] = calibration['summary']
            if calibration['metric']:
                row['metrics'].insert(0, calibration['metric'])
        if fit_evidence:
            row['rabiFitEvidence'] = fit_evidence
            row['metrics'] = qualify_fit_metrics(row['metrics'], fit_evidence)
        current_stages = [s for a in history if a['current'] for s in a['stages']]
        brief = list(dict.fromkeys(s.get('brief') for s in current_stages if s.get('brief')))
        row['acquisitionSummary'] = (brief[0] if len(brief) == 1 else ' / '.join(
            s['label']+'：'+s['brief'] for s in current_stages if s.get('brief')))
        row['presentationSummary'] = summary if len(summary) <= 130 else f'{label}：{status}。完整诊断和候选归属见后续说明页。'
        nodes.append(row)
        ledger.append({'claim': summary, 'source': f'workflow:{wf["id"]}/node:{node["id"]}/attempt:{node.get("latestAttemptId")}',
                       'actor': '实验控制器', 'strength': status, 'scope': wf.get('coverage', 'selected_workflow'),
                       'causality': '无额外因果推断', 'necessaryLimitation': None if ok else status})
    count = sum(n['accepted'] for n in nodes)
    source = '历史实测回放' if wf.get('mode') == 'replay' else '本轮真机测量'
    summary = (f'{len(nodes)} 项历史记录，供比较参考。' if wf.get('mode') == 'replay'
               else f'{len(nodes)} 项测量中，{count} 项通过本轮诊断门槛及独立复测。')
    scope = ('从已有工作点开始的脉冲测量。历史参数仅用于初始化。' if wf.get('coverage') == 'working_point'
             else '当前任务中已选择的测量方法。')
    stamp = report.get('generatedAt')
    try:
        dt = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError('timestamp timezone missing')
        snapshot_time = dt.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') + '（北京时间）'
    except (ValueError, TypeError):
        snapshot_time = '未记录快照时间'
    workpoints = committed_workpoints(report)
    for point in workpoints:
        point['qualificationSummary'] = '；'.join(
            row['label']+'：'+row['status'] for row, item in zip(nodes, report.get('nodes', []))
            if item['node'].get('resourceId') == point['resourceId']
            and item['node'].get('procedureId') in ('sq_rabi_amplitude', 'sq_rabi_time'))
    return {'schema': 'qcal-reader-report/1', 'title': f'{wf.get("target", "量子")} 测量报告',
            'workflowId': wf['id'], 'revision': wf.get('revision'), 'generatedAt': report.get('generatedAt'),
            'snapshotTime': snapshot_time,
            'machine': wf.get('machine', ''), 'sourceLabel': source, 'summary': summary, 'scope': scope,
            'verified': count, 'total': len(nodes), 'nodes': nodes, 'claimLedger': ledger,
            'workpoints': workpoints,
            'acquisitionHistory': sorted(acquisition_history, key=lambda r: r.get('acquiredAt') or ''),
            'policyVersion': POLICY_VERSION}


def writing_audit(model, skill_root):
    """Two passes over authored, evidence-bound templates, not a keyword detector.

    Material claims are kept exactly; no automatic language action can alter a gate.
    Human acceptance remains unclaimed and can be recorded separately by a reviewer.
    """
    skill_root = Path(skill_root)
    source = json.loads((skill_root / 'source.json').read_text(encoding='utf-8'))
    for name, expected in source['files'].items():
        if hashlib.sha256((skill_root / name).read_bytes()).hexdigest() != expected:
            raise ValueError('报告写作规范文件发生变化，需要重新核对')
    sentences = [model['summary'], model['scope'], *[n['summary'] for n in model['nodes']],
                 *[p['summary'] for p in model.get('workpoints', [])],
                 *[r['workpoint'] for r in model.get('acquisitionHistory', [])]]
    audit = [{'sentence': sentence, 'function': 'limitation' if i == 1 else 'claim',
              'consumer': '实验负责人和汇报读者', 'decision': '确定当前可用结果和待复测项目',
              'inaccuracyIfRemoved': '读者无法判断测量范围或验收状态', 'action': 'KEEP',
              'reason': '直接说明结果、范围或下一步判断所需的限制', 'revision': sentence}
             for i, sentence in enumerate(sentences)]
    return {'schema': 'report-writing-audit/1', 'policyVersion': POLICY_VERSION, 'sourceSha256': source['sourceSha256'],
            'policyApplied': True, 'humanAccepted': False, 'readerDecision': '哪些测量结果已验证，哪些仍需复测',
            'claimLedger': model['claimLedger'], 'passA': audit,
            'passB': {'approvedActions': 'KEEP only: no material fact edits', 'text': sentences, 'changeLog': audit},
            'retainedLimitations': [c for c in model['claimLedger'] if c['necessaryLimitation']],
            'unresolvedVerification': [], 'evaluation': {'evidenceBound': True, 'materialEdits': 0,
                'humanAcceptancePending': True, 'notClaimed': '自动化规范应用不代表人工审阅完成'}}


def html_report(controller, report):
    model = reader_model(report)
    # This same policy/audit is applied for every HTML rendering and every PPT job.
    writing_audit(model, Path(controller.root) / 'knowledge/report-writing/defensive-language-audit')
    esc = lambda value: html.escape(str(value))
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>' + esc(model['title']) + '</title><style>body{font:17px/1.7 "Microsoft YaHei",system-ui;margin:0;background:#f5f5f5;color:#242424}main{max-width:1160px;margin:30px auto;padding:40px;background:white}h1{font-size:32px;border-bottom:3px solid #b72022;padding-bottom:20px}h2{font-size:25px;margin-top:36px}p{max-width:1000px}.lead{font-size:23px}.meta,figcaption{color:#60656c;font-size:14px}.figures{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}figure{margin:8px 0}img{width:100%;object-fit:contain}table{border-collapse:collapse;width:100%;margin:18px 0}th{text-align:left;background:#f7eeee}th,td{padding:10px;border-bottom:1px solid #ddd}details{margin:20px 0}summary{cursor:pointer;color:#555}pre{font:12px/1.5 monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:16px}.failed{color:#a71920}@media(max-width:700px){main{padding:20px;margin:0}.figures{grid-template-columns:1fr}}@media print{body{background:white}main{padding:0;margin:0}section{break-inside:avoid}details{display:none}}</style><main>',
             '<h1>' + esc(model['title']) + '</h1><p class="meta">' + esc(model['machine']) + '　测试人：Agent　' + esc(model['sourceLabel']) + '</p>',
             '<p class="meta">数据快照：' + esc(model['snapshotTime']) + '</p>',
             '<p class="lead">' + esc(model['summary']) + '</p><p>' + esc(model['scope']) + '</p>',
             '<table><tr><th>测量项目</th><th>当前状态</th><th>主要结果</th></tr>']
    if model.get('smartGate'):
        gate = model['smartGate']
        section = ['<h2>整形 X/2 门校准</h2><table><tr><th>实验</th><th>本轮状态</th></tr>']
        section.extend('<tr><td>' + esc(step['label']) + '</td><td>' + esc(step['status']) + '</td></tr>' for step in gate['steps'])
        section.append('</table>')
        if gate['verified']:
            section.append('<h2>X/2 门条件保真度</h2><table><tr><th>采集</th><th>保真度</th><th>95% 条件置信区间</th></tr>')
            for sample in gate['fidelity']:
                low, high = sample['interval95']
                section.append(f"<tr><td>{esc(sample['role'])}</td><td>{100*sample['value']:.3f}%</td><td>{100*low:.3f}% – {100*high:.3f}%</td></tr>")
            section.append('</table><p>区间由配对序列的 bootstrap 得到，系统模型误差与泄漏另行评估。</p>')
        parts.insert(-1, ''.join(section))
    workflow = report['workflow']
    provenance = {key: workflow[key] for key in ('seedReference', 'completionScope', 'chipVersionId',
                  'deploymentVersionId', 'initializationSource') if key in workflow}
    if provenance:
        parts.append('<details><summary>参数来源与测量范围（技术记录）</summary><pre>' +
                     esc(json.dumps(provenance, ensure_ascii=False, indent=2)) + '</pre></details>')
    for node in model['nodes']:
        parts.append('<tr><td>' + esc(node['label']) + '</td><td>' + esc(node['status']) + '</td><td>' + esc('，'.join(m['label'] + ' ' + m['value'] for m in node['metrics'][:2]) or '等待结果') + '</td></tr>')
    parts.append('</table>')
    parts.append('<section><h2>已提交参数快照</h2><p class="meta">账本可能保留已失效的旧值，本页不认定当前有效校准，也不是仪器读回。各轮实际参数见下表；当前资格以测项验收状态为准。</p>')
    for point in model['workpoints']:
        parts.append('<p><strong>' + esc(point['resourceId']) + '</strong>　' + esc(point['summary']) + '</p>')
        if point.get('qualificationSummary'):
            parts.append('<p class="failed">' + esc(point['qualificationSummary']) + '</p>')
    if not model['workpoints']:
        parts.append('<p>本归档未包含已提交参数快照；不从最近一次扫描推断当前工作点。</p>')
    parts.append('</section>')
    if model['acquisitionHistory']:
        parts.append('<section><h2>采集轮次与工作点</h2><p class="meta">时间为北京时间；幅度为无量纲比例。历史通过不替代当前工况验收。</p><table><tr><th>采集时间 / 测项</th><th>轮次 / 状态</th><th>本轮归档参数</th></tr>')
        for row in model['acquisitionHistory']:
            parts.append('<tr><td>' + esc(row['time']+' · '+row['label']) + '</td><td>' + esc(f'第{row["number"]}次 · {row["role"]} · {row["status"]}') + '</td><td>' + esc(row['workpoint']) +
                         '<details><summary>原始操作来源</summary><pre>' + esc(json.dumps({k: row.get(k) for k in ('nodeId','attemptId','operationId','acquiredAt','parameterSource')},ensure_ascii=False,indent=2)) + '</pre></details></td></tr>')
        parts.append('</table></section>')
    def figures(items):
        content = ['<div class="figures">']
        for figure in items:
            try:
                path, _ = controller.artifact(figure['artifactId'])
                blob = path.read_bytes()
                if blob.startswith(b'\x89PNG\r\n\x1a\n'): mime = 'image/png'
                elif blob.startswith(b'\xff\xd8'): mime = 'image/jpeg'
                else: raise ValueError('不支持的图像类型')
                content.append('<figure><img alt="' + esc(figure['caption']) + '" src="data:' + mime + ';base64,' + base64.b64encode(blob).decode() + '"><figcaption>' + esc(figure['caption']) + '</figcaption></figure>')
            except Exception:
                content.append('<p class="failed">该图暂时无法读取，请检查本地数据文件。</p>')
        return ''.join(content) + '</div>'
    source_nodes = list(report['nodes'])
    for linked in report.get('smartGateReports', []):
        source_nodes.extend(linked['report']['nodes'])
    unmeasured = [i for i, n in enumerate(model['nodes']) if not (
        n['figures'] or n['metrics'] or n['history'] or source_nodes[i]['node'].get('attempt'))]
    ordered = [i for i in range(len(model['nodes'])) if i not in unmeasured] + unmeasured
    for index in ordered:
        node = model['nodes'][index]
        if unmeasured and index == unmeasured[0]:
            parts.append('<details><summary>未采集项目（' + str(len(unmeasured)) + '项）</summary>')
        parts.extend(['<section><h2>' + esc(node['qubits']) + ' · ' + esc(node['label']) + '</h2><p>' + esc(node['summary']) + '</p>'])
        if node.get('acquisitionSummary'):
            parts.append('<p class="meta">本图采集参数：' + esc(node['acquisitionSummary']) + '</p>')
        if node['metrics']:
            parts.append('<table><tr><th>指标</th><th>测量值</th></tr>' + ''.join('<tr><td>' + esc(m['label']) + '</td><td>' + esc(m['value']) + '</td></tr>' for m in node['metrics']) + '</table>')
        parts.append(figures(node['figures']))
        old = [a for a in node['history'] if not a['current']]
        if old:
            parts.append('<details><summary>历次尝试（' + str(len(old)) + '次）</summary>')
            for attempt in old:
                parts.append('<h3>第' + esc(attempt['number']) + '次尝试</h3>' + figures([f for s in attempt['stages'] for f in s['figures']]))
            parts.append('</details>')
        if node['decisions']:
            parts.append('<details><summary>调整过程</summary>' + ''.join('<p><strong>' + esc(d['action']) + '</strong>　' + esc(d['reason']) + '</p>' for d in node['decisions']) + '</details>')
        parts.append('<details><summary>技术记录与数据来源（JSON）</summary><pre>' + esc(json.dumps(source_nodes[index], ensure_ascii=False, indent=2)) + '</pre></details></section>')
    if unmeasured:
        parts.append('</details>')
    parts.append('</main></html>')
    return ''.join(parts).encode('utf-8')
