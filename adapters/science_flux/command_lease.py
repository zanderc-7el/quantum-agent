"""Continuous OS reservations between commanded-bias selection and verification.

No instrument I/O. A completed command is a command coordinate, not readback.
An incomplete, changed, or lost reservation is never silently released/reused.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
from .bias_session import durable_save
from .command_contracts import authorization,validate_authorization,validate_command_journal
from adapters.science_runtime.policy import digest,sha
from adapters.f8 import _digest as transport_digest

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def operation_folder(operations,operation):return Path(operations)/hashlib.sha256(operation.encode()).hexdigest()
def reservation_path(operations,lease_hash):return Path(operations)/'_command_leases'/(lease_hash+'.json')

def save_reservation(plan,folder,state):
    lease=plan['commandLease'];path=reservation_path(Path(folder).parent,lease['leaseTokenSha256']);path.parent.mkdir(exist_ok=True)
    durable_save(path,{'schema':'qcal-command-reservation/1','operationId':plan['operationId'],'lease':deepcopy(lease),
        'hardwareLocks':deepcopy(plan['hardwareLocks']),'state':state})

def lease_context(request,stage):
    return {'schema':'qcal-command-lease/1','stage':stage,
        **{k:request.get(k) for k in ('runId','nodeId','attemptId','resourceId')},
        'leaseTokenSha256':digest(request['leaseToken']) if request.get('leaseToken') else None}

def bind_locks(plan,folder,locks,token):
    save_reservation(plan,folder,'active')
    for path in locks:
        value=read(path)
        if value.get('token')!=token or value.get('operationId')!=plan['operationId']:raise ValueError('Cannot bind unowned command lock')
        value.update(commandLease=deepcopy(plan['commandLease']),biasRouteSha256=digest(plan['biasRoute']),commandLeaseState='active',root=str(folder))
        durable_save(path,value)

def retain_locks(plan,folder,locks,token,end):
    value={'schema':'qcal-command-continuation/1','state':'held_for_verification',
        'operationId':plan['operationId'],'lease':deepcopy(plan['commandLease']),
        'routeSha256':digest(plan['biasRoute']),'lockTokenSha256':digest(token),
        'hardwareLocks':deepcopy(plan['hardwareLocks']),'endCommandSha256':end['command']['sha256'],
        'endReceiptSha256':digest(end),'endSettingV':plan['biasRoute']['endSettingV']}
    value['sha256']=digest(value)
    for path in locks:
        current=read(path)
        if current.get('token')!=token or current.get('commandLease')!=plan['commandLease']:raise ValueError('Command reservation changed before handoff')
        current.update(commandLeaseState='awaiting_verification',commandContinuationSha256=value['sha256'])
        durable_save(path,current)
    save_reservation(plan,folder,'awaiting_verification')
    return value

def _source(operations,op,route,locks,context,*,require_locks):
    folder=operation_folder(operations,op);plan=read(folder/'plan.json');completion=read(folder/'completion.json')
    receipt=read(folder/'adapter_receipt.json');intent=read(folder/'adapter_intent.json');actual=intent['request']
    if (plan.get('operationId')!=op or completion.get('operationId')!=op or receipt.get('operationId')!=op or actual.get('operationId')!=op
        or receipt.get('status')!='completed' or completion.get('state')!='completed' or completion.get('shutdown_ok') is not True
        or completion.get('testOnly') is not False):raise ValueError('Prior command operation is not completed physical acquisition')
    if plan.get('biasRoute')!=route or plan.get('hardwareLocks')!=locks:raise ValueError('Prior command physical route/lock set differs')
    expected={**context,'stage':'selection'}
    if plan.get('commandLease')!=expected or lease_context(actual,'selection')!=expected:raise ValueError('Prior command does not belong to the same run/node/attempt/lease')
    if not context.get('leaseTokenSha256') or any(not context.get(k) for k in ('runId','nodeId','attemptId','resourceId')):raise ValueError('Continuous controller lease identity missing')
    auth=validate_authorization(plan.get('biasCommandAuthorization'),route,actual)
    if auth['stage']!='selection':raise ValueError('Prior operation is not an authorized selection')
    if (actual.get('biasCommandAuthorization')!=auth or plan.get('requestHash')!=digest(actual)
        or plan.get('transportRequestHash')!=transport_digest(actual) or intent.get('requestHash')!=transport_digest(actual)):
        raise ValueError('Prior command request/plan/intent hashes differ')
    profile=plan.get('profileSnapshot',{})
    if digest(profile)!=plan.get('profileHash') or transport_digest(profile)!=intent.get('profileHash'):raise ValueError('Prior command profile identity differs')
    if digest(plan.get('sources'))!=plan.get('sourceHash'):raise ValueError('Prior command source closure differs')
    journal=read(folder/'results/bias_journal.json')
    validate_command_journal(journal,route,{'operationId':op,'resourceId':route['resourceId']},auth)
    if any(journal.get(k)!=completion.get(k) for k in ('initial','restoration')):raise ValueError('Prior command completion/journal differs')
    if completion.get('rfShutdown',{}).get('verified') is not True or completion.get('transportClose',{}).get('shutdown_ok') is not True or completion.get('providerClosed') is not True:raise ValueError('Prior RF/station closure is incomplete')
    registered=receipt.get('result',{}).get('artifacts',[])
    for rel in ('plan.json','completion.json','adapter_intent.json','results/bias_journal.json'):
        path=folder/rel
        if not any(a.get('name')==path.name and a.get('sha256')==sha(path) for a in registered):raise ValueError('Prior command metadata is not receipt-bound: '+rel)
    c=completion.get('commandContinuation',{});end=completion['restoration']
    expected_c={'schema':'qcal-command-continuation/1','state':'held_for_verification','operationId':op,'lease':expected,
        'routeSha256':digest(route),'lockTokenSha256':c.get('lockTokenSha256'),'hardwareLocks':locks,
        'endCommandSha256':end['command']['sha256'],'endReceiptSha256':digest(end),'endSettingV':route['endSettingV']}
    expected_c['sha256']=digest(expected_c)
    if c!=expected_c or not isinstance(c.get('lockTokenSha256'),str) or len(c['lockTokenSha256'])!=64:raise ValueError('Prior command lock handoff receipt differs')
    records=[]
    if require_locks:
        for path in locks:
            value=read(path)
            if (value.get('operationId')!=op or value.get('root')!=str(folder) or value.get('commandLease')!=expected
                or value.get('commandLeaseState')!='awaiting_verification' or value.get('biasRouteSha256')!=digest(route)
                or value.get('commandContinuationSha256')!=c['sha256'] or digest(value.get('token'))!=c['lockTokenSha256']):raise ValueError('Continuous shared command lock was lost or changed')
            records.append(value)
    evidence={'kind':'prior_end_command','operationId':op,'completionSha256':sha(folder/'completion.json'),
        'commandSha256':end['command']['sha256'],'receiptSha256':digest(end),
        'leaseTokenSha256':context['leaseTokenSha256'],'lockTokenSha256':c['lockTokenSha256']}
    return evidence,records

def verification_authorization(operations,request,route,locks,source_operation):
    evidence,_=_source(operations,source_operation,route,locks,lease_context(request,'verification'),require_locks=True)
    return authorization(route,request,stage='verification',entry_evidence=evidence)

def validate_prior_entry(operations,plan,*,require_locks=False):
    auth=plan['biasCommandAuthorization']
    if auth['stage']!='verification':return
    evidence,_=_source(operations,auth['entryEvidence']['operationId'],plan['biasRoute'],plan['hardwareLocks'],plan['commandLease'],require_locks=require_locks)
    if evidence!=auth['entryEvidence']:raise ValueError('Prior end authorization differs from archived source')
    previous=read(operation_folder(operations,evidence['operationId'])/'plan.json')
    for key in ('profileHash','sourceHash','routing','instrument','configurationBinding','observedResourceBinding'):
        if previous.get(key)!=plan.get(key):raise ValueError('Prior command configuration changed before verification: '+key)

def ensure_no_connection(instrument):
    result=subprocess.run(['netstat.exe','-ano','-p','tcp'],capture_output=True,text=True,timeout=15,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('Cannot confirm exclusive local chassis connection')
    target=instrument['host']+':'+str(instrument['port'])
    if any(target in line and 'ESTABLISHED' in line for line in result.stdout.splitlines()):raise ValueError('Existing chassis connection prevents lock handoff')

def acquire_command_locks(plan,folder,operations):
    auth=plan['biasCommandAuthorization']
    if auth['stage']!='verification':
        from platform_worker import acquire_locks
        locks,token=acquire_locks(plan,folder);bind_locks(plan,folder,locks,token);return locks,token
    evidence,records=_source(operations,auth['entryEvidence']['operationId'],plan['biasRoute'],plan['hardwareLocks'],plan['commandLease'],require_locks=True)
    if evidence!=auth['entryEvidence']:raise ValueError('Handoff authorization differs')
    ensure_no_connection(plan['instrument']);token=records[0]['token'];locks=[Path(p) for p in plan['hardwareLocks']]
    # Lock files are never deleted/recreated during handoff. A partial metadata
    # update leaves a reservation for reconciliation, never a free resource.
    for path,record in zip(locks,records):
        current=read(path)
        if current!=record:raise ValueError('Lock changed during handoff')
        current.update(pid=os.getpid(),operationId=plan['operationId'],root=str(folder),commandLease=deepcopy(plan['commandLease']),
            commandLeaseState='active',entryEvidence=deepcopy(evidence))
        durable_save(path,current)
    save_reservation(plan,folder,'active')
    os.environ['QCAL_ARCLIGHT_LOCK_TOKEN']=token;os.environ['QCAL_ARCLIGHT_LOCKS']=json.dumps([str(p) for p in locks])
    return locks,token

def release_command_locks(plan,folder,locks,token):
    records=[read(p) for p in locks]
    if any(r.get('token')!=token or r.get('operationId')!=plan['operationId'] or r.get('commandLease')!=plan['commandLease'] for r in records):raise ValueError('Command lock ownership changed before release')
    for p in reversed(locks):Path(p).unlink()
    save_reservation(plan,folder,'released_after_shutdown')

def finalize_lease(operations,locks,lease_token):
    """Controller finally hook. Filesystem only; never release unknown state."""
    try:
        index=reservation_path(operations,digest(lease_token))
        reservation=read(index) if index.exists() else None
        records=[]
        for p in locks:
            path=Path(p)
            if path.exists():records.append((path,read(path)))
        owned=[(p,r) for p,r in records if r.get('commandLease',{}).get('leaseTokenSha256')==digest(lease_token)]
        if not owned:
            if reservation is None:return {'released':False,'needsReconciliation':False,'reason':'No command reservation was created for this lease'}
            if reservation.get('state')!='released_after_shutdown':raise ValueError('Command reservation vanished without a known shutdown release')
            folder=operation_folder(operations,reservation['operationId']);completion=read(folder/'completion.json');plan=read(folder/'plan.json')
            if (reservation.get('hardwareLocks')!=locks or plan.get('commandLease')!=reservation.get('lease')
                or plan['commandLease'].get('leaseTokenSha256')!=digest(lease_token)
                or completion.get('shutdown_ok') is not True or completion.get('state') not in ('completed','cancelled','failed_after_shutdown')
                or completion.get('operationId')!=plan.get('operationId')):raise ValueError('Released command reservation lacks matching known shutdown')
            if not completion.get('restoration',{}).get('noSessionOpened'):
                journal=read(folder/'results/bias_journal.json')
                if completion.get('restoration',{}).get('noWriteAttempted'):
                    if journal.get('events')!=[] or journal.get('state')!='no_write_attempted' or journal.get('restoration')!=completion['restoration']:raise ValueError('No-write cleanup lacks empty durable journal')
                else:validate_command_journal(journal,plan['biasRoute'],
                    {'operationId':plan['operationId'],'resourceId':plan['resourceId']},plan['biasCommandAuthorization'],
                    allow_partial_initial=completion.get('state') in ('cancelled','failed_after_shutdown') and completion.get('pointsCompleted')==0)
            return {'released':True,'needsReconciliation':False,'reason':'Native worker released same-lease locks after confirmed shutdown/end'}
        if len(owned)!=len(locks):raise ValueError('Partial or mixed command reservation; retain remaining locks')
        first=owned[0][1];op=first['operationId'];plan=read(operation_folder(operations,op)/'plan.json')
        _source(operations,op,plan['biasRoute'],locks,first['commandLease'],require_locks=True)
        # Recheck the whole set before removing any lock. The API invokes this
        # only after its worker has returned and before dropping the DB lease.
        if any(read(p)!=r for p,r in owned):raise ValueError('Command reservation changed before final release')
        release={'schema':'qcal-command-lease-release/1','operationId':op,'leaseTokenSha256':digest(lease_token),
            'lockTokenSha256':digest(first['token']),'reason':'Controller finished without a verification handoff','hardwareLocks':locks}
        durable_save(operation_folder(operations,op)/'command_lease_release.json',release)
        for path,_ in reversed(owned):path.unlink()
        save_reservation(plan,operation_folder(operations,op),'released_after_shutdown')
        return {'released':True,'needsReconciliation':False,'reason':release['reason']}
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return {'released':False,'needsReconciliation':True,'reason':str(exc)}
