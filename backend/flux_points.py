"""Server-only working-point projection from two immutable flux operations.

This module never writes a device or asserts a held bias. Native execution must
authenticate the resulting proof again before applying a point.
"""
from copy import deepcopy
import json
import re
from .common import APIError,digest,number


def require(ok,message):
    if not ok:raise APIError('FLUX_POINT_EVIDENCE',message,409)


def project_point(record,originals,operations,inputs,*,working_point_id=None):
    require(record.get('kind')=='measurement' and record.get('result',{}).get('procedureId')=='ro_flux_scan',
            '点位必须来自原生偏置扫描采集，模型二维图不能用于施加偏置')
    index=inputs.get('selectedBiasIndex')
    require(type(index) is int and 0<=index<21,'请选择扫描中实际采集的偏置点')
    require(inputs.get('sourceRecordId')==record.get('id') and inputs.get('purpose','work') in ('work','idle'),
            '点位来源或用途不一致')
    require(inputs.get('biasUnit','V')=='V','原生偏置设定单位必须为 V')
    point_id='bias-'+str(index);points=[];origins=[];routes=[];modes=[]
    for stage in ('selection','verification'):
        original=originals.get(stage,{})
        source=original.get('provenance',{}).get('scienceFlux',{})
        matches=[p for p in source.get('points',[]) if p.get('pointId')==point_id and p.get('phase')=='cavity']
        require(len(matches)==1,'首测与独立复测均须包含同一个有效偏置截线')
        point=matches[0];operation=[op for op in operations if op.get('stage')==stage]
        require(len(operation)==1 and operation[0].get('operationId'),'偏置来源操作身份缺失')
        require(point.get('cutSha256')==digest({k:v for k,v in point.items() if k!='cutSha256'}),'原始偏置截线摘要已改变')
        require(point.get('analysis',{}).get('accepted') is True,'所选偏置截线没有可重复且明确的腔共振')
        require(isinstance(point.get('rawSha256'),str) and re.fullmatch('[0-9a-f]{64}',point['rawSha256']),'偏置截线缺少原始数据摘要')
        receipt=point.get('biasReceipt',{});setting=receipt.get('setting',{});measured=receipt.get('measurement') or {}
        commanded=source.get('route',{}).get('schema')=='qcal-bias-command-route/1';modes.append(commanded)
        if commanded:
            from adapters.science_flux.contracts import policy_for,command_authorization
            from adapters.science_flux.command_contracts import validate_command_receipt,validate_authorization
            try:
                route=source['route'];require(source.get('routeSha256')==digest(route) and source.get('policy')==policy_for(route),'点位线路或策略不一致')
                authorization=source.get('commandAuthorization')
                validate_authorization(authorization,route,{'operationId':operation[0]['operationId'],'resourceId':record['scope']['resourceId']})
                require(authorization.get('stage')==stage,'点位首测与复测命令阶段不一致')
                validate_command_receipt(receipt,route,setting=point.get('requestedSettingV'),
                    identity={'operationId':operation[0]['operationId'],'resourceId':record['scope']['resourceId']},authorization_value=source['commandAuthorization'])
            except (ValueError,TypeError,KeyError) as exc:raise APIError('FLUX_POINT_EVIDENCE','点位设定命令证据无效：'+str(exc),409) from None
        else:
            require(setting.get('kind')=='hardware_setting_register' and measured.get('kind')=='measured_output',
                '点位必须同时包含硬件设置寄存器和真实测量回读，缓存不可替代')
            require(all(number(v) for v in (point.get('requestedSettingV'),setting.get('valueV'),measured.get('valueV'),measured.get('uncertaintyV')))
                and measured['uncertaintyV']>=0,'点位电压或测量不确定度无效')
        xs,ys=point.get('frequenciesHz'),point.get('responseMagnitude')
        require(isinstance(xs,list) and isinstance(ys,list) and 5<=len(xs)<=101 and len(xs)==len(ys)
                and all(number(v) for v in xs+ys) and all(a<b for a,b in zip(xs,xs[1:])), '点位缺少完整原始频率截线')
        require(number(point.get('readoutFrequencyHz')) and xs[0]<point['readoutFrequencyHz']<xs[-1],'读取腔候选必须位于原始扫描范围内部')
        require(isinstance(source.get('routeSha256'),str) and re.fullmatch('[0-9a-f]{64}',source['routeSha256']),'点位偏置线路摘要无效')
        routes.append(source['routeSha256']);points.append(point)
        origins.append({'stage':stage,'operationId':operation[0]['operationId'],'cutSha256':point['cutSha256'],
                        'pointReceiptSha256':digest(receipt),'rawSha256':point['rawSha256']})
    first,last=points
    require(modes[0]==modes[1] and routes[0]==routes[1] and first['requestedSettingV']==last['requestedSettingV']
            and first['frequenciesHz']==last['frequenciesHz'],'首测与复测未使用同一偏置设置和扫描轴')
    require(origins[0]['operationId']!=origins[1]['operationId'] and origins[0]['rawSha256']!=origins[1]['rawSha256'],
            '点位必须有不同操作与独立采集原始数据')
    if 'bias' in inputs:require(number(inputs['bias']) and inputs['bias']==last['requestedSettingV'],'点位偏置必须来自所选原始截线')
    if 'readoutFrequencyHz' in inputs:require(inputs['readoutFrequencyHz']==last['readoutFrequencyHz'],'执行点位腔频必须来自独立复测候选')
    value={'schema':'qcal-flux-point-evidence/2' if modes[-1] else 'qcal-flux-point-evidence/1','workingPointId':working_point_id,'recordId':record['id'],
        'pointId':point_id,'resourceId':record.get('scope',{}).get('resourceId'),'purpose':inputs.get('purpose','work'),
        'requestedSettingV':last['requestedSettingV'],'measuredOutputV':None if modes[-1] else last['biasReceipt']['measurement']['valueV'],
        'measurementUncertaintyV':None if modes[-1] else last['biasReceipt']['measurement']['uncertaintyV'],'routeSha256':routes[-1],
        'usableReadoutBandHz':[last['frequenciesHz'][0],last['frequenciesHz'][-1]],
        'originalCut':{'frequenciesHz':deepcopy(last['frequenciesHz']),'responseMagnitude':deepcopy(last['responseMagnitude']),
                       'readoutFrequencyHz':last['readoutFrequencyHz']},'sourceOperations':origins}
    value['sha256']=digest(value);return value


def source_point(controller,rt,record,inputs,*,working_point_id=None):
    from .science_dependencies import ScienceDependencyResolver
    resolver=ScienceDependencyResolver(controller)
    proof=resolver.resolve(rt,kind='measurement',scope=record['scope'],refs=[{'role':'flux_scan','recordId':record['id']}],inputs={})
    state=proof['assessment']['nodes'].get(record['id'],{})
    require(state.get('current') is True and state.get('controllerVerified') is True,'偏置点来源必须为当前已独立验收的采集')
    node=next(n for n in proof['nodes'] if n['recordId']==record['id']);originals={}
    for op in node['operations']:
        row=rt.db.execute('SELECT result FROM platform_effects WHERE operation_id=? AND state=?',(op['operationId'],'completed')).fetchone()
        require(row is not None,'点位原始操作回执不存在');originals[op['stage']]=json.loads(row[0])
    return project_point(record,originals,node['operations'],inputs,working_point_id=working_point_id)


def resolve_point(controller,rt,working_point_id,*,resource_id,scope=None):
    from .science_dependencies import ScienceDependencyResolver
    resolver=ScienceDependencyResolver(controller);cache={}
    point,_=resolver._record(rt,working_point_id,cache)
    require(point.get('kind')=='working_point','请选择服务器保存的工作点或闲置点')
    source_id=point.get('sourceRecordId')
    require(source_id and source_id==point.get('input',{}).get('sourceRecordId'),'点位来源编号不一致')
    source,_=resolver._record(rt,source_id,cache)
    require(source.get('scope',{}).get('resourceId')==resource_id,'点位属于不同资源')
    resolver._compatible(rt,point['scope'],source['scope'])
    if scope is not None:resolver._compatible(rt,source['scope'],scope)
    value=source_point(controller,rt,source,point['input'],working_point_id=working_point_id)
    frozen=point.get('result',{}).get('fluxPointEvidence')
    require(isinstance(frozen,dict),'历史手工点位没有原生偏置采集证据，请从实测扫描重新保存')
    check=deepcopy(value);check['workingPointId']=None;check.pop('sha256');check['sha256']=digest(check)
    require(check==frozen,'保存点位的原始截线或回读身份已改变')
    route=controller.profile.get('biasRoutes',{}).get(resource_id)
    require(route and digest(route)==value['routeSha256'],'执行配置的偏置线路与点位来源不同')
    return value
