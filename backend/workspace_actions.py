"""Natural operator commands using existing controller execution contracts."""
import re
from .common import APIError

ACTIONS = {'pause':r'暂停|\bpause\b', 'abort':r'终止|停止|取消|\babort\b|\bstop\b',
           'resume':r'继续|恢复|\bresume\b',
           'skip':r'跳过|\bskip\b',
           'start':r'开始|启动|跑|测一下|测一遍|测控|测量|测(?=\s*(?:Q{1,2}\d|八号机))|\bstart\b|\brun\b'}
COMMAND_PREFIX = r'^(?:(?:好的|好吧|好|行吧|行|那|请|麻烦|劳驾|帮我|帮忙|给我|你|您|直接|现在|马上|立即|先|就|再|只|我需要你|我要你|我想让你|please\b)\s*)*'
NO_IMPORT_CLAUSE = r'(?:也)?(?:不用|无需|不必|不要|暂不|先不|不需要)(?:再|重新|先|额外)?(?:导入|上传|分析|读取|读|整理)(?:芯片|交付)?(?:文件|资料|文档)?(?:了|吧)?'
MANUAL_CLAUSE = r'(?:(?:按|用|采用)?(?:(?:人工|手动)(?:模式)?|(?:人工|手动)?(?:逐步|单步|一步一步)(?:模式|执行|测量)?)|每(?:一)?步(?:都)?(?:由我|让我|我|人工|手动)?(?:确认|验收)(?:后再继续)?|manual(?: mode)?|step[- ]by[- ]step)'

def manual_requested(text):
    return bool(re.search(r'手动|人工(?:模式|逐步|单步)|一步一步|每(?:一)?步.{0,8}(?:确认|验收)|逐步(?:模式|执行|测量)|\bmanual\b|\bstep[- ]by[- ]step\b',text,re.I))

def explicit_mutation(text, verbs):
    """A write verb inside a question, quotation or report is not permission."""
    if re.search(r'为什么|为何|怎么|如何|如果|假如|假设|是否|能否|可否|什么|哪里|多久|多少|讨论|解释|示例|比如|例如|教程|建议|[吗呢么?？“”‘’「」『』\"\x27`]|说|表示|提到|(?:的|运行|测量)(?:结果|状态|进度|记录|历史)|\b(?:said|says|asked)\b',text,re.I):
        return False
    if re.search(r'(?:不要|不用|先不|暂不|不必|不能|不可|不想|别|勿|禁止|\bnot\b|\bnever\b)\s*(?:(?:再|先|立即|帮我|重新|直接|马上|也)\s*)*(?:'+verbs+r')',text,re.I):
        return False
    for clause in re.split(r'[，,。.;；!！\r\n]+|并且|然后|随后|并',text):
        stripped=re.sub(COMMAND_PREFIX,'',clause.strip(),count=1,flags=re.I)
        if (re.match(r'(?:'+verbs+r')',stripped,re.I) or
                re.match(r'(?:给|为|把|将)?\s*Q{1,2}\d+\s*(?:的参数|的配置|配置|参数)?\s*(?:'+verbs+r')',stripped,re.I)):
            return True
    return False

def requested_resources(text):
    normalized=re.sub(r'(?i)QQ(?=\d)', 'Q',text)
    result={q.upper() for q in re.findall(r'(?<![A-Za-z0-9_])Q\d+(?![A-Za-z0-9_])',normalized,re.I)}
    for a,b in re.findall(r'Q(\d+)\s*(?:到|至|[~～—–-])\s*Q?(\d+)',normalized,re.I):
        if 0<=int(b)-int(a)<200:result.update('Q'+str(i) for i in range(int(a),int(b)+1))
    return result


def scoped_workflow_request(text):
    """Parse a complete single-task envelope; never erase arbitrary negation.

    Quotes delimit a task label only after an explicit control verb. Every
    remaining clause must be an exact identity, preservation or scope clause.
    Resolution below authenticates these labels/IDs against the current ledger.
    """
    clauses=[x.strip() for x in re.split(r'[，,。.;；!！\r\n]+',text.strip()) if x.strip()]
    if not clauses:return None
    head=re.sub(COMMAND_PREFIX,'',clauses[0],count=1,flags=re.I)
    identifier=r'wf-[0-9a-f]{32}'
    label=r'(?:「[^「」\r\n]{1,240}」|“[^“”\r\n]{1,240}”|"[^"\r\n]{1,240}"|\x27[^\x27\r\n]{1,240}\x27)'
    verbs='|'.join(ACTIONS[a] for a in ('pause','abort','resume'))
    match=re.fullmatch(r'(?P<verb>'+verbs+r')\s*(?:已暂停的\s*)?'
        r'(?:(?:真机)?任务(?:\s*ID)?\s*[:：]?\s*)?'
        r'(?P<target>'+identifier+'|'+label+r')\s*(?:(?:的)?(?:真机)?任务)?(?:吧)?',head,re.I)
    if not match:return None
    action=next(a for a in ('pause','abort','resume') if re.fullmatch(ACTIONS[a],match['verb'],re.I))
    target=match['target']
    parsed={'action':action,'workflowId':target.lower() if re.fullmatch(identifier,target,re.I) else None,
            'name':None if re.fullmatch(identifier,target,re.I) else target[1:-1],'nodeLabel':None}
    excluded=r'(?:新建|创建|启动|开始|恢复|继续|暂停|终止|停止|取消|操作)'
    for clause in clauses[1:]:
        identity=re.fullmatch(r'任务\s*ID\s*[:：]?\s*('+identifier+r')',clause,re.I)
        if identity:
            value=identity[1].lower()
            if parsed['workflowId'] and parsed['workflowId']!=value:return None
            parsed['workflowId']=value
        elif re.fullmatch(r'(?:请\s*)?(?:不要|不)'+excluded+r'(?:(?:或|和|、)'+excluded+r')*其他任务',clause):
            continue
        elif re.fullmatch(r'(?:沿用|使用)(?:该|当前|这个|此)任务(?:已确认的|的)?(?:八号机)?接线和参数',clause):
            continue
        elif clause in ('请执行并返回任务状态','执行并返回任务状态'):
            continue
        elif action=='resume' and clause=='此次明确授权实际测量':
            continue
        elif action=='resume' and re.fullmatch(r'恢复当前[^「」“”\"\x27?？]{1,40}采集',clause):
            label=clause[len('恢复当前'):-len('采集')]
            if parsed['nodeLabel'] and parsed['nodeLabel']!=label:return None
            parsed['nodeLabel']=label
        else:
            return None
    return parsed


def command_action(text):
    scoped=scoped_workflow_request(text)
    if scoped:return scoped['action']
    # An invalid explicit-ID envelope cannot fall back to the looser legacy
    # resource grammar and silently discard its requested task identity.
    if re.search(r'\bwf-',text,re.I):return None
    # “帮我” makes this bounded courtesy form an operator request. Normalize
    # only its outer question words; the complete inner command still passes
    # the same negation, quotation, condition, action and target checks below.
    polite=re.fullmatch(r'(?:能不能|可以|能否)\s*帮我\s*(.+?)(?:吗)?\s*[?？。!！]*',text.strip())
    if polite:text=polite.group(1).strip()
    # A measurement keyword in a question, report or quote is not authorization.
    if re.search(r'为什么|为何|怎么|如何|如果|假如|假设|是否|能否|可否|哪里|哪儿|什么|哪个|多少|何时|多久|多长|几(?:个)?(?:秒钟?|分钟|小时|天)|讨论|解释|示例|举例|比如|例如|含义|意思|教程|说明|[吗呢么]|[?？“”‘’「」『』\"\x27`]|说|表示|提到|建议|(?:的|运行|测控|测量)(?:结果|状态|进度|记录|历史)|\b(?:said|says|asked)\b',text,re.I):return None
    # Alternative choices do not authorize executing every mentioned target.
    if re.search(r'或|还是|\bor\b',text,re.I):return None
    joined_action=r'(?:并且|并|同时|和|之后|以后|后)(?=\s*(?:请|再|直接|马上|立即|先|就|你|您)*\s*(?:'+'|'.join(ACTIONS.values())+r'))'
    clauses=[x.strip() for x in re.split(r'[，,。.;；!！\r\n]+|然后|接着|随后|'+joined_action,text) if x.strip()]
    actions=set();authorized_resources=set()
    for clause in clauses:
        command=re.sub(COMMAND_PREFIX,'',clause,count=1,flags=re.I)
        if not command:continue  # A standalone “好的” is only a courtesy.
        if re.fullmatch(MANUAL_CLAUSE,command,re.I):continue
        # This narrowly described file preference carries no experiment target
        # or operation. Every other negated clause still blocks execution.
        if re.fullmatch(NO_IMPORT_CLAUSE,command):continue
        no_import_head=re.match(NO_IMPORT_CLAUSE+r'(?=只|直接|现在|马上|立即|开始|启动|测)',command)
        if no_import_head:
            command=re.sub(COMMAND_PREFIX,'',command[no_import_head.end():],count=1,flags=re.I)
        no_import_tail=re.search(NO_IMPORT_CLAUSE+r'$',command)
        if no_import_tail:
            command=command[:no_import_tail.start()].rstrip()
        if re.search(r'[不没未勿别]|禁止|无需|\b(?:do\s+not|don.t|never|not)\b',command,re.I):return None
        subject=re.match(r'(?:(?:把|将)\s*)?(?:八号机\s*)?(?:Q{1,2}\d+(?![A-Za-z0-9_])|当前任务|这个任务|该任务)\s*(?:的任务)?\s*',command,re.I)
        if subject:
            command=command[subject.end():]
            subject_resources=requested_resources(subject.group())
            tail_resources=requested_resources(command)
            if subject_resources and tail_resources and subject_resources!=tail_resources:return None
            command=re.sub(COMMAND_PREFIX,'',command,count=1,flags=re.I)
        action=next((a for a,p in ACTIONS.items() if re.match('(?:'+p+')',command,re.I)),None)
        if not action and manual_requested(text) and re.match(r'创建|新建|建立|\bcreate\b',command,re.I):
            action='start'  # Manual creation is a draft-only operation below.
        if not action:return None
        resources=list(re.finditer(r'Q{1,2}\d+',clause,re.I))
        if len(resources)>1:
            between=clause[resources[0].end():resources[-1].start()]
            if any(re.search(p,between,re.I) for a,p in ACTIONS.items() if a!=action):return None
        actions.add(action)
        authorized_resources.update(requested_resources(clause))
    # Never apply one clause's action to a qubit mentioned only in another topic.
    if len(actions)!=1 or authorized_resources!=requested_resources(text):return None
    return next(iter(actions))

def resolve_workflow(ws,text,selected=None,explicit_id=None):
    workflows=ws.c.list()
    real=[w for w in workflows if w['mode']=='real']
    resources=requested_resources(text)
    scoped=scoped_workflow_request(text)
    if scoped:
        candidates=real
        if scoped['workflowId']:
            candidates=[w for w in candidates if w['id']==scoped['workflowId']]
        if scoped['name'] is not None:
            candidates=[w for w in candidates if w['name']==scoped['name']]
        if selected and not scoped['workflowId']:
            candidates=[w for w in candidates if w['id']==selected]
        if len(candidates)!=1:
            raise APIError('WORKFLOW_TARGET_REQUIRED','任务名称或 ID 不能唯一对应；请选中该任务或提供准确 ID。')
        if resources and set(candidates[0]['resourceIds'])!=resources:
            raise APIError('WORKFLOW_SCOPE_REQUIRED','任务与指令中的比特不一致。')
        if scoped['nodeLabel'] is not None:
            node=next((n for n in candidates[0]['nodes'] if n['id']==candidates[0].get('currentNodeId')),None)
            if not node or node.get('label')!=scoped['nodeLabel']:
                raise APIError('WORKFLOW_SCOPE_REQUIRED','指令中的测项与任务当前步骤不一致。')
    elif re.search(r'当前|这个|该任务|current',text,re.I) and selected:
        candidates=[w for w in real if w['id']==selected]
        if candidates and resources and set(candidates[0]['resourceIds'])!=resources:
            raise APIError('WORKFLOW_SCOPE_REQUIRED','当前选中任务与指令中的比特不一致。')
    elif resources:
        candidates=[w for w in real if set(w['resourceIds'])==resources and w['status'] not in ('aborted','completed')]
        active=[w for w in candidates if w['status'] in ('running','queued')]
        if active:candidates=active
        selected_candidates=[w for w in candidates if w['id']==selected]
        if selected_candidates:candidates=selected_candidates
    else:
        candidates=[w for w in real if w['name'] and re.search(r'(?<![\w])'+re.escape(w['name'])+r'(?![\w])',text)]
        if not candidates:candidates=[w for w in real if w['status'] in ('running','queued')]
    if len(candidates)!=1:raise APIError('WORKFLOW_TARGET_REQUIRED','请选中要操作的任务，或写出任务名称。')
    if explicit_id and candidates[0]['id']!=explicit_id:raise APIError('WORKFLOW_SCOPE_REQUIRED','操作对象与当前指令不一致。')
    return candidates[0]

def direct_proposal(ws,body,scope):
    if body.get('intent')=='discuss' or scope['kind']!='workspace':return None
    from .workspace_reports import report_requested, resolve_report_workflow
    if report_requested(body['message']):
        wf=resolve_report_workflow(ws,body['message'],body.get('selectedWorkflowId'))
        appendix=64 if re.search(r'(?<!\d)64\s*(?:张|图)',body['message']) else 16
        return {'tool':'export_report','arguments':{'workflowId':wf['id'],'plotsPerAppendix':appendix}}
    text=body['message'];action=command_action(text)
    if re.search(r'整批|批次|跳过',text):return None
    if not action:return None
    resources=requested_resources(text)
    if action=='start' and resources:
        return {'tool':'start_measurement','arguments':{'resourceIds':sorted(resources,key=lambda q:int(q[1:])),
                **({'policy':'strict'} if manual_requested(text) else {})}}
    if action in ('pause','abort','resume'):
        wf=resolve_workflow(ws,text,body.get('selectedWorkflowId'))
        return {'tool':'workflow_action','arguments':{'workflowId':wf['id'],'action':action}}
    return None

def legacy_resources(ws):
    capabilities=ws.c.adapter.capabilities()
    supported=capabilities.get('resources')
    return [q['id'] for q in ws.c.history['resources'] if
            (not isinstance(supported,list) or q['id'] in supported)]

def execute(ws,proposal,body,conv,index):
    args=proposal['arguments'];text=body['message'];tool=proposal['tool']
    if conv['scope']['kind']!='workspace':raise APIError('INVALID_SCOPE','请在总控对话中操作实验任务。')
    action=command_action(text)
    prefix=body['operationId']+':tool:'+str(index)
    if tool=='workflow_action':
        if set(args)!={'workflowId','action'} or args['action'] not in ('pause','abort','resume') or action!=args['action']:
            raise APIError('EXPLICIT_REQUEST_REQUIRED','请明确任务和要执行的操作。')
        wf=resolve_workflow(ws,text,body.get('selectedWorkflowId'),args['workflowId'])
        result=ws.c.action(wf['id'],{'operationId':prefix,'expectedRevision':wf['revision'],'action':action})
        return receipt(result,action)
    targets=args.get('resourceIds',[])
    if set(args)-{'resourceIds','policy'} or action!='start' or not targets or not isinstance(targets,list) or len(set(targets))!=len(targets) or set(targets)!=requested_resources(text):
        raise APIError('RESOURCE_AUTHORIZATION_REQUIRED','开始测量需要明确本次比特范围。')
    if args.get('policy','autopilot') not in ('strict','autopilot'):
        raise APIError('INVALID_POLICY','不支持该运行策略。')
    policy = 'strict' if manual_requested(text) else args.get('policy','autopilot')
    if body.get('selectedWorkflowId') and policy != 'strict':
        selected=next((w for w in ws.c.list() if w['id']==body['selectedWorkflowId']),None)
        if selected and selected.get('policy')=='strict' and set(selected['resourceIds']) & set(targets):
            raise APIError('MANUAL_STEP_REQUIRED','所选任务采用人工逐步模式，请在蓝图中选择步骤开始。',409)
    if body.get('selectedWorkflowId') and re.search(r'当前|这个|该任务|current',text,re.I):
        selected=resolve_workflow(ws,text,body['selectedWorkflowId'])
        if selected.get('policy')=='strict':
            raise APIError('MANUAL_STEP_REQUIRED','当前任务采用人工逐步模式，请在蓝图中选择步骤开始。',409)
    # Resolve published configurations before the transaction. Existing native
    # resources remain usable even when their optional migration drafts are empty.
    bindings={};blocked={};legacy=legacy_resources(ws)
    for target in targets:
        if target in legacy:bindings[target]=None;continue
        candidates=[]
        if hasattr(ws,'chips'):
            for chip in ws.chips.list_chips():
                # Publishing a new version supersedes the previous default for
                # this chip. Different chips/deployments remain ambiguous.
                for cv in chip.get('versions',[])[-1:]:
                    if not any(q['id']==target for q in cv.get('qubits',[])):continue
                    for deployment in ws.chips.list_deployments():
                        versions=deployment.get('versions',[])
                        if not versions:continue
                        try:candidates.append(ws.chips.resolve_binding(cv['id'],versions[-1]['id'],[target]))
                        except APIError:continue
        if len(candidates)!=1:
            blocked[target]={'status':'blocked','resourceId':target,'code':'CONFIGURATION_REQUIRED',
                'message':target+' 尚无唯一可用配置，未启动。可复用现场接线并直接填写参数，也可导入资料。'}
        else:
            bindings[target]=candidates[0]
    with ws.store.transaction() as rt:
        command={'operationId':prefix,'resourceIds':targets,'policy':policy}
        old,fingerprint=ws._command(rt,command,'start_measurement')
        if old is not None:return old
        prior=ws.store.workflows(rt);outcomes=[]
        for target in targets:
            if target in blocked:outcomes.append(blocked[target])
        for target,binding in bindings.items():
            scoped=ws.c.with_binding(binding) if binding else ws.c
            active=[w for w in prior if w['mode']=='real' and w['resourceIds']==[target] and
                    w['status'] in ('running','queued') and (w.get('policy')=='strict')==(policy=='strict')
                    and w.get('adapterProfileHash')==scoped.adapter_profile_hash]
            if active:
                outcomes.append(receipt(active[0],'start'));continue
            wf=scoped.create({'name':'八号机 '+target+' · 单比特测控','mode':'real','resourceIds':[target],
                              'coverage':'full','policy':policy},_rt=rt,_binding=binding)
            if policy=='strict':
                outcome=receipt(wf,'create')
                outcome.update(resourceId=target,newTask=True,
                    message=wf['name']+'：已建立人工逐步任务；在蓝图中选择步骤开始。')
                outcomes.append(outcome)
                continue
            # Reuse an actual recorded handover only for the identical native
            # profile. Ownership is still refreshed by the measurement controller.
            confirmation=next((w for w in reversed(prior) if w['mode']=='real' and w.get('hardwareConfirmed') and
                w.get('adapterId')==scoped.adapter_id and w.get('adapterProfileHash')==scoped.adapter_profile_hash),None)
            if not binding and confirmation:
                wf.update(hardwareConfirmed=True,hardwareConfirmedAt=confirmation.get('hardwareConfirmedAt'),
                          hardwareConfirmationSource=confirmation['id'])
                ws.store.save(rt,wf,'hardware_confirmation_reused',{'message':'沿用相同设备配置的现场接线记录；启动时重新核查设备占用。'})
            started=scoped.action(wf['id'],{'operationId':prefix+':'+target,'expectedRevision':wf['revision'],'action':'start'},_rt=rt)
            outcome=receipt(started,'start')
            outcome.update(resourceId=target,newTask=True)
            if any(w['mode']=='real' and w['resourceIds']==[target] and w['status']=='paused' for w in prior):
                outcome['message']='已创建新任务。'+outcome['message']+'原暂停任务保留；继续原任务请指定继续。'
            outcomes.append(outcome)
        result={'status':'task_receipt','outcomes':outcomes,'message':'\n'.join(r['message'] for r in outcomes)}
        ws._remember(rt,command,fingerprint,result)
        return result

def receipt(wf,action):
    if wf.get('cancelRequested'):
        text='已提交'+('终止' if wf['cancelRequested']=='abort' else '暂停')+'，等待当前采集停止回执。'
    else:
        text={'queued':'已加入测量队列。','running':'正在运行。','paused':'已暂停。','aborted':'已终止。',
              'completed':'任务已完成。','attention':'需要处理：'+str((wf.get('events') or [{}])[-1].get('message','请查看任务'))}.get(wf['status'],'当前状态：'+wf['status'])
    return {'status':'task_receipt','workflowId':wf['id'],'workflowState':wf['status'],'action':action,
            'message':wf['name']+'：'+text}
