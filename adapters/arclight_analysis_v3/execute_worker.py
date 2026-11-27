"""Verify the versioned plan, then use the unchanged bounded native worker.

The only replacement is the explicitly frozen offline analysis module. Existing
native compilation, acquisition, DC preservation, STOP and cleanup are reused.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

LEGACY_MANIFEST='adapters/arclight_runtime/source_manifest.json'
LEGACY_SHA='1531ee2cd78774e6c6a5e089be54c3f35a98d8c0fdf03aa0547169106527a322'
MANIFEST='adapters/arclight_analysis_v3/source_manifest.json'
PROFILE='adapters/arclight_profile_v3.json'
EXTRA={'adapters/arclight_analysis_v3/__init__.py','adapters/arclight_analysis_v3/execute_worker.py',
       'adapters/arclight_analysis_v3/platform_analysis.py',PROFILE,LEGACY_MANIFEST,
       'adapters/arclight.py','adapters/f8.py','adapters/history.py','adapters/progress_delivery.py'}

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(value,compact=False):
    options={'separators':(',',':')} if compact else {}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,**options).encode()).hexdigest()

def verify_sources(root):
    root=Path(root).resolve()
    if sha(root/LEGACY_MANIFEST)!=LEGACY_SHA:raise ValueError('Frozen legacy manifest changed')
    legacy=read(root/LEGACY_MANIFEST);rows=read(root/MANIFEST)
    expected={r['path'] for r in legacy}|EXTRA
    if (not isinstance(rows,list) or len(rows)!=len(expected)
            or {r.get('path') for r in rows}!=expected):raise ValueError('Analysis version source closure differs')
    checked={}
    for row in rows:
        path=(root/row['path']).resolve()
        if not path.is_relative_to(root) or sha(path)!=row['sha256']:raise ValueError('Analysis source changed: '+row['path'])
        checked[row['path']]=row['sha256']
    return checked

def validate_admission(root,folder,plan):
    root=Path(root).resolve();folder=Path(folder).resolve()
    intent=read(folder/'adapter_intent.json');request=intent.get('request',{});profile=read(root/PROFILE)
    frozen={k:v for k,v in profile.items() if k not in ('enabled','connectionConfirmedAt')}
    expected=digest({'profile':frozen,'sourceManifestSha256':sha(root/MANIFEST)},compact=True)
    if (intent.get('operationId')!=request.get('operationId') or intent.get('requestHash')!=digest(request)
            or intent.get('profileHash')!=digest({**profile,'enabled':True})
            or request.get('adapterId')!=profile['id'] or request.get('adapterProfileHash')!=expected):
        raise ValueError('Versioned worker intent/profile binding differs')
    from adapters.arclight import ArcLightAdapter
    rebuilt=ArcLightAdapter(root,profile).build_plan(request)
    if digest(plan,compact=True)!=digest(rebuilt,compact=True):raise ValueError('Versioned worker plan differs from frozen request')
    return request

def _module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module

def main():
    root=Path(__file__).resolve().parents[2]
    if len(sys.argv)!=2:raise ValueError('Only an explicit controller plan is supported')
    plan_path=Path(sys.argv[1]).resolve();folder=plan_path.parent
    if Path(os.environ.get('QCAL_ARCLIGHT_RUN_DIR','')).resolve()!=folder:raise ValueError('Worker operation directory differs')
    if not folder.is_relative_to(root/'data/runs/arclight_operations'):raise ValueError('Operation is outside the controller archive')
    verify_sources(root)
    sys.path.insert(0,str(root))
    request=validate_admission(root,folder,read(plan_path))
    if folder.name!=hashlib.sha256(request['operationId'].encode()).hexdigest():raise ValueError('Operation directory does not match its identity')
    # No driver/native entry point is imported until all immutable identities pass.
    legacy=root/'adapters/arclight_runtime';sys.path.insert(0,str(legacy))
    analysis=_module('platform_analysis',root/'adapters/arclight_analysis_v3/platform_analysis.py')
    worker=_module('_qcal_versioned_native_worker',legacy/'platform_worker.py')
    if sys.modules.get('platform_analysis') is not analysis:raise ValueError('Analysis module binding changed')
    worker.main()

if __name__=='__main__':main()
