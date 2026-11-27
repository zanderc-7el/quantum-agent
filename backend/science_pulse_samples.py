"""Read registered native waveform bytes. No compilation, writes or device I/O."""
import ast
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import re
import struct
import zipfile
from .common import APIError,checked_path,number
from .science_pulse_index import index_file,read_row,MAX_MANIFEST_BYTES

MAX_FILE_BYTES=64*1024*1024
MAX_ARRAY_SAMPLES=1_000_000
MAX_DISPLAY_SAMPLES=2048


def require(ok,message):
    if not ok:raise APIError('SCIENCE_PULSE_EVIDENCE',message,409)


def _json(raw):
    def unique(items):
        value={}
        for key,item in items:
            require(key not in value,'归档 JSON 包含重复字段');value[key]=item
        return value
    return json.loads(raw.decode('utf8'),object_pairs_hook=unique,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite JSON')))


def _array(archive,name,expected):
    require(isinstance(name,str) and re.fullmatch(r'[A-Za-z0-9_]+',name),'归档数组名称无效')
    info=archive.getinfo(name+'.npy')
    require(info.file_size<=8*MAX_ARRAY_SAMPLES+4096,'归档数组超过读取上限')
    data=archive.read(info)
    require(data[:6]==b'\x93NUMPY' and data[6:8] in (b'\x01\x00',b'\x02\x00'),'归档数组不是受支持的 NPY')
    start=10 if data[6]==1 else 12;size=int.from_bytes(data[8:start],'little')
    require(size<=4096,'归档数组头超过上限')
    header=ast.literal_eval(data[start:start+size].decode('latin1'))
    require(isinstance(header,dict) and header.get('fortran_order') is False and header.get('descr') in ('<f4','<f8'),
            '归档数组类型不受支持')
    shape=header.get('shape')
    require(isinstance(shape,tuple) and len(shape)==1 and type(shape[0]) is int and 0<shape[0]<=MAX_ARRAY_SAMPLES,
            '归档数组形状无效')
    fmt,width=('f',4) if header['descr']=='<f4' else ('d',8);payload=data[start+size:]
    require(len(payload)==shape[0]*width and expected.get('dtype')==header['descr'],'归档数组长度或类型不一致')
    require(hashlib.sha256(payload).hexdigest()==expected.get('sha256'),'归档数组内容摘要不一致')
    require(expected.get('samples',expected.get('shape',[None])[0])==shape[0],'归档数组样点数量不一致')
    values=[v[0] for v in struct.iter_unpack('<'+fmt,payload)]
    require(all(math.isfinite(v) for v in values),'归档数组包含非有限样点')
    return values


def _sample_indices(count):
    return list(range(count)) if count<=MAX_DISPLAY_SAMPLES else sorted({round(i*(count-1)/(MAX_DISPLAY_SAMPLES-1)) for i in range(MAX_DISPLAY_SAMPLES)})


def _source_binding(service,rt,record):
    """DB ownership/currentness only; selected files are checked by load().

    Viewing a segment is not a full acquisition-integrity or calibration check.
    Keep ScienceService._reference and execution-source authentication unchanged.
    """
    from .science_dependencies import quality_policy_current
    reference=record['reference'];scope,wf=service._scope(rt,record['scope'],reference)
    row=rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=?',
        (reference['attemptId'],reference['workflowId'])).fetchone()
    require(row is not None,'采集尝试不属于所选任务')
    attempt=json.loads(row[0]);raw=attempt.get('result') or {}
    node=next((n for n in wf['nodes'] if n['id']==attempt.get('nodeId')),None)
    require(node and node['resourceId']==scope.get('resourceId') and attempt.get('procedureId')==node.get('procedureId'),
            '采集节点、资源或方法归属不一致')
    require(wf.get('mode')=='real' and raw.get('dataKind')=='real' and raw.get('acquiredAt'),'来源不是已保存的真实采集回执')
    require(record['result'].get('parameters')==raw.get('parameters',attempt.get('parameters',{}))
        and record['result'].get('procedureId')==attempt.get('procedureId'),'该记录与采集参数或方法不一致')
    evidence=[];seen={}
    sources=[raw]+([raw['selectionResult']] if isinstance(raw.get('selectionResult'),dict) else [])
    for result in sources:
        for item in result.get('artifacts',[]):
            if item.get('kind')!='raw':continue
            if item['id'] in seen:
                require(seen[item['id']]==item.get('sha256'),'采集重复产物摘要冲突');continue
            row=rt.db.execute('SELECT sha256 FROM platform_artifacts WHERE id=?',(item['id'],)).fetchone()
            require(row and row['sha256']==item.get('sha256'),'采集产物登记不一致')
            seen[item['id']]=item['sha256'];evidence.append({'id':item['id'],'sha256':item['sha256'],'kind':'raw'})
    current=bool(node.get('latestAttemptId')==attempt['id'] and not raw.get('stale') and not (node.get('result') or {}).get('stale')
        and node.get('engineState')!='stale' and quality_policy_current(node['procedureId'],raw.get('quality',{}),
            wf.get('gates',{}),wf.get('gatesVersion'),independent=bool(attempt.get('verificationOperationId'))))
    return {'workflow':wf,'attempt':attempt,'raw':raw,'evidence':evidence,'current':current}


def read(service,rt,record,query):
    """An archived record/attempt owns the only permitted artifact inventory."""
    try:return _read(service,rt,record,query or {})
    except APIError:raise
    except (ValueError,TypeError,KeyError,IndexError,OSError,EOFError,SyntaxError,struct.error,zipfile.BadZipFile) as exc:
        raise APIError('SCIENCE_PULSE_EVIDENCE','脉冲归档不完整或格式无效',409) from exc


def _read(service,rt,record,query):
    require(set(query)<= {'stage','rowId','segmentId'},'脉冲样点查询包含未知范围字段')
    def q(key,default=None):
        value=query.get(key,default)
        if isinstance(value,list):require(len(value)==1,'脉冲样点查询字段重复');value=value[0]
        return value
    require(record.get('kind')=='measurement' and record.get('source')=='acquisition' and record.get('reference'),
            '仅已归档的采集记录可读取执行脉冲')
    source=_source_binding(service,rt,record)
    require(record.get('evidence')==source['evidence'] and record['result'].get('acquiredAt')==source['raw'].get('acquiredAt'),
            '该记录与当前尝试回执不一致，请重新关联对应采集')
    attempt=source['attempt'];main=source['raw'];selection=main.get('selectionResult')
    has_verify=isinstance(selection,dict) and bool(attempt.get('verificationOperationId')) and not main.get('verificationPending')
    stage=q('stage','verification' if has_verify else 'selection')
    require(stage in ('selection','verification') and (stage!='verification' or has_verify),'所选采集阶段不存在')
    raw=main if stage=='verification' or not has_verify else selection
    operation=attempt['verificationOperationId'] if stage=='verification' else attempt['operationId']
    inventory=raw.get('artifacts',[]);by_name={}
    for item in inventory:
        name=item.get('name')
        if name in ('compiled_plan.json','axes.json','uploads.jsonl'):
            require(name not in by_name,'同一采集存在重复脉冲归档');by_name[name]=item
    artifacts=[];cache={}
    def locate(item,limit=MAX_FILE_BYTES):
        registered=rt.db.execute('SELECT path,sha256 FROM platform_artifacts WHERE id=?',(item['id'],)).fetchone()
        require(registered and registered['sha256']==item.get('sha256'),'脉冲文件未登记或摘要不一致')
        path=checked_path(registered['path']);root=service.c.root.resolve()
        require(path.is_relative_to(root) or str(path).replace('\\','/').lower().startswith('d:/总体-8号机测试/'),
                '脉冲文件不属于允许的实验归档')
        require(path.stat().st_size<=limit,'脉冲文件超过读取容量上限')
        return path
    def described(item):
        artifacts.append({k:item[k] for k in ('id','name','sha256')});artifacts[-1]['url']='/api/artifacts/'+item['id']
    def load(item):
        if item['id'] in cache:return cache[item['id']]
        path=locate(item)
        with path.open('rb') as stream:data=stream.read(min(path.stat().st_size+1,MAX_FILE_BYTES+1))
        require(len(data)<=MAX_FILE_BYTES,'脉冲文件超过读取容量上限')
        require(hashlib.sha256(data).hexdigest()==item['sha256'],'脉冲文件内容已改变')
        described(item)
        cache[item['id']]=(path,data);return path,data
    out={'schema':'qcal-archived-pulse-samples/1','recordId':record['id'],'workflowId':source['workflow']['id'],
        'attemptId':attempt['id'],'operationId':operation,'stage':stage,
        'stages':[{'id':'selection','label':'首测'}]+([{'id':'verification','label':'独立复测'}] if has_verify else []),
        'currentEvidence':source['current'],'verifiedEvidenceScope':'selected_stage_pulse_artifacts',
        'rows':[],'segments':[],'selected':None,'segment':None,'samples':None,
        'upload':{'status':'not_recorded','receipts':[],'deviceWaveformReadback':False},'artifacts':artifacts,
        'status':'unavailable','reason':'该归档未保存载频前 I/Q 样点。'}
    if 'compiled_plan.json' not in by_name:return out
    require(raw.get('sourceRunId',raw.get('provenance',{}).get('operationId'))==operation,'采集阶段与执行回执不一致')
    if raw.get('provenance',{}).get('operationId') is not None:
        require(raw['provenance']['operationId']==operation,'脉冲执行标识不一致')
    compiled_item=by_name['compiled_plan.json'];compiled_path=locate(compiled_item,MAX_MANIFEST_BYTES)
    index=index_file(compiled_path,compiled_item['sha256'],'compiled');described(compiled_item)
    folder=compiled_path.parent;indexed=index['rows'];require(indexed,'脉冲行清单为空')
    legacy=[bool(item.get('legacyMetadataOnly')) for item in indexed.values()]
    if any(legacy):
        require(all(legacy) and attempt.get('procedureId') not in ('sq_drag_half_scan','sq_drag_irb'),
                '该采集的脉冲清单与方法或归档格式不一致')
        out['reason']='旧版清单只保存波形摘要和时序，未绑定可展示的驱动 I/Q 样点。原始 ADC I/Q 不属于驱动波形。'
        return out
    axis_by_id={}
    if 'axes.json' in by_name:
        item=by_name['axes.json'];axes_path=locate(item,MAX_MANIFEST_BYTES)
        require(axes_path.parent==folder,'扫描轴与脉冲归档目录不同')
        axis_by_id=index_file(axes_path,item['sha256'],'axes')['rows'];described(item)
        require(set(axis_by_id)<=set(indexed),'扫描轴与脉冲行编号不一致')
    for rid in indexed:
        out['rows'].append({'rowId':rid,**{k:v for k,v in axis_by_id.get(rid,{}).items() if k in ('condition','stage','capture','axisValue','logicalState')}})
    row_arg=q('rowId');require(row_arg is None or re.fullmatch(r'0|[1-9]\d{0,5}',str(row_arg)),'脉冲行编号无效')
    rid=min(indexed) if row_arg is None else int(row_arg);row=read_row(compiled_path,index,rid)
    segments=row.get('segments',[]);require(isinstance(segments,list) and len(segments)<=8192,'脉冲分段清单无效')
    out['segments']=[deepcopy(s) for s in segments if s.get('channel')=='xy']
    require(len({s['id'] for s in out['segments']})==len(out['segments']),'脉冲分段编号重复')
    sid=q('segmentId',out['segments'][0]['id'] if out['segments'] else None)
    if sid is None:return out
    chosen=next((s for s in out['segments'] if s['id']==sid),None);require(chosen is not None,'所选脉冲分段不存在')
    out.update(selected={'rowId':rid,'segmentId':sid},segment=deepcopy(chosen))
    candidates=[item for item in inventory if item.get('name')==Path(row['file']).name and item.get('sha256')==row.get('sha256')]
    require(len(candidates)==1,'脉冲行文件未唯一绑定到该次采集')
    path,npz=load(candidates[0]);require(path.parent==folder and path==checked_path(row['file']),'脉冲行文件超出本次归档目录')
    waves=row['waves'];xy=[w for name,w in waves.items() if name.startswith('xy_')]
    require(len(xy)==1,'脉冲行必须对应唯一 XY 输出');wave=xy[0]
    if 'uploads.jsonl' in by_name:
        upload_path,data=load(by_name['uploads.jsonl']);require(upload_path.parent==folder,'上传回执目录不一致')
        require(len(data)<=8*1024*1024,'上传回执超过读取上限')
        lines=data.splitlines();require(len(lines)<=16384,'上传回执行数超过上限')
        receipts=[_json(line) for line in lines if line.strip()]
        matches=[v for v in receipts if v.get('rowId')==rid and v.get('name')==wave['name']]
        if matches:
            require(all(all(v.get(k)==value for k,value in wave.items()) and type(v.get('rowId')) is int
                and v.get('channel')=='XY' and v.get('uploadCommandsCompleted') is True
                and v.get('deviceWaveformReadback') is False for v in matches),
                    '上传回执与归档输出样点不一致')
            out['upload'].update(status='matched',receipts=matches)
    if not chosen.get('basebandI') or not chosen.get('basebandQ'):return out
    require(wave.get('fill') is True,'未填充波形不支持按此分段位置读取')
    with zipfile.ZipFile(io.BytesIO(npz)) as z:
        names=z.namelist();require(len(names)<=20000 and len(names)==len(set(names)) and all('/' not in n and '\\' not in n for n in names),
                '脉冲压缩归档包含无效数组项')
        i=_array(z,chosen['basebandI']['array'],chosen['basebandI']);qq=_array(z,chosen['basebandQ']['array'],chosen['basebandQ'])
        rf=_array(z,wave['name'],wave)
    start,end=chosen['startSample'],chosen['endSample'];rate=chosen['sampleRateHz']
    require(type(start) is int and type(end) is int and 0<=start<end<=len(rf) and end-start==len(i)==len(qq)==chosen['sampleCount'],
            '分段样点与实际 RF 位置不一致')
    require(number(rate) and rate>0 and rate==wave['sampleRateHz'],'分段与 RF 采样率不一致')
    require(max(math.hypot(a,b) for a,b in zip(i,qq))<=1+1e-12 and max(abs(v) for v in rf)<=.990001,'归档脉冲超过受支持的幅度上限')
    selected_indices=_sample_indices(len(i));rf=rf[start:end]
    out.update(status='available',reason=None,samples={'timeNs':[n/rate*1e9 for n in selected_indices],
        'i':[i[n] for n in selected_indices],'q':[qq[n] for n in selected_indices],
        'rfTimeNs':[n/rate*1e9 for n in selected_indices],'rf':[rf[n] for n in selected_indices],
        'timeOrigin':'relative_to_selected_segment_start','basebandSampleCount':len(i),'rfSampleCount':len(rf),
        'decimation':'exact-index-subsampling','maxDisplaySamples':MAX_DISPLAY_SAMPLES})
    return out
