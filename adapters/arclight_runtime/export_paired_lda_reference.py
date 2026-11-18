"""Offline export of separately acquired frozen LDA reference for paired RB.

Output is a new JSON; no source job/report/raw files are changed. Prepared-state
normalization is not a pure-state probability or verified readout fidelity.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).absolute().parent))
import argparse
import copy
import hashlib
import json
import numpy as np
from readout_reference_support import ROOT,_safe,_load_run


def build_reference(folder):
    folder=_safe(folder); path=_safe(folder/'independent_projection.json')
    report=json.loads(path.read_text(encoding='utf-8-sig'))
    frozen=copy.deepcopy(report['projection'])
    if frozen.get('independent_reference') is not True or not frozen.get('reference_contrast_repeated'):
        raise ValueError('Independent held-out-reference contrast was not validated')
    if _safe(frozen['source'])!=path or _safe(frozen['reference_raw'])!=folder/'raw_scan.npz':
        raise ValueError('Frozen reference source paths differ')
    axes,iq,job,hashes=_load_run(folder)
    if hashes['raw_scan.npz']!=frozen['reference_raw_sha256']:
        raise ValueError('Frozen independent reference raw hash mismatch')
    train=list(frozen['training_captures']); held=list(frozen['validation_captures'])
    if not train or not held or set(train)&set(held): raise ValueError('Invalid frozen capture split')
    means=[]
    for label in ('ground','excited'):
        clouds=[]
        for cap in train:
            points=[i for i,a in enumerate(axes) if a['capture']==cap and a['axes'].get('exp.cir.default')==label]
            if len(points)!=1: raise ValueError('Each training capture needs one ground/excited point')
            clouds.append(iq[points[0],0])
        z=np.concatenate(clouds);means.append(np.array([z.real.mean(),z.imag.mean()]))
    g,e=means; d=np.asarray(frozen['direction'],float)
    if d.shape!=(2,) or not np.isfinite(d).all() or not np.isclose(np.linalg.norm(d),1,atol=1e-10):
        raise ValueError('Frozen LDA direction must be a finite unit vector')
    if not np.allclose((g+e)/2,frozen['origin'],rtol=0,atol=1e-15):
        raise ValueError('Training centroid midpoint differs from frozen origin')
    contrast=float((e-g)@d)
    if contrast<=1e-15: raise ValueError('Fixed reference contrast must be positive')
    q='Q'+str(job['spec']['meta']['qubit']);para=job['config'][q]['para']
    projection={**frozen,'ground_iq':g.tolist(),'excited_iq':e.tolist(),
        'frequency_hz':para['probFreq'],'amplitude':para['probCarryScale'],
        'prepared_state_projected_contrast':contrast,
        'normalization_convention':'ground prepared reference=1, excited prepared reference=0, unclipped',
        'normalization_formula':'((excited_iq-IQ) dot frozen_direction)/((excited_iq-ground_iq) dot frozen_direction)',
        'normalization_centroids':'Only the original frozen training captures; never current RB/IRB or AllXY data',
        'reference_folder':str(folder),
        'interpretation':'Fixed prepared-reference IQ scale; not independently verified pure-state probability'}
    return {'schema':'arclight_independent_lda_paired_rb_reference_v1','target':job['target'],
        'paired_rb_projection':projection,'source_input_sha256':hashes,
        'source_report_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'export_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'reference_conditions':{'xy_physical_channel':job['xy_physical_channel'],
            'xy_attenuation_db':job.get('xy_attenuation_db',0),'xy_delay_s':job.get('xy_delay_s',0),
            'full_X':job['config'][q]['gate']['U_gates'],'drive_hz':para['drivFreq'],
            'readout_hz':para['probFreq'],'readout_amplitude':para['probCarryScale'],
            'ADC_Meas':job['config']['ADC_Meas']},
        'held_out_contrast_ci95_t':report['held_out_capture_contrast_ci95_t'],
        'held_out_prepared_label_accuracy':report['held_out_prepared_label_accuracy_mean'],
        'future_run_reference_reuse':'Historical independent reference for comparison; reacquire independent reference before future RB campaign as planned',
        'reference_uncertainty_in_RB_bootstrap':False,'source_files_modified':False}


def export(folder,output):
    output=_safe(output,exists=False)
    if output.exists(): raise FileExistsError(output)
    result=build_reference(folder)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();result=export(args.folder,args.output)
    print(json.dumps({'target':result['target'],'output':str(args.output),
        'prepared_state_contrast':result['paired_rb_projection']['prepared_state_projected_contrast']},ensure_ascii=False))
