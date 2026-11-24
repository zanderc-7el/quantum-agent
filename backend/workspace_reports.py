"""Report actions from the global conversation, using the ordinary export service."""
import hashlib
import re

from .common import APIError
from .workspace_actions import ACTIONS, command_action, requested_resources, explicit_mutation


def report_requested(text):
    text = text.strip()
    polite = re.fullmatch(r'(?:能不能|能否|可以)\s*帮我\s*(.+?)(?:吗)?[?？。!！]*', text)
    if polite:
        text = polite.group(1)
    if re.search(r'Q{1,2}\d+\s*(?:或者?|还是|\bor\b)\s*Q{1,2}\d+', text, re.I):
        return False
    # Keep this normalization local to reports: "重新" must not broaden the
    # execution grammar for measurement or configuration actions.
    actions='|'.join(ACTIONS.values())
    clauses=re.split(r'[，,。.;；!！\r\n]+|并且|然后|随后|接着|并|同时|但是|但|(?:之后|以后|后|再)(?=\s*(?:'+actions+r'))',text)
    request_clauses=[]
    for clause in clauses:
        clause=clause.strip()
        if not clause:continue
        # These are constraints to preserve state, not new pause/resume orders.
        if re.fullmatch(r'(?:请\s*)?(?:保持|维持)\s*(?:(?:(?:当前|这个|该)?任务|Q{1,2}\d+)\s*)?(?:的)?'
                        r'(?:暂停(?:状态)?|现状|(?:当前)?状态不变)(?:不变|即可|就好|吧)?',clause,re.I):
            continue
        # Never consume a real mixed experiment command in the report shortcut.
        if command_action(re.sub(r'重新(?=(?:'+actions+r'))','',clause,flags=re.I)):
            return False
        request_clauses.append(clause)
    request='。'.join(request_clauses)
    return bool(explicit_mutation(request, r'(?:重新)?(?:导出|生成|制作|做(?:一份|一个|份|个|一下)?)')
                and re.search(r'PPT|PowerPoint|汇报|报告', request, re.I))


def resolve_report_workflow(ws, text, selected=None, explicit_id=None):
    candidates = ws.c.list()
    resources = requested_resources(text)
    if selected and re.search(r'当前|这个|该任务', text):
        candidates = [w for w in candidates if w['id'] == selected]
        if candidates and resources and set(candidates[0].get('resourceIds', [])) != resources:
            raise APIError('WORKFLOW_SCOPE_REQUIRED', '当前任务与指定比特不一致。')
    elif resources:
        candidates = [w for w in candidates if set(w.get('resourceIds', [])) == resources]
    else:
        candidates = [w for w in candidates if w.get('name') and w['name'] in text]
    chosen = [w for w in candidates if w['id'] == selected]
    if chosen:
        candidates = chosen
    if len(candidates) != 1:
        raise APIError('WORKFLOW_TARGET_REQUIRED', '请选中要导出报告的任务。')
    if explicit_id and candidates[0]['id'] != explicit_id:
        raise APIError('WORKFLOW_SCOPE_REQUIRED', '报告对象与指定任务不一致。')
    return candidates[0]


def export_receipt(job):
    state = job['status']
    message = {'queued': 'PPT 已加入生成队列。', 'processing': 'PPT 正在生成。',
               'completed': 'PPT 已生成。', 'failed': 'PPT 生成失败，请重试。',
               'interrupted': 'PPT 生成已中断，请重新导出。'}.get(state, '报告状态待核对。')
    result = {'status': 'report_receipt', 'exportId': job['id'], 'workflowId': job['workflowId'],
              'state': state, 'statusUrl': '/api/report-exports/' + job['id'], 'message': message}
    download = '/api/report-exports/' + job['id'] + '/file'
    if state == 'completed' and job.get('downloadUrl') == download:
        result['downloadUrl'] = download
    return result


def execute_export(ws, args, body, conv, index):
    if conv['scope']['kind'] != 'workspace' or body.get('intent') != 'execute' or not report_requested(body['message']):
        raise APIError('EXPLICIT_REQUEST_REQUIRED', '请在总 Agent 中指定要导出的任务。')
    if not isinstance(args, dict) or set(args) - {'workflowId', 'plotsPerAppendix'} or not isinstance(args.get('workflowId'), str):
        raise APIError('INVALID_REPORT', '报告请求无效。')
    appendix = args.get('plotsPerAppendix', 16)
    if type(appendix) is not int or appendix not in (16, 64):
        raise APIError('INVALID_REPORT', '每页附录支持 16 或 64 张图。')
    wf = resolve_report_workflow(ws, body['message'], body.get('selectedWorkflowId'), args['workflowId'])
    # A fixed operation identity survives retries even when a later scan advances revision.
    operation = 'workspace-report-' + hashlib.sha256((body['operationId'] + ':' + str(index)).encode()).hexdigest()
    service = ws.c.report_exports
    for prior in service.list(wf['id']) if hasattr(service, 'list') else []:
        if prior.get('operationId') == operation:
            return export_receipt(prior)
    job = service.create(wf['id'], {'operationId': operation, 'expectedRevision': wf['revision'],
                                  'format': 'pptx', 'appendixCount': appendix})
    return export_receipt(job)
