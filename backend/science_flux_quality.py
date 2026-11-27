"""Independent, standard-library checks of bias-scoped acquisition receipts."""
from datetime import datetime
import math
import re
from statistics import mean,median
from .common import digest,number


def need(ok,message):
    if not ok:raise ValueError(message)


def near(a,b):return number(a) and number(b) and abs(a-b)<=max(1e-12,abs(b)*1e-12)


def stamp(value):
    date=datetime.fromisoformat(value);need(date.tzinfo is not None,'回读时间缺少时区');return date.timestamp()


def receipt(value,route,*,setting,measured=None,initial=None,acquired_at=None,identity=None,authorization=None):
    if route.get('schema')=='qcal-bias-command-route/1':
        from adapters.science_flux.command_contracts import validate_command_receipt
        validate_command_receipt(value,route,setting=setting,identity=identity,authorization_value=authorization)
        if acquired_at is not None:need(stamp(value['observedAt'])<=stamp(acquired_at)+1,'偏置命令回执晚于采集完成')
        return
    need(isinstance(value,dict),'缺少实测偏置回执')
    samples=value.get('stabilitySamples')
    need(isinstance(samples,list) and len(samples)==route['stabilitySamples'],'缺少完整稳定性回读序列')
    nonces=[];times=[];measurements=[]
    for sample in samples:
        need(sample.get('providerId')==route['providerId'] and sample.get('protocolSha256')==route['providerProtocolSha256'],'测量提供器身份不一致')
        state=sample.get('outputState',{});register=sample.get('setting',{});actual=sample.get('measurement',{})
        need(state.get('kind')=='hardware_setting_register' and state.get('mode')=='static_dc' and state.get('outputEnabled') is True and state.get('raw'),'缺少硬件静态直流输出状态')
        if initial:need(state==initial['outputState'],'偏置过程中共享输出设置改变')
        need(register.get('kind')=='hardware_setting_register' and actual.get('kind')=='measured_output','缓存或命令确认不能替代偏置回读')
        need(register.get('raw') and actual.get('raw') and actual.get('plane')==route['measurementPlane']
             and actual.get('instrumentIdentity')==route['measurementInstrumentIdentity'],'缺少原始回读，或测量平面/仪表身份不一致')
        need(all(number(v) for v in (register.get('valueV'),actual.get('valueV'),actual.get('uncertaintyV'))),'偏置回读必须为有限数值')
        need(route['minimumV']<=register['valueV']<=route['maximumV'] and 0<=actual['uncertaintyV']<=route['maximumUncertaintyV'],'设置或回读不确定度超出范围')
        need(route['minimumMeasuredV']<=actual['valueV']-actual['uncertaintyV']<=actual['valueV']+actual['uncertaintyV']<=route['maximumMeasuredV'],'实测电压超出工作范围')
        target=route['offsetToOutput']['gain']*setting+route['offsetToOutput']['offsetV'] if measured is None else measured
        uncertainty=actual['uncertaintyV']+route['offsetToOutput']['uncertaintyV']+(initial['measurement']['uncertaintyV'] if measured is not None and initial else 0)
        need(abs(register['valueV']-setting)<=route['toleranceV']/max(abs(route['offsetToOutput']['gain']),1),'寄存器回读与本点设置不符')
        need(abs(actual['valueV']-target)+uncertainty<=route['toleranceV'],'实测偏置超出声明容差')
        need(isinstance(sample.get('nonce'),str) and sample['nonce'],'回读缺少独立请求身份')
        nonces.append(sample['nonce']);times.append(stamp(sample.get('observedAt')));measurements.append(actual['valueV'])
    need(len(set(nonces))==len(nonces) and all(a<=b for a,b in zip(times,times[1:])),'回读复用了请求或时间顺序不一致')
    if acquired_at is not None:need(times[-1]<=stamp(acquired_at)+1,'回读晚于采集完成时间')
    need(max(measurements)-min(measurements)<=route['toleranceV'],'偏置回读未稳定')
    need(all(value.get(key)==samples[-1].get(key) for key in ('nonce','observedAt','setting','measurement','outputState')),'点位最终回读与稳定样本不一致')


def cavity(point,captures):
    analysis=point.get('analysis',{});xs=point['frequenciesHz'];series=analysis.get('series',[]);declared=analysis.get('captureCandidates',[])
    need(len(series)==captures and len(declared)==captures,'读取腔截线缺少独立采集轮次')
    candidates=[];snrs=[]
    for i,(trace,reported) in enumerate(zip(series,declared)):
        ys=trace.get('y');sem=trace.get('sem')
        need(trace.get('x')==xs and isinstance(ys,list) and isinstance(sem,list) and len(ys)==len(xs)==len(sem)
            and all(number(v) and v>=0 for v in ys+sem),'原始腔截线或误差数组无效')
        edge=max(2,len(ys)//8);left,right=median(ys[:edge]),median(ys[-edge:])
        depth=[left+(right-left)*j/(len(ys)-1)-y for j,y in enumerate(ys)]
        k=max(range(len(depth)),key=depth.__getitem__)
        rough=median(abs(ys[j+2]-2*ys[j+1]+ys[j]) for j in range(len(ys)-2))/1.65
        snr=max(0,depth[k])/max(median(sem),rough,1e-30)
        ambiguous=any(abs(j-k)>2 and depth[j]>depth[j-1] and depth[j]>=depth[j+1] and depth[j]>=.8*depth[k] for j in range(1,len(ys)-1))
        need(0<k<len(xs)-1 and snr>=5 and not ambiguous,'读取腔截线未形成唯一、内部且信噪比合格的共振')
        need(reported.get('capture')==i and reported.get('pointCount')==len(xs) and reported.get('peakIndex')==k
            and reported.get('interior') is True and reported.get('ambiguous') is False
            and near(reported.get('candidateHz'),xs[k]) and near(reported.get('snr'),snr),'腔频诊断与原始截线复算不符')
        candidates.append(xs[k]);snrs.append(snr)
    tolerance=max(5e4,2*median(b-a for a,b in zip(xs,xs[1:])))
    need(max(candidates)-min(candidates)<=tolerance and near(analysis.get('candidateHz'),mean(candidates))
         and near(point.get('readoutFrequencyHz'),mean(candidates)),'腔频候选未在各次采集重复')
    need(near(analysis.get('contrast_snr'),min(snrs)),'腔频信噪比摘要不一致')
    need(len(point['responseMagnitude'])==len(xs) and all(near(y,mean(t['y'][j] for t in series)) for j,y in enumerate(point['responseMagnitude'])),'原始截线均值不一致')
    return mean(candidates),tolerance


def spectrum(point,captures):
    a=point['analysis'];xs=point['frequenciesHz'];repeats=a.get('captureCandidates',[])
    signals=a.get('signalCaptures',[]);traces=a.get('series',[])
    need(len(repeats)==len(signals)==len(traces)==captures,'比特频谱缺少完整重复采集与复数误差证据')
    candidates=[];snrs=[];curves=[]
    for index,(r,signal,trace) in enumerate(zip(repeats,signals,traces)):
        need(signal.get('capture')==index and signal.get('frequenciesHz')==xs and trace.get('x')==xs,'同偏置比特谱采集轴或轮次不一致')
        fields=('offMeanI','offMeanQ','onMeanI','onMeanQ','offComplexSem','onComplexSem')
        need(all(isinstance(signal.get(k),list) and len(signal[k])==len(xs) and all(number(v) for v in signal[k]) for k in fields),'比特谱缺少完整复数均值与采集误差数组')
        need(all(v>=0 for k in fields[-2:] for v in signal[k]),'比特谱采集误差不能为负')
        delta=[complex(a-b,c-d) for a,b,c,d in zip(signal['onMeanI'],signal['offMeanI'],signal['onMeanQ'],signal['offMeanQ'])]
        edge=max(1,len(xs)//8);ends=delta[:edge]+delta[-edge:]
        background=complex(median(v.real for v in ends),median(v.imag for v in ends))
        reported_background=signal.get('backgroundComplex',[])
        need(len(reported_background)==2 and near(reported_background[0],background.real) and near(reported_background[1],background.imag),'比特谱复数背景与边缘样点不符')
        excursion=[abs(v-background) for v in delta];k=max(range(len(xs)),key=excursion.__getitem__)
        snr=excursion[k]/max(math.hypot(signal['offComplexSem'][k],signal['onComplexSem'][k]),1e-30)
        need(all(isinstance(values,list) and len(values)==len(xs) and all(near(v,actual) for v,actual in zip(values,excursion))
            for values in (signal.get('excursionMagnitude'),trace.get('y'))),'比特谱观测曲线与复数均值不一致')
        f=r.get('candidate_frequency_hz');snr=r.get('candidate_significance')
        actual_snr=excursion[k]/max(math.hypot(signal['offComplexSem'][k],signal['onComplexSem'][k]),1e-30)
        need(r.get('candidate_detected') is True and 0<k<len(xs)-1 and near(f,xs[k]) and near(snr,actual_snr) and actual_snr>=5,'同偏置比特谱峰未在内部重复达到实际信噪比门槛')
        candidates.append(f);snrs.append(actual_snr);curves.append(excursion)
    tolerance=max(5e4,2*median(b-a for a,b in zip(xs,xs[1:])))
    need(max(candidates)-min(candidates)<=tolerance and near(a.get('candidateHz'),mean(candidates))
        and near(point.get('f01Hz'),mean(candidates)),'同偏置比特频谱重复候选不一致')
    need(near(a.get('contrast_snr'),min(snrs)) and len(point['responseMagnitude'])==len(xs)
        and all(near(value,mean(curve[i] for curve in curves)) for i,value in enumerate(point['responseMagnitude'])),'比特谱信噪比或观测曲线摘要不一致')
    return mean(candidates),tolerance


def qualified(result,procedure):
    from adapters.science_flux.contracts import policy_for,is_commanded,validate_route,command_authorization
    from adapters.science_flux.plans import build_campaign
    flux=result.get('provenance',{}).get('scienceFlux',{})
    need(flux.get('schema')=='qcal-science-flux-result/1','缺少原生偏置采集与结束回执')
    route=validate_route(flux.get('route'));need(flux.get('routeSha256')==digest(route),'偏置线路摘要不一致')
    commanded=is_commanded(route);need(flux.get('policy')==policy_for(route),'偏置执行策略与线路不一致')
    identity={'operationId':result.get('sourceRunId'),'resourceId':route['resourceId']}
    authorization=flux.get('commandAuthorization') if commanded else None
    if commanded:
        from adapters.science_flux.command_contracts import validate_authorization
        validate_authorization(authorization,route,identity)
        need(authorization.get('stage') in ('selection','verification'),'规划假设不能作为实际设定命令证据')
        lease=flux.get('commandLease',{})
        need(lease.get('schema')=='qcal-command-lease/1' and lease.get('stage')==authorization['stage']
            and lease.get('resourceId')==route['resourceId'] and all(isinstance(lease.get(k),str) and lease[k] for k in ('runId','nodeId','attemptId'))
            and isinstance(lease.get('leaseTokenSha256'),str) and re.fullmatch('[0-9a-f]{64}',lease['leaseTokenSha256']), '偏置独占租约身份缺失')
        need(isinstance(flux.get('completionSha256'),str) and re.fullmatch('[0-9a-f]{64}',flux['completionSha256']),'缺少原生完成回执摘要')
    need(result.get('candidateParameters')=={},'偏置作用域频率不能提交为普通工作点参数')
    context=flux.get('biasContext',{})
    need(context.get('sha256')==digest({k:v for k,v in context.items() if k!='sha256'})
        and context.get('heldAfterOperation') is False and context.get('requiresReapplicationAndMeasuredReadback') is (not commanded)
        and context.get('routeSha256')==flux['routeSha256'] and context.get('resourceId')==route['resourceId']
        and context.get('operationId')==result.get('sourceRunId'),'偏置作用域或恢复后状态声明不完整')
    if commanded:need(context.get('requiresAuthorizedReapplication') is True and context.get('biasCoordinate')=='commanded_setpoint'
        and context.get('measuredOutputV') is None,'设定电压不可声明已独立测量或保持在器件上')
    initial=flux.get('initial',{});setting=initial.get('setting',{}).get('valueV');need(number(setting),'初始偏置设置缺失')
    if commanded:need(setting==route['startSettingV'],'首次命令没有使用声明的起始设定')
    receipt(initial,route,setting=setting,acquired_at=result.get('acquiredAt'),identity=identity,authorization=authorization)
    restoration=flux.get('restoration',{})
    need((restoration.get('commandEndVerified') is True and restoration.get('verified') is False
          and restoration.get('initialPhysicalStateRestored') is False and restoration.get('endPolicy')=='return_declared_setting' if commanded else restoration.get('verified') is True)
        and flux.get('rfShutdown',{}).get('verified') is True
        and flux.get('transportClose',{}).get('shutdown_ok') is True and flux.get('providerClosed') is True,'偏置恢复或设备关闭未独立确认')
    # Restoration occurs after acquisition; do not substitute its measured
    # output for the original hardware register command.
    if commanded:receipt(restoration,route,setting=route['endSettingV'],identity=identity,authorization=authorization)
    else:receipt(restoration,route,setting=setting,measured=initial['measurement']['valueV'],initial=initial)
    if commanded and authorization['stage']=='selection':
        continuation=flux.get('commandContinuation',{})
        need(continuation.get('schema')=='qcal-command-continuation/1' and continuation.get('state')=='held_for_verification'
            and continuation.get('operationId')==result['sourceRunId'] and continuation.get('lease')==lease
            and continuation.get('routeSha256')==digest(route) and continuation.get('endSettingV')==route['endSettingV']
            and continuation.get('endCommandSha256')==restoration['command']['sha256'] and continuation.get('endReceiptSha256')==digest(restoration)
            and continuation.get('sha256')==digest({k:v for k,v in continuation.items() if k!='sha256'}),'首测结束设置与连续独占锁回执不一致')
    campaign=build_campaign(procedure,result.get('parameters',{}),route,point=flux.get('sourcePoint'))
    points=flux.get('points');need(isinstance(points,list) and len(points)==len(campaign['cuts']),'采集截线数量与冻结计划不符')
    raw_hashes={a.get('sha256') for a in result.get('artifacts',[]) if a.get('kind')=='raw'};output={}
    for point,cut in zip(points,campaign['cuts']):
        need(point.get('cutSha256')==digest({k:v for k,v in point.items() if k!='cutSha256'}),'偏置截线摘要改变')
        need(all(point.get(k)==cut.get(k) for k in ('pointId','cutId','phase','frequenciesHz')) and point.get('rowIds')==cut['rows'],'偏置截线与实际计划身份不同')
        target=setting if cut['requestedSettingV'] is None else cut['requestedSettingV']
        need(point.get('requestedSettingV')==target and point.get('rawSha256') in raw_hashes,'偏置设置或原始截线产物缺失')
        receipt(point.get('biasReceipt'),route,setting=target,initial=initial,acquired_at=result.get('acquiredAt'),identity=identity,authorization=authorization)
        if point['analysis'].get('accepted') is True:
            center,tolerance=(spectrum if point['phase']=='f01' else cavity)(point,campaign['parameters']['capture_repeats'])
            if point['phase']!='baseline':output[(point['phase'],point['pointId'])]=(point,center,tolerance)
    need(bool(output),'没有可用的实测偏置共振点')
    if procedure=='sq_flux_point_recheck':
        need(len(output)==len(points),'点位所需的腔核查或比特谱未通过')
        local=next(p for p in points if p['phase']=='local_cavity')
        for point in points:
            if point['phase']=='f01':need(point['readoutFrequencyHz']==local['readoutFrequencyHz'],'比特谱没有使用本次同偏置腔频')
    return flux,route,output


def check(result,procedure):
    try:qualified(result,procedure);return []
    except (ValueError,TypeError,KeyError,IndexError,ZeroDivisionError,AttributeError) as exc:return ['偏置采集证据未通过独立检查：'+str(exc)]


def check_independent(selection,verification,procedure):
    try:
        a,route,left=qualified(selection,procedure);b,other,right=qualified(verification,procedure)
        need(route==other and selection.get('sourceRunId')!=verification.get('sourceRunId'),'首测与复测偏置线路或操作身份不一致')
        if route.get('schema')=='qcal-bias-command-route/1':
            auth=b.get('commandAuthorization',{});entry=auth.get('entryEvidence',{});continuation=a.get('commandContinuation',{})
            need(a['commandAuthorization']['stage']=='selection' and auth.get('stage')=='verification'
                and b['commandLease']=={**a['commandLease'],'stage':'verification'}
                and entry.get('operationId')==selection['sourceRunId'] and entry.get('completionSha256')==a.get('completionSha256')
                and entry.get('commandSha256')==a['restoration']['command']['sha256'] and entry.get('receiptSha256')==digest(a['restoration'])
                and entry.get('leaseTokenSha256')==a['commandLease']['leaseTokenSha256'] and entry.get('lockTokenSha256')==continuation.get('lockTokenSha256'),
                '独立复测未接续本次首测的已知结束设置和连续独占锁')
        common=set(left)&set(right);need(bool(common),'首测与独立复测没有共同合格的偏置点')
        for key in common:
            p,x,tol=left[key];q,y,other_tol=right[key]
            need(p['frequenciesHz']==q['frequenciesHz'] and p['requestedSettingV']==q['requestedSettingV']
                and p['rawSha256']!=q['rawSha256'],'独立复测未重复同一偏置点与扫描轴')
            need(abs(x-y)<=min(tol,other_tol),'首测与独立复测候选频率不一致')
            if route.get('schema')!='qcal-bias-command-route/1':
                m,n=p['biasReceipt']['measurement'],q['biasReceipt']['measurement']
                need(abs(m['valueV']-n['valueV'])+m['uncertaintyV']+n['uncertaintyV']<=route['toleranceV'],'首测与复测实测偏置不在同一容差内')
        if procedure=='sq_flux_point_recheck':need(set(left)==set(right) and a.get('sourcePoint')==b.get('sourcePoint'),'独立点位复核范围改变')
        return []
    except (ValueError,TypeError,KeyError,IndexError,ZeroDivisionError,AttributeError) as exc:return ['同偏置独立复测未通过：'+str(exc)]
