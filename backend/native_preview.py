"""Task-bound native compilation, kept separate from hardware admission."""
from copy import deepcopy
from pathlib import Path
import threading

from adapters.science_runtime.compile_client import compile_offline
from adapters.science_runtime.policy import POLICY, normalize
from .common import APIError, checked_path, digest, dumps, file_hash, ident, now

_COMPILE_SLOTS=threading.BoundedSemaphore(2)


class NativePreviewService:
    def __init__(self,controller):
        self.c=controller
        self.code_root=Path(__file__).resolve().parents[1]

    def _context(self,rt,body):
        scope=body.get('scope')
        allowed={'workflowId','resourceId','chipVersionId','deploymentVersionId'}
        if not isinstance(scope,dict) or set(scope)-allowed or not scope.get('workflowId'):
            raise APIError('NATIVE_SCOPE_REQUIRED','原生编译须绑定当前任务和比特')
        if any(not isinstance(v,str) or not 1<=len(v)<=256 for v in scope.values()):raise APIError('NATIVE_SCOPE_REQUIRED','任务范围标识无效')
        wf=self.c.store.get(rt,scope['workflowId'])
        if not wf:raise APIError('WORKFLOW_NOT_FOUND','任务不存在',404)
        for field in ('chipVersionId','deploymentVersionId'):
            if field in scope and scope[field]!=wf.get(field):raise APIError('NATIVE_SCOPE_MISMATCH','芯片或接线版本与任务不一致')
        node_id=body.get('nodeId')
        if node_id is not None and (not isinstance(node_id,str) or not node_id):raise APIError('NATIVE_NODE_REQUIRED','步骤标识无效')
        if node_id is None and len(wf.get('resourceIds',[]))!=1:raise APIError('NATIVE_NODE_REQUIRED','多比特任务必须指定当前步骤')
        node=next((n for n in wf['nodes'] if n['id']==node_id),None) if node_id else next(iter(wf['nodes']),None)
        if not node:raise APIError('NATIVE_NODE_REQUIRED','所选步骤不属于当前任务')
        resource=node['resourceId']
        if resource not in wf['resourceIds'] or scope.get('resourceId',resource)!=resource:
            raise APIError('NATIVE_SCOPE_MISMATCH','步骤和比特范围不一致')
        bound=self.c.for_workflow(wf['id'],rt)
        params,versions=bound._parameters(rt,wf,node)
        actual={'workflowId':wf['id'],'resourceId':resource,'nodeId':node['id']}
        for field in ('chipVersionId','deploymentVersionId'):
            if wf.get(field):actual[field]=wf[field]
        source={'workflowId':wf['id'],'nodeId':node['id'],'resourceId':resource,
                'parameters':params,'parameterVersions':versions,
                'configurationBindingHash':digest(wf.get('configurationBinding')),
                'adapterProfileHash':digest(bound.profile),
                'route':deepcopy(bound.profile.get('instrument',{})),
                'seedCalibrationStatus':bound.profile.get('resources',{}).get(resource,{}).get('calibrationStatus','unvalidated_seed'),
                'xyFilterMHz':params.get('xy_filter_mhz'),
                'latestAttemptId':node.get('latestAttemptId'),'acceptedCalibration':False}
        return actual,source

    def preview(self,body):
        allowed={'kind','scope','nodeId','input','policy'}
        if not isinstance(body,dict) or set(body)-allowed:raise APIError('NATIVE_INPUT_INVALID','原生预览包含未允许字段')
        if body.get('policy',POLICY)!=POLICY:raise APIError('NATIVE_POLICY_INVALID','不支持此编译策略')
        with self.c.store.connect() as rt:scope,source=self._context(rt,body)
        inputs=body.get('input',{})
        if not isinstance(inputs,dict):raise APIError('NATIVE_INPUT_INVALID','脉冲输入须为对象')
        inputs=deepcopy(inputs);explicit=set(inputs);params=source['parameters']
        bindings={'durationNs':('drive_length_s',1e9),'amplitude':('drive_amp',1),
                  'frequencyHz':('drive_hz',1),'readoutWidthNs':('readout_width_s',1e9),
                  'readoutAmplitude':('readout_amp',1),'readoutFrequencyHz':('readout_hz',1)}
        inherited=set()
        for target,(name,factor) in bindings.items():
            if target not in inputs and name in params:inputs[target]=params[name]*factor;inherited.add(target)
        request={'kind':body.get('kind','pulse'),'input':inputs,'scope':scope,'source':source,'policy':POLICY}
        try:request=normalize(request)
        except (TypeError,ValueError) as exc:raise APIError('NATIVE_INPUT_INVALID',str(exc)) from exc
        source['parameterSources']={k:'explicit_compile_input' if k in explicit else 'server_workflow_parameter_seed' if k in inherited else 'native_compile_template_default' for k in request['input']}
        source['evidenceHash']=digest({k:v for k,v in source.items() if k not in ('parameterSources','evidenceHash')})
        request['source']=source
        if not _COMPILE_SLOTS.acquire(blocking=False):raise APIError('NATIVE_COMPILER_BUSY','两个离线编译正在进行，请稍后重试',429)
        record_id=ident('native-compile')
        output=checked_path(self.c.root/'data/science_compiles'/record_id,parent=self.c.root)
        try:
            result=compile_offline(self.code_root,request,output,cancel=self.c.stop_event.is_set)
            with self.c.store.transaction() as rt:
                current_scope,current=self._context(rt,body)
                if current_scope!=scope or digest(current)!=source['evidenceHash']:
                    raise APIError('NATIVE_SOURCE_CHANGED','编译期间任务参数或配置证据已变化，请重新编译',409)
                result.update(id=record_id,createdAt=now())
                artifacts=[]
                for item in result['artifacts']:
                    artifact={**item,'id':ident('native-artifact'),'localPath':str(output/item['name']),
                              'dataKind':'compiled','workflowId':scope['workflowId'],'nodeId':scope['nodeId']}
                    artifacts.append(self.c._artifact(rt,artifact))
                result['artifacts']=artifacts
                # The record is write-once; array files and exact normalized
                # request have already been frozen by the child protocol.
                with (output/'record.json').open('x',encoding='utf-8') as stream:stream.write(dumps(result))
                record_artifact={'id':ident('native-record'),'name':'record.json','kind':'compiled_record',
                                 'localPath':str(output/'record.json'),'sha256':file_hash(output/'record.json'),
                                 'dataKind':'compiled','workflowId':scope['workflowId'],'nodeId':scope['nodeId']}
                self.c._artifact(rt,record_artifact)
            return result
        except APIError:raise
        except InterruptedError as exc:raise APIError('NATIVE_COMPILE_CANCELLED',str(exc),409) from exc
        except TimeoutError as exc:raise APIError('NATIVE_COMPILE_TIMEOUT',str(exc),408) from exc
        except (ValueError,OSError) as exc:raise APIError('NATIVE_COMPILE_FAILED',str(exc),422) from exc
        finally:_COMPILE_SLOTS.release()
