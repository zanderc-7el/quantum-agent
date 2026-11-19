"""Evidence-bound reader view, shared by HTML and the local PowerPoint exporter."""
from __future__ import annotations
import base64
import hashlib
import html
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def status_label(node, workflow):
    result = node.get('result') or {}
    if node.get('engineState') == 'stale' or result.get('stale'):
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
        return '部分采集'
    return {'acquiring': '正在采集', 'verifying': '正在独立复测', 'waiting_hardware': '等待接线'}.get(node.get('engineState'), '待测' if not result else '待验收')


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


def reader_reasons(result):
    original = ' '.join(str(s) for s in result.get('quality', {}).get('reasons', []))
    rules = [('reference changed', '读出参考在测量前后发生漂移'), ('reference_drift_fraction', '读出参考在测量前后发生漂移'),
             ('model interval is too wide', '拟合置信区间过宽'), ('valid repeat fits', '有效重复拟合数量不足'),
             ('BIC improvement', '衰减拟合未充分优于趋势模型'), ('r_squared', '拟合质量未达到当前门槛'),
             ('contrast_snr', '响应信噪比未达到当前门槛')]
    translated = list(dict.fromkeys(text for fragment, text in rules if fragment.lower() in original.lower()))
    if not translated and original:
        translated = [original] if all(not ('a' <= char.lower() <= 'z' or char in '{}_') for char in original) else ['采集结果未满足本次验收规则']
    return '。'.join(translated) + ('。' if translated else '')


def _stages(attempt):
    result = attempt.get('result') or {}
    if not result:
        return []
    if isinstance(result.get('selectionResult'), dict):
        return [('首测', result['selectionResult']), ('独立复测', result)]
    return [('本次采集', result)]


def reader_model(report):
    wf = report['workflow']
    nodes, ledger = [], []
    for item in report.get('nodes', []):
        node = item['node']
        ok = accepted(node, wf)
        status = status_label(node, wf)
        attempts = item.get('attempts', [])
        latest = next((a for a in attempts if a.get('id') == node.get('latestAttemptId')), None)
        result = node.get('result') or {}
        label = METHOD_LABELS.get(node.get('procedureId'), node.get('label', '测量'))
        summary = f'{label}：{status}。'
        if not ok:
            summary += reader_reasons(result)
        if node.get('procedureId') in ('ro_iq01', 'ro_optimize'):
            summary += '此处衡量读出状态的区分能力。'
        elif node.get('procedureId') == 'sq_allxy':
            summary += '此处检查脉冲组合的响应偏差。'
        elif node.get('procedureId') == 'sq_rb' and not ok:
            summary += '当前单门保真度尚未通过验收。'
        history = []
        for attempt in attempts:
            stages = []
            current = attempt.get('id') == node.get('latestAttemptId')
            for role, stage in _stages(attempt):
                stage_ok = current and ok and role == '独立复测'
                stage_label = '历史回放' if wf.get('mode') == 'replay' or not fresh(stage) else role
                if not current:
                    stage_label += '，历次尝试'
                stage_status = ('部分采集' if stage.get('partial') else
                                '独立复测通过' if stage_ok else
                                '未通过' if stage.get('quality', {}).get('status') == 'failed' else '候选结果')
                figures = [{'artifactId': a['id'], 'caption': f'{label} · 第{attempt.get("number", "?")}次 · {stage_label} · {stage_status}',
                            'role': role, 'status': stage_status}
                           for a in stage.get('artifacts', []) if a.get('kind') == 'image']
                stages.append({'label': stage_label, 'status': stage_status, 'metrics': metric_rows(stage, stage_ok), 'figures': figures})
            history.append({'attemptId': attempt.get('id'), 'number': attempt.get('number'), 'current': current, 'stages': stages})
        figures = [f for a in history if a['current'] for s in a['stages'] for f in s['figures']]
        # Persisted current result may exist before its attempt row is visible; never silently substitute older figures.
        row = {'id': node['id'], 'label': label, 'qubits': node.get('qubits', wf.get('target', '')),
               'status': status, 'summary': summary, 'accepted': ok, 'metrics': metric_rows(result, ok),
               'figures': figures, 'history': history, 'decisions': [
                   {'reason': d.get('reason', ''), 'action': {'retry': '调整后重测', 'backtrack': '回到前置步骤', 'continue': '继续', 'wait': '等待', 'stop': '停止'}.get(d.get('action'), '记录'),
                    'time': d.get('createdAt', '')} for d in item.get('decisions', [])]}
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
    return {'schema': 'qcal-reader-report/1', 'title': f'{wf.get("target", "量子")} 测量报告',
            'workflowId': wf['id'], 'revision': wf.get('revision'), 'generatedAt': report.get('generatedAt'),
            'snapshotTime': snapshot_time,
            'machine': wf.get('machine', ''), 'sourceLabel': source, 'summary': summary, 'scope': scope,
            'verified': count, 'total': len(nodes), 'nodes': nodes, 'claimLedger': ledger,
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
    sentences = [model['summary'], model['scope'], *[n['summary'] for n in model['nodes']]]
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
    for node in model['nodes']:
        parts.append('<tr><td>' + esc(node['label']) + '</td><td>' + esc(node['status']) + '</td><td>' + esc('，'.join(m['label'] + ' ' + m['value'] for m in node['metrics'][:2]) or '等待结果') + '</td></tr>')
    parts.append('</table>')
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
    for index, node in enumerate(model['nodes']):
        parts.extend(['<section><h2>' + esc(node['qubits']) + ' · ' + esc(node['label']) + '</h2><p>' + esc(node['summary']) + '</p>'])
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
        parts.append('<details><summary>技术记录与数据来源（JSON）</summary><pre>' + esc(json.dumps(report['nodes'][index], ensure_ascii=False, indent=2)) + '</pre></details></section>')
    parts.append('</main></html>')
    return ''.join(parts).encode('utf-8')
