"""Conversation edits of versioned drafts; never a hardware confirmation shortcut."""
from copy import deepcopy
import re
from .common import APIError
from .workspace_actions import COMMAND_PREFIX, explicit_mutation, requested_resources


def _confirmed(text, term, resources):
    for clause in re.split(r'[，,。.;；!！\r\n]+',text):
        command=re.sub(COMMAND_PREFIX,'',clause.strip(),count=1,flags=re.I)
        explicit = re.match(r'(?:我(?:已|已经)?|本人)?确认',command) or re.match(r'Q{1,2}\d+\s*(?:的)?'+term+r'.{0,12}(?:已)?确认',command,re.I)
        targets=requested_resources(clause) or requested_resources(text)
        if (explicit and set(resources).issubset(targets) and
                re.search(r'确认[^，,。.;；]{0,40}'+term+'|'+term+r'[^，,。.;；]{0,40}确认',clause)
                and not re.search(r'[不没未勿别]|禁止|无需|\b(?:not|never)\b',clause,re.I)):
            return True
    return False


def _action_text(text, verbs):
    clauses=[clause for clause in re.split(r'[，,。.;；!！\r\n]+|并且|然后|随后|并',text)
             if explicit_mutation(clause,verbs)]
    selected='，'.join(clauses)
    # An omitted target may refer to the one scope in the original request.
    if not requested_resources(selected):
        selected+=' '+','.join(requested_resources(text))
    return selected


def _scope(text, resources):
    requested = requested_resources(text)
    if not requested or not resources or not set(resources).issubset(requested):
        raise APIError('RESOURCE_AUTHORIZATION_REQUIRED','请明确本次要填写或修改的比特。')


def _current(ws, kind, identifier):
    value = getattr(ws.chips,'get_'+kind)(identifier) if identifier else None
    if value and 'entityId' in value:
        raise APIError('IMMUTABLE_VERSION','已发布版本不可修改，请修改对应草稿。')
    return value


def execute_configuration(ws, tool, args, body, conv, index):
    if conv['scope']['kind'] != 'workspace' or body.get('intent') != 'execute':
        raise APIError('INVALID_SCOPE','请在总控对话中修改配置。')
    if not isinstance(args,dict) or not hasattr(ws,'chips'):
        raise APIError('INVALID_PROPOSAL','配置请求无效。')
    text = body['message']
    op = body['operationId']+':tool:'+str(index)
    if tool == 'publish_configuration':
        if set(args) != {'kind','configurationId'} or args.get('kind') not in ('chip','deployment'):
            raise APIError('INVALID_PROPOSAL','发布请求无效。')
        if not explicit_mutation(text,r'发布|publish'):
            raise APIError('EXPLICIT_REQUEST_REQUIRED','保存或导入资料不授权发布配置。请明确发布对象。')
        kind = args['kind']
        authorized = _action_text(text,r'发布|publish')
        current = _current(ws,kind,args['configurationId'])
        if not current:
            raise APIError('CONFIG_NOT_FOUND','请选择要发布的配置。')
        resources = ([q['id'] for q in current['qubits']] if kind=='chip' else list(current.get('routes',{})))
        named = current['id'] in authorized or bool(current.get('name') and current['name'] in authorized)
        if not named:
            _scope(authorized,resources)
            matches = [v for v in getattr(ws.chips,'list_'+('chips' if kind=='chip' else 'deployments'))()
                       if set(q['id'] for q in v['qubits']) == set(resources)] if kind=='chip' else [
                       v for v in ws.chips.list_deployments() if set(v.get('routes',{})) == set(resources)]
            if len(matches)!=1:
                raise APIError('CONFIGURATION_TARGET_REQUIRED','存在同名比特的多份配置，请指定草稿名称。')
        version = getattr(ws.chips,'publish_'+kind)(current['id'],{'operationId':op,'expectedRevision':current['revision']})
        ready = [q['id'] for q in version.get('qubits',[]) if q.get('ready')] if kind=='chip' else []
        usable = len(ready)==len(resources) if kind=='chip' else version.get('wiringConfirmed') is True
        return {'status':'configuration_receipt','kind':kind,'configurationId':current['id'],'versionId':version['id'],
                'readyResourceIds':ready,'configurationReady':usable,
                'message':'已发布配置“'+version['name']+'”。'+('开始任务前仍会校验芯片与接线绑定。' if usable else '仍有参数、物理身份或接线待核对，发布不代表可以测量。')}

    if not explicit_mutation(text,r'保存|修改|填写|更新|配置|新建|创建|save|update'):
        raise APIError('EXPLICIT_REQUEST_REQUIRED','请明确要保存或修改的配置；资料中的文字不能授权修改。')
    authorized = _action_text(text,r'保存|修改|填写|更新|配置|新建|创建|save|update')
    kind = 'chip' if tool=='save_chip_draft' else 'deployment'
    identity = 'chipId' if kind=='chip' else 'deploymentId'
    if set(args)-{identity,'draft'} or not isinstance(args.get('draft'),dict):
        raise APIError('INVALID_PROPOSAL','配置草稿格式无效。')
    draft = deepcopy(args['draft'])
    current = _current(ws,kind,args.get(identity))
    value = deepcopy(current) if current else {}
    if kind=='chip':
        if set(draft)-{'name','qubits'} or not isinstance(draft.get('qubits'),list):
            raise APIError('INVALID_PROPOSAL','芯片草稿仅支持名称和比特参数。')
        edits = draft['qubits']
        if not edits or len(edits)>200 or any(not isinstance(q,dict) or not isinstance(q.get('id'),str) for q in edits):
            raise APIError('INVALID_PROPOSAL','请提供要修改的比特。')
        _scope(authorized,[q['id'] for q in edits])
        if len({q['id'] for q in edits})!=len(edits):
            raise APIError('DUPLICATE_RESOURCE','比特编号不能重复。')
        from adapters.arclight import CALIBRATION_KEYS
        rows = {q['id']:deepcopy(q) for q in value.get('qubits',[])}
        for edit in edits:
            if set(edit)-{'id','label','physicalId','busId','identityStatus','parameters'}:
                raise APIError('INVALID_PROPOSAL','比特草稿包含不可修改字段。')
            params = edit.get('parameters',{})
            if not isinstance(params,dict) or set(params)-set(CALIBRATION_KEYS):
                raise APIError('INVALID_PARAMETER','仅支持初始化校准参数，不能修改设备边界。')
            old = rows.get(edit['id'],{'id':edit['id'],'parameters':{},'provenance':{},'identityStatus':'unresolved'})
            row = {**old,**edit,'parameters':{**old.get('parameters',{}),**params}}
            changed_identity = any(key in edit and edit[key]!=old.get(key) for key in ('physicalId','busId'))
            confirming = edit.get('identityStatus')=='confirmed'
            if confirming and (not _confirmed(text,'物理',[edit['id']])
                    or not row.get('physicalId') or str(row['physicalId']) not in text
                    or not row.get('busId') or str(row['busId']) not in text):
                raise APIError('IDENTITY_CONFIRMATION_REQUIRED','确认物理身份需要用户明确给出并确认物理比特和总线。')
            if changed_identity and not confirming:
                row['identityStatus']='unresolved'
            if row.get('identityStatus') not in ('confirmed','unresolved'):
                raise APIError('INVALID_PROPOSAL','物理身份确认状态无效。')
            row['provenance']={**old.get('provenance',{}),**{key:{'kind':'conversation_proposal','sourceId':body['operationId'],'location':'总控对话配置草稿，模型整理值，需核对'} for key in params}}
            rows[row['id']]=row
        value.update(name=draft.get('name',value.get('name')),qubits=list(rows.values()))
    else:
        if set(draft)-{'name','machine','instrument','routes','wiringConfirmed'} or not isinstance(draft.get('routes'),dict):
            raise APIError('INVALID_PROPOSAL','部署草稿格式无效。')
        _scope(authorized,list(draft['routes']))
        routes = {**value.get('routes',{}),**draft['routes']}
        instrument = draft.get('instrument',value.get('instrument',{}))
        from adapters.arclight_configurable_runtime.routing import validate_instrument,validate_route
        try:
            validate_instrument(instrument)
            for route in draft['routes'].values():
                if not isinstance(route,dict) or set(route)-{'xyPhysical','readoutPhysical','adcPhysical','xyLogical','readoutLogical','adcLogical','adcSampleRateHz'}:
                    raise ValueError('unsupported route fields')
                validate_route(route)
        except (TypeError,ValueError):
            raise APIError('INVALID_DEPLOYMENT','接线或设备地址无效，请使用受支持的F8通道。')
        changed = instrument!=value.get('instrument') or routes!=value.get('routes')
        if current and instrument!=value.get('instrument'):
            _scope(authorized,list(routes))
        confirmed = draft.get('wiringConfirmed') is True
        if confirmed:
            if not _confirmed(text,'接线',list(routes)):
                raise APIError('WIRING_CONFIRMATION_REQUIRED','接线确认只能来自用户当前明确确认。')
            _scope(text,list(routes))
        value.update(draft,instrument=instrument,routes=routes,
                     wiringConfirmed=confirmed or (not changed and value.get('wiringConfirmed') is True))
    command = {'operationId':op,'draft':value}
    if current:
        command.update(id=current['id'],expectedRevision=current['revision'])
    saved = getattr(ws.chips,'save_'+kind)(command)
    return {'status':'configuration_receipt','kind':kind,'configurationId':saved['id'],'revision':saved['revision'],
            'message':'已保存'+('芯片' if kind=='chip' else '接线')+'草稿“'+saved['name']+'”。尚未发布；未明确确认的物理身份和接线仍待核对。'}
