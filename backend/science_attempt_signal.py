"""Read immutable workflow acquisitions without materializing a scientific record."""
import ast
from array import array
import hashlib
import json
import math
import re
import sys
import zipfile

from .common import APIError, checked_path, digest
from . import science_pulse_samples


def require(ok, reason):
    if not ok:
        raise APIError('ATTEMPT_SIGNAL_EVIDENCE', reason, 409)


def _field(query, key):
    value = query.get(key)
    if isinstance(value, list):
        require(len(value) == 1, '采集来源字段重复')
        value = value[0]
    require(isinstance(value, str) and 0 < len(value) <= 256, '采集来源字段不完整')
    return value


def _identity(rt, wf, node, attempt, raw, op):
    rows = rt.db.execute(
        'SELECT request,result FROM platform_effects WHERE workflow_id=? AND attempt_id=? AND operation_id=? AND state=?',
        (wf['id'], attempt['id'], op, 'completed')).fetchone()
    require(rows, '此阶段没有已完成的采集回执')
    request, result = json.loads(rows['request']), json.loads(rows['result'])
    expected = dict(runId=wf['id'], nodeId=node['id'], resourceId=node['resourceId'],
                    procedureId=node['procedureId'], attemptId=attempt['id'], operationId=op)
    require(all(request.get(k) == v for k, v in expected.items()), '回执的任务、节点或比特归属不一致')
    require(raw.get('sourceRunId') == op and raw.get('provenance', {}).get('operationId', op) == op,
            '回执的采集阶段不一致')
    require(all(result.get(k) == raw.get(k) for k in ('dataKind', 'acquiredAt', 'sourceRunId', 'parameters',
                'series', 'metrics', 'methodLabel', 'artifacts')), '曲线、参数或产物与此阶段的完成回执不一致')


def _registered_file(service, rt, item):
    row = rt.db.execute('SELECT path,sha256,payload FROM platform_artifacts WHERE id=?', (item['id'],)).fetchone()
    require(row and row['sha256'] == item['sha256'], '原始 ADC 文件登记或摘要不一致')
    metadata = json.loads(row['payload'])
    require(all(metadata.get(k) == item.get(k) for k in ('id','name','kind','sha256')), '原始 ADC 文件身份不一致')
    path = checked_path(row['path'])
    require(path.is_relative_to(service.c.root.resolve()) or path.as_posix().lower().startswith('d:/总体-8号机测试/'),
            'ADC 文件不属于本机实验归档')
    return path


def _adc_row(path, sha, row, tone):
    """Bound and authenticate the same file handle used for ZIP reads."""
    limit = 128 * 1024 * 1024
    with path.open('rb') as f:
        f.seek(0, 2); size = f.tell(); f.seek(0)
        require(0 < size <= limit, '该 ADC 文件超出逐行查看容量')
        hashed = hashlib.sha256()
        for chunk in iter(lambda: f.read(1024*1024), b''):
            hashed.update(chunk)
        require(hashed.hexdigest() == sha, '原始 ADC 文件内容摘要已改变')
        f.seek(0)
        with zipfile.ZipFile(f) as archive:
            entries = archive.infolist()
            require(len(entries) <= 128 and len({i.filename for i in entries}) == len(entries),
                    'ADC 文件目录重复或超过上限')
            if not {'i.npy','q.npy'} <= {i.filename for i in entries}:
                return {'status':'unavailable','reason':'该归档未分别保存可读取的 ADC I、Q 数组。'}
            shape = None
            out = {}
            for key in ('i','q'):
                info = archive.getinfo(key+'.npy')
                require(info.file_size <= limit and info.compress_type in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED),
                        'ADC 数组容量或压缩格式不受支持')
                with archive.open(info) as stream:
                    version = stream.read(8)
                    if version not in (b'\x93NUMPY\x01\x00', b'\x93NUMPY\x02\x00'):
                        return {'status':'unavailable','reason':'该归档的 ADC 数组格式不支持逐行查看。'}
                    count = int.from_bytes(stream.read(2 if version[6] == 1 else 4),'little')
                    require(0 < count <= 4096,'ADC 数组头超过上限')
                    header = ast.literal_eval(stream.read(count).decode('latin1'))
                    require(isinstance(header,dict), 'ADC 数组头格式无效')
                    dims = header.get('shape')
                    if (not isinstance(dims,tuple) or len(dims)!=3 or
                        not all(type(v) is int and v>0 for v in dims) or header.get('fortran_order') is not False or
                        header.get('descr') not in ('<f4','<f8')):
                        return {'status':'unavailable','reason':'该归档的 ADC 维度或数值类型缺少可核对的逐行定义。'}
                    require(shape is None or shape == dims, 'ADC I 与 Q 的行、解调频点或发次不一致')
                    shape = dims
                    nr,nc,shots = dims
                    require(0 <= row < nr and 0 <= tone < nc and shots <= 1_000_000, '所选 ADC 行、解调频点或每点发次数超出范围')
                    width = int(header['descr'][-1])
                    data_start=stream.tell()
                    require(info.file_size == data_start+nr*nc*shots*width, 'ADC 数组头与文件长度不一致')
                    stream.seek(data_start+(row*nc+tone)*shots*width)
                    arr=array('d' if width == 8 else 'f')
                    arr.frombytes(stream.read(shots*width))
                    if sys.byteorder!='little': arr.byteswap()
                    require(len(arr)==shots and all(math.isfinite(v) for v in arr), 'ADC 行不完整或含非有限值')
                    indexes=range(shots) if shots<=512 else [round(i*(shots-1)/511) for i in range(512)]
                    out[key]=[arr[index] for index in indexes]
            return dict(status='available',row=row,tone=tone,rows=nr,tones=nc,shots=shots,
                        shotIndices=list(indexes),**out)


def read(service, rt, endpoint, query):
    from .science_pulse_samples import _source_binding
    identity={'workflowId','nodeId','attemptId'}
    extra={'attempt-signals':set(),'attempt-signal-pulse':{'stage','rowId','segmentId'},
           'attempt-signal-adc':{'row','tone'}}[endpoint]
    require(set(query) >= identity and set(query) <= identity|extra, '本次采集来源或查询范围不正确')
    w, n, a = (_field(query,key) for key in ('workflowId','nodeId','attemptId'))
    row=rt.db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=? AND id=?',(w,a)).fetchone()
    require(row, '未找到所选任务的该次采集')
    attempt=json.loads(row[0])
    require(attempt.get('id')==a and attempt.get('nodeId')==n and attempt.get('mode')=='real'
            and attempt.get('endedAt'), '所选采集未结束或不属于该节点')
    raw=attempt.get('result') or {}; reference={'workflowId':w,'attemptId':a}
    require(raw.get('dataKind')=='real' and raw.get('acquiredAt') and not raw.get('verificationPending'),
            '本次主结果尚无已完成的真实回执')
    wf=service.store.get(rt,w)
    node=next((x for x in (wf or {}).get('nodes',[]) if x.get('id')==n),None)
    require(node and node.get('resourceId') and node.get('procedureId')==attempt.get('procedureId'), '采集节点或比特归属不一致')
    scope={'workflowId':w,'resourceId':node['resourceId']}
    verify=bool(attempt.get('verificationOperationId'))
    require(verify==isinstance(raw.get('selectionResult'),dict), '首测与独立复测的阶段记录不完整')
    selected=attempt['verificationOperationId'] if verify else attempt.get('operationId')
    _identity(rt,wf,node,attempt,raw,selected)
    if verify:
        _identity(rt,wf,node,attempt,raw['selectionResult'],attempt.get('operationId'))
    result=service._measurement({'raw':raw,'attempt':attempt})
    if node['procedureId']=='sq_rabi_amplitude' and raw.get('methodLabel')=='ArcLight native rabi_amplitude':
        # Native analyze_single_qubit serializes these two raw traces from views['projection'].
        for trace in (*result['sourceSeries'], *result['series']):
            match=re.fullmatch(r'raw trace ([1-9]\d*)', trace.get('label',''))
            if match:
                trace.update(label='第 '+match[1]+' 组 · IQ 投影',quantity='iq_projection')
    record=dict(id='attempt-signal-'+digest([w,n,a,selected]),kind='measurement',source='acquisition',dataKind='real',
                scope=scope, reference=reference,result=result,input={},
                readOnlyAttempt={'workflowId':w,'nodeId':n,'attemptId':a,'operationId':selected,
                                 'stage':'verification' if verify else 'selection','attemptNumber':attempt.get('number')},
                provenance={'independentlyVerified':attempt.get('state')=='verified' and raw.get('quality',{}).get('status')=='passed' and not raw.get('partial')})
    binding=_source_binding(service,rt,record)
    require(binding['evidence'], '本次采集缺少登记的原始文件')
    record['evidence']=binding['evidence']
    if endpoint=='attempt-signal-pulse':
        return science_pulse_samples.read(service,rt,record,{k:v for k,v in query.items() if k in extra})
    aggregates=[item for item in raw.get('artifacts',[]) if item.get('kind')=='raw' and item.get('name')=='raw_scan.npz']
    require(len(aggregates)<=1, '本次同阶段登记了多个 ADC 汇总文件')
    if endpoint=='attempt-signals':
        record['readOnlyAttempt']['adcAvailable']=bool(aggregates)
        return record
    require(aggregates,'本次主结果未登记可逐行读取的 ADC 汇总文件')
    def index(key):
        value=_field(query,key) if key in query else '0'
        require(re.fullmatch(r'0|[1-9]\d{0,6}',value), 'ADC 行或解调频点编号无效')
        return int(value)
    item=aggregates[0]
    try:
        data=_adc_row(_registered_file(service,rt,item),item['sha256'],index('row'),index('tone'))
    except (OSError,ValueError,TypeError,KeyError,IndexError,OverflowError,SyntaxError,zipfile.BadZipFile) as exc:
        raise APIError('ATTEMPT_SIGNAL_EVIDENCE','原始 ADC 文件不可读或格式不完整',409) from exc
    return dict(data,recordId=record['id'],operationId=selected,artifactId=item['id'],sha256=item['sha256'])
