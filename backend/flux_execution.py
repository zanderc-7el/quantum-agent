"""Server-owned command authorization and lock-finalization; no device writes."""
import json
from .common import APIError,digest,now


def start_requirement(profile,node):
    from adapters.science_flux.contracts import is_commanded
    route=profile.get('biasRoutes',{}).get(node.get('resourceId'),{})
    if node.get('procedureId') not in ('ro_flux_scan','sq_flux_point_recheck') or not is_commanded(route):return None
    return {'resourceId':node['resourceId'],'nodeId':node['id'],'lineId':route['lineId'],
        'routeSha256':digest(route),'startSettingV':route['startSettingV'],'endSettingV':route['endSettingV'],'unit':'V'}


def present_start_requirements(wf,profile):
    result={node['id']:value for node in wf.get('nodes',[]) if (value:=start_requirement(profile,node))}
    if result:wf['biasStartRequirements']=result
    return wf


def manual_confirmation(profile,wf,node,payload):
    required=start_requirement(profile,node)
    supplied=payload.get('biasStartConfirmation')
    if required is None:
        if supplied is not None:raise APIError('BIAS_START_CONFIRMATION_CHANGED','当前步骤不使用偏置起始确认',409)
        return None
    if not isinstance(supplied,dict) or supplied.get('confirmed') is not True:
        raise APIError('BIAS_START_CONFIRMATION_REQUIRED','运行前请核对本次线路与起始设定，并勾选“已核对当前起始设定”',409)
    expected={'confirmed':True,**{key:required[key] for key in ('routeSha256','startSettingV','endSettingV')}}
    if digest(supplied)!=digest(expected):
        raise APIError('BIAS_START_CONFIRMATION_CHANGED','偏置线路或起止设定与本次确认不一致，请重新核对',409)
    return {'schema':'qcal-bias-start-confirmation/1','runId':wf['id'],'nodeId':node['id'],
        'resourceId':node['resourceId'],'commandOperationId':payload['operationId'],
        'confirmedRevision':wf['revision'],'confirmedAt':now(),
        **{key:required[key] for key in ('routeSha256','startSettingV','endSettingV')}}


def selection_confirmation(wf,node,request,route):
    permit=wf.get('manualStep') or {};value=permit.get('biasStartConfirmation')
    if (not isinstance(value,dict) or permit.get('targetNodeId')!=node['id']
        or permit.get('attemptId')!=request.get('attemptId')
        or request.get('operationId')!=str(request.get('attemptId'))+'/measure'
        or value.get('commandOperationId')!=permit.get('operationId')
        or value.get('confirmedRevision')!=permit.get('requestedRevision')
        or any(value.get(k)!=v for k,v in {'runId':wf['id'],'nodeId':node['id'],'resourceId':node['resourceId'],
            'routeSha256':digest(route),'startSettingV':route['startSettingV'],'endSettingV':route['endSettingV']}.items())):
        raise APIError('BIAS_START_CONFIRMATION_REQUIRED','本次采集缺少匹配的人工起始设定确认，请重新运行本步',409)
    result={**value,'operationId':request['operationId'],'attemptId':request['attemptId']}
    result['confirmationId']=digest(result)
    return result


def request_authorization(controller,rt,wf,node,request):
    from adapters.science_flux.contracts import command_authorization,is_commanded
    route=controller.profile.get('biasRoutes',{}).get(node['resourceId'],{})
    previous=request.get('biasCommandAuthorization')
    if not is_commanded(route):
        if previous is not None:raise APIError('BIAS_AUTHORIZATION_CHANGED','测压策略不能混入设定命令声明',409)
        return
    if isinstance(previous,dict) and previous.get('stage')=='verification':
        source_id=previous.get('entryEvidence',{}).get('operationId')
        source=rt.db.execute("SELECT * FROM platform_effects WHERE operation_id=? AND workflow_id=? AND attempt_id=? AND state='completed'",
            (source_id,wf['id'],request.get('attemptId'))).fetchone()
        if not source:raise APIError('BIAS_CONTINUATION_MISSING','独立复测缺少同次首测的已完成结束回执',409)
        first=json.loads(source['request'])
        if (first.get('resourceId')!=request.get('resourceId') or first.get('nodeId')!=request.get('nodeId')
            or first.get('leaseToken')!=request.get('leaseToken') or not request.get('leaseToken')
            or first.get('operationId')!=str(request.get('attemptId'))+'/measure'
            or request.get('operationId')!=str(request.get('attemptId'))+'/verify'):
            raise APIError('BIAS_CONTINUATION_MISMATCH','复测必须接续同节点、同次采集和同一独占租约',409)
        done=rt.db.execute("SELECT request,result FROM platform_effects WHERE operation_id=? AND workflow_id=? AND attempt_id=? AND state='completed'",
            (request['operationId'],wf['id'],request.get('attemptId'))).fetchone()
        if done:
            expected=json.loads(done['request']).get('biasCommandAuthorization')
            if json.loads(done['result']).get('provenance',{}).get('scienceFlux',{}).get('commandAuthorization')!=expected:
                raise APIError('BIAS_AUTHORIZATION_CHANGED','完成回执与冻结复测命令声明不一致',409)
        else:expected=controller.adapter.command_verification_authorization(request,source_id,source_effect={'request':first,'result':json.loads(source['result'])})
        actual_source=json.loads(source['result']);flux=actual_source.get('provenance',{}).get('scienceFlux',{})
        end=flux.get('restoration',{});continuation=flux.get('commandContinuation',{}) or {}
        expected_entry={'kind':'prior_end_command','operationId':source_id,'completionSha256':flux.get('completionSha256'),
            'commandSha256':end.get('command',{}).get('sha256'),'receiptSha256':digest(end),
            'leaseTokenSha256':digest(request['leaseToken']),'lockTokenSha256':continuation.get('lockTokenSha256')}
        if actual_source.get('sourceRunId')!=source_id or expected.get('entryEvidence')!=expected_entry:
            raise APIError('BIAS_CONTINUATION_CHANGED','原生接续证明与已登记首测结束回执不一致',409)
    else:
        stage='plan_only' if not request.get('leaseToken') or (isinstance(previous,dict) and previous.get('stage')=='plan_only') else 'selection'
        options={'start_confirmation':selection_confirmation(wf,node,request,route)} if stage=='selection' else {}
        expected=command_authorization(route,request['operationId'],stage=stage,**options)
    if previous is not None and previous!=expected:
        raise APIError('BIAS_AUTHORIZATION_CHANGED','本次偏置起止命令声明已改变',409)
    from adapters.science_flux.command_contracts import validate_authorization
    validate_authorization(expected,route,request)
    request['biasCommandAuthorization']=expected


def verification_authorization(controller,request,source_operation_id):
    from adapters.science_flux.contracts import is_commanded
    route=controller.profile.get('biasRoutes',{}).get(request['resourceId'],{})
    if is_commanded(route):
        with controller.store.connect() as rt:
            source=rt.db.execute("SELECT request,result FROM platform_effects WHERE operation_id=? AND workflow_id=? AND attempt_id=? AND state='completed'",
                (source_operation_id,request['runId'],request['attemptId'])).fetchone()
            if not source:raise APIError('BIAS_CONTINUATION_MISSING','独立复测缺少已登记首测完成回执',409)
            effect={'request':json.loads(source['request']),'result':json.loads(source['result'])}
        request['biasCommandAuthorization']=controller.adapter.command_verification_authorization(request,source_operation_id,source_effect=effect)


def owns_verification_reservation(controller,request,ownership):
    """Narrow exception for the still-held, freshly authenticated same-lease lock set.

    Socket owners and unrelated/missing locks remain blockers. Native admission
    checks these files again immediately before the uninterrupted handoff.
    """
    from pathlib import Path
    from adapters.science_flux_adapter import ScienceFluxAdapter,shared_locks
    from adapters.science_flux.contracts import is_commanded
    auth=request.get('biasCommandAuthorization') or {}
    if (type(controller.adapter) is not ScienceFluxAdapter or ownership.get('verified') is not True
        or request.get('procedureId') not in ('ro_flux_scan','sq_flux_point_recheck')
        or not is_commanded(controller.profile.get('biasRoutes',{}).get(request.get('resourceId')))
        or auth.get('stage')!='verification'):return False
    owners=ownership.get('owners')
    locks=shared_locks(controller.root,controller.profile)
    if not isinstance(owners,list) or not owners or len(owners)!=len(locks):return False
    source=auth.get('entryEvidence',{}).get('operationId')
    if any(not isinstance(o,dict) or o.get('kind')!='chassis_lock' or not isinstance(o.get('path'),str)
        or o.get('operationId')!=source for o in owners):return False
    if {Path(o['path']).resolve() for o in owners}!={Path(p).resolve() for p in locks}:return False
    # This rechecks immutable DB selection request/result against native files,
    # completion/end hashes, route, same lease token and every current OS lock.
    with controller.store.connect() as rt:
        lease=rt.db.execute("SELECT 1 FROM platform_leases WHERE workflow_id=? AND attempt_id=? AND token=? AND state='active'",
            (request.get('runId'),request.get('attemptId'),request.get('leaseToken'))).fetchone()
        pending=rt.db.execute("SELECT 1 FROM platform_effects WHERE operation_id=? AND workflow_id=? AND attempt_id=? AND state='pending'",
            (request.get('operationId'),request.get('runId'),request.get('attemptId'))).fetchone()
        if not lease or not pending:return False
        wf=controller.get(request['runId'],rt)
        request_authorization(controller,rt,wf,controller._node(wf,request['nodeId']),request)
    return True


def finalize_lease(controller,request,token):
    from adapters.science_flux.contracts import is_commanded
    if request.get('procedureId') not in ('ro_flux_scan','sq_flux_point_recheck') or not is_commanded(controller.profile.get('biasRoutes',{}).get(request['resourceId'],{})):
        return
    try:result=controller.adapter.finalize_command_lease(token)
    except Exception as exc:raise APIError('BIAS_LOCK_RECONCILIATION','偏置连续独占锁结束核验失败：'+str(exc),409) from None
    if not isinstance(result,dict) or type(result.get('released')) is not bool or result.get('needsReconciliation') is not False:
        raise APIError('BIAS_LOCK_RECONCILIATION','偏置结束命令或连续独占锁释放未确认，需人工核对：'+str((result or {}).get('reason','回执缺失')),409)
