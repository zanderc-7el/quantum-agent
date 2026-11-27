"""Bounded external channel evidence. No device dispatch or calibration promotion."""
from copy import deepcopy
from datetime import datetime
import base64
import hashlib
import json
import math
import re

from .common import APIError, digest

SCHEMA = 'qcal-external-channel-comparison/1'
VERSION = 'qcal-channel-observation/1'
KIND = 'predistortion_comparison'
MAX_FILE_BYTES = 512 * 1024
MAX_POINTS = 8192
PLACEHOLDERS = {'unknown','none','null','n/a','tbd','待填写','未知','待核实','无'}
UNITS = {'voltage':{'V','mV','uV'}, 'current':{'A','mA','uA'},
         'iq_projection':{'V','mV','ADC','IQ a.u.'}, 'measured_signal':{'ADC','a.u.'}}


def invalid(message, code='SCIENCE_CHANNEL_INVALID'):
    raise APIError(code, message)


def fields(value, expected, label):
    if not isinstance(value,dict) or set(value)!=set(expected):
        invalid(label+'字段缺失或包含未知声明')


def text(value, label, limit=512):
    if (not isinstance(value,str) or not 1<=len(value.strip())<=limit
            or value.strip().lower() in PLACEHOLDERS or any(ord(c)<32 for c in value)):
        invalid('请填写'+label+'，内容须明确且非空')
    return value


def finite(value):
    try:return not isinstance(value,bool) and isinstance(value,(float,int)) and math.isfinite(value) and abs(value)<=1e100
    except (OverflowError,ValueError):return False


def axis(value, label, *, increasing=False, nonnegative=False, count=None):
    if (not isinstance(value,list) or not 2<=len(value)<=MAX_POINTS or count is not None and len(value)!=count
            or any(not finite(x) for x in value)):
        invalid(label+'须为有界、等长的有限数值数组')
    if increasing and any(b<=a for a,b in zip(value,value[1:])):invalid(label+'须严格递增')
    if nonnegative and value[0]<0:invalid(label+'不能为负')
    return value


def timestamp(value):
    try:
        if not isinstance(value,str) or len(value)>64:raise ValueError()
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError()
        return parsed
    except (ValueError,TypeError):invalid('采集和标定有效时间须为带时区 ISO 日期')


def context(value):
    fields(value,('channelId','measurementPoint','quantity','unit','conditions'),'通道测点条件')
    for key,label in (('channelId','通道名称'),('measurementPoint','物理测点')):text(value[key],label)
    quantity=value['quantity'];unit=value['unit']
    if not isinstance(quantity,str) or quantity not in UNITS or not isinstance(unit,str) or unit not in UNITS[quantity]:
        invalid('观测量与单位必须明确且匹配')
    settings=value['conditions']
    if not isinstance(settings,dict) or not 1<=len(settings)<=64:invalid('须记录非空的同条件仪器/负载设置')
    for key,item in settings.items():
        text(key,'条件名称',128)
        if isinstance(item,str):text(item,'条件值')
        elif not isinstance(item,bool) and not finite(item):invalid('条件只接受有限标量、布尔值或明确文本')
    return value


def validate_scope(scope):
    if not isinstance(scope,dict) or not scope.get('resourceId') or not (
            scope.get('workflowId') or scope.get('chipVersionId') and scope.get('deploymentVersionId')):
        invalid('请选择任务中的比特，或已发布的芯片与接线版本','SCIENCE_SCOPE_MISMATCH')


def same_context(left,right):
    context(right)
    # JSON booleans are switches, not numeric gains. Python otherwise equates
    # True with 1. Numeric 1 and 1.0 retain their ordinary physical equivalence.
    return left==right and all(isinstance(left['conditions'][key],bool)==isinstance(value,bool)
                              for key,value in right['conditions'].items())


def template(scope):
    validate_scope(scope)
    ctx={'channelId':'','measurementPoint':'','quantity':'voltage','unit':'V','conditions':{}}
    source={'document':'','provider':'','sha256':'','recordPath':''}
    common={'dataOrigin':'external_measurement','context':deepcopy(ctx),'acquisitionId':'','acquiredAt':None,
            'source':source,'calibrationId':''}
    calibration={**deepcopy(common),'version':'','validFrom':None,'validUntil':None,
                 'frequencyHz':[],'real':[],'imag':[],'unit':'ratio'}
    trace={**deepcopy(common),'calibrationVersion':'','windowS':[None,None],'timeS':[],'values':[]}
    return {'schema':SCHEMA,'dataOrigin':'external_measurement','scope':deepcopy(scope),'context':ctx,
            'target':{'kind':'specified_target','context':deepcopy(ctx),'windowS':[None,None],'timeS':[],'values':[]},
            'calibration':calibration,'baseline':deepcopy(trace),'compensated':deepcopy(trace)}


def _document(inputs):
    if not isinstance(inputs,dict) or set(inputs)-{'fileName','documentText','documentBase64','sourceRecordId'}:
        invalid('导入只接受文件名、原始 JSON 与可选来源记录编号')
    text(inputs.get('fileName'),'文件名',256)
    if 'sourceRecordId' in inputs:text(inputs['sourceRecordId'],'来源记录编号',256)
    if ('documentText' in inputs)==('documentBase64' in inputs):invalid('请选择一种原始 JSON 编码')
    try:
        if 'documentBase64' in inputs:
            encoded=inputs['documentBase64']
            if not isinstance(encoded,str) or len(encoded)>4*((MAX_FILE_BYTES+2)//3):raise ValueError()
            original=base64.b64decode(encoded,validate=True)
        else:
            value=inputs['documentText']
            if not isinstance(value,str) or len(value)>MAX_FILE_BYTES:raise ValueError()
            original=value.encode('utf8')
        if not 1<=len(original)<=MAX_FILE_BYTES:raise ValueError()
        decoded=original.decode('utf-8-sig')
        def pairs(items):
            value={}
            for key,item in items:
                if key in value:raise ValueError('duplicate key')
                value[key]=item
            return value
        def bad_constant(value):raise ValueError(value)
        document=json.loads(decoded,object_pairs_hook=pairs,parse_constant=bad_constant)
    except (ValueError,TypeError,UnicodeError,RecursionError,OverflowError):
        invalid('文件须为不超过 512 KiB、无重复键或非有限值的 UTF-8 JSON')
    return document,hashlib.sha256(original).hexdigest()


def _acquisition(value, ctx, origin):
    if value['dataOrigin']!=origin:invalid('模型、测试与外部采集不可混为同一组来源')
    if not same_context(ctx,value['context']):invalid('通道、测点、物理量、单位或仪器条件不同','SCIENCE_CHANNEL_CONTEXT_MISMATCH')
    text(value['acquisitionId'],'独立采集编号')
    acquired=timestamp(value['acquiredAt'])
    source=value['source'];fields(source,('document','provider','sha256','recordPath'),'采集来源')
    for key,label in (('document','来源文件名称'),('provider','数据提供方'),('recordPath','文件内采集记录路径')):
        text(source[key],label,2000)
    if not isinstance(source['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',source['sha256']):invalid('来源文件内容摘要（SHA-256）须为 64 位小写十六进制字符')
    # No filesystem paths or commands are dereferenced. This is a record selector
    # within an external container; one HDF5/JSON file can hold many captures.
    if not source['recordPath'].startswith('/'):invalid('容器内记录路径须以 / 开始')
    return acquired


def _wave(value,ctx):
    if not same_context(ctx,value['context']):invalid('目标及波形测点、单位或条件不同','SCIENCE_CHANNEL_CONTEXT_MISMATCH')
    times=axis(value['timeS'],'时间轴',increasing=True)
    axis(value['values'],'时域样点',count=len(times))
    window=value['windowS']
    if (not isinstance(window,list) or len(window)!=2 or not all(finite(x) for x in window)
            or window!=[times[0],times[-1]]):invalid('采样窗口必须精确覆盖首末样点')


def import_comparison(inputs,scope):
    """Pure, deterministic validation and arithmetic; source claims stay claims."""
    validate_scope(scope);doc,sha=_document(inputs)
    fields(doc,('schema','dataOrigin','scope','context','target','calibration','baseline','compensated'),'导入包')
    if doc['schema']!=SCHEMA:invalid('不支持的通道比较文件版本')
    if doc['scope']!=scope:invalid('文件范围与所选任务、比特或配置版本不一致','SCIENCE_SCOPE_MISMATCH')
    origin=doc['dataOrigin']
    if origin not in ('external_measurement','test_fixture'):invalid('只支持外部测量声明或明确测试样例；模型不能充当实测')
    ctx=context(doc['context']);target=doc['target'];cal=doc['calibration']
    fields(target,('kind','context','windowS','timeS','values'),'目标')
    if target['kind']!='specified_target':invalid('目标须明确标为指定目标')
    _wave(target,ctx)
    common=('dataOrigin','context','calibrationId','acquisitionId','acquiredAt','source')
    fields(cal,common+('version','validFrom','validUntil','frequencyHz','real','imag','unit'),'复数校准')
    when={'calibration':_acquisition(cal,ctx,origin)}
    for key,label in (('calibrationId','校准编号'),('version','校准版本')):text(cal[key],label)
    frequencies=axis(cal['frequencyHz'],'校准频率 Hz',increasing=True,nonnegative=True)
    axis(cal['real'],'H(f) 实部',count=len(frequencies));axis(cal['imag'],'H(f) 虚部',count=len(frequencies))
    if cal['unit']!='ratio':invalid('H(f) 必须为同单位输入/输出的无量纲复数比值')
    start,end=timestamp(cal['validFrom']),timestamp(cal['validUntil'])
    errors={};metrics={'sampleCount':len(target['timeS'])};source_summary={}
    for role,label in (('baseline','未补偿基线'),('compensated','补偿后')):
        value=doc[role]
        fields(value,common+('calibrationVersion','windowS','timeS','values'),label+'时域采集')
        when[role]=_acquisition(value,ctx,origin);_wave(value,ctx)
        if value['timeS']!=target['timeS'] or value['windowS']!=target['windowS']:
            invalid('目标、基线与补偿后必须使用相同时间轴和窗口；不自动插值','SCIENCE_CHANNEL_AXIS_MISMATCH')
        if value['calibrationId']!=cal['calibrationId'] or value['calibrationVersion']!=cal['version']:
            invalid('基线/补偿后引用的校准编号或版本不一致')
        error=[a-b for a,b in zip(value['values'],target['values'])];errors[role]=error
        metrics[role+'Rmse']=math.hypot(*error)/math.sqrt(len(error))
        metrics[role+'MaxAbsError']=max(map(abs,error))
        metrics[role+'Overshoot']=max(0.,max(value['values'])-max(target['values']))
        metrics[role+'Undershoot']=max(0.,min(target['values'])-min(value['values']))
    if not all(start<=at<=end for at in when.values()):invalid('校准有效时间未覆盖校准、基线和补偿后采集')
    if when['compensated']<=when['baseline']:invalid('补偿后须为基线之后的独立采集')
    captures=[doc[role] for role in ('baseline','compensated')]
    if len({v['acquisitionId'] for v in captures})!=2 or len({(v['source']['sha256'],v['source']['recordPath']) for v in captures})!=2:
        invalid('不能把同一采集编号或同一容器记录用于自比较','SCIENCE_CHANNEL_NOT_INDEPENDENT')
    for role in ('calibration','baseline','compensated'):
        value=doc[role]
        source_summary[role]={'acquisitionId':value['acquisitionId'],'acquiredAt':value['acquiredAt'],
                              'source':deepcopy(value['source']),'contentSha256':digest(value)}
    metrics['rmseReduction']=metrics['baselineRmse']-metrics['compensatedRmse']
    metrics['rmseReductionPercent']=100*metrics['rmseReduction']/metrics['baselineRmse'] if metrics['baselineRmse'] else None
    percent=metrics['rmseReductionPercent']
    metrics['rmseReductionPercentUnavailableReason']='baseline_zero' if percent is None else None
    if percent is not None and not math.isfinite(percent):
        metrics['rmseReductionPercent']=None;metrics['rmseReductionPercentUnavailableReason']='numeric_range'
    labels={'target':'指定目标','baseline':'未补偿基线','compensated':'补偿后独立采集',
            'baseline_error':'基线−目标','compensated_error':'补偿后−目标'}
    curves=[]
    for role in labels:
        values=errors[role[:-6]] if role.endswith('_error') else doc[role]['values']
        curves.append({'id':role,'label':labels[role],'values':deepcopy(values),'quantity':ctx['quantity'],
                       'unit':ctx['unit'],'source':'target' if role=='target' else 'provided_data'})
    qualification='test_fixture_not_measurement' if origin=='test_fixture' else 'external_claim_not_platform_verified'
    return {'kind':KIND,'modelVersion':VERSION,'dataKind':'provided_data','dataOrigin':origin,
            'source':'external_channel_comparison','qualification':qualification,'dispatched':False,
            'acceptedCalibration':False,'physicalAccepted':False,'comparisonCompatible':True,
            'context':deepcopy(ctx),'windowS':deepcopy(target['windowS']),
            'axes':{'x':{'name':'Time','unit':'s','values':deepcopy(target['timeS'])}},'series':curves,
            'transfer':{key:deepcopy(cal[key]) for key in ('frequencyHz','real','imag','unit')} | {
                'magnitude':[math.hypot(r,i) for r,i in zip(cal['real'],cal['imag'])],
                'phaseRad':[math.atan2(i,r) if r or i else None for r,i in zip(cal['real'],cal['imag'])],
                'phaseDefined':[bool(r or i) for r,i in zip(cal['real'],cal['imag'])]},
            'metrics':metrics,'metricDefinitions':{'error':'response-target','rmse':'sqrt(mean(error^2))',
                'overshoot':'max(0,max(response)-max(target))','undershoot':'max(0,min(target)-min(response))',
                'rmseReduction':'baselineRmse-compensatedRmse; positive means lower observed error'},
            'calibrationId':cal['calibrationId'],'calibrationVersion':cal['version'],
            'sourceSummary':source_summary,'contextSha256':digest(ctx),'documentSha256':sha,
            'document':doc,'fileName':inputs['fileName'],
            'independence':'baseline/compensated have distinct declared acquisition IDs, times and container records; not platform-authenticated',
            'transferUsage':'provenance_only; no inverse filtering, waveform dispatch or spectral coverage claim',
            'meaning':'外部提供数据的同条件数值比较；来源声明及独立性未被平台实机认证，不构成校准验收或下发许可。'}
