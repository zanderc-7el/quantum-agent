"""Authenticate the new smart policy, then reuse frozen native acquisition/counting."""
import hashlib
import json
import os
from pathlib import Path
import sys

MANIFEST = 'adapters/arclight_smart_rabi16_runtime/source_manifest.json'
INHERITED = 'adapters/arclight_rabi16_runtime/source_manifest.json'
INHERITED_SHA = 'ce2424fc1179c9953aed4660454dda47621d7bb87a2020da62d5c6851ce484a0'
NEW = {INHERITED,'adapters/arclight_profile_smart_rabi16_v1.json','adapters/arclight_smart_rabi16_adapter.py',
    'adapters/arclight_smart_rabi16_runtime/__init__.py','adapters/arclight_smart_rabi16_runtime/policy.py',
    'adapters/arclight_smart_rabi16_runtime/execute_worker.py'}

def read(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def verify_sources(root):
    root = Path(root).resolve()
    if sha(root/INHERITED) != INHERITED_SHA: raise ValueError('Frozen R31 source manifest changed')
    base=read(root/INHERITED); rows=read(root/MANIFEST); old={r['path']:r['sha256'] for r in base}
    if not isinstance(rows,list) or len(rows)!=len(set(old)|NEW) or {r.get('path') for r in rows}!=set(old)|NEW:
        raise ValueError('Smart sampling source closure differs')
    checked={}
    for row in rows:
        p=(root/row['path']).resolve()
        if not p.is_relative_to(root) or sha(p)!=row['sha256'] or row['path'] in old and old[row['path']]!=row['sha256']:
            raise ValueError('Smart sampling source changed: '+row['path'])
        checked[row['path']]=row['sha256']
    return checked

def validate_admission(root,folder,plan):
    from adapters.arclight_smart_rabi16_runtime import policy
    from adapters.arclight_smart_rabi16_adapter import ArcLightSmartRabi16Adapter
    root,folder=Path(root).resolve(),Path(folder).resolve();profile=policy.load_profile(root)
    intent=read(folder/'adapter_intent.json');request=intent.get('request',{})
    policy.validate_binding(root,profile,request,executable=True)
    if (intent.get('operationId')!=request.get('operationId') or intent.get('requestHash')!=policy.intent_digest(request)
        or intent.get('profileHash')!=policy.intent_digest({**profile,'enabled':True})):
        raise ValueError('Smart intent/profile identity differs')
    if not folder.is_relative_to(root/'data/runs/arclight_operations') or folder.name!=hashlib.sha256(request['operationId'].encode()).hexdigest():
        raise ValueError('Smart operation directory differs')
    if policy.digest(plan)!=policy.digest(ArcLightSmartRabi16Adapter(root,profile).build_plan(request)):
        raise ValueError('Smart plan differs from complete frozen request')
    return request

def main():
    root=Path(__file__).resolve().parents[2]
    if len(sys.argv)!=2: raise ValueError('Only a controller plan is supported')
    path=Path(sys.argv[1]).resolve();folder=path.parent
    if Path(os.environ.get('QCAL_ARCLIGHT_RUN_DIR','')).resolve()!=folder: raise ValueError('Operation directory differs')
    verify_sources(root);sys.path.insert(0,str(root));plan=read(path);validate_admission(root,folder,plan)
    from adapters.arclight_rabi16_runtime.execute_worker import _module, sampling_receipt, save
    legacy=root/'adapters/arclight_runtime';sys.path.insert(0,str(legacy))
    analysis=_module('platform_analysis',root/'adapters/arclight_analysis_v3/platform_analysis.py')
    worker=_module('_qcal_smart_rabi16_native_worker',legacy/'platform_worker.py')
    if sys.modules.get('platform_analysis') is not analysis: raise ValueError('Frozen analysis binding changed')
    worker.main();completion=read(folder/'completion.json')
    try: sampling_receipt(folder,plan,completion)
    except BaseException as exc:
        completion.update(state='failed_after_shutdown',error='Sampling receipt validation failed: '+str(exc))
        save(folder/'completion.json',completion);raise
    save(folder/'completion.json',completion)

if __name__=='__main__':main()
