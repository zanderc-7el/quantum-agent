"""Bounded, read-only stability snapshots; no scheduler or adapter calls."""
from contextlib import closing
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from html import escape
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote, urlencode

from .common import APIError, digest, dumps, now, number
from .stability_clock import POLICY as ACQUISITION_CLOCK

MAX_SCHEDULES = 1000
MAX_ROUNDS = 10000
MAX_BYTES = 8 * 1024 * 1024
BEIJING = timezone(timedelta(hours=8))
FILTERS = ('startDate','endDate','chipVersionId','resourceId','sourceWorkflowId')
STATUS = {'draft':'未启动','queued':'等待执行','running':'采集中','completed':'完成',
          'failed':'失败','interrupted':'采集中断','paused':'暂停','aborted':'终止'}


def _filters(query):
    if set(query)-set(FILTERS)-{'format','download'}:
        raise APIError('INVALID_STABILITY_REPORT_FILTER','未知报告筛选条件')
    out={}
    for key,values in query.items():
        if not isinstance(values,list) or len(values)!=1 or not isinstance(values[0],str) or len(values[0])>256:
            raise APIError('INVALID_STABILITY_REPORT_FILTER','每个筛选条件只能有一个值')
        value=values[0]
        if any(ord(c)<32 for c in value):raise APIError('INVALID_STABILITY_REPORT_FILTER','筛选值含无效字符')
        if key in FILTERS and value:out[key]=value
    if query.get('format',['json'])[0] not in ('json','html') or query.get('download',['0'])[0] not in ('0','1'):
        raise APIError('INVALID_STABILITY_REPORT_FILTER','请选择 JSON 或 HTML 报告')
    bounds=[]
    for key in ('startDate','endDate'):
        value=out.get(key)
        try:
            if value and not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError()
            day=date.fromisoformat(value) if value else None
            if key=='endDate' and day:day+=timedelta(days=1)
            bounds.append(datetime.combine(day,time(),BEIJING) if day else None)
        except (ValueError,OverflowError):raise APIError('INVALID_STABILITY_REPORT_FILTER','日期须为有效的 YYYY-MM-DD') from None
    if bounds[0] and bounds[1] and bounds[0]>=bounds[1]:
        raise APIError('INVALID_STABILITY_REPORT_FILTER','开始日期不能晚于结束日期')
    return {**out,'timezone':'Asia/Shanghai','dateBasis':'round_acquisition_or_legacy_queue_at','endDateInclusive':True},bounds


def _stamp(value):
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        return stamp if stamp.tzinfo else None
    except (ValueError,TypeError,AttributeError):return None


def _load(db,table,identity,workflow=None):
    sql=f'SELECT payload FROM {table} WHERE id=?'
    args=[identity]
    if workflow is not None:sql+=' AND workflow_id=?';args.append(workflow)
    row=db.execute(sql,args).fetchone()
    return json.loads(row[0]) if row else None


def _clock(db, schedule, row):
    queued = row.get('startedAt')
    if schedule.get('clockPolicy') != ACQUISITION_CLOCK:
        return queued, queued, '入队时间（旧计划）', None
    anchor = row.get('intervalAnchor') or {}
    attempt = _load(db, 'platform_attempts', anchor.get('attemptId'), row['workflowId']) or {}
    result = attempt.get('result') or {}
    selection = result.get('selectionResult') or result
    valid = (anchor.get('schema') == 'qcal-stability-interval-anchor/1'
        and anchor.get('basis') == ACQUISITION_CLOCK and anchor.get('workflowId') == row['workflowId']
        and anchor.get('nodeId') == attempt.get('nodeId') and attempt.get('procedureId') == 'sq_spectroscopy'
        and anchor.get('operationId') == attempt.get('operationId') == str(attempt.get('id'))+'/measure'
        and selection.get('dataKind') == 'real' and not selection.get('partial')
        and _stamp(anchor.get('acquiredAt')) is not None and selection.get('acquiredAt') == anchor['acquiredAt'])
    if valid:
        return anchor['acquiredAt'], anchor['acquiredAt'], '首测采集完成', None
    return queued, None, '入队（尚无可核验采集时间）', '首测采集时间尚未获得或来源待核查'


def operation_binding(attempt):
    """Bind both real acquisitions to the persisted attempt operation IDs."""
    identity=attempt.get('id')
    if not isinstance(identity,str) or not identity:return False
    result=attempt.get('result') or {}
    selected=result.get('selectionResult') or {}
    for field,suffix,receipt in (('operationId','/measure',selected),
                                 ('verificationOperationId','/verify',result)):
        operation=attempt.get(field)
        provenance=receipt.get('provenance') or {}
        if (operation!=identity+suffix or receipt.get('dataKind')!='real'
                or provenance.get('operationId')!=operation or provenance.get('freshAcquisition') is not True
                or any(receipt.get(k) for k in ('stale','partial','verificationPending'))):return False
    return True


def _evidence(db,child,schedule,row,kind):
    method='sq_spectroscopy' if kind=='frequency' else 'sq_t1'
    identity=row.get('frequencyAttemptId' if kind=='frequency' else 't1AttemptId')
    if not identity:return None,None
    a=_load(db,'platform_attempts',identity,row['workflowId'])
    label='定频' if kind=='frequency' else 'T₁'
    bad=label+'来源不完整或已失效，未计入有效值'
    if not child or not a:return None,bad
    binding=child.get('stabilityBinding',{})
    if (child.get('mode')!='real' or schedule['resourceId'] not in child.get('resourceIds',[])
            or binding.get('scheduleId')!=schedule['id'] or binding.get('round')!=row['number']
            or child.get('chipVersionId')!=schedule.get('chipVersionId')
            or child.get('deploymentVersionId')!=schedule.get('deploymentVersionId')):return None,bad
    node=next((n for n in child.get('nodes',[]) if n.get('id')==a.get('nodeId')),None)
    r=a.get('result') or {}
    if (not node or node.get('procedureId')!=method or node.get('resourceId')!=schedule['resourceId']
            or a.get('procedureId')!=method or a.get('state')!='verified'
            or node.get('engineState')!='verified' or node.get('latestAttemptId')!=identity or node.get('result')!=r
            or not operation_binding(a)
            or r.get('quality',{}).get('status')!='passed'
            or any(r.get(k) for k in ('stale','partial','verificationPending'))
            or not _stamp(r.get('acquiredAt'))):return None,bad
    value=r.get('candidateParameters',{}).get('drive_hz') if kind=='frequency' else r.get('metrics',{}).get('t1_s')
    if not number(value) or value<=0:return None,bad
    expected=row.get('frequencyHz' if kind=='frequency' else 't1Seconds')
    if value!=expected:return None,label+'来源值与周期保存值不同，未计入有效值'
    actual=r.get('parameters',{}).get('drive_hz')
    if kind=='t1' and actual!=row.get('frequencyHz'):return None,'T₁实际驱动频率与本轮定频不同，未计入有效值'
    selection=r.get('selectionResult') or {}
    return {'attemptId':identity,'nodeId':node['id'],'procedureId':method,'value':value,
        'acquiredAt':r['acquiredAt'],'operationId':a['operationId'],
        'verificationOperationId':a['verificationOperationId'],
        'selection':{'acquiredAt':selection.get('acquiredAt'),
            'candidateParameters':deepcopy(selection.get('candidateParameters',{})),
            'resultSha256':digest(selection)} if selection else None,
        'verificationCandidateParameters':deepcopy(r.get('verificationCandidateParameters',{})),
        'actualDriveHz':actual,'resultSha256':digest(r),'quality':deepcopy(r.get('quality')),
        'series':deepcopy(r.get('series',[])),
        'artifacts':[{k:x[k] for k in ('id','name','kind','sha256') if k in x} for x in r.get('artifacts',[])],
        'evidenceMeaning':'Saved verified result and scope rechecked; artifact bytes are not rehashed by this report'},None


def snapshot(database,query):
    filters,(start,end)=_filters(query)
    result={'schema':'qcal-stability-report/1','capturedAt':now(),'filters':filters,'groups':[],
        'choices':{'chipVersionIds':[],'resourceIds':[],'sourceWorkflowIds':[]},
        'choiceLabels':{'chipVersionId':{},'deploymentVersionId':{},'sourceWorkflowId':{}},
        'snapshotPolicy':'single_read_transaction','hardwareDispatched':False,
        'dateMeaning':'新计划按定频首测采集完成时刻筛选；尚无可核验采集时间及旧计划按入队时刻列出。结束日期含当日（北京时间）。',
        'interpretation':'只归集已有周期记录，不生成缺失轮次，不以报告代替物理验收。'}
    groups={};included=0
    uri=Path(database).resolve().as_uri()+'?mode=ro'
    with closing(sqlite3.connect(uri,uri=True)) as db:
        db.execute('BEGIN')
        has_config=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='config_versions'").fetchone())
        def label(field,identity):
            if not identity:return None
            labels=result['choiceLabels'][field]
            if identity not in labels:
                entity=_load(db,'platform_workflows',identity) if field=='sourceWorkflowId' else _load(db,'config_versions',identity) if has_config else None
                prefix={'sourceWorkflowId':'任务','chipVersionId':'芯片版本','deploymentVersionId':'接线版本'}[field]
                labels[identity]=(entity or {}).get('name') or prefix+' '+identity[-8:]
            return labels[identity]
        for field in ('chipVersionId','sourceWorkflowId'):
            label(field,filters.get(field))
        schedules=db.execute('SELECT payload FROM platform_stability ORDER BY id LIMIT ?', (MAX_SCHEDULES+1,)).fetchall()
        if len(schedules)>MAX_SCHEDULES:raise APIError('STABILITY_REPORT_LIMIT','周期记录过多，请归档后再生成报告',413)
        for packed, in schedules:
            schedule=json.loads(packed)
            chip_name=label('chipVersionId',schedule.get('chipVersionId'))
            deployment_name=label('deploymentVersionId',schedule.get('deploymentVersionId'))
            origin_name=label('sourceWorkflowId',schedule.get('sourceWorkflowId'))
            for key,field in [('chipVersionIds','chipVersionId'),('resourceIds','resourceId'),('sourceWorkflowIds','sourceWorkflowId')]:
                value=schedule.get(field)
                if value and value not in result['choices'][key]:result['choices'][key].append(value)
            if any(filters.get(key) and schedule.get(key)!=filters[key] for key in ('chipVersionId','resourceId','sourceWorkflowId')):continue
            known=bool(schedule.get('chipVersionId') and schedule.get('deploymentVersionId'))
            group_key=(schedule.get('chipVersionId'),schedule.get('deploymentVersionId'),schedule['resourceId'],
                       None if known else schedule['sourceWorkflowId'])
            for row in schedule.get('rounds',[]):
                display_at, plot_at, time_basis, clock_issue = _clock(db, schedule, row)
                stamp=_stamp(display_at)
                if stamp is None:raise APIError('STABILITY_REPORT_TIME','周期记录缺少带时区的开始时间，不能静默丢弃',409)
                if (start and stamp<start) or (end and stamp>=end):continue
                included+=1
                if included>MAX_ROUNDS:raise APIError('STABILITY_REPORT_LIMIT','报告超过轮次上限，请缩小日期或芯片范围',413)
                group=groups.setdefault(group_key,{'id':digest(group_key),'chipVersionId':group_key[0],
                    'deploymentVersionId':group_key[1],'resourceId':group_key[2],
                    'chipVersionName':chip_name,'deploymentVersionName':deployment_name,
                    'sourceWorkflowName':None if known else origin_name,
                    'identityStatus':'published_binding' if known else 'unconfirmed_design',
                    'sourceWorkflowId':group_key[3],'rows':[]})
                child=_load(db,'platform_workflows',row['workflowId'])
                frequency,fe=_evidence(db,child,schedule,row,'frequency')
                t1,te=_evidence(db,child,schedule,row,'t1')
                if t1 and not frequency:t1=None;te='本轮有效频率缺失，T₁未计入同轮趋势'
                ref=row.get('referenceFrequencyHz')
                group['rows'].append({'scheduleId':schedule['id'],'scheduleName':schedule.get('name',schedule['id']),
                    'scheduleRevision':schedule['revision'],'scheduleState':schedule['state'],
                    'sourceWorkflowId':schedule['sourceWorkflowId'],'workflowId':row['workflowId'],
                    'workflowRevision':child.get('revision') if child else None,'number':row['number'],
                    'state':row['state'],'startedAt':row['startedAt'],'finishedAt':row.get('finishedAt'),
                    'displayAt':display_at,'plotAt':plot_at,'timeBasis':time_basis,
                    'clockPolicy':schedule.get('clockPolicy'),'intervalAnchor':deepcopy(row.get('intervalAnchor')),
                    'frequencyHz':frequency['value'] if frequency else None,'t1Seconds':t1['value'] if t1 else None,
                    'referenceFrequencyHz':ref,'referenceDeltaHz':frequency['value']-ref if frequency and number(ref) else None,
                    'adjacentDeltaHz':None,'previousRound':None,'frequencyEvidence':frequency,'t1Evidence':t1,
                    'archivedValues':{k:row.get(k) for k in ('frequencyHz','t1Seconds','driftHz')},
                    'error':row.get('error'),'evidenceIssues':[x for x in (fe,te,clock_issue) if x],
                    'frequencyAttempts':deepcopy(row.get('frequencyAttempts',[])),
                    'recoveryEvents':deepcopy(row.get('recoveryEvents',[])),
                    'calibrationHash':schedule.get('calibrationHash')})
        db.rollback()
    for group in groups.values():
        group['rows'].sort(key=lambda r:(_stamp(r['displayAt']),r['scheduleId'],r['number']))
        previous=None
        for row in group['rows']:
            if previous:
                row['previousRound']={k:previous[k] for k in ('scheduleId','number','startedAt','displayAt','timeBasis','frequencyHz')}
                if number(previous['frequencyHz']) and number(row['frequencyHz']):row['adjacentDeltaHz']=row['frequencyHz']-previous['frequencyHz']
            previous=row
        result['groups'].append(group)
    rows=[r for g in result['groups'] for r in g['rows']]
    result['summary']={'rounds':len(rows),'validFrequencyRounds':sum(r['frequencyHz'] is not None for r in rows),
        'validT1Rounds':sum(r['t1Seconds'] is not None for r in rows),'groups':len(groups)}
    result['snapshotSha256']=digest(result)
    if len(dumps(result).encode('utf-8'))>MAX_BYTES:raise APIError('STABILITY_REPORT_LIMIT','报告证据超过8MiB，请缩小筛选范围',413)
    return result


def _text(value):return escape(str(value),quote=True)


def _local(value):
    stamp=_stamp(value)
    return stamp.astimezone(BEIJING).strftime('%Y-%m-%d %H:%M:%S')+'（北京时间）' if stamp else '未记录'


def _value(value,unit='',scale=1):return f'{value*scale:.9g} {unit}'.strip() if number(value) else '—'


def _plot(rows,key,title,scale,unit):
    valid=[r for r in rows if number(r[key]) and _stamp(r.get('plotAt',r['startedAt']))]
    if not valid:return '<p>'+title+'：暂无有效数据</p>'
    timestamps=[_stamp(r.get('plotAt',r['startedAt'])) for r in rows]
    known=[t.timestamp() for t in timestamps if t];base=min(known);span=max(known)-base or 1
    values=[r[key]*scale for r in valid];lo=min(values);hi=max(values);delta=hi-lo or max(abs(hi)*.02,1)
    low=lo-delta*.1;high=hi+delta*.1
    def ycoord(v):return 150-(v-low)/(high-low)*115
    parts=[];previous=None
    for row,stamp in zip(rows,timestamps):
        if not number(row[key]) or stamp is None:previous=None;continue
        x=65+(stamp.timestamp()-base)/span*590;y=ycoord(row[key]*scale)
        if previous:parts.append(f'<line x1="{previous[0]}" y1="{previous[1]}" x2="{x}" y2="{y}" stroke="#087f8c"/>')
        parts.append(f'<circle cx="{x}" cy="{y}" r="4" fill="#087f8c"><title>{_text(_local(row.get("displayAt",row["startedAt"])))}: {_value(row[key],unit,scale)}</title></circle>');previous=(x,y)
    ticks=[hi] if hi==lo else [hi,lo]
    labels=''.join(f'<text x="0" y="{ycoord(v)}" dominant-baseline="middle">{v:.8g}</text>' for v in ticks)
    return f'<figure><figcaption>{title}（{unit}；横轴按表中时间基准，缺测断开）</figcaption><svg viewBox="0 0 710 190" role="img" aria-label="{title}">{labels}{"".join(parts)}<text x="65" y="180">{_text(_local(rows[0].get("displayAt",rows[0]["startedAt"])))}</text></svg></figure>'


def render_html(report):
    f=report['filters'];choices=report['choices'];parts=[]
    def select(key,label,values):
        opts=['<option value="">全部</option>']
        values=list(values)
        if f.get(key) and f[key] not in values:values.append(f[key])
        for value in values:
            shown=report.get('choiceLabels',{}).get(key,{}).get(value) or (value if key=='resourceId' else '记录 '+value[-8:])
            opts.append(f'<option value="{_text(value)}"'+(' selected' if f.get(key)==value else '')+f'>{_text(shown)}</option>')
        return f'<label>{label}<select name="{key}">{"".join(opts)}</select></label>'
    fields=''.join(f'<label>{label}<input type="date" name="{key}" value="{_text(f.get(key,""))}"></label>' for key,label in [('startDate','开始日期'),('endDate','结束日期')])
    fields+=select('chipVersionId','芯片版本',choices['chipVersionIds'])+select('resourceId','比特',choices['resourceIds'])+select('sourceWorkflowId','来源任务',choices['sourceWorkflowIds'])
    query={k:f[k] for k in FILTERS if f.get(k)}
    download='/api/stability/report?'+urlencode({**query,'format':'html','download':'1'})
    for group in report['groups']:
        identity=group.get('chipVersionName') or '版图未确认（按来源任务隔离）'
        parts.append(f'<section><h2>{_text(group["resourceId"])} · {_text(identity)}</h2><p>接线版本：{_text(group.get("deploymentVersionName") or "未确认")}；来源：{_text(group.get("sourceWorkflowName") or "同已发布绑定归集")}</p>')
        rows=group['rows'];parts.append(_plot(rows,'frequencyHz','频率趋势',1e-9,'GHz'));parts.append(_plot(rows,'t1Seconds','T₁趋势',1e6,'μs'))
        parts.append('<table><thead><tr><th>计划/轮次/时间基准</th><th>状态</th><th>频率与采集时刻</th><th>相邻两轮/参考差</th><th>T₁与实际驱动</th><th>来源/异常</th></tr></thead><tbody>')
        for row in rows:
            fe=row['frequencyEvidence'];te=row['t1Evidence'];prev=row['previousRound']
            freq=_value(row['frequencyHz'],'GHz',1e-9) if fe else '未获得有效频率'
            tv=_value(row['t1Seconds'],'μs',1e6) if te else '本轮未测或来源待核查'
            adjacent=_value(row['adjacentDeltaHz'],'Hz') if row['adjacentDeltaHz'] is not None else '相邻有效值不足'
            if prev:adjacent+=f'<br>前轮 {_text(_local(prev.get("displayAt",prev["startedAt"])))} / {_value(prev["frequencyHz"],"GHz",1e-9)}'
            evidence={'queuedAt':row['startedAt'],'intervalAnchor':row.get('intervalAnchor'),'frequency':fe,'t1':te,'frequencyAttempts':row['frequencyAttempts'],'recoveryEvents':row['recoveryEvents'],'archivedValues':row['archivedValues']}
            link='/api/workflows/'+quote(row['workflowId'],safe='')+'/report?format=html'
            issues='；'.join([row.get('error') or '',*row['evidenceIssues']]).strip('；')
            parts.append(f'<tr><td>{_text(row["scheduleName"])} / {row["number"]}<br>{_text(row.get("timeBasis","入队时间（旧计划）"))}：{_text(_local(row.get("displayAt",row["startedAt"])))}</td><td>{_text(STATUS.get(row["state"],row["state"]))}</td><td>{freq}<br>{_text(_local(fe.get("acquiredAt") if fe else None))}</td><td>{adjacent}<br>对参考 {_value(row["referenceFrequencyHz"],"GHz",1e-9)}：{_value(row["referenceDeltaHz"],"Hz")}</td><td>{tv}<br>{_text(_local(te.get("acquiredAt") if te else None))}<br>实际驱动：{_value(te.get("actualDriveHz") if te else None,"GHz",1e-9)}</td><td>{_text(issues)}<br><a href="{link}">打开本轮工作流报告</a><details><summary>原始曲线与来源摘要</summary><pre>{_text(json.dumps(evidence,ensure_ascii=False,indent=2))}</pre></details></td></tr>')
        parts.append('</tbody></table></section>')
    if not report['groups']:parts.append('<p class="empty">暂无周期记录</p>')
    raw=json.dumps(report,ensure_ascii=False).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    html=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>周期稳定性报告</title><style>body{{font:15px system-ui;margin:28px;color:#172b35}}h1,h2{{color:#08717e}}form{{display:flex;gap:12px;flex-wrap:wrap}}label{{display:grid;gap:4px}}input,select,button{{font:inherit;padding:7px}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;vertical-align:top;border:1px solid #cbd5df;padding:9px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;max-width:650px}}figure{{display:inline-block;width:47%;min-width:320px;margin:10px 1%}}svg{{width:100%}}svg text{{font-size:12px}}small{{overflow-wrap:anywhere}}section{{margin-top:28px}}@media print{{form,.download{{display:none}}body{{margin:0}}tr{{break-inside:avoid}}}}</style><h1>周期定频与同轮 T₁ 稳定性报告</h1><p>快照截至 {_text(_local(report['capturedAt']))}。{_text(report['dateMeaning'])}</p><form method="GET" action="/api/stability/report"><input type="hidden" name="format" value="html">{fields}<button>按范围查看</button></form><p class="download"><a href="{_text(download)}" download>下载当前范围 HTML</a>（离线副本保留曲线、数值与来源；重新下载会捕获当时最新快照）</p><p>共 {report['summary']['rounds']} 轮；有效频率 {report['summary']['validFrequencyRounds']} 轮；同轮 T₁ {report['summary']['validT1Rounds']} 轮。</p><details><summary>报告来源与快照摘要</summary><p>{_text(report['interpretation'])}</p><small>快照 SHA256：{report['snapshotSha256']}；单次只读事务。原始附件摘要随报告保留，本报告不重新验证每份附件字节。</small></details>{''.join(parts)}<script type="application/json" id="stability-snapshot">{raw}</script></html>'''
    encoded=html.encode('utf-8')
    if len(encoded)>MAX_BYTES*3:raise APIError('STABILITY_REPORT_LIMIT','HTML报告过大，请缩小筛选范围',413)
    return encoded

