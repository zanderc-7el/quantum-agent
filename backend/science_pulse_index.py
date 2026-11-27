"""Bounded in-memory JSON row offsets; every use still hashes the whole file."""
from collections import OrderedDict
import codecs
import hashlib
import json
import threading
from .common import APIError,number

# Native campaigns limit total primitives to 100000. This accommodates their
# serialized segment descriptors without treating a 64 MiB file as a run limit.
MAX_MANIFEST_BYTES=256*1024*1024
MAX_ROW_BYTES=16*1024*1024
MAX_ROWS=2048
_CACHE=OrderedDict()
_LOCK=threading.Lock()


def need(ok,message):
    if not ok:raise APIError('SCIENCE_PULSE_EVIDENCE',message,409)


def unique(pairs):
    result={}
    for key,value in pairs:
        need(key not in result,'脉冲行包含重复 JSON 字段');result[key]=value
    return result


_DECODER=json.JSONDecoder(object_pairs_hook=unique,
    parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite JSON')))


def _project(item,kind):
    need(isinstance(item,dict),'脉冲清单行不是对象')
    # The v2 compiler archived summaries, not the drive arrays. Recognize only
    # that closed shape; a missing rowId in a modern archive remains an error.
    if kind=='compiled' and set(item)=={'base_index','axes','waves','pulse_timing'}:
        key=item['base_index']
        need(type(key) is int and key>=0 and isinstance(item['axes'],dict)
            and isinstance(item['waves'],list) and isinstance(item['pulse_timing'],list),
            '旧版脉冲摘要格式无效')
        need(all(isinstance(w,dict) for w in item['waves'])
            and all(isinstance(p,dict) for p in item['pulse_timing']),'旧版脉冲摘要格式无效')
        return key,{'rowId':key,'legacyMetadataOnly':True}
    key=item.get('rowId' if kind=='compiled' else 'id')
    need(type(key) is int and key>=0,'脉冲清单行编号无效')
    if kind=='compiled':return key,{'rowId':key}
    summary={'id':key}
    for name in ('condition','stage','capture','axisValue','logicalState'):
        if name not in item:continue
        value=item[name]
        valid=(isinstance(value,str) and len(value)<=256) if name in ('condition','stage') else number(value)
        need(value is None or valid,'扫描轴行摘要超出类型或长度限制')
        summary[name]=value
    return key,summary


def index_file(path,expected,kind):
    """Cache offsets, not verification. File SHA is rechecked on each request."""
    key=(str(path),expected,kind)
    with _LOCK:cached=_CACHE.get(key)
    need(path.stat().st_size<=MAX_MANIFEST_BYTES,'脉冲清单超过读取容量上限')
    hasher=hashlib.sha256();used=0
    with path.open('rb') as stream:
        if cached is not None:
            while chunk:=stream.read(1024*1024):
                used+=len(chunk);need(used<=MAX_MANIFEST_BYTES,'脉冲清单超过读取容量上限');hasher.update(chunk)
            need(hasher.hexdigest()==expected,'脉冲清单内容已改变')
            return cached
        decoder=codecs.getincrementaldecoder('utf8')();buffer='';offset=0;eof=False
        offsets={};rows={}
        def more():
            nonlocal buffer,used,eof
            chunk=stream.read(64*1024);used+=len(chunk)
            need(used<=MAX_MANIFEST_BYTES,'脉冲清单超过读取容量上限')
            hasher.update(chunk);eof=not chunk
            buffer+=decoder.decode(chunk,final=eof)
            need(len(buffer.encode('utf8'))<=MAX_ROW_BYTES+64*1024,'单个脉冲行超过读取容量上限')
        def consume(count):
            nonlocal buffer,offset
            offset+=len(buffer[:count].encode('utf8'));buffer=buffer[count:]
        def whitespace():
            while True:
                stripped=buffer.lstrip();consume(len(buffer)-len(stripped))
                if buffer or eof:return
                more()
        more();whitespace();need(buffer.startswith('['),'脉冲清单不是数组');consume(1)
        first=True
        while True:
            whitespace()
            if buffer.startswith(']'):
                need(first or offsets,'脉冲清单无效');consume(1);break
            need(bool(buffer),'脉冲清单不完整')
            while True:
                try:item,end=_DECODER.raw_decode(buffer);break
                except json.JSONDecodeError:
                    need(not eof,'脉冲清单 JSON 不完整');more()
            raw=buffer[:end].encode('utf8');need(len(raw)<=MAX_ROW_BYTES,'单个脉冲行超过读取容量上限')
            row_id,summary=_project(item,kind)
            need(row_id not in offsets and len(offsets)<MAX_ROWS,'脉冲清单行重复或超过上限')
            offsets[row_id]=(offset,len(raw),hashlib.sha256(raw).hexdigest());rows[row_id]=summary
            consume(end);item=None;raw=None;first=False;whitespace()
            need(bool(buffer) and buffer[0] in ',]','脉冲清单分隔符无效')
            if buffer[0]==']':consume(1);break
            consume(1);whitespace();need(not buffer.startswith(']'),'脉冲清单包含尾随逗号')
        whitespace();need(not buffer and eof,'脉冲清单包含额外内容')
    need(hasher.hexdigest()==expected,'脉冲清单内容已改变')
    value={'offsets':offsets,'rows':rows}
    with _LOCK:
        _CACHE[key]=value;_CACHE.move_to_end(key)
        while len(_CACHE)>8:_CACHE.popitem(last=False)
    return value


def read_row(path,index,row_id):
    need(row_id in index['offsets'],'所选脉冲行不存在')
    offset,size,expected=index['offsets'][row_id]
    with path.open('rb') as stream:stream.seek(offset);raw=stream.read(size)
    need(len(raw)==size and hashlib.sha256(raw).hexdigest()==expected,'所选脉冲行内容已改变')
    return _DECODER.decode(raw.decode('utf8'))
