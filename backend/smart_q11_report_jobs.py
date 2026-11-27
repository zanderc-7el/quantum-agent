"""Durable post-acquisition report for a fully verified one-start Q11 run."""
import json
import sqlite3
import time
from contextlib import closing

from .common import APIError, digest, now


def enqueue(rt, wf):
    smart = wf['smartRun']
    if smart.get('main') != 'verified' or smart.get('gate') != 'verified' or not smart.get('gateEvidence'):
        raise APIError('SMART_GATE_REQUIRED', '报告等待本轮同一 X/2 门的独立复测', 409)
    key = digest({'workflowId': wf['id'], 'evidence': smart['gateEvidence'], 'children': smart['children']})
    rt.db.execute('''CREATE TABLE IF NOT EXISTS platform_smart_reports(
        workflow_id TEXT PRIMARY KEY, evidence_key TEXT NOT NULL, observed_revision INTEGER NOT NULL,
        generation INTEGER NOT NULL DEFAULT 1, job_id TEXT, state TEXT NOT NULL, due REAL NOT NULL)''')
    rt.db.execute('''INSERT INTO platform_smart_reports(workflow_id,evidence_key,observed_revision,state,due)
        VALUES(?,?,?,'pending',0) ON CONFLICT(workflow_id) DO UPDATE SET evidence_key=excluded.evidence_key,
        observed_revision=excluded.observed_revision,state='pending',generation=1,job_id=NULL,due=0
        WHERE evidence_key<>excluded.evidence_key''', (wf['id'],key,wf['revision']+1))


def _owner(wf, item):
    smart = wf.get('smartRun') or {}
    return (wf.get('status') in ('queued','running') and not wf.get('cancelRequested')
            and smart.get('schema') == 'q11-full-same-gate/1' and smart.get('main') == 'verified'
            and smart.get('gate') == 'verified' and smart.get('gateEvidence')
            and item['evidence_key'] == digest({'workflowId': wf['id'], 'evidence': smart['gateEvidence'],
                                                'children': smart.get('children')}))


def tick(controller):
    with closing(sqlite3.connect(controller.store.path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='platform_smart_reports' AND type='table'").fetchone():
            return
        row = db.execute("""SELECT * FROM platform_smart_reports WHERE state IN ('pending','exporting')
                               AND due<=? ORDER BY due LIMIT 1""", (time.time(),)).fetchone()
        if not row:
            return
        item = dict(row)
    wf = controller.get(item['workflow_id'])
    wf_id, key = wf['id'], item['evidence_key']
    if not _owner(wf,item):
        with controller.store.transaction() as rt:
            if wf.get('status') == 'aborted':
                rt.db.execute("UPDATE platform_smart_reports SET state='cancelled' WHERE workflow_id=? AND evidence_key=?",(wf_id,key))
            else:
                rt.db.execute('UPDATE platform_smart_reports SET due=? WHERE workflow_id=? AND evidence_key=?',
                              (time.time()+10, wf_id,key))
        return
    exports = controller.report_exports
    op = f"smart-report/{key}/{item['generation']}"
    error, proof = None, None
    try:
        job = (exports.get(item['job_id']) if item['job_id'] else
               exports.create(wf_id, {'operationId':op,'expectedRevision':item['observed_revision'],
                                      'format':'pptx','appendixCount':16}))
        if job['workflowId'] != wf_id or job['operationId'] != op:
            raise APIError('SMART_REPORT_SCOPE','导出回执未绑定当前 Q11 测量',409)
        if job['status'] == 'completed':
            exports.file(job['id'])
            from .common import checked_path, file_hash
            directory = checked_path(exports.output_root / job['id'], parent=exports.output_root)
            reader = json.loads((directory/'reader.json').read_text('utf-8'))
            validation = json.loads((directory/'build'/'validation.json').read_text('utf-8'))
            gate = reader.get('smartGate') or {}
            evidence = wf['smartRun']['gateEvidence']
            pair = gate.get('fidelity', [])
            if (reader.get('workflowId') != wf_id or reader.get('revision') != job['revision']
                    or gate.get('verified') is not True or len(pair) != 2
                    or not all(p.get('sourceWorkflowId') == evidence.get('childWorkflowId') and
                               p.get('attemptId') == evidence.get('attemptId') for p in pair)
                    or file_hash(directory/'report.json') != job.get('snapshotSha256')
                    or validation.get('passed') is not True or validation.get('sha256') != job.get('sha256')
                    or not job.get('archiveId')):
                raise APIError('SMART_REPORT_PROOF','报告缺少本轮同门结果或文件校验',409)
            proof = {k:job[k] for k in ('id','sha256','bytes','archiveId','revision','downloadUrl')}
        elif job['status'] in ('failed','interrupted'):
            error = job.get('error',{}).get('code') or job['status']
    except (OSError,ValueError,APIError) as exc:
        job = None
        error = exc.code if isinstance(exc,APIError) else type(exc).__name__
    with controller.store.transaction() as rt:
        wf = controller.get(wf_id,rt)
        latest = rt.db.execute('SELECT * FROM platform_smart_reports WHERE workflow_id=?',(wf_id,)).fetchone()
        if not latest or dict(latest) != item or not _owner(wf,item):
            return
        if proof:
            rt.db.execute("UPDATE platform_smart_reports SET state='completed',job_id=? WHERE workflow_id=?", (proof['id'],wf_id))
            wf['smartRun'].update(report='completed',reportEvidence=proof)
            wf.update(workflowComplete=True,status='completed',engineState='verified',progress=100)
            wf.setdefault('resultSummary',{}).update(workflowComplete=True,meaning='主流程与同一 X/2 门独立复测通过，报告已归档。')
            controller._finish_timing(wf)
            controller.store.save(rt,wf,'smart_report_archived',{'message':'X/2 门保真度报告已归档','reportId':proof['id']})
        elif error:
            # A running manual export is a queue condition. Preserve the original intent.
            if error == 'REPORT_EXPORT_ACTIVE':
                rt.db.execute('UPDATE platform_smart_reports SET due=? WHERE workflow_id=?',(time.time()+5,wf_id))
                return
            # Interrupted rendering is safe to retry. Trace failures stay visible and retain the source data.
            retry = error in ('EXPORT_INTERRUPTED','interrupted')
            rt.db.execute('UPDATE platform_smart_reports SET state=?,generation=generation+?,job_id=?,due=? WHERE workflow_id=?',
                          ('pending' if retry else 'failed',int(retry),None if retry or not job else job['id'],time.time()+5,wf_id))
            wf['smartRun']['report'] = 'retrying' if retry else 'failed'
            if not retry:
                wf.update(status='attention',engineState='report_failed')
            controller.store.save(rt,wf,'smart_report_error',{'message':'报告中断，正在重新生成' if retry else '报告归档失败，请查看导出详情',
                                                              'code':error})
        else:
            rt.db.execute("UPDATE platform_smart_reports SET state='exporting',job_id=?,due=? WHERE workflow_id=?",
                          (job['id'],time.time()+2,wf_id))
