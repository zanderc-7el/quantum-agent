"""Current-message authority for exactly one strict workflow node.

The model can only propose identities read from the controller. This module
independently resolves the user's named/selected task and node, checks the fresh
revision and attempt, and invokes existing controller actions in one transaction.
"""
import re
from .common import APIError, digest
from .manual_steps import awaiting_acceptance

RUN = r'重新\s*(?:测量|采集|测|跑)|重测|再\s*(?:测|跑|采集)\s*(?:一次|一遍|一轮)|(?:执行|进行)\s*(?:一次|一轮|本次|本轮)?\s*(?:重新)?\s*(?:测量|采集)|(?:运行|开始|启动|测量|采集|测|跑)'
REMEASURE = r'重新\s*(?:测量|采集|测|跑)|重测|再\s*(?:测|跑|采集)'


def request_text(text):
    """Recognize affirmative action, leaving identity resolution to the ledger."""
    if not isinstance(text,str):return None
    text=text.strip()
    polite=re.fullmatch(r'(?:能不能|可以|能否)\s*帮我\s*(.+?)(?:吗)?\s*[?？。!！]*',text)
    if polite:text=polite[1].strip()
    # A quoted instruction or document/report attribution is data. Quoted task
    # and node names remain resolvable, but action verbs must be outside quotes.
    unquoted=re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"]*"|\x27[^\x27]*\x27|`[^`]*`',' ',text)
    if re.search(r'为什么|为何|怎么|如何|如果|假如|假设|是否|能否|可否|能不能|可不可以|会不会|吗|讨论|解释|示例|教程|建议|[?？]|'
                 r'想问|询问|想了解|想知道|讲讲|(?:重测|测量|采集)(?:的)?(?:结果|历史|记录|进度)|'
                 r'(?:重测|采集|测量|运行)(?:过|完成|结束|了)|'
                 r'(?:文档|附件|日志|源码|报告|提示词).{0,16}(?:说|写|要求|指令|重测|测量)|'
                 r'草稿|原话|原文|引述|转述|转发|引用|(?<!等)待(?:确认|批准)|等我确认|'
                 r'等(?:待)?(?:我|用户)?(?:确认|批准|同意)(?:之后|以后|后|再)|'
                 r'(?:命令|指令|消息|示例|例子|模板)\s*[:：]|'
                 r'(?:他说|她说|技术员说|模型说|建议说|表示|提到)|\b(?:said|says|example|if|explain)\b',unquoted,re.I):return None
    cleaned=re.sub(r'(?:不要|不|勿|无需|不用)(?:自动|再)?(?:运行|执行|启动|继续)(?:任何)?(?:后续|后面|其他|下一)(?:步骤|测项|测量|任务)?','',unquoted)
    cleaned=re.sub(r'(?:不要|不|勿|无需|不用)(?:更改|修改|调整|改变)(?:扫描|任何|现有|当前)?参数','',cleaned)
    cleaned=re.sub(r'不后续|不改参','',cleaned)
    # Any remaining refusal suspends the entire message's permit, including a
    # later “先不要执行” clause. Only explicit no-next/no-parameter constraints
    # above are removed; a model cannot reinterpret a pending plan as authority.
    negative = r'不要|不想|不打算|不准备|不希望|不愿|不需要|不允许|不可以|不该|不应|不再|不必|不能|不用|无需|先不|暂不|暂时不|还不|别|勿|禁止|拒绝|取消|停止|暂停|不(?=对|为|把|将|给|执行|进行|重测|重新|测量|采集|运行|启动|开始)'
    if re.search(negative,cleaned):return None
    if re.search(r'自动|后续|全流程|全部|所有步骤|另外.{0,8}任务|其他任务|修改|调整|更改|设置|调高|调低|改参',cleaned):return None
    if not re.search(RUN,cleaned):return None
    # “当前状态/测量结果” alone is not a measurement command.
    if not re.search(REMEASURE+r'|(?:执行|进行).{0,8}(?:测量|采集)|(?:运行|开始|启动|测|跑).{0,12}(?:本步|这一步|该步骤|一次|一遍|一轮)',cleaned):
        if not re.match(r'^(?:(?:请|麻烦|帮我|现在|直接|先|就)\s*)*(?:'+RUN+r')',cleaned):return None
    return {'text':text,'remeasure':bool(re.search(REMEASURE,cleaned))}


def _named_targets(text,items,label,error_code,message):
    """Match whole quoted names and maximal occurrences, never global longest.

    Each separate mention constrains the same target. A unique ID can resolve a
    duplicated name, but two different explicit targets remain a conflict.
    """
    mentions=[];unquoted=list(text)
    for quote in re.finditer(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"]*"|\x27[^\x27]*\x27|`[^`]*`',text):
        value=quote[0][1:-1]
        ids={item['id'] for item in items if value in (item['id'],item.get(label))}
        noun=r'任务' if label=='name' else r'(?:测项|步骤|节点)'
        explicitly_named=re.search(noun+r'(?:名称|名字|名|编号)?\s*[:：=]?\s*$',text[:quote.start()])
        if label=='name':
            explicitly_named=explicitly_named or re.match(r'\s*的(?:当前)?(?:步骤|测项)',text[quote.end():])
        if explicitly_named and not ids:
            raise APIError(error_code,'指令明确指定的'+('任务' if label=='name' else '测项')+'不存在或不属于当前范围。',409)
        if ids:mentions.append(ids)
        unquoted[quote.start():quote.end()]='\x00'*(quote.end()-quote.start())
    remaining=''.join(unquoted);occurrences=[]
    if label=='name':
        known_ids={item['id'] for item in items}
        if any(match[0] not in known_ids for match in re.finditer(r'(?<![A-Za-z0-9_-])wf-[A-Za-z0-9_-]+',remaining)):
            raise APIError(error_code,'指令明确指定的任务编号不存在或不属于当前范围。',409)
        # A bounded explicit unquoted target must resolve as a whole, including
        # typo/deleted long names whose prefix happens to be another task.
        for mention in re.finditer(r'任务(?:名称|名字|名|编号)?\s*[:：=]?\s*([^\s\x00，,。；;“”「」『』"\x27`]+?)(?=的?当前(?:步骤|测项|状态))',remaining):
            value=mention[1]
            if value in ('的','当前','这个','该','本'):continue
            ids={item['id'] for item in items if value in (item['id'],item.get(label))}
            if not ids:raise APIError(error_code,'指令明确指定的任务不存在或不属于当前范围。',409)
            mentions.append(ids)
    for item in items:
        for key in ('id',label):
            value=item.get(key)
            if not value:continue
            pattern=re.escape(value)
            if key=='id':pattern=r'(?<![A-Za-z0-9_-])'+pattern+r'(?![A-Za-z0-9_-])'
            for match in re.finditer(pattern,remaining):
                if key==label and label=='name' and not re.search(r'任务(?:名称|名字|名|编号)?\s*[:：=]?\s*$',remaining[:match.start()]):continue
                if key==label and not re.match(r'(?:$|[\s\x00，,。；;:：()（）]|的|当前|重新|重测|执行|进行|运行|采集|测量|和|与|及|以及|、)',remaining[match.end():]):continue
                occurrences.append((match.start(),match.end(),item['id']))
    spans={}
    for start,end,item_id in occurrences:
        if any(a<=start and end<=b and (a,b)!=(start,end) for a,b,_ in occurrences):continue
        spans.setdefault((start,end),set()).add(item_id)
    mentions.extend(spans.values())
    if not mentions:return []
    ids=set.intersection(*mentions)
    if len(ids)!=1:raise APIError(error_code,message,409)
    return [item for item in items if item['id'] in ids]


def resolve(ws,body,scope):
    parsed=request_text(body.get('message'))
    if not parsed:raise APIError('EXPLICIT_STEP_REQUIRED','请明确指定某个任务的一项测量；讨论、条件或文档内容不授权执行。',409)
    if scope.get('kind') not in ('workspace','campaign'):
        raise APIError('INVALID_SCOPE','任务讨论不授予执行权限，请使用总控或所属批次对话。',409)
    text=parsed['text'];workflows=[wf for wf in ws.c.list() if wf.get('mode')=='real']
    if scope['kind']=='campaign':
        ids={child['workflowId'] for child in ws.get_campaign(scope['id'])['children']}
        workflows=[wf for wf in workflows if wf['id'] in ids]
    quoted=[m[0][1:-1] for m in re.finditer(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"]*"|\x27[^\x27]*\x27|`[^`]*`',text)]
    deictic={'当前任务','这个任务','本任务','该任务','当前步骤','本步','这一步','该步骤','当前测项'}
    def identities(candidates):
        return deictic|{value for item in candidates for value in (item['id'],item.get('name')) if value}|{
            value for item in candidates for node in item['nodes'] for value in (node['id'],node.get('label')) if value}
    # Quoted content never grants authority through a selected/current fallback.
    # This narrow execution entry accepts only resolvable identity tokens; other
    # quoted data belongs in discussion, not in a hardware execution permit.
    if any(value not in identities(workflows) for value in quoted):
        raise APIError('WORKFLOW_TARGET_REQUIRED','引号中的任务或测项无法唯一核对，请使用当前任务和测项的完整名称。',409)
    named=_named_targets(text,workflows,'name','WORKFLOW_TARGET_REQUIRED','指令对应多个任务，请指定唯一任务。')
    selected=body.get('selectedWorkflowId')
    if named:
        if len(named)!=1:raise APIError('WORKFLOW_TARGET_REQUIRED','指令对应多个任务，请指定唯一任务。',409)
        wf=named[0]
        if selected and selected!=wf['id']:raise APIError('WORKFLOW_SCOPE_REQUIRED','所选任务与指令中的任务不一致。',409)
    elif selected and re.search(r'当前|这个|该任务|本任务',text):
        matches=[wf for wf in workflows if wf['id']==selected]
        if len(matches)!=1:raise APIError('WORKFLOW_TARGET_REQUIRED','当前选中任务不属于该对话范围。',409)
        wf=matches[0]
    else:raise APIError('WORKFLOW_TARGET_REQUIRED','请写出任务名称或选中当前任务。',409)
    if any(value not in identities([wf]) for value in quoted):
        raise APIError('NODE_TARGET_REQUIRED','引号中的测项不属于指定任务，未执行当前步骤。',409)
    if wf.get('policy')!='strict':raise APIError('MANUAL_POLICY_REQUIRED','该入口仅用于人工逐步任务，不切换运行策略。',409)
    nodes=_named_targets(text,wf['nodes'],'label','NODE_TARGET_REQUIRED','请指定唯一测项或当前步骤。')
    if not nodes and re.search(r'当前步骤|本步|这一步|该步骤|当前测项',text):
        nodes=[node for node in wf['nodes'] if node['id']==wf.get('currentNodeId')]
    if len(nodes)!=1:raise APIError('NODE_TARGET_REQUIRED','请指定唯一测项或当前步骤。',409)
    return wf,nodes[0],parsed


def direct_proposal(ws,body,scope):
    if body.get('intent')!='execute' or not request_text(body.get('message')):return None
    # Generic start/pause commands remain owned by their existing parser.
    if not re.search(r'步骤|测项|本步|这一步|重测|重新测量|独立复测',body['message']):return None
    wf,node,parsed=resolve(ws,body,scope)
    return {'tool':'run_manual_step','arguments':{'workflowId':wf['id'],'nodeId':node['id'],
        'expectedRevision':wf['revision'],'expectedAttemptId':node.get('latestAttemptId'),'remeasure':parsed['remeasure']}}


def execute(ws,args,body,conv):
    if (not isinstance(args,dict) or set(args)!={'workflowId','nodeId','expectedRevision','expectedAttemptId','remeasure'}
            or type(args['expectedRevision']) is not int or type(args['remeasure']) is not bool
            or any(not isinstance(args[key],str) or not 1<=len(args[key])<=256 for key in ('workflowId','nodeId'))
            or (args['expectedAttemptId'] is not None and not isinstance(args['expectedAttemptId'],str))):
        raise APIError('INVALID_PROPOSAL','单步提案必须包含读取到的任务、节点、版本和当前尝试编号。')
    command={'operationId':'chat-manual-step/'+digest(body['operationId']), 'arguments':args,
             'message':body['message'],'selectedWorkflowId':body.get('selectedWorkflowId')}
    with ws.store.transaction() as rt:
        prior,fingerprint=ws._command(rt,command,'run_manual_step')
        if prior is not None:return prior
        wf,node,parsed=resolve(ws,body,conv['scope'])
        if (wf['id']!=args['workflowId'] or node['id']!=args['nodeId'] or parsed['remeasure']!=args['remeasure']):
            raise APIError('MANUAL_STEP_SCOPE','提案超出当前指令指定的单个任务和测项。',409)
        wf=ws.c.get(wf['id'],rt);node=ws.c._node(wf,node['id'])
        if wf['revision']!=args['expectedRevision'] or node.get('latestAttemptId')!=args['expectedAttemptId']:
            raise APIError('STALE_MANUAL_STEP','任务或采集已更新，请重新读取后再提交本步。',409)
        if awaiting_acceptance(wf):raise APIError('ACCEPTANCE_REQUIRED','当前结果等待人工验收；本次测量指令不包含代为验收。',409)
        controller=ws.c.for_workflow(wf['id'],rt)
        if node['engineState']=='verified':
            if not args['remeasure']:raise APIError('REMEASURE_REQUIRED','已验收步骤需要明确要求重新测量。',409)
            wf=controller.action(wf['id'],{'operationId':command['operationId']+'/backtrack',
                'expectedRevision':wf['revision'],'action':'backtrack','targetNodeId':node['id'],'nodeId':node['id']},_rt=rt)
        wf=controller.action(wf['id'],{'operationId':command['operationId']+'/run',
            'expectedRevision':wf['revision'],'action':'run_step','nodeId':node['id']},_rt=rt)
        result={'status':'task_receipt','action':'run_step','workflowId':wf['id'],'nodeId':node['id'],
            'workflowState':wf['status'],'workflowRevision':wf['revision'],'remeasure':args['remeasure'],
            'permitOperationId':wf['manualStep']['operationId'],'expectedAttemptId':args['expectedAttemptId'],
            'message':wf['name']+'：已提交“'+node['label']+'”单步测量。控制器完成首测和独立复测后等待人工确认。'}
        ws._remember(rt,command,fingerprint,result)
        return result
