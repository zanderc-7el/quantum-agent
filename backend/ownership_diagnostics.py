"""Bounded ownership observations, separate from control and authorization."""
from copy import deepcopy
from datetime import datetime, timezone
import json, math
from .common import APIError, ident, now

SCHEMA='qcal-hardware-ownership-check/1'
STATUSES={'completed','timeout','failed','unsupported_platform','unconfigured_endpoint','invalid_response','adapter_error','unreported'}

def observation(value, stage):
    value=value if isinstance(value,dict) else {}
    rows=value.get('owners');valid_rows=isinstance(rows,list)
    rows=rows if valid_rows else []
    verified=value.get('verified') is True and valid_rows
    counts={'socket':0,'lock':0,'unknown':0};pids=set();pids_truncated=False
    for row in rows:
        if not isinstance(row,dict):counts['unknown']+=1;continue
        if row.get('kind')=='chassis_lock':counts['lock']+=1
        elif row.get('kind') in (None,'tcp_connection') and type(row.get('pid')) is int and row['pid']>0 and type(row.get('localPort')) is int and 0<row['localPort']<=65535:counts['socket']+=1
        else:counts['unknown']+=1
        pid=row.get('pid')
        if type(pid) is int and 0<pid<=2**32-1 and pid not in pids:
            if len(pids)<8:pids.add(pid)
            else:pids_truncated=True
    check=value.get('check') if isinstance(value.get('check'),dict) else {}
    # Status comes only from the typed local probe; never infer timeout from text.
    status=check.get('status') if check.get('schema')=='qcal-socket-check/1' else 'unreported'
    status=status if status in STATUSES else 'unreported'
    sources=[]
    if check.get('source')=='windows_tcp_table' or counts['socket']:sources.append('windows_tcp_table')
    if counts['lock']:sources.append('chassis_lock_files')
    if not sources or counts['unknown']:sources.append('adapter_ownership')
    stamp=value.get('checkedAt')
    checked_at=now()
    if type(stamp) in (int,float) and math.isfinite(stamp):
        try:checked_at=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
        except (ValueError,OverflowError,OSError):pass
    nonzero=[k for k,v in counts.items() if v]
    outcome='unverified' if not verified else ('mixed_records' if len(nonzero)>1 else {'socket':'socket_records','lock':'lock_records','unknown':'unknown_records'}.get(next(iter(nonzero),''),'unverified'))
    return {'schema':SCHEMA,'id':ident('ownership-check'),'stage':stage,'checkedAt':checked_at,'verified':verified,
        'outcome':outcome,'checkStatus':status,'sources':sources,'ownerCount':len(rows),
        'socketCount':counts['socket'],'lockCount':counts['lock'],'unknownCount':counts['unknown'],
        'visiblePids':sorted(pids),'pidsTruncated':pids_truncated}

def checked_evidence(value):
    """Ignore unknown fields and older/malformed optional presentation data."""
    if not isinstance(value,dict) or value.get('schema')!=SCHEMA:return None
    text_keys={'schema','id','stage','checkedAt','outcome','checkStatus'}
    if any(not isinstance(value.get(k),str) or len(value[k])>128 for k in text_keys):return None
    if value['stage'] not in ('confirmation','schedule','dispatch') or value['outcome'] not in ('unverified','socket_records','lock_records','mixed_records','unknown_records') or value['checkStatus'] not in STATUSES:return None
    if type(value.get('verified')) is not bool or type(value.get('pidsTruncated')) is not bool:return None
    counts=('ownerCount','socketCount','lockCount','unknownCount')
    if any(type(value.get(k)) is not int or value[k]<0 for k in counts):return None
    if value['ownerCount']!=sum(value[k] for k in counts[1:]):return None
    pids=value.get('visiblePids');sources=value.get('sources')
    if not isinstance(pids,list) or len(pids)>8 or any(type(v) is not int or not 0<v<=2**32-1 for v in pids):return None
    if not isinstance(sources,list) or not 1<=len(sources)<=3 or any(v not in ('windows_tcp_table','chassis_lock_files','adapter_ownership') for v in sources):return None
    try:datetime.fromisoformat(value['checkedAt'])
    except ValueError:return None
    return {k:deepcopy(value[k]) for k in (*text_keys,*counts,'verified','pidsTruncated','visiblePids','sources')}

def message(evidence):
    if evidence['outcome']=='unverified':
        return ('设备独占状态核查超时' if evidence['checkStatus']=='timeout' else '设备独占状态未能核实')+'，未下发测量；请重新核查。'
    return {'socket_records':'检测到机箱 TCP 连接记录，尚未确认可独占；未下发测量。',
        'lock_records':'检测到尚未释放或未核实的机箱锁记录；未下发测量。',
        'mixed_records':'检测到连接或锁等占用记录，尚未确认可独占；未下发测量。',
        'unknown_records':'检测到未识别的占用记录，尚未确认可独占；未下发测量。'}[evidence['outcome']]

class OwnershipRefusal(APIError):
    def __init__(self,workflow,node,value,stage):
        self.ownershipCheck=observation(value,stage)
        self.audit={'workflowId':workflow['id'],'nodeId':node.get('id'),'attemptId':node.get('latestAttemptId'),
            'revision':workflow['revision'],'ownershipCheck':self.ownershipCheck}
        super().__init__('DEVICE_IN_USE',message(self.ownershipCheck),409)

def record_rollback_refusal(rt,error):
    """Called only after command rollback: append evidence, not workflow state."""
    if isinstance(error,OwnershipRefusal):
        rt._event(error.audit['workflowId'],'hardware_ownership_rejected',
            {**error.audit,'message':error.message,'code':error.code})

def latest_refusal(rt,wf,node):
    row=rt.db.execute("SELECT payload FROM events WHERE run_id=? AND event='hardware_ownership_rejected' ORDER BY seq DESC LIMIT 1",(wf['id'],)).fetchone()
    if row:
        value=json.loads(row[0]);check=checked_evidence(value.get('ownershipCheck'))
        if value.get('nodeId')==node.get('id') and value.get('attemptId')==node.get('latestAttemptId') and check is not None:
            return deepcopy(check)
    return None
