"""Offline AllXY adapter for frozen independent LDA direction references.

Never edits acquired job/reference/raw files. Centroids come only from the
already selected training captures of the separate frozen reference, and the
original LDA direction is preserved. Actual rearmed blocks are verified.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).absolute().parent))
from copy import deepcopy
import argparse
import hashlib
import json
import numpy as np
from analyze_allxy import analyze_arrays,save_analysis
from readout_reference_support import ROOT,_safe,_load_run
from analyze_single_qubit import normalized_acquisition_blocks


def _sha(path): return hashlib.sha256(_safe(path).read_bytes()).hexdigest()
def _json(path): return json.loads(_safe(path).read_text(encoding='utf-8-sig'))


def verify_real_blocks(folder,iq,job):
    count=job.get('acquisitions_per_point',1); shots=int(job['spec']['meta']['shots'])
    with np.load(_safe(folder/'raw_scan.npz'),allow_pickle=False) as raw:
        index=raw['acquisition_block_index'].copy() if 'acquisition_block_index' in raw.files else None
    normalized=normalized_acquisition_blocks(index,(len(iq),iq.shape[-1]),count)
    if iq.shape[-1]!=count*shots: raise ValueError('Real shot total differs from declared acquisition blocks')
    hashes={};duplicate_points=[]
    for point in range(len(iq)):
        with np.load(_safe(folder/f'point_{point:04d}.npz'),allow_pickle=False) as raw:
            if index is not None:
                pi=normalized_acquisition_blocks(raw['acquisition_block_index'],(1,iq.shape[-1]),count)
                if not np.array_equal(pi[0],normalized[point]): raise ValueError('Point block indices differ from aggregate')
        manifest_path=folder/f'point_{point:04d}_acquisition_blocks.json'
        manifest=_json(manifest_path); hashes[manifest_path.name]=_sha(manifest_path)
        if (manifest['state']!='complete' or manifest['blocks_completed']!=count
            or manifest['effective_shots_completed']!=shots*count or len(manifest['blocks'])!=count):
            raise ValueError('Incomplete real acquisition block manifest')
        previous=[]
        for block,row in enumerate(manifest['blocks']):
            name=f'point_{point:04d}_block_{block:03d}.npz'; path=folder/name
            if row['block_index']!=block or row['block_file']!=name or row['shots_in_block']!=shots:
                raise ValueError('Block manifest identity/count mismatch')
            digest=_sha(path)
            if digest!=row['sha256']: raise ValueError('Block NPZ hash mismatch')
            hashes[name]=digest
            with np.load(_safe(path),allow_pickle=False) as f:
                i,q=f['i'].copy(),f['q'].copy()
                if i.shape!=q.shape or i.shape!=(1,shots): raise ValueError('Unexpected block raw shape')
                if not np.array_equal(f['acquisition_block_index'],np.full(shots,block)):
                    raise ValueError('Unexpected block index vector')
                if not np.array_equal(i+1j*q,iq[point,:,block*shots:(block+1)*shots]):
                    raise ValueError('Block contents differ from aggregate measured IQ')
            h=hashlib.sha256()
            for array in (i,q):
                h.update(array.dtype.str.encode('ascii'));h.update(json.dumps(list(array.shape)).encode('ascii'))
                h.update(np.ascontiguousarray(array).tobytes())
            if h.hexdigest()!=row['raw_iq_sha256']: raise ValueError('Block raw IQ hash mismatch')
            dup=[b for b,v in enumerate(previous) if np.array_equal(v,i+1j*q)]
            if dup: duplicate_points.append({'point':point,'block':block,'duplicates':dup})
            previous.append(i+1j*q)
    return index,{'verified':True,'point_count':len(iq),'blocks_per_point':count,
        'shots_per_block':shots,'effective_shots_per_point':shots*count,
        'blocks_are_not_outer_capture_replicates':True,'exact_duplicate_blocks':duplicate_points,
        'block_files_and_manifest_sha256':hashes}


def effective_reference(job,allxy_folder):
    original=deepcopy(job['calibration']['projection'])
    if original.get('independent_reference') is not True or not original.get('reference_contrast_repeated'):
        raise ValueError('A frozen, independently validated reference is required')
    report_path=_safe(original['source']); folder=_safe(Path(original['reference_raw']).parent)
    if folder==allxy_folder or Path(original['reference_raw']).name!='raw_scan.npz':
        raise ValueError('Reference must be a different acquired raw scan')
    report=_json(report_path)
    if report['projection']!=original: raise ValueError('Acquired job frozen projection differs from source report')
    axes,iq,refjob,hashes=_load_run(folder)
    if hashes['raw_scan.npz']!=original['reference_raw_sha256']:
        raise ValueError('Independent reference raw hash mismatch')
    if refjob['target']!=job['target']: raise ValueError('Reference target differs from AllXY')
    q='Q'+str(job['spec']['meta']['qubit']); rq='Q'+str(refjob['spec']['meta']['qubit'])
    for key in ('probFreq','probCarryScale','demodPointStart','demodPointLen','demodPhase','drivFreq'):
        if job['config'][q]['para'][key]!=refjob['config'][rq]['para'][key]:
            raise ValueError('Reference and AllXY physical parameter differ: '+key)
    for key in ('xy_delay_s','xy_attenuation_db','xy_physical_channel'):
        if job.get(key)!=refjob.get(key): raise ValueError('Reference and AllXY routing/gain differs: '+key)
    if job['config'][q]['gate']['U_gates']!=refjob['config'][rq]['gate']['U_gates']:
        raise ValueError('Reference full-X preparation differs from AllXY')
    if job['config']['ADC_Meas']!=refjob['config']['ADC_Meas']:
        raise ValueError('Reference readout/ADC timing differs from AllXY')
    train=list(original['training_captures']); validate=list(original['validation_captures'])
    if not train or set(train)&set(validate): raise ValueError('Invalid frozen reference capture split')
    centroids=[]
    for label in ('ground','excited'):
        clouds=[]
        for cap in train:
            indices=[i for i,a in enumerate(axes) if a['capture']==cap and a['axes'].get('exp.cir.default')==label]
            if len(indices)!=1: raise ValueError('Each training capture requires one ground/excited point')
            clouds.append(iq[indices[0],0,:])
        z=np.concatenate(clouds); centroids.append([float(z.real.mean()),float(z.imag.mean())])
    g,e=map(np.asarray,centroids); direction=np.asarray(original['direction'])
    midpoint=(g+e)/2
    if not np.allclose(midpoint,original['origin'],rtol=0,atol=1e-15):
        raise ValueError('Reconstructed training midpoint differs from frozen reference origin')
    contrast=float((e-g)@direction)
    if contrast<=0: raise ValueError('Reference projected contrast must be positive')
    reference={**original,'ground_iq':g.tolist(),'excited_iq':e.tolist(),
        'frequency_hz':job['config'][q]['para']['probFreq'],
        'amplitude':job['config'][q]['para']['probCarryScale'],
        'normalization_provenance':'Only original independent-reference training captures; original frozen LDA direction retained',
        'normalization_prepared_contrast':contrast}
    return reference,{'original_frozen_reference':original,'effective_prepared_iq_reference':reference,
        'reference_input_sha256':hashes,'reference_report_sha256':_sha(report_path),
        'reference_held_out_contrast_ci95_t':report['held_out_capture_contrast_ci95_t'],
        'reference_held_out_label_accuracy':report['held_out_prepared_label_accuracy_mean'],
        'reference_and_acquisition_readout_and_full_X_parameters_match':True,
        'source_files_modified':False,'allxy_data_used_for_reference':False}


def analyze(folder):
    folder=_safe(folder);axes,iq,job,hashes=_load_run(folder)
    blocks,block_proof=verify_real_blocks(folder,iq,job)
    reference,reference_proof=effective_reference(job,folder)
    effective=deepcopy(job);effective['calibration']['projection']=reference
    result,views=analyze_arrays(axes,iq,effective,acquisition_block_index=blocks)
    result.update(input_sha256=hashes,real_acquisition_block_verification=block_proof,
        reference_adapter=reference_proof,
        analysis_sources={name:_sha(ROOT/name) for name in ('analyze_allxy.py','analyze_historical_allxy.py','analyze_single_qubit.py','readout_reference_support.py')},
        pulse_compatibility=job['spec']['meta'].get('pulse_compatibility'))
    if blocks is not None: views['acquisition_block_index']=blocks
    return save_analysis(folder,result,views)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path)
    result=analyze(p.parse_args().folder)
    print(json.dumps({k:result[k] for k in ('target','rms_deviation_from_ideal','rms_by_capture',
        'level_summary','image')},ensure_ascii=False))
