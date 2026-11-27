"""Explicit immutable fixed-station versions; never migrate a workflow in place."""
import json
from pathlib import Path
from .common import APIError, checked_path, digest, file_hash

PROFILES = {
    'arclight-private-f8-q11-v1':'adapters/arclight_profile.json',
    'arclight-private-f8-q11-v2':'adapters/arclight_profile_v2.json',
    'arclight-private-f8-q11-v3':'adapters/arclight_profile_v3.json',
}


def fingerprint(root, profile):
    frozen={k:v for k,v in profile.items() if k not in ('enabled','connectionConfirmedAt')}
    manifest=checked_path(Path(root)/profile['sourceManifest'],parent=Path(root))
    return digest({'profile':frozen,'sourceManifestSha256':file_hash(manifest)})


def profile_for_workflow(root, wf):
    if wf.get('mode')!='real' or wf.get('configurationBinding') or wf.get('adapterId') not in PROFILES:
        return None
    path=checked_path(Path(root)/PROFILES[wf['adapterId']],parent=Path(root))
    try:
        profile=json.loads(path.read_text(encoding='utf-8-sig'))
        valid=profile.get('id')==wf['adapterId'] and fingerprint(root,profile)==wf.get('adapterProfileHash')
    except (OSError,ValueError,KeyError,TypeError):
        valid=False
    if not valid:
        raise APIError('ADAPTER_CHANGED','该任务冻结的执行版本或源码已变化，不能改用另一版本继续',409)
    return profile


def fresh_lineage(controller, data, mode, policy, coverage, selected, rt):
    parent=data.get('analysisParentWorkflowId')
    if parent is None:return None
    if (not isinstance(parent,str) or not 1<=len(parent)<=256 or mode!='real' or policy!='strict'
            or coverage!='full' or data.get('experiment','single')!='single' or selected!=['Q11']
            or controller.profile.get('id')!='arclight-private-f8-q11-v3'
            or data.get('executionProfile') or data.get('chipVersionId') or data.get('deploymentVersionId')):
        raise APIError('ANALYSIS_PARENT_SCOPE','分析版本重测只允许新建固定 Q11 的完整人工逐步任务',409)
    source=controller.get(parent,rt)
    bound=controller.for_workflow(parent,rt)
    bound._assert_adapter_binding(source)
    if (source.get('mode')!='real' or source.get('resourceIds')!=selected
            or source.get('adapterId') not in PROFILES or source.get('status') in ('queued','running','stopping')
            or source.get('configurationBinding')):
        raise APIError('ANALYSIS_PARENT_SCOPE','来源须为已停止采集的同一固定 Q11 任务；不会继承其验收',409)
    return {'schema':'qcal-fresh-analysis-lineage/1','parentWorkflowId':parent,
        'parentRevision':source['revision'],'parentAdapterId':source['adapterId'],
        'parentAdapterProfileHash':source['adapterProfileHash'],'acceptedCalibrationInherited':False,
        'parametersInherited':False,'meaning':'独立重新采集；来源仅用于追踪分析版本升级，不替代任何首测或复测'}
