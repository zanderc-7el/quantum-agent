"""Validate a completed fresh PRE reference and attach it to two unused RB plans.

Offline only. Never edits acquired jobs/raw data or existing frozen reports.
Only calibration and paired_rb_projection fields of the two unused plans may
change. Any live result for either RB plan prevents all plan updates.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).absolute().parent))
from copy import deepcopy
import argparse
import hashlib
import json
import os
import uuid
import numpy as np
from readout_reference_support import ROOT,_safe,_load_run
from analyze_historical_allxy import verify_real_blocks
import freeze_historical_point_reference as freezer
from export_paired_lda_reference import export,build_reference


CONDITION_FIELDS=('target','config','xy_physical_channel','xy_filter_mhz','xy_attenuation_db','xy_delay_s')
META_FIELDS=('qubit','shots','readout_mode','sync','group','time_to_read','pulse_compatibility')


def read(path): return json.loads(_safe(path).read_text(encoding='utf-8-sig'))
def sha(path): return hashlib.sha256(_safe(path).read_bytes()).hexdigest()


def assert_conditions(reference,other):
    for key in CONDITION_FIELDS:
        if reference.get(key)!=other.get(key): raise ValueError('Physical/reference condition differs: '+key)
    for key in META_FIELDS:
        if reference['spec']['meta'].get(key)!=other['spec']['meta'].get(key):
            raise ValueError('Reference meta condition differs: '+key)
    if reference['spec'].get('static_config',{}) or other['spec'].get('static_config',{}):
        raise ValueError('This frozen-workpoint campaign requires empty static_config')


def load_reference(folder,*,role):
    folder=_safe(folder)
    if not folder.is_relative_to(ROOT/'results'): raise ValueError('Only isolated task result folders are allowed')
    axes,iq,job,hashes=_load_run(folder);status=read(folder/'status.json')
    if status.get('hardware_connected') is not False or status.get('shutdown_ok') is not True:
        raise ValueError('Reference acquisition must be complete with successful shutdown')
    target=job['target']
    if target not in ('Q11','Q18'): raise ValueError('This campaign supports Q11 or Q18 only')
    name=f'{target}_historical_rb_readout_{role}'
    if job['name']!=name or not folder.name.endswith('_live_'+name):
        raise ValueError('Expected the specifically acquired fresh RB '+role+' reference, not a historical reused run')
    if job.get('kind')!='readout' or job.get('captures')!=6 or job.get('acquisitions_per_point')!=2:
        raise ValueError('Fresh RB reference requires six captures and two real acquisitions per point')
    if job['spec']['meta']['shots']!=2000 or len(axes)!=12:
        raise ValueError('Fresh RB reference requires 2000 shots/train and 12 ground/excited points')
    expected={(c,s) for c in range(6) for s in ('ground','excited')}
    observed=[]
    for a in axes:
        if set(a['axes'])!={'exp.cir.default'}: raise ValueError('Reference has unexpected scan axes')
        observed.append((a['capture'],a['axes']['exp.cir.default']))
    if len(set(observed))!=12 or set(observed)!=expected: raise ValueError('Reference capture/state coverage is incomplete')
    blocks,proof=verify_real_blocks(folder,iq,job)
    if proof['exact_duplicate_blocks']: raise ValueError('Reference contains exactly repeated IQ acquisition blocks')
    return axes,iq,job,hashes,blocks,proof


def require_unused(names):
    # Check the directory name even if a live job has not yet written status.
    # Offline preflights are allowed; failed/incomplete live attempts are not.
    for entry in (ROOT/'results').iterdir():
        if any(entry.name.endswith('_live_'+name) for name in names):
            _safe(entry)
            raise RuntimeError('Live RB result already exists; refusing plan changes: '+str(entry))


def _save_new(path,value):
    path=_safe(path,exists=False)
    if path.exists(): raise FileExistsError(path)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def prepare(folder):
    folder=_safe(folder)
    # Read just the completed reference job to identify the protected targets;
    # no plan or frozen-reference mutations occur before all live checks.
    initial=read(folder/'job.json');target=initial.get('target')
    if target not in ('Q11','Q18'): raise ValueError('Unsupported target')
    names=[f'{target}_historical_rb_reference',f'{target}_historical_rb_interleaved']
    require_unused(names)
    paths=[_safe(ROOT/'plans'/(name+'.json')) for name in names]
    before_bytes=[p.read_bytes() for p in paths];plans=[json.loads(b.decode('utf-8-sig')) for b in before_bytes]
    axes,iq,refjob,input_hashes,blocks,block_proof=load_reference(folder,role='pre')
    for plan,name,kind in zip(plans,names,('rb_reference','rb_interleaved')):
        if plan['name']!=name or plan['kind']!=kind or str(plan['spec']['meta']['type'])!='3':
            raise ValueError('Unexpected RB plan name/kind/type')
        assert_conditions(refjob,plan)
    if plans[0]['spec']!=plans[1]['spec']:
        raise ValueError('Reference/IRB sequence specs or paired seed settings differ')
    if plans[0].get('interleaved_gate') or plans[1].get('interleaved_gate')!='X/2':
        raise ValueError('Require native reference RB and X/2 interleaved RB')
    for key in ('captures','acquisitions_per_point','randomize','order_seed'):
        if plans[0].get(key)!=plans[1].get(key): raise ValueError('RB pair sampling differs: '+key)
    require_unused(names)
    if any(p.read_bytes()!=b for p,b in zip(paths,before_bytes)):
        raise RuntimeError('RB plans changed during validation; no update performed')

    # Explicit reference-only API: this does not attach or edit any Rabi plan.
    frozen=freezer.freeze(folder,plan=None)
    if frozen['target']!=target or frozen['projection'].get('reference_contrast_repeated') is not True:
        raise ValueError('Fresh independent held-out contrast did not validate; RB plans remain untouched')
    export_path=folder/'paired_rb_projection_export.json'
    expected_export=build_reference(folder)
    if export_path.exists():
        exported=read(export_path)
        if exported!=expected_export: raise ValueError('Existing export differs; refusing overwrite')
    else: exported=export(folder,export_path)
    fresh_source={'role':'fresh_pre_before_RB','folder':str(folder),
        'reference_job':refjob['name'],'reference_job_sha256':input_hashes['job.json'],
        'raw_scan_sha256':input_hashes['raw_scan.npz'],'axes_sha256':input_hashes['axes.json'],
        'frozen_reference_sha256':sha(folder/'independent_projection.json'),
        'paired_reference_export':str(export_path),'paired_reference_export_sha256':sha(export_path)}
    updated=[]
    for plan in plans:
        new=deepcopy(plan)
        new.setdefault('calibration',{}).update(projection=deepcopy(frozen['projection']),fresh_source=deepcopy(fresh_source))
        new['paired_rb_projection']={**deepcopy(exported['paired_rb_projection']),'fresh_source':deepcopy(fresh_source)}
        for key in set(new)|set(plan):
            if key not in ('calibration','paired_rb_projection') and new.get(key)!=plan.get(key):
                raise AssertionError('Attempted to change a non-calibration plan field')
        updated.append(new)
    if updated==plans:
        return {'state':'already_prepared','target':target,'plans':[str(p) for p in paths],
                'fresh_source':fresh_source,'no_plan_fields_changed':True}
    require_unused(names)
    if any(p.read_bytes()!=b for p,b in zip(paths,before_bytes)):
        raise RuntimeError('RB plans changed before commit; no update performed')
    audit_path=folder/'rb_fresh_preparation.json'
    if audit_path.exists(): raise FileExistsError('Preparation audit already exists but plans differ')
    source_dir=folder/'rb_preparation_sources';source_dir.mkdir(exist_ok=True)
    source_hashes={}
    for name in ('prepare_rb_from_fresh_reference.py','freeze_historical_point_reference.py',
                 'export_paired_lda_reference.py','analyze_historical_allxy.py','readout_reference_support.py'):
        data=(ROOT/name).read_bytes();source_hashes[name]=hashlib.sha256(data).hexdigest()
        path=source_dir/name
        if path.exists() and path.read_bytes()!=data: raise ValueError('Preparation source snapshot differs')
        if not path.exists(): path.write_bytes(data)
    temporaries=[]
    try:
        for path,new in zip(paths,updated):
            temporary=path.with_name(path.name+'.prepare-'+uuid.uuid4().hex+'.tmp')
            temporary.write_text(json.dumps(new,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
            temporaries.append(temporary)
        # Each file is replaced atomically. Any interrupted pair is unmeasured
        # and can be inspected/retried; this is not a claimed multi-file transaction.
        require_unused(names)
        if any(p.read_bytes()!=b for p,b in zip(paths,before_bytes)):
            raise RuntimeError('RB plans changed at commit boundary')
        for path,temporary in zip(paths,temporaries): os.replace(temporary,path)
    finally:
        for temporary in temporaries:
            if temporary.exists(): temporary.unlink()
    audit={'state':'prepared','target':target,'fresh_source':fresh_source,
        'only_updated_fields':['calibration','paired_rb_projection'],
        'plans':[{'path':str(p),'before_sha256':hashlib.sha256(b).hexdigest(),'after_sha256':sha(p)}
                 for p,b in zip(paths,before_bytes)],
        'reference_block_verification':block_proof,'preparation_source_sha256':source_hashes,
        'independent_reference_frozen_before_any_RB_live_result':True,
        'source_acquisition_files_modified':False,'hardware_connected':False}
    _save_new(audit_path,audit)
    return audit


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('fresh_pre_folder',type=Path);args=p.parse_args()
    result=prepare(args.fresh_pre_folder)
    print(json.dumps({'state':result['state'],'target':result['target'],
        'plans':result['plans'],'fresh_source':result['fresh_source']},ensure_ascii=False))
