"""Declared command coordinates; deliberately no voltage readback claims."""
from copy import deepcopy
import datetime as dt
import math
import re
from .contracts import ContractError,number,hash_value
from adapters.science_runtime.policy import digest

SCHEMA='qcal-bias-command-route/1'
POLICY='f8_offset_only_commanded_return_v2'
TEXT=('resourceId','deviceKey','lineId','stationIdentity')
HASHES=('preparedStaticDcEvidenceSha256','confirmationEvidenceSha256','startSettingEvidenceSha256','endSettingEvidenceSha256')
NUMBERS={'minimumV':(-10,10),'maximumV':(-10,10),'maxStepV':(1e-9,1),'maxSlewVPerSecond':(1e-9,10),
    'maxRampSeconds':(.01,120),'settleSeconds':(0,30),'commandTimeoutSeconds':(.01,10),
    'endTimeoutSeconds':(.1,180),'startSettingV':(-10,10),'endSettingV':(-10,10)}
REQUIRED=set(TEXT+HASHES+tuple(NUMBERS))|{'schema','board','channel','unit','polarity','endPolicy','commandReplyContract'}

def validate_command_route(source):
    if not isinstance(source,dict) or set(source)!=REQUIRED:
        raise ContractError('Command route fields differ; missing: '+', '.join(sorted(REQUIRED-set(source or {}))))
    r=deepcopy(source)
    if r['schema']!=SCHEMA or r['unit']!='V' or r['endPolicy']!='return_declared_setting':raise ContractError('Unsupported commanded bias schema/unit/endPolicy')
    for key in TEXT:
        if not isinstance(r[key],str) or not r[key].strip() or len(r[key])>128 or any(ord(c)<32 for c in r[key]):raise ContractError('Invalid '+key)
    for key in HASHES:hash_value(r[key],key)
    ack=r['commandReplyContract']
    if not isinstance(ack,dict) or set(ack)!={'kind','moduleReplies','channelReplies','offsetReplies','evidenceSha256'} or ack['kind']!='exact_tokens':
        raise ContractError('Confirmed exact native command reply contract required')
    hash_value(ack['evidenceSha256'],'command reply protocol evidence')
    for key in ('moduleReplies','channelReplies','offsetReplies'):
        tokens=ack[key]
        if (not isinstance(tokens,list) or not 1<=len(tokens)<=8
            or any(not isinstance(s,str) or not s.strip() or len(s)>2048 or s.strip().lower() in ('unknown','tbd','none','null','待核实')
                   or re.search(r'(?i)(?:\berr(?:or)?\b|\bfail(?:ed|ure)?\b|\bnack\b|\binvalid\b|\bunknown\b)',s) for s in tokens)
            or len(set(tokens))!=len(tokens)):raise ContractError('Invalid confirmed successful '+key)
    for key,bounds in NUMBERS.items():number(r[key],key,*bounds)
    for key,lo,hi in [('board',0,5),('channel',0,3)]:
        if type(r[key]) is not int or not lo<=r[key]<=hi:raise ContractError('Invalid '+key)
    if type(r['polarity']) is not int or r['polarity'] not in (-1,1):raise ContractError('Invalid polarity')
    if r['minimumV']>=r['maximumV']:raise ContractError('Invalid command interval')
    for key in ('startSettingV','endSettingV'):number(r[key],key,r['minimumV'],r['maximumV'])
    steps=max(1,math.ceil((r['maximumV']-r['minimumV'])/r['maxStepV']))
    if steps>2048:raise ContractError('Command ramp exceeds 2048 writes')
    worst=(r['maximumV']-r['minimumV'])/r['maxSlewVPerSecond']+steps*r['commandTimeoutSeconds']+r['settleSeconds']
    if worst>min(r['maxRampSeconds'],r['endTimeoutSeconds']):raise ContractError('Command/end deadline cannot cover bounded ramp')
    return r

def validate_start_confirmation(value,route,identity):
    required={'schema','operationId','runId','nodeId','resourceId','attemptId','confirmedAt','routeSha256',
        'startSettingV','endSettingV','confirmationId','commandOperationId','confirmedRevision'}
    if not isinstance(value,dict) or set(value)!=required or value['schema']!='qcal-bias-start-confirmation/1':raise ContractError('Explicit per-attempt initial-setting confirmation required')
    for key in ('operationId','runId','nodeId','resourceId','attemptId','confirmationId','commandOperationId'):
        if not isinstance(value[key],str) or not 1<=len(value[key])<=256 or any(ord(c)<32 for c in value[key]):raise ContractError('Invalid initial confirmation '+key)
    for key in ('operationId','runId','nodeId','resourceId','attemptId'):
        if key in identity and value[key]!=identity[key]:raise ContractError('Initial confirmation belongs to a different '+key)
    if value['resourceId']!=route['resourceId'] or value['routeSha256']!=digest(route):raise ContractError('Initial confirmation route differs')
    for key in ('startSettingV','endSettingV'):number(value[key],'confirmed '+key,route['minimumV'],route['maximumV'])
    if value['startSettingV']!=route['startSettingV'] or value['endSettingV']!=route['endSettingV']:raise ContractError('Initial confirmation start/end differs')
    if type(value['confirmedRevision']) is not int or value['confirmedRevision']<0:raise ContractError('Initial confirmation revision is missing')
    if value['confirmationId']!=digest({k:v for k,v in value.items() if k!='confirmationId'}):raise ContractError('Initial confirmation content hash differs')
    timestamp(value['confirmedAt'])
    return deepcopy(value)

def authorization(route,identity,*,stage='selection',entry_evidence=None,start_confirmation=None):
    r=validate_command_route(route)
    if not isinstance(identity.get('operationId'),str) or not identity['operationId'] or identity.get('resourceId')!=r['resourceId']:
        raise ContractError('Command authorization operation/resource missing')
    if stage=='selection':
        confirmation=validate_start_confirmation(start_confirmation,r,identity)
        expected={'kind':'declared_start','evidenceSha256':r['startSettingEvidenceSha256'],'confirmation':confirmation}
        if entry_evidence is not None and entry_evidence!=expected:raise ContractError('Selection requires its declared entry evidence')
        entry_evidence=expected;entry=r['startSettingV']
    elif stage=='plan_only':
        if start_confirmation is not None:raise ContractError('Budget preview cannot consume an initial-setting authorization')
        expected={'kind':'budget_end_assumption'}
        if entry_evidence is not None and entry_evidence!=expected:raise ContractError('Budget evidence differs')
        entry_evidence=expected;entry=r['endSettingV']
    elif stage=='verification':
        if start_confirmation is not None:raise ContractError('Continuous verification uses its prior end, not another initial confirmation')
        required={'kind','operationId','completionSha256','commandSha256','receiptSha256','leaseTokenSha256','lockTokenSha256'}
        if not isinstance(entry_evidence,dict) or set(entry_evidence)!=required or entry_evidence['kind']!='prior_end_command':raise ContractError('Verification requires authenticated prior end command')
        if not isinstance(entry_evidence['operationId'],str) or not entry_evidence['operationId'] or entry_evidence['operationId']==identity['operationId']:raise ContractError('Prior end must be a distinct operation')
        for key in required-{'kind','operationId'}:hash_value(entry_evidence[key],key)
        entry=r['endSettingV']
    else:raise ContractError('Unknown command authorization stage')
    return {'schema':'qcal-bias-command-authorization/1','operationId':identity['operationId'],'resourceId':r['resourceId'],
        'stage':stage,'entrySettingV':entry,'entryEvidence':deepcopy(entry_evidence),
        'routeSha256':digest(r),**{k:r[k] for k in ('startSettingV','endSettingV','startSettingEvidenceSha256',
            'endSettingEvidenceSha256','preparedStaticDcEvidenceSha256')}}

def validate_authorization(value,route,identity):
    if not isinstance(value,dict) or value!=authorization(route,identity,stage=value.get('stage'),entry_evidence=value.get('entryEvidence'),
        start_confirmation=value.get('entryEvidence',{}).get('confirmation')):raise ContractError('Exact operation start/end authorization is required')
    return deepcopy(value)

def timestamp(value):
    try:
        date=dt.datetime.fromisoformat(value)
        if date.tzinfo is None:raise ValueError()
        return date.timestamp()
    except (TypeError,ValueError):raise ContractError('Command receipt timestamp must have timezone') from None

def validate_native_reply(reply,route,target):
    if (not isinstance(reply,dict) or reply.get('nativeFunctions')!=['boardSelect','channSelect','setOffset']
        or reply.get('requestedSettingV')!=target or reply.get('unit')!='V' or reply.get('measuredOutput') is not False
        or not isinstance(reply.get('replies'),list) or len(reply['replies'])!=3):raise ContractError('Incomplete native command transaction')
    for raw,key in zip(reply['replies'],('moduleReplies','channelReplies','offsetReplies')):
        if not isinstance(raw,str) or raw not in route['commandReplyContract'][key]:raise ContractError('Unknown or unsuccessful native command reply: '+key)
    return reply

def validate_command_receipt(receipt,route,*,setting=None,identity=None,authorization_value=None):
    r=validate_command_route(route)
    if not isinstance(receipt,dict) or receipt.get('schema')!='qcal-commanded-bias-receipt/1' or receipt.get('policy')!=POLICY:
        raise ContractError('Missing commanded bias receipt')
    target=receipt.get('requestedSettingV') if setting is None else setting
    number(target,'commanded point',r['minimumV'],r['maximumV'])
    if receipt.get('setting')!={'kind':'commanded_setpoint','valueV':target} or receipt.get('measurement') is not None or receipt.get('measuredOutputV') is not None:
        raise ContractError('Commanded coordinate cannot claim a measured output')
    if receipt.get('readbackStatus')!='not_measured' or receipt.get('physicalBiasVerified') is not False:raise ContractError('Command/readback qualification differs')
    command=receipt.get('command',{})
    if command.get('schema')!='qcal-lf-command/1' or digest({k:v for k,v in command.items() if k!='sha256'})!=command.get('sha256'):
        raise ContractError('LF command digest differs')
    if command.get('routeSha256')!=digest(r) or command.get('valueV')!=target or not command.get('id'):
        raise ContractError('Command route/value/identity differs')
    if identity and any(command.get(k)!=identity[k] for k in ('operationId','resourceId')):raise ContractError('Command belongs to another operation')
    if command.get('resourceId')!=r['resourceId']:raise ContractError('Command resource differs')
    previous=number(command.get('previousCommandedV'),'previous commanded voltage',r['minimumV'],r['maximumV'])
    if abs(target-previous)>r['maxStepV']+1e-12:raise ContractError('Command step exceeds route bound')
    number(command.get('elapsedSeconds'),'command duration',0,r['commandTimeoutSeconds'])
    number(command.get('delaySeconds'),'command slew delay',abs(target-previous)/r['maxSlewVPerSecond']-1e-12,r['maxRampSeconds'])
    expected_authorization=validate_authorization(receipt.get('authorization'),r,command)
    if expected_authorization['stage']=='plan_only':raise ContractError('Budget assumptions cannot be a completed command receipt')
    if receipt.get('authorizationSha256')!=digest(expected_authorization):raise ContractError('Command authorization identity differs')
    if authorization_value is not None and expected_authorization!=validate_authorization(authorization_value,r,command):raise ContractError('Receipt authorization differs from operation')
    validate_native_reply(command.get('reply'),r,target)
    if timestamp(command['completedAt'])<timestamp(command['issuedAt']) or timestamp(receipt['observedAt'])<timestamp(command['completedAt']):
        raise ContractError('Command receipt time order differs')
    settle=receipt.get('settle',{})
    if settle.get('seconds')!=r['settleSeconds'] or settle.get('stableReadbackVerified') is not False:
        raise ContractError('Settle interval is not a measured stability claim')
    number(settle.get('elapsedSeconds'),'elapsed settling',r['settleSeconds'],r['maxRampSeconds']+r['endTimeoutSeconds'])
    return receipt

def validate_command_journal(journal,route,identity,authorization_value,*,allow_partial_initial=False):
    r=validate_command_route(route);validate_authorization(authorization_value,r,identity)
    if (journal.get('schema')!='qcal-commanded-bias-journal/1' or journal.get('policy')!=POLICY
        or journal.get('identity')!=identity or journal.get('route')!=r or journal.get('authorization')!=authorization_value
        or journal.get('state')!='returned_to_declared_setting'):
        raise ContractError('Command journal identity/end state differs')
    events=journal.get('events')
    if not isinstance(events,list) or not 2<=len(events)<=4096:raise ContractError('Command journal count out of bounds')
    commands={};previous=authorization_value['entrySettingV']
    for event in events:
        c=event.get('command',{})
        if event.get('type')!='set_offset' or event.get('outcome')!='transport_reply' or event.get('commandId')!=c.get('id') or event.get('valueV')!=c.get('valueV'):
            raise ContractError('Unknown/incomplete native command event')
        if c.get('id') in commands or c.get('previousCommandedV')!=previous:raise ContractError('Command journal continuity differs')
        if digest({k:v for k,v in c.items() if k!='sha256'})!=c.get('sha256'):raise ContractError('Command journal event hash differs')
        number(c.get('valueV'),'journal voltage',r['minimumV'],r['maximumV'])
        if c.get('routeSha256')!=digest(r) or any(c.get(k)!=identity[k] for k in ('operationId','resourceId')):raise ContractError('Intermediate command identity differs')
        if abs(c['valueV']-previous)>r['maxStepV']+1e-12:raise ContractError('Intermediate command step exceeds bound')
        number(c.get('delaySeconds'),'journal slew delay',abs(c['valueV']-previous)/r['maxSlewVPerSecond']-1e-12,r['maxRampSeconds'])
        number(c.get('elapsedSeconds'),'journal command duration',0,r['commandTimeoutSeconds'])
        validate_native_reply(c.get('reply'),r,c['valueV'])
        commands[c['id']]=c;previous=c['valueV']
    for name,value,point in [('initial',r['startSettingV'],'authorized_start'),('restoration',r['endSettingV'],'declared_end')]:
        receipt=journal.get(name,{})
        if name=='initial' and not receipt and allow_partial_initial:
            if any(e['command'].get('pointId') not in ('authorized_start','declared_end') for e in events):raise ContractError('Missing initial receipt after acquisition commands')
            continue
        validate_command_receipt(receipt,r,setting=value,identity=identity,authorization_value=authorization_value)
        if receipt.get('pointId')!=point or commands.get(receipt['command']['id'])!=receipt['command']:raise ContractError('Start/end receipt is absent from native journal')
    initial=journal.get('initial')
    initial_index=next(i for i,e in enumerate(events) if e['command']==initial['command']) if initial else -1
    if (any(e['command'].get('pointId')!='authorized_start' for e in events[:initial_index+1]) or events[-1]['command']!=journal['restoration']['command']
        or journal['restoration'].get('commandEndVerified') is not True or journal['restoration'].get('verified') is not False
        or journal['restoration'].get('initialPhysicalStateRestored') is not False):raise ContractError('Declared end command not independently journal-bound')
    return commands
