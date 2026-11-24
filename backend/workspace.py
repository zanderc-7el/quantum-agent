"""Persistent workspace, campaign and attention authority. No instrument I/O here."""
from copy import deepcopy, copy
import json
import re
from .common import APIError, digest, dumps, ident, now
from .stopped_receipts import stopped_summary


DEVICE_STATES = {'needs_reconciliation', 'device_disconnected', 'ownership_unknown'}


def device_key(profile):
    instrument = profile.get('instrument', {})
    host, port = instrument.get('host'), instrument.get('port')
    if host and port:
        return 'device:' + str(host).strip().lower() + ':' + str(int(port))
    return instrument.get('resourceGroup', 'CY8_SHARED_BUS1')


def device_fault_blocks(workflow):
    """Pausing a fault owner must not acknowledge its shared-device fault."""
    return workflow.get('mode') == 'real' and (
        (workflow.get('status') == 'attention' and workflow.get('engineState') in DEVICE_STATES)
        or (workflow.get('sharedDeviceFault') and workflow.get('status') in ('attention', 'paused')))


class Workspace:
    def __init__(self, controller, *, initialize_catalog=True):
        self.c, self.store = controller, controller.store
        with self.store.connect() as rt:
            rt.db.executescript('''
                CREATE TABLE IF NOT EXISTS platform_campaigns(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS platform_conversations(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS platform_attention(id TEXT PRIMARY KEY, signature TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspace_commands(id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, response TEXT NOT NULL);
            ''')
        if initialize_catalog:
            from .chip_catalog import ChipCatalog
            from .document_imports import DocumentImports
            self.chips = ChipCatalog(self.store, self.c.root, self.c.profile, brain=self.c._brain)
            self.imports = DocumentImports(self.store, self.c.root, self.chips, brain=self.c._brain)
        # Interrupted model calls are preserved, never silently executed again.
        with self.store.transaction() as rt:
            for row in rt.db.execute('SELECT payload FROM platform_conversations').fetchall():
                conv = json.loads(row[0])
                if conv.get('pending'):
                    op = conv.pop('pending')
                    conv.update(revision=conv['revision'] + 1, error='对话请求被中断，请重新发送。')
                    self._put(rt, 'conversations', conv)
                    rt.db.execute('UPDATE workspace_commands SET response=? WHERE id=?', (dumps(conv), op))

    @staticmethod
    def _put(rt, table, value):
        assert table in ('campaigns', 'conversations')
        rt.db.execute(f'INSERT OR REPLACE INTO platform_{table} VALUES(?,?)', (value['id'], dumps(value)))

    @staticmethod
    def _get(rt, table, id):
        assert table in ('campaigns', 'conversations')
        row = rt.db.execute(f'SELECT payload FROM platform_{table} WHERE id=?', (id,)).fetchone()
        if not row:
            raise APIError('NOT_FOUND', '记录不存在', 404)
        return json.loads(row[0])

    @staticmethod
    def _command(rt, body, kind):
        op = body.get('operationId')
        if not isinstance(op, str) or not 1 <= len(op) <= 256:
            raise APIError('OPERATION_ID_REQUIRED', '缺少操作编号')
        fingerprint = digest({'kind': kind, 'body': body})
        row = rt.db.execute('SELECT * FROM workspace_commands WHERE id=?', (op,)).fetchone()
        if row:
            if row['request_hash'] != fingerprint:
                raise APIError('IDEMPOTENCY_CONFLICT', '操作编号已用于不同请求', 409)
            return json.loads(row['response']), fingerprint
        return None, fingerprint

    @staticmethod
    def _remember(rt, body, fingerprint, response):
        rt.db.execute('INSERT INTO workspace_commands VALUES(?,?,?)', (body['operationId'], fingerprint, dumps(response)))

    @staticmethod
    def _revision(entity, body, *, stop_intent=False):
        expected = body.get('expectedRevision')
        older_stop = (stop_intent and isinstance(expected,int) and not isinstance(expected,bool)
                      and 1 <= expected <= entity['revision'])
        if (stop_intent and not older_stop) or (not stop_intent and expected != entity['revision']):
            raise APIError('STALE_REVISION', '内容已更新，请刷新后处理', 409)

    def _campaign_view(self, rt, campaign):
        value = deepcopy(campaign)
        children = []
        for child in value['children']:
            wf = self.store.get(rt, child['workflowId'])
            node = next((n for n in wf['nodes'] if n['id'] == wf.get('currentNodeId')), None)
            children.append({**child, 'status': 'skipped' if wf['status']=='aborted' and (wf.get('skipped') or wf.get('skipRequested')) else wf['status'],
                'currentStep': node['label'] if node else ('配置检查' if wf.get('configurationIssue') else ''), 'engineState': wf['engineState'],
                'verifiedCount': sum(n['engineState'] == 'verified' for n in wf['nodes']),
                'totalCount': len(wf['nodes']), 'error': (wf.get('configurationIssue',{}).get('message') or
                    (node or {}).get('detail') or (wf.get('events') or [{}])[-1].get('message','')) if wf['status']=='attention' else '',
                'workflowRevision': wf['revision']})
        value['children'] = children
        states = {x['status'] for x in children}
        value['state'] = ('running' if states & {'running','queued'} else 'attention' if 'attention' in states
                          else 'draft' if states == {'draft'} else 'paused' if states & {'paused','draft'}
                          else 'aborted' if states <= {'aborted','skipped'} else 'failed' if 'failed' in states
                          else 'partial' if states & {'aborted','skipped'} else 'completed')
        return value

    def list_campaigns(self):
        with self.store.connect() as rt:
            return [self._campaign_view(rt, json.loads(row[0])) for row in rt.db.execute('SELECT payload FROM platform_campaigns ORDER BY rowid DESC')]

    def get_campaign(self, id):
        with self.store.connect() as rt:
            return self._campaign_view(rt, self._get(rt, 'campaigns', id))

    def create_campaign(self, body):
        selected = body.get('resourceIds')
        if not isinstance(selected, list) or not selected or len(selected) > 200 or any(not isinstance(x, str) for x in selected) or len(set(selected)) != len(selected):
            raise APIError('INVALID_RESOURCES', '请选择不重复的比特')
        name = str(body.get('name', '')).strip()
        if not 1 <= len(name) <= 200:
            raise APIError('INVALID_NAME', '请填写批次名称')
        with self.store.transaction() as rt:
            old, fingerprint = self._command(rt, body, 'create_campaign')
            if old is not None:
                return old
        chip = self.chips.get_chip(body.get('chipVersionId'))
        deployment = self.chips.get_deployment(body.get('deploymentVersionId'))
        if 'entityId' not in chip or 'entityId' not in deployment:
            raise APIError('VERSION_REQUIRED','请选择已发布的配置版本')
        resources = {q['id']: q for q in chip['qubits']}
        if any(q not in resources for q in selected):
            raise APIError('UNKNOWN_RESOURCE','所选比特不属于该芯片版本')
        bindings = []
        for q in selected:
            try:
                bindings.append(self.chips.resolve_binding(body.get('chipVersionId'), body.get('deploymentVersionId'), [q]))
            except APIError as exc:
                if exc.status != 409:
                    raise
                bindings.append({'blocked': {'code':exc.code,'message':exc.message}})
        with self.store.transaction() as rt:
            old, fingerprint = self._command(rt, body, 'create_campaign')
            if old is not None:
                return old
            campaign = {'id': ident('campaign'), 'revision': 1, 'name': name, 'createdAt': now(),
                'chipVersionId': body['chipVersionId'], 'deploymentVersionId': body['deploymentVersionId'],
                'resourceIds': selected, 'children': [], 'permits': []}
            for resource, binding in zip(selected, bindings):
                data = {'name': name + ' · ' + resource, 'mode':'real', 'resourceIds':[resource],
                        'coverage':body.get('coverage','full'),'policy':body.get('policy','autopilot'),
                        'budget':body.get('budget',{})}
                if 'blocked' in binding:
                    # A configuration-only placeholder has no execution binding
                    # or accepted calibration; it cannot be started or resumed.
                    scoped = copy(self.c)
                    scoped.history = {'resources':[resources[resource]],'records':[]}
                    scoped.catalog = copy(self.c.catalog)
                    scoped.catalog.history = lambda: {'resources':[resources[resource]],'records':[]}
                    wf = scoped.create({**data,'mode':'replay'}, _rt=rt)
                    wf.update(mode='real',status='attention',engineState='configuration_required',
                        configurationIssue=binding['blocked'],campaignId=campaign['id'],
                        chipVersionId=body['chipVersionId'],deploymentVersionId=body['deploymentVersionId'])
                    wf['nodes'],wf['edges'] = scoped.catalog.nodes([resources[resource]], 'real', self.c.adapter.capabilities(), 'single',data['coverage'])
                    self.store.save(rt,wf,'configuration_required',{'message':binding['blocked']['message']})
                else:
                    wf = self.c.with_binding(binding).create(data, _binding=binding, _campaign_id=campaign['id'], _rt=rt)
                campaign['children'].append({'resourceId': resource, 'workflowId': wf['id']})
            self._put(rt, 'campaigns', campaign)
            response = self._campaign_view(rt, campaign)
            self._remember(rt, body, fingerprint, response)
            return response

    @staticmethod
    def _campaign_action_eligible(wf, action):
        if action in ('start','resume') and wf.get('mode')=='real' and wf.get('policy')=='strict':
            return False
        if action in ('pause','abort'):
            # The controller preserves unknown effects and quarantine while
            # recording the stop; a batch must not silently omit that child.
            return not (action=='pause' and wf.get('skipRequested'))
        if wf['status'] in ('completed','aborted') or wf['engineState']=='needs_reconciliation':
            return False
        if action in ('start','resume') and (wf.get('configurationIssue') or wf.get('cancelRequested')):
            return False
        if action=='start':
            return wf['status']=='draft'
        if action=='resume':
            blocked=('configuration_required','budget_exhausted','plan_rejected','unsupported','unsupported_method',
                     'adapter_changed','diagnostic_closed','diagnostic_failed','diagnostic_complete',
                     'diagnostic_interrupted','supplemental_acceptance_mismatch')
            return wf['status'] in ('paused','attention','failed') and wf['engineState'] not in blocked
        if action=='pause':
            return wf['status'] in ('running','queued') and not wf.get('cancelRequested')
        if action in ('abort','skip'):
            return wf.get('cancelRequested')!='abort'
        return False

    def campaign_action(self, id, body):
        action = body.get('action')
        if action not in ('start','pause','resume','abort','skip'):
            raise APIError('INVALID_ACTION', '不支持该批次操作')
        with self.store.transaction() as rt:
            old, fingerprint = self._command(rt, body, 'campaign_action:' + id)
            if old is not None:
                return old
            campaign = self._get(rt, 'campaigns', id)
            self._revision(campaign, body, stop_intent=action in ('pause','abort'))
            selected = body.get('resourceIds', campaign['resourceIds'])
            if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected) or any(q not in campaign['resourceIds'] for q in selected):
                raise APIError('INVALID_SCOPE', '所选比特不属于该批次')
            outcomes, eligible = [], []
            for child in campaign['children']:
                if child['resourceId'] not in selected:
                    continue
                wf = self.c.get(child['workflowId'], rt)
                if self._campaign_action_eligible(wf, action):
                    eligible.append((child, wf))
                elif wf.get('configurationIssue') and action in ('start','resume'):
                    outcomes.append({'resourceId':child['resourceId'],'status':'blocked','error':wf['configurationIssue']['message']})
                elif action in ('start','resume') and wf.get('mode')=='real' and wf.get('policy')=='strict':
                    outcomes.append({'resourceId':child['resourceId'],'status':'unchanged','code':'MANUAL_STEP_REQUIRED',
                        'message':'人工逐步任务请在蓝图中选择步骤开始。'})
                else:
                    outcomes.append({'resourceId':child['resourceId'],'status':'unchanged'})
            if not eligible:
                if outcomes and all(o.get('code')=='MANUAL_STEP_REQUIRED' for o in outcomes):
                    raise APIError('MANUAL_STEP_REQUIRED','人工逐步任务请在蓝图中选择步骤开始。',409)
                raise APIError('INVALID_STATE', '所选比特当前均不支持这项操作', 409)
            for child, wf in eligible:
                if action in ('start','resume'):
                    if self.queue_blocker(wf, rt=rt):
                        raise APIError('DEVICE_QUEUE_PAUSED', '同一设备有待核查事项，请先处理', 409)
                    if wf.get('configurationBinding'):
                        wf.update(hardwareConfirmed=True, hardwareConfirmedAt=now())
                        self.store.save(rt, wf, 'campaign_start_permit', {'operationId': body['operationId']})
                changed = self.c.action(wf['id'], {'operationId': body['operationId'] + ':' + child['resourceId'],
                    'expectedRevision': wf['revision'], 'action': 'abort' if action=='skip' else action}, _rt=rt)
                if action == 'skip':
                    stopped = changed['status']=='aborted'
                    changed.update(skipped=stopped, skipRequested=not stopped)
                    self.store.save(rt, changed, 'skipped' if stopped else 'skip_requested',
                        {'message':'本批次已跳过此比特，保留已采集数据' if stopped else '已请求跳过此比特，等待采集停止检查点'})
                outcomes.append({'resourceId': child['resourceId'], 'status': 'skipped' if changed.get('skipped') else changed['status']})
            campaign['revision'] += 1
            campaign['permits'].append({'operationId': body['operationId'], 'action': action,
                'resourceIds': [child['resourceId'] for child, _ in eligible], 'at': now()})
            self._put(rt, 'campaigns', campaign)
            response = {**self._campaign_view(rt, campaign), 'outcomes': outcomes}
            self._remember(rt, body, fingerprint, response)
        self.c.wake.set()
        return response

    def queue_blocker(self, workflow, *, rt=None):
        profile = workflow.get('configurationBinding', {}).get('profile', self.c.profile)
        group = device_key(profile)
        for other in self.store.workflows(rt):
            if other['id'] == workflow['id'] or other.get('mode') != 'real':
                continue
            if device_fault_blocks(other):
                other_profile = other.get('configurationBinding', {}).get('profile', self.c.profile)
                if device_key(other_profile) == group:
                    return other
        return None

    def _scope(self, scope):
        if not isinstance(scope, dict) or set(scope) - {'kind', 'id'}:
            raise APIError('INVALID_SCOPE', '对话范围无效')
        kind, id = scope.get('kind'), scope.get('id')
        if kind == 'workspace' and not id:
            return {'kind':'workspace'}
        if not isinstance(id, str) or not id:
            raise APIError('INVALID_SCOPE', '缺少对话关联对象')
        if kind == 'workflow':
            self.c.get(id)
        elif kind == 'campaign':
            self.get_campaign(id)
        else:
            raise APIError('INVALID_SCOPE', '对话范围无效')
        return {'kind': kind, 'id': id}

    def list_conversations(self):
        with self.store.connect() as rt:
            return [json.loads(r[0]) for r in rt.db.execute('SELECT payload FROM platform_conversations ORDER BY rowid DESC')]

    def get_conversation(self, id):
        with self.store.connect() as rt:
            conv = self._get(rt, 'conversations', id)
        self._scope(conv['scope'])
        return conv

    def create_conversation(self, body):
        scope = self._scope(body.get('scope'))
        with self.store.transaction() as rt:
            old, fingerprint = self._command(rt, body, 'create_conversation')
            if old is not None:
                return old
            conv = {'id': ident('conversation'), 'revision': 1, 'scope': scope, 'messages': [], 'documentIds':[], 'createdAt': now()}
            self._put(rt, 'conversations', conv)
            self._remember(rt, body, fingerprint, conv)
            return conv

    def _context(self, conv, body):
        # Conversation previews never replace the complete stored import used by
        # analyze_import. Budget serialized UTF-8, leaving room for the message.
        size = lambda value: len(dumps(value).encode('utf-8'))
        def brief(value, nested=False):
            result = {key: (value[key][:240] if isinstance(value[key],str) else value[key])
                for key in ('id','entityId','name','label','revision','status','state','engineState',
                            'publishedAt','physicalId','busId','identityStatus','ready')
                if key in value and isinstance(value[key],(str,int,float,bool,type(None)))}
            result['preview'] = True
            for key in ('resourceIds','routes','qubits','versions','children'):
                rows = value.get(key)
                if isinstance(rows,dict):
                    result['total'+key.title()+'Count'] = len(rows)
                    if not nested:
                        result[key] = [str(key)[:100] for key in list(rows)[:30]]
                elif isinstance(rows,list):
                    result['total'+key.title()+'Count'] = len(rows)
                    if not nested:
                        result[key] = [brief(row, True) if isinstance(row,dict) else str(row)[:100] for row in rows[:30]]
            return result
        def bounded(rows, budget, compact=brief):
            selected = []
            for row in rows:
                item = deepcopy(row)
                if size(selected+[item]) > budget:
                    item = compact(row)
                if size(selected+[item]) > budget:
                    break
                selected.append(item)
            return selected, {'totalCount':len(rows),'returnedCount':len(selected),
                'preview':len(selected)!=len(rows) or any(row.get('preview') for row in selected)}

        originals = [self.imports.get(id) for id in conv.get('documentIds', [])] if hasattr(self, 'imports') else []
        documents = []
        per_document_budget = 60000 // max(1,min(len(originals),40))
        for original in originals[:40]:
            blocks = original.get('blocks',[])
            draft = original.get('draft',{})
            qubits = draft.get('qubits',[])
            document = {key:deepcopy(original[key]) for key in ('id','filename','revision','status','sha256') if key in original}
            document.update(documentId=original['id'],totalBlockCount=len(blocks),blocks=[],preview=False,
                draftSummary={'name':str(draft.get('name',''))[:240],'totalQubitCount':len(qubits),
                    'qubits':[{key:str(q[key])[:120] for key in ('id','label','identityStatus') if key in q} for q in qubits[:20]],
                    'preview':True},
                totalIssueCount=len(original.get('issues',[])),
                sourceRetention='complete_original_retained',analysisSource='complete_stored_document',
                contentScope='complete_parsed_blocks')
            # Reserve each attachment a fair share, including its truthful metadata.
            if size(document)+160 > per_document_budget:
                document['draftSummary']['qubits'] = []
            def fits(candidate):
                return size(candidate)+160 <= per_document_budget and size(documents+[candidate])+160 <= 60000
            if not fits(document):
                break
            for block in blocks:
                item = {key:deepcopy(block[key]) for key in ('id','location','text') if key in block}
                if not fits({**document,'blocks':document['blocks']+[item]}):
                    # A large first block can still provide a useful source preview.
                    text = str(item.get('text',''))
                    item.update(totalTextCharacters=len(text),preview=True)
                    while text:
                        item['text'] = text
                        if fits({**document,'blocks':document['blocks']+[item]}):
                            document['blocks'].append(item)
                            break
                        text = text[:len(text)//2]
                    document['preview'] = True
                    break
                document['blocks'].append(item)
            document['preview'] = document['preview'] or len(document['blocks'])!=len(blocks)
            document['previewBlockCount'] = len(document['blocks'])
            if document['preview']:
                document['contentScope'] = 'parsed_block_preview_only'
            documents.append(document)
        context = {'scope':conv['scope'],'intent':body.get('intent','discuss'),'documents':documents,
            'documentSummary':{'totalCount':len(originals),'returnedCount':len(documents),
                'preview':len(documents)!=len(originals) or any(d['preview'] for d in documents),
                'previewBudgetBytes':60000,'fullAnalysisUsesStoredOriginal':True},'snapshotAt':now()}
        lists = {'campaigns':self.list_campaigns(),
            'workflows':[{'id':w['id'],'name':w['name'],'resourceIds':w['resourceIds'],
                          'status':w['status'],'engineState':w['engineState'],'policy':w['policy'],
                          'revision':w['revision']} for w in self.c.list()],
            'chips':self.chips.list_chips() if hasattr(self,'chips') else [],
            'deployments':self.chips.list_deployments() if hasattr(self,'chips') else []}
        if conv['scope']['kind']=='campaign':
            campaign = self.get_campaign(conv['scope']['id'])
            workflow_ids = {c['workflowId'] for c in campaign['children']}
            lists['campaigns'] = [campaign]
            lists['workflows'] = [w for w in lists['workflows'] if w['id'] in workflow_ids]
            lists['chips'] = [self.chips.get_chip(campaign['chipVersionId'])] if hasattr(self,'chips') else []
            lists['deployments'] = [self.chips.get_deployment(campaign['deploymentVersionId'])] if hasattr(self,'chips') else []
        context['collectionSummaries'] = {}
        for key, rows in lists.items():
            context[key], context['collectionSummaries'][key] = bounded(rows,16000)
        from .workspace_actions import legacy_resources
        context['execution'] = {'existingNativeResources':legacy_resources(self) if conv['scope']['kind']=='workspace' else [],'existingConfigurationUsable':True,
            'note':'已有执行器配置可直接测量，迁移草稿未发布不阻塞已有资源；新比特可直接填写或导入配置。'}
        # Direct controller actions bypass the model service. Their authoritative
        # receipts must still be visible on the next model-backed conversation.
        history = [{'id':m['id'],'role':m['role'],'content':str(m.get('content',''))[:4000]}
                   for m in conv.get('messages',[])[-20:]]
        while size(history)>12000:
            history.pop(0)
        context['conversationHistory'] = history
        selected = body.get('selectedWorkflowId')
        if isinstance(selected,str) and any(w['id']==selected for w in lists['workflows']):
            if conv['scope']['kind']=='workspace' or any(c['workflowId']==selected for c in self.get_campaign(conv['scope']['id']).get('children',[])):
                context['selectedWorkflowId'] = selected
        return context

    @staticmethod
    def _requested_resources(text):
        from .workspace_actions import requested_resources
        return requested_resources(text)

    def _execute_proposal(self, proposal, body, conv, index):
        if not isinstance(proposal,dict) or set(proposal)-{'tool','arguments'}:
            raise APIError('INVALID_PROPOSAL','任务方案格式无效')
        tool, args = proposal.get('tool'), deepcopy(proposal.get('arguments', {}))
        text = body['message']
        scope = conv['scope']
        if body.get('intent') != 'execute' or scope['kind'] == 'workflow':
            return {'status':'proposed'}
        if tool == 'export_report':
            from .workspace_reports import execute_export
            return execute_export(self,args,body,conv,index)
        if tool in ('save_chip_draft','save_deployment_draft','publish_configuration'):
            from .workspace_config import execute_configuration
            return execute_configuration(self,tool,args,body,conv,index)
        if tool in ('start_measurement','workflow_action'):
            from .workspace_actions import execute
            return execute(self,proposal,body,conv,index)
        if not isinstance(args, dict):
            raise APIError('INVALID_PROPOSAL','任务方案格式无效')
        verbs = (r'分析|读取|读|导入|整理|配置|analy\w*|read|import' if tool=='analyze_import' else
                 '创建|建立|新建|安排|create' if tool=='create_campaign' and re.search(r'创建|建立|新建|安排|create',text,re.I) else
                 '测|run' if tool=='create_campaign' else
                 {'start':'启动|开始|执行|测|start|run','resume':'继续|恢复|resume',
                  'pause':'暂停|pause','abort':'终止|停止|abort|stop','skip':'跳过|skip'}.get(args.get('action'),'操作'))
        # Negation applies within its clause; a separate requested action remains explicit.
        negated = re.search(r'(?:不要|不用|先不|暂不|不必|不能|不可|不想|别|勿|不)[^，,。.;；!！?？:：\r\n]{0,5}(?:'+verbs+r')|(?:do\s+not|don.t|never|\bnot\b)\s+(?:'+verbs+r')',text,re.I)
        if negated or re.search(r'如果|假如|假设|是否|能否|可否|怎么|如何|解释|讨论|示例|[?？]',text,re.I):
            raise APIError('EXPLICIT_REQUEST_REQUIRED','这项操作保留为建议。需要执行时，请直接指定对象和操作。')
        if tool == 'analyze_import':
            from .workspace_actions import explicit_mutation
            if not explicit_mutation(text,r'读取|读|分析|整理|保存|导入|read|analy\w*|import'):
                raise APIError('EXPLICIT_REQUEST_REQUIRED','请明确要读取或整理的资料；资料内容不是操作指令。')
            if set(args) != {'documentId'} or args['documentId'] not in conv.get('documentIds',[]):
                raise APIError('DOCUMENT_SCOPE_REQUIRED','只能分析当前对话已添加的资料')
            if not re.search(r'读|分析|导入|整理|配置|测|任务|read|analy|import',text,re.I):
                raise APIError('EXPLICIT_REQUEST_REQUIRED','请明确要整理的资料')
            document = self.imports.get(args['documentId'])
            prefix = body['operationId']+':tool:'+str(index)
            parsed = self.imports.analyze(document['id'],{'operationId':prefix+':analyze','expectedRevision':document['revision']})
            chip_draft = deepcopy(parsed['draft'])
            generated = re.fullmatch(r'codex-clipboard-(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?:\.[a-z0-9]+)?', str(chip_draft.get('name','')), re.I)
            qubits = chip_draft.get('qubits',[])
            if generated and len(qubits)==1:
                label = str(qubits[0].get('label') or qubits[0].get('id') or '').strip()
                if re.fullmatch(r'Q\d+',label,re.I):
                    chip_draft['name'] = label+' 参数草稿'
            chip = self.chips.save_chip({'operationId':prefix+':chip','draft':chip_draft})
            return {'status':'draft_ready','documentId':document['id'],'chipId':chip['id'],
                    'message':'已保存芯片草稿“'+chip['name']+'”，可在“芯片与接线”中核对缺项和接线。'}
        if tool == 'create_campaign':
            from .workspace_actions import explicit_mutation, manual_requested
            if not explicit_mutation(text,r'创建|新建|安排|建立|测|run|create'):
                raise APIError('EXPLICIT_REQUEST_REQUIRED','请明确要创建的实验批次。')
            if not re.search(r'测|创建|新建|安排|建立|run|create', text, re.I):
                raise APIError('EXPLICIT_REQUEST_REQUIRED','请明确要安排的实验')
            requested = self._requested_resources(text)
            if not requested or not set(args.get('resourceIds', [])).issubset(requested):
                raise APIError('RESOURCE_AUTHORIZATION_REQUIRED','方案中的比特超出了本次指令范围')
            if scope['kind'] == 'campaign':
                raise APIError('INVALID_SCOPE','批次对话不能创建其他批次')
            allowed = {'name','chipVersionId','deploymentVersionId','resourceIds','coverage','policy'}
            if set(args)-allowed:
                raise APIError('INVALID_PROPOSAL','任务方案含未知字段')
            if manual_requested(text):
                args['policy']='strict'
            return self.create_campaign({**args,'operationId':body['operationId']+':tool:'+str(index)})
        if tool == 'campaign_action':
            from .workspace_actions import command_action
            if command_action(text) != args.get('action'):
                raise APIError('EXPLICIT_REQUEST_REQUIRED','请明确要操作的批次和动作。')
            id = args.get('campaignId')
            if scope['kind'] == 'campaign' and id != scope['id']:
                raise APIError('INVALID_SCOPE','不能操作其他批次')
            campaign = self.get_campaign(id)
            actions = {'start':r'开始|启动|执行|start|run','resume':r'继续|恢复|resume',
                       'pause':r'暂停|pause','abort':r'终止|停止|abort|stop','skip':r'跳过|skip'}
            if args.get('action') not in actions or not re.search(actions[args['action']],text,re.I):
                raise APIError('EXPLICIT_REQUEST_REQUIRED','当前消息没有授权这项操作')
            if scope['kind'] == 'workspace' and body.get('campaignId') != id and campaign['name'] not in text:
                raise APIError('CAMPAIGN_AUTHORIZATION_REQUIRED','请指定要操作的批次')
            requested = self._requested_resources(text)
            selected = args.get('resourceIds', campaign['resourceIds'])
            if requested and set(selected)-requested:
                raise APIError('RESOURCE_AUTHORIZATION_REQUIRED','方案超出了本次指定比特')
            if not requested and not re.search(r'整批|全部|所有|all', text, re.I):
                raise APIError('RESOURCE_AUTHORIZATION_REQUIRED','请指定比特或整批操作')
            return self.campaign_action(id, {'operationId':body['operationId']+':tool:'+str(index),
                'expectedRevision':campaign['revision'],'action':args['action'],'resourceIds':selected})
        raise APIError('TOOL_NOT_ALLOWED','该对话工具不可用')

    def _proposal_rounds(self, reply, body, conv, *, continue_model=False):
        """Feed real controller receipts back without widening user authority."""
        proposals, results, inspections, traces, seen = [], [], [], [], set()
        for round_number in range(3):
            if isinstance(reply.get('inspections'),list):
                inspections.extend(deepcopy(reply['inspections'][:10]))
            offered = reply.get('proposals',[])
            offered = offered if isinstance(offered,list) else []
            advanced = False
            for proposal in offered[:10]:
                fingerprint = digest(proposal)
                if fingerprint in seen or len(proposals) >= 10:
                    continue
                seen.add(fingerprint)
                index = len(proposals)
                proposals.append(proposal)
                try:
                    result = self._execute_proposal(proposal,body,conv,index)
                except APIError as exc:
                    result = {'status':'rejected','message':exc.message,'code':exc.code}
                except Exception:
                    result = {'status':'rejected','message':'该项操作未完成，请查看任务或资料记录。','code':'TOOL_FAILED'}
                results.append(result)
                executed = result.get('status') not in ('rejected','proposed')
                traces.append({'tool':proposal.get('tool') if isinstance(proposal,dict) else None,
                    'arguments':proposal.get('arguments',{}) if isinstance(proposal,dict) else {},
                    'status':result.get('status','completed'),'executed':executed,'result':deepcopy(result)})
                advanced = advanced or result.get('status') != 'proposed'
            if not continue_model or not advanced or round_number==2 or len(proposals)>=10:
                break
            try:
                context = self._context(conv,body)
                feedback = deepcopy(traces)
                if len(dumps(feedback).encode('utf-8'))>48000:
                    for trace in feedback:
                        result=trace['result']
                        trace['result']={key:value for key,value in result.items() if key in
                            ('id','status','state','revision','message','code','documentId','chipId','configurationId',
                             'versionId','workflowId','exportId','statusUrl','downloadUrl','configurationReady')}
                        trace['result']['preview']=True
                        trace['arguments']={'preview':True}
                context.update(continuation=True,continuationRound=round_number+1,toolResults=feedback)
                continued = self.c._brain('/workspace',{'sessionId':conv['id'],'message':body['message'],'context':context})
                if not isinstance(continued,dict) or continued.get('status') not in (None,'ok'):
                    reply = {'message':'后续分析暂未完成，已执行操作保留。' if any(t['executed'] for t in traces)
                             else '后续分析暂未完成，请查看操作回执。','proposals':[]}
                    break
                reply = continued
            except Exception:
                # Already committed results remain visible if the model is unavailable.
                reply = {'message':'后续分析暂未完成，已执行操作保留。' if any(t['executed'] for t in traces)
                         else '后续分析暂未完成，请查看操作回执。','proposals':[]}
                break
        return reply,proposals,results,inspections,traces

    def message(self, id, body):
        message = body.get('message')
        if not isinstance(message,str) or not 1 <= len(message.strip()) <= 20000:
            raise APIError('INVALID_MESSAGE','请输入消息')
        if body.get('intent','discuss') not in ('discuss','execute','auto'):
            raise APIError('INVALID_INTENT','对话操作无效')
        if body.get('intent')=='auto':
            from .workspace_actions import command_action, explicit_mutation
            from .workspace_reports import report_requested
            importing=bool(re.search(r'读取|分析|整理|保存.*草稿|导入',message) and not re.search(r'怎么|如何|为什么|如果|是否|[?？]',message))
            configuring=explicit_mutation(message,r'保存|修改|填写|更新|配置|发布|创建|新建|建立|安排|导出|生成|export')
            body={**body,'intent':'execute' if command_action(message) or importing or configuring or report_requested(message) else 'discuss'}
        with self.store.transaction() as rt:
            old, fingerprint = self._command(rt, body, 'message:'+id)
            if old is not None:
                return old
            conv = self._get(rt,'conversations',id)
            if 'scope' in body and body['scope'] != conv['scope']:
                raise APIError('INVALID_SCOPE','消息范围与会话不一致',409)
            if 'expectedRevision' in body:
                from .workspace_actions import command_action
                self._revision(conv, body, stop_intent=command_action(message) in ('pause','abort'))
            if conv.get('pending'):
                raise APIError('CONVERSATION_BUSY','上一条消息仍在处理',409)
            attachments = body.get('attachmentIds',[])
            if not isinstance(attachments,list) or len(attachments)>20 or any(not isinstance(i,str) for i in attachments):
                raise APIError('INVALID_ATTACHMENTS','资料列表无效')
            if attachments and conv['scope']['kind']=='workflow':
                raise APIError('INVALID_SCOPE','任务讨论不修改芯片资料')
            for document_id in attachments:
                self.imports.get(document_id,rt)
            conv['documentIds'] = list(dict.fromkeys(conv.get('documentIds',[])+attachments))
            conv.update(pending=body['operationId'],revision=conv['revision']+1)
            conv['messages'].append({'id':ident('msg'),'role':'user','content':message,'attachmentIds':attachments,'createdAt':now()})
            self._put(rt,'conversations',conv)
            self._remember(rt,body,fingerprint,conv)
        inspections, traces, analysis_message = [], [], ''
        try:
            self._scope(conv['scope'])
            from .workspace_actions import direct_proposal
            direct=direct_proposal(self,body,conv['scope'])
            if direct:
                reply={'message':'','proposals':[direct]}
            elif conv['scope']['kind']=='workflow':
                reply = self.c.for_workflow(conv['scope']['id']).chat({'workflowId':conv['scope']['id'],
                    'sessionId':conv['id'],'message':message, **({'nodeId':body['nodeId']} if body.get('nodeId') else {})})
            else:
                reply = self.c._brain('/workspace',{'sessionId':conv['id'],'message':message,'context':self._context(conv,body)})
            reply,proposals,results,inspections,traces = self._proposal_rounds(reply,body,conv,
                continue_model=not direct and conv['scope']['kind']!='workflow' and body.get('intent')=='execute')
            if traces and not direct:
                analysis_message = str(reply.get('message') or '')[:12000]
            content = str(reply.get('message') or '模型没有返回回答。')
            if body.get('intent')=='execute' and results:
                unique_proposals, fingerprints = [], set()
                for proposal in proposals[:10]:
                    key = digest(proposal)
                    if key not in fingerprints:
                        fingerprints.add(key)
                        unique_proposals.append(proposal)
                receipts, proposed = [], False
                for proposal, result in zip(unique_proposals, results):
                    if result.get('status')=='rejected':
                        receipts.append('未执行：'+result.get('message','该项操作未完成。'))
                    elif result.get('status')=='proposed':
                        proposed = True
                    elif result.get('status') in ('task_receipt','report_receipt','configuration_receipt'):
                        receipts.append(result['message'])
                    elif result.get('status')=='draft_ready':
                        chip = None
                        try:
                            getter = getattr(getattr(self,'chips',None),'get_chip',None)
                            chip = getter(result['chipId']) if callable(getter) else None
                        except Exception:
                            pass  # A saved result remains a receipt even if its optional detail read fails.
                        lead = ('已保存芯片草稿“'+chip['name']+'”' if chip and chip.get('name') else
                            result.get('message','芯片草稿已保存。').split('，可在“芯片与接线”',1)[0]).rstrip('。')+'。'
                        unresolved = chip is None or any(q.get('identityStatus')!='confirmed' or not q.get('physicalId') for q in chip.get('qubits',[]))
                        receipts.append(lead+('物理比特身份与当前接线待核对。' if unresolved else '请在“芯片与接线”核对当前接线。'))
                    elif result.get('id') and isinstance(result.get('children'),list) and result.get('state'):
                        name = str(result.get('name') or '批次')
                        if proposal.get('tool')=='create_campaign':
                            waiting = sum(c.get('status') in ('attention','blocked') for c in result['children'])
                            receipts.append('已创建批次草稿“'+name+'”，共 '+str(len(result['children']))+' 个比特。'+
                                (str(waiting)+' 个比特待处理。' if waiting else ''))
                        else:
                            action = proposal.get('arguments',{}).get('action')
                            verb = {'start':'启动','resume':'继续','pause':'暂停','abort':'终止','skip':'跳过'}.get(action,'操作')
                            counts = {}
                            for row in result.get('outcomes',result['children']):
                                status = row.get('status')
                                if status!='unchanged':counts[status]=counts.get(status,0)+1
                            names = {'draft':'草稿','queued':'排队中','running':'运行中','paused':'已暂停','attention':'待处理',
                                     'blocked':'受阻','aborted':'已终止','skipped':'已跳过','completed':'已完成','failed':'失败'}
                            if action in ('pause','abort','skip'):names['running']='等待停止检查点'
                            summary = '；'.join(str(count)+' 个比特'+names.get(status,'待核对') for status,count in counts.items())
                            receipts.append('批次“'+name+'”已提交'+verb+'。'+(summary+'。' if summary else ''))
                            for row in result.get('outcomes',[]):
                                if row.get('status')=='blocked' and row.get('error'):
                                    receipts.append(str(row['error']))
                if receipts:
                    content = '\n\n'.join(dict.fromkeys(receipts+(['其余建议尚未执行。'] if proposed else [])))
                elif proposed:
                    content += '\n\n建议尚未执行。'
            elif any(r.get('status')=='rejected' for r in results):
                content += '\n\n'+'\n'.join(r['message'] for r in results if r.get('status')=='rejected')
            if results and all(r.get('status')=='proposed' for r in results) and '建议尚未执行。' not in content:
                content += '\n\n建议尚未执行。'

        except Exception as exc:
            content, proposals, results = ('对话请求未完成：'+str(exc))[:500], [], []
        with self.store.transaction() as rt:
            conv = self._get(rt,'conversations',id)
            conv.pop('pending',None)
            conv.update(revision=conv['revision']+1,proposals=proposals,toolResults=results,actionTrace=traces)
            conv['messages'].append({'id':ident('msg'),'role':'assistant','content':content,'createdAt':now(),
                **({'inspections':inspections} if inspections else {}),
                **({'actionTrace':traces} if traces else {}),
                **({'analysisMessage':analysis_message} if analysis_message else {})})
            self._put(rt,'conversations',conv)
            rt.db.execute('UPDATE workspace_commands SET response=? WHERE id=?',(dumps(conv),body['operationId']))
        return conv

    def _sync_attention(self, rt):
        active = set()
        for wf in self.store.workflows(rt):
            shared_fault = bool(wf.get('sharedDeviceFault') and device_fault_blocks(wf))
            if wf['status'] != 'attention' and not shared_fault:
                continue
            if (wf.get('policy')=='strict' and wf['engineState'] in ('awaiting_acceptance','manual_ready')
                    and not wf.get('sharedDeviceFault')):
                continue
            node = next((n for n in wf['nodes'] if n['id']==wf.get('currentNodeId')), {})
            signature = digest([wf['id'],node.get('id'),node.get('latestAttemptId'),
                                'shared_device_fault' if shared_fault else wf['engineState']])
            active.add(signature)
            row = rt.db.execute('SELECT payload FROM platform_attention WHERE signature=?',(signature,)).fetchone()
            prior = json.loads(row[0]) if row else None
            state = wf['engineState']
            summary = None
            diagnostic_state = node.get('engineState') if shared_fault and wf['status'] == 'paused' else state
            if diagnostic_state in ('cancelled', 'failed_after_shutdown') and node.get('latestAttemptId'):
                receipts = [json.loads(row[0]) for row in rt.db.execute(
                    "SELECT result FROM platform_effects WHERE workflow_id=? AND attempt_id=? AND state IN ('cancelled','failed_after_shutdown')",
                    (wf['id'], node['latestAttemptId']))]
                summary = stopped_summary(receipts, selection_retained=bool((node.get('result') or {}).get('selectionResult')))
            same_revision = prior and prior['workflowRevision']==wf['revision']
            if same_revision and prior.get('executionSummary') == summary:
                continue
            connection_failed = bool(summary and summary['reason'] == 'connection_failed')
            options = ([('reconcile','核查执行回执')] if state=='needs_reconciliation' else
                       [('open_workflow','查看设备连接与失败详情')] if connection_failed else
                       [('open_workflow','查看故障并选择本步重试')] if shared_fault and wf['status']=='paused' and wf.get('policy')=='strict' else
                       [('resume','检查设备后重试')] if shared_fault and wf['status']=='paused' else
                       [('abort','结束此任务并补齐配置')] if state=='configuration_required' else
                       [('confirm_hardware','确认接线与设备释放')] if state=='waiting_hardware' else
                       [('open_workflow','查看步骤并处理')] if wf.get('policy')=='strict' else
                       [('decide','重新分析')] if state in ('quality_attention','waiting_model','budget_updated') and wf['policy']=='autopilot' else
                       [('open_workflow','查看任务并调整')] if state in ('budget_exhausted','plan_rejected','unsupported_method','adapter_changed','diagnostic_closed','diagnostic_failed','supplemental_acceptance_mismatch') else
                       [('resume','继续')])
            if state not in ('needs_reconciliation','configuration_required'):
                options.append(('abort','终止该任务'))
            recommendation = {'budget_exhausted':'本轮预算已用完。查看已采集结果，调整预算后继续，或结束任务。',
                'configuration_required':'补齐芯片身份、参数和接线，发布新版本后为此比特建立任务。',
                'plan_rejected':'查看参数检查结果，修正对应参数后复测。',
                'diagnostic_failed':'本次诊断未通过，请查看结果并选择复测或回退步骤。',
                'unsupported_method':'当前执行器未接入这项方法，请查看任务范围。'}.get(state,options[0][1])
            if connection_failed:
                recommendation = '先核对机箱网络、设备地址与占用，确认连接后再决定是否重测。'
            item = {'id': prior['id'] if prior else ident('attention'), 'revision':prior['revision']+1 if prior else 1,
                'workflowId':wf['id'],'campaignId':wf.get('campaignId'),'nodeId':node.get('id'),
                'attemptId':node.get('latestAttemptId'),'workflowRevision':wf['revision'],
                'resourceId':node.get('resourceId',wf.get('target')),
                'title':wf['target']+' · '+node.get('label','设备准备'),
                'message':summary['message'] if summary else node.get('detail') or (wf.get('events') or [{}])[-1].get('message','需要处理'),
                **({'executionSummary':summary} if summary else {}),
                'recommendation':recommendation, 'actions':[{'id':a,'label':b} for a,b in options],
                'state':prior['state'] if same_revision else 'open',
                'severity':'error' if state in DEVICE_STATES or wf.get('sharedDeviceFault') else 'warning',
                'scope':'device' if state in DEVICE_STATES or wf.get('sharedDeviceFault') else 'resource',
                'engineState':state,'createdAt':prior['createdAt'] if prior else now()}
            rt.db.execute('INSERT OR REPLACE INTO platform_attention VALUES(?,?,?)',(item['id'],signature,dumps(item)))
        for row in rt.db.execute('SELECT * FROM platform_attention').fetchall():
            item = json.loads(row['payload'])
            if row['signature'] not in active and item['state']!='resolved':
                item.update(state='resolved',revision=item['revision']+1,resolvedAt=now())
                rt.db.execute('UPDATE platform_attention SET payload=? WHERE id=?',(dumps(item),item['id']))

    def attention_items(self):
        with self.store.transaction() as rt:
            self._sync_attention(rt)
            return [json.loads(r[0]) for r in rt.db.execute('SELECT payload FROM platform_attention ORDER BY rowid')]

    def attention_action(self, id, body):
        with self.store.transaction() as rt:
            old, fingerprint = self._command(rt,body,'attention:'+id)
            if old is not None:
                return old
            self._sync_attention(rt)
            row=rt.db.execute('SELECT payload FROM platform_attention WHERE id=?',(id,)).fetchone()
            if not row:
                raise APIError('NOT_FOUND','待处理事项不存在',404)
            item=json.loads(row[0])
            self._revision(item,body)
            if item['state']=='resolved':
                raise APIError('ATTENTION_RESOLVED','该事项已经处理',409)
            action=body.get('action')
            if action=='defer':
                item.update(state='deferred',revision=item['revision']+1)
            elif action=='open_workflow' and action in {a['id'] for a in item['actions']}:
                item.update(state='deferred',revision=item['revision']+1)
            elif action in {a['id'] for a in item['actions']}:
                self.c.action(item['workflowId'],{'operationId':body['operationId']+':workflow',
                    'expectedRevision':item['workflowRevision'],'action':action,
                    **({'nodeId':item['nodeId']} if item.get('nodeId') else {})},_rt=rt)
                item.update(state='resolved',revision=item['revision']+1,resolvedAt=now())
            else:
                raise APIError('INVALID_ACTION','该事项不支持此操作')
            rt.db.execute('UPDATE platform_attention SET payload=? WHERE id=?',(dumps(item),id))
            self._remember(rt,body,fingerprint,item)
            return item
