"""Offline readout-grid selection and separately acquired IQ-reference export.

No Arclight/core/driver imports. Projection selection matches paired_rb_support's
fixed Euclidean ground/excited-centroid projection, rather than fitting RB data.
"""
from copy import deepcopy
from pathlib import Path
import argparse
import hashlib
import json
import math
import os
import stat
import numpy as np
from scipy.stats import norm

ROOT = Path(__import__('os').environ.get('QCAL_ARCLIGHT_RUN_DIR',str(Path(__file__).absolute().parent))).absolute()


def _integer(x, name):
    if isinstance(x, bool) or not isinstance(x, (int,np.integer)) or x < 0:
        raise ValueError(name+' must be a nonnegative integer')
    return int(x)


def _clouds(records, iq, *, fixed_setting=None):
    z = np.asarray(iq)
    if z.ndim == 3 and z.shape[1] == 1:
        z = z[:,0,:]
    if z.ndim != 2 or z.shape[0] != len(records) or z.shape[1] < 64:
        raise ValueError('Require complex IQ [points,1,shots>=64] or [points,shots>=64]')
    if not np.iscomplexobj(z) or not np.isfinite(z).all():
        raise ValueError('IQ must be finite complex shot data')
    data, bases, cap_bases = {}, {}, {}
    for record, row in zip(records,z):
        capture = _integer(record['capture'],'capture')
        base = _integer(record['base_index'],'base_index')
        a = record['axes']
        state = str(a['exp.cir.default']).lower()
        if state not in ('ground','excited'):
            raise ValueError('Expected ground/excited preparation labels')
        fkeys = [k for k in a if k.startswith('cfg.') and k.endswith('.para.probFreq')]
        akeys = [k for k in a if k.startswith('cfg.') and k.endswith('.para.probCarryScale')]
        if len(fkeys) == len(akeys) == 1:
            setting = (float(a[fkeys[0]]),float(a[akeys[0]]))
        elif not fkeys and not akeys and fixed_setting is not None:
            setting = tuple(map(float,fixed_setting))
        else:
            raise ValueError('Cannot uniquely identify readout frequency and amplitude')
        if not np.isfinite(setting).all() or setting[0] <= 0 or not 0 < setting[1] <= 1:
            raise ValueError('Invalid readout setting')
        extras = set(a)-set(fkeys)-set(akeys)-{'exp.cir.default'}
        if extras:
            raise ValueError('Unrecognized varying axes: '+','.join(sorted(extras)))
        key = (*setting,capture,state)
        if key in data:
            raise ValueError('Duplicate (frequency,amplitude,capture,state)')
        token = (*setting,state)
        if base in bases and bases[base] != token:
            raise ValueError('base_index changes meaning across captures')
        if base in cap_bases.setdefault(capture,set()):
            raise ValueError('Repeated base_index within capture')
        cap_bases[capture].add(base); bases[base] = token
        data[key] = np.column_stack([row.real,row.imag])
    settings = sorted({k[:2] for k in data})
    captures = sorted(cap_bases)
    if len(captures) < 2:
        raise ValueError('At least two separately acquired captures required')
    required = {(*s,c,t) for s in settings for c in captures for t in ('ground','excited')}
    if set(data) != required:
        raise ValueError('Incomplete setting/capture/state coverage')
    if any(v != cap_bases[captures[0]] for v in cap_bases.values()):
        raise ValueError('Capture base-index coverage differs')
    return data,settings,captures


def _mean_sem(y):
    """Conservative max of IID SEM and contiguous-block SEM; not a stationarity proof."""
    y = np.asarray(y,float)
    iid = float(y.std(ddof=1)/np.sqrt(len(y)))
    count = min(32,max(4,len(y)//32))
    means = np.array([v.mean() for v in np.array_split(y,count)])
    block = float(means.std(ddof=1)/np.sqrt(count))
    return max(iid,block,1e-30), {'iid_sem':iid,'block_sem':block,'block_count':count}


def _train(g,e):
    difference = e.mean(axis=0)-g.mean(axis=0)
    if np.linalg.norm(difference) <= 1e-20:
        raise ValueError('Zero prepared-state training contrast')
    direction = difference/np.linalg.norm(difference)
    threshold = float((g.mean(axis=0)+e.mean(axis=0))@direction/2)
    return direction,threshold


def _wilson(success,n,z=1.96):
    p=success/n; scale=1+z*z/n
    mid=(p+z*z/(2*n))/scale
    half=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/scale
    return [float(mid-half),float(mid+half)]


def _evaluate(g,e,direction,threshold,zscore):
    pg,pe = g@direction,e@direction
    sg,meta_g = _mean_sem(pg); se,meta_e = _mean_sem(pe)
    delta=float(pe.mean()-pg.mean()); sem=float(np.hypot(sg,se))
    sigma=float(np.sqrt((pg.var(ddof=1)+pe.var(ddof=1))/2))
    sigma=max(sigma,1e-30)
    n0,n1=len(pg),len(pe)
    c0,c1=int((pg<=threshold).sum()),int((pe>threshold).sum())
    ci0,ci1=_wilson(c0,n0),_wilson(c1,n1)
    return {'ground_mean':float(pg.mean()),'excited_mean':float(pe.mean()),
        'ground_sem':sg,'excited_sem':se,'signed_contrast':delta,'contrast_sem':sem,
        'signed_contrast_z':delta/sem,'signed_dprime':delta/sigma,
        'signed_dprime_lower_bound':(delta-zscore*sem)/sigma,
        'single_shot_pooled_std':sigma,'direction':direction.tolist(),'threshold':threshold,
        'ground_sem_diagnostics':meta_g,'excited_sem_diagnostics':meta_e,
        'prepared_label_assignment_accuracy':(c0/n0+c1/n1)/2,
        'P_assign_ground_given_prep_ground':c0/n0,'P_assign_excited_given_prep_excited':c1/n1,
        'ci95_ground_assignment_iid_wilson':ci0,'ci95_excited_assignment_iid_wilson':ci1,
        'assignment_ci_assumption':'Conditional IID shots; not a confidence interval for pure-state readout fidelity',
        'shots_by_prepared_label':[n0,n1]}


def _cross_capture(data,setting,captures,zscore):
    rows=[]; own_directions=[]
    for capture in captures:
        train_g=np.concatenate([data[*setting,c,'ground'] for c in captures if c!=capture])
        train_e=np.concatenate([data[*setting,c,'excited'] for c in captures if c!=capture])
        g,e=data[*setting,capture,'ground'],data[*setting,capture,'excited']
        direction,threshold=_train(train_g,train_e)
        own_direction,_=_train(g,e); own_directions.append(own_direction)
        result=_evaluate(g,e,direction,threshold,zscore)
        rows.append({'validation_capture':capture,'training_captures':[c for c in captures if c!=capture],**result})
    cosine=min(float(a@b) for i,a in enumerate(own_directions) for b in own_directions[i+1:])
    # Common axis enables a noise-aware comparison of centroid drift across captures.
    all_g=np.concatenate([data[*setting,c,'ground'] for c in captures])
    all_e=np.concatenate([data[*setting,c,'excited'] for c in captures])
    direction,threshold=_train(all_g,all_e)
    stats=[_evaluate(data[*setting,c,'ground'],data[*setting,c,'excited'],direction,threshold,zscore) for c in captures]
    drift=0.0
    for j,a in enumerate(stats):
        for b in stats[j+1:]:
            for label in ('ground','excited'):
                drift=max(drift,abs(a[label+'_mean']-b[label+'_mean'])/max(np.hypot(a[label+'_sem'],b[label+'_sem']),1e-30))
    return rows,cosine,float(drift),stats


def select_readout_grid(records,iq,*,source,alpha=.05):
    """Rank all grid points for a NEW reference run, never claim selection independence."""
    if not source or not 0<alpha<1:
        raise ValueError('Source identifier and valid alpha required')
    data,settings,captures=_clouds(records,iq)
    frequencies=sorted({s[0] for s in settings}); amplitudes=sorted({s[1] for s in settings})
    if len(settings)!=len(frequencies)*len(amplitudes):
        raise ValueError('Readout grid is not a complete frequency/amplitude Cartesian grid')
    # Two-sided Bonferroni over settings x validation captures. The dprime bound
    # treats its scale estimate as fixed; raw contrast is the primary statistic.
    zscore=float(norm.ppf(1-alpha/(2*len(settings)*len(captures))))
    rows=[]
    for setting in settings:
        try:
            fold,cosine,drift,_=_cross_capture(data,setting,captures,zscore)
            lower=min(r['signed_dprime_lower_bound'] for r in fold)
            stable=lower>0 and cosine>=.5 and drift<=4
            rows.append({'frequency_hz':setting[0],'amplitude':setting[1],
                'capture_validation':fold,'min_direction_cosine':cosine,
                'max_centroid_drift_z':drift,'worst_capture_dprime_lower_bound':lower,
                'stable_grid_candidate':bool(stable),'quality_reasons':
                ([] if lower>0 else ['contrast_not_resolved_in_every_capture'])+
                ([] if cosine>=.5 else ['projection_direction_not_stable'])+
                ([] if drift<=4 else ['centroid_drift_exceeds_four_standard_errors'])})
        except ValueError as exc:
            rows.append({'frequency_hz':setting[0],'amplitude':setting[1],
                'capture_validation':[],'worst_capture_dprime_lower_bound':None,
                'stable_grid_candidate':False,'quality_reasons':[str(exc)]})
    rows.sort(key=lambda r:(r['stable_grid_candidate'],
        r['worst_capture_dprime_lower_bound'] if r['worst_capture_dprime_lower_bound'] is not None else -float('inf'),
        -r['amplitude']),reverse=True)
    best=rows[0]
    return {'schema':'arclight_readout_grid_selection_v1','source':str(source),
        'readout_frequency_hz':best['frequency_hz'],'amplitude':best['amplitude'],
        'candidate':{'frequency_hz':best['frequency_hz'],'amplitude':best['amplitude'],
                     'stable_grid_candidate':best['stable_grid_candidate']},
        'status':'candidate_requires_fresh_reference','independent_projection_exported':False,
        'settings_count':len(settings),'captures':captures,'ranking':rows,
        'selection_rule':'Prefer stable candidates; maximize worst-capture signed dprime lower bound; amplitude is final tie breaker',
        'simultaneous_contrast_z_threshold':zscore,'family_alpha':alpha,
        'multiple_comparison_method':'Bonferroni over grid points and held-out captures; normal approximation with max(IID,block) SEM',
        'interpretation':'Cross-capture statistics used for selection are exploratory; final validation and projection require a fresh acquired reference.',
        'label_note':'Prepared ground/excited labels include preparation error; assignment accuracy is not gate fidelity or pure-state readout fidelity.'}


def make_reference_job(grid_job,selection,*,captures=4,shots=2047,name=None):
    """Return a copy only: fixed selected frequency/amplitude, fresh paired preparations."""
    if not 2<=captures<=12 or not 64<=shots<=2047:
        raise ValueError('Require 2..12 captures and 64..2047 shots')
    job=deepcopy(grid_job); candidate=selection['candidate']
    q='Q'+str(job['spec']['meta']['qubit'])
    job['config'][q]['para'].update(probFreq=candidate['frequency_hz'],
        probCarryFreq=candidate['frequency_hz'],probCarryScale=candidate['amplitude'])
    job['spec']['sweeps']=[s for s in job['spec']['sweeps'] if s.get('bindings')=='exp.cir.default']
    if len(job['spec']['sweeps'])!=1 or set(job['spec']['sweeps'][0]['values'])!={'ground','excited'}:
        raise ValueError('Expected exactly one ground/excited preparation sweep')
    job.update(name=name or str(job['target'])+'_fresh_readout_reference',captures=captures,
        kind='readout',randomize=True,order_seed=2026111702,
        reference_provenance={'selection_source':selection['source'],
            'selected_setting':candidate,'purpose':'Independent IQ reference for future experiments'})
    job['spec']['meta']['shots']=shots
    job.pop('calibration',None)
    return job


def _capture_aggregate_sem(samples,means):
    pooled=np.concatenate(samples)
    within=_mean_sem(pooled)[0]
    between=float(np.std(means,ddof=1)/np.sqrt(len(means)))
    return max(within,between,1e-30)


def export_fresh_reference(records,iq,job,selection,*,source):
    """Export only after fresh-data contrast and capture stability checks pass."""
    if not source or str(source)==str(selection['source']):
        raise ValueError('Fresh reference source must differ from grid-selection source')
    if job.get('reference_provenance',{}).get('selection_source')!=selection['source']:
        raise ValueError('Reference job must record the selected grid source')
    q='Q'+str(job['spec']['meta']['qubit']); para=job['config'][q]['para']
    setting=(float(para['probFreq']),float(para['probCarryScale']))
    chosen=selection['candidate']
    if abs(setting[0]-chosen['frequency_hz'])>1 or abs(setting[1]-chosen['amplitude'])>1e-12:
        raise ValueError('Fresh reference setting differs from selected setting')
    data,settings,captures=_clouds(records,iq,fixed_setting=setting)
    if settings!=[setting]:
        raise ValueError('Fresh reference must measure one fixed setting')
    zscore=float(norm.ppf(1-.05/(2*len(captures))))
    try:
        fold,cosine,drift,fixed_stats=_cross_capture(data,setting,captures,zscore)
    except ValueError as exc:
        return {'schema':'arclight_fresh_readout_reference_v1','source':str(source),
            'selection_source':selection['source'],'target':job['target'],
            'accepted':False,'reasons':[str(exc)],'projection':None,
            'calibration':None,'paired_rb_projection':None}
    gs=[data[*setting,c,'ground'] for c in captures]
    es=[data[*setting,c,'excited'] for c in captures]
    g,e=np.concatenate(gs),np.concatenate(es)
    gc,ec=g.mean(axis=0),e.mean(axis=0)
    direction,threshold=_train(g,e)
    gp,ep=g@direction,e@direction
    contrast=float((ec-gc)@direction)
    sg=_capture_aggregate_sem([s@direction for s in gs],[s.mean(axis=0)@direction for s in gs])
    se=_capture_aggregate_sem([s@direction for s in es],[s.mean(axis=0)@direction for s in es])
    contrast_sem=float(np.hypot(sg,se)); contrast_z=contrast/contrast_sem
    reasons=[]
    if contrast_z<5: reasons.append('independent_reference_contrast_below_five_standard_errors')
    if any(f['signed_dprime_lower_bound']<=0 for f in fold): reasons.append('reference_not_resolved_in_every_capture')
    if cosine<.5: reasons.append('reference_direction_not_stable')
    if drift>4: reasons.append('reference_centroid_drift')
    sigma=float(max(gp.std(ddof=1),ep.std(ddof=1)))
    normalized_sigma=sigma/contrast
    dprime=float(contrast/np.sqrt((gp.var(ddof=1)+ep.var(ddof=1))/2))
    budgets={str(err):math.ceil((normalized_sigma/err)**2) for err in (.1,.05,.02)}
    comparison_shots={str(delta):math.ceil(2*(3*normalized_sigma/delta)**2) for delta in (.5,.2,.1)}
    projection=None
    if not reasons:
        projection={'ground_iq':gc.tolist(),'excited_iq':ec.tolist(),
            'direction':direction.tolist(),'origin':gc.tolist(),
            'independent_reference':True,'source':str(source),
            'frequency_hz':setting[0],'amplitude':setting[1],
            'prepared_labels_not_verified_pure_states':True,
            'selection_source':selection['source'],
            'calibration_contrast_relative_sem':contrast_sem/contrast,
            'interpretation':'Fixed linear prepared-label IQ scale; not independently calibrated pure-state population'}
    return {'schema':'arclight_fresh_readout_reference_v1','source':str(source),
        'selection_source':selection['source'],'target':job['target'],
        'accepted':not reasons,'reasons':reasons,'projection':projection,
        'calibration':{'projection':projection} if projection is not None else None,
        'paired_rb_projection':projection,
        'frequency_hz':setting[0],'amplitude':setting[1],'captures':captures,
        'ground_iq_diagnostic':gc.tolist(),'excited_iq_diagnostic':ec.tolist(),
        'capture_cross_validation':fold,'min_direction_cosine':cosine,
        'max_centroid_drift_z':drift,'contrast_z':contrast_z,
        'contrast_relative_sem':contrast_sem/contrast,'pooled_dprime':dprime,
        'statistics':{'normalized_single_shot_std_max':normalized_sigma,
            'normalized_mean_sem_at_2047_shots':normalized_sigma/np.sqrt(2047),
            'normalized_mean_sem_at_12x2047_shots_iid':normalized_sigma/np.sqrt(12*2047),
            'shots_per_point_for_normalized_mean_sem_iid':budgets,
            'shots_per_point_for_two_point_three_sigma_difference_iid':comparison_shots,
            'calibration_midpoint_sem_floor_approx':float(.5*np.hypot(sg,se)/contrast),
            'interpretation':'IID shot budgets are lower estimates if correlated drift persists; fixed-calibration RB bootstrap excludes this calibration uncertainty.'},
        'label_note':'Held-out prepared-label assignment includes X-preparation and readout errors. Low assignment accuracy can coexist with detectable averaged IQ contrast.'}


def _safe(path,*,exists=True):
    path=Path(os.path.abspath(path))
    if not path.is_relative_to(ROOT): raise ValueError('Only current task-root paths are allowed')
    chain=[ROOT]
    for part in path.relative_to(ROOT).parts: chain.append(chain[-1]/part)
    for p in chain:
        try:
            info=p.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
            raise ValueError('Linked paths are not allowed')
    if exists and not path.exists(): raise FileNotFoundError(path)
    return path


def _load_run(folder):
    folder=_safe(folder)
    paths={name:_safe(folder/name) for name in ('status.json','job.json','axes.json','raw_scan.npz')}
    status=json.loads(paths['status.json'].read_text(encoding='utf-8-sig'))
    if status.get('mode')!='real_hardware' or status.get('state')!='acquired':
        raise ValueError('Only complete acquired real-hardware runs can be selected/exported')
    axes=json.loads(paths['axes.json'].read_text(encoding='utf-8-sig'))
    job=json.loads(paths['job.json'].read_text(encoding='utf-8-sig'))
    with np.load(paths['raw_scan.npz'],allow_pickle=False) as f:
        i,q=f['i'].copy(),f['q'].copy()
    if i.shape!=q.shape or any(status.get(k,len(axes))!=len(axes) for k in ('points_expected','points_completed')):
        raise ValueError('Raw arrays or status point counts mismatch')
    for j in range(len(axes)):
        with np.load(_safe(folder/f'point_{j:04d}.npz'),allow_pickle=False) as f:
            if not np.array_equal(f['i'],i[j]) or not np.array_equal(f['q'],q[j]):
                raise ValueError('Per-point file differs from aggregate raw data')
    hashes={name:hashlib.sha256(p.read_bytes()).hexdigest() for name,p in paths.items()}
    return axes,i+1j*q,job,hashes


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['select','export']); p.add_argument('folder',type=Path)
    p.add_argument('--selection',type=Path); p.add_argument('--output',type=Path)
    p.add_argument('--reference-job',type=Path)
    p.add_argument('--reference-captures',type=int,default=6)
    p.add_argument('--reference-shots',type=int,default=2047)
    a=p.parse_args(); folder=_safe(a.folder)
    records,iq,job,hashes=_load_run(folder)
    if a.mode=='select':
        result=select_readout_grid(records,iq,source=str(folder))
        output=a.output or folder/'readout_grid_selection.json'
        if a.reference_job:
            path=_safe(a.reference_job,exists=False)
            if path.exists(): raise FileExistsError(path)
            path.write_text(json.dumps(make_reference_job(job,result,captures=a.reference_captures,
                shots=a.reference_shots,name=path.stem),indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    else:
        if not a.selection: p.error('--selection is required for export')
        selection=json.loads(_safe(a.selection).read_text(encoding='utf-8-sig'))
        result=export_fresh_reference(records,iq,job,selection,source=str(folder))
        if selection.get('input_sha256',{}).get('raw_scan.npz')==hashes['raw_scan.npz']:
            raise ValueError('Fresh reference reuses identical grid raw data')
        output=a.output or folder/'fresh_readout_reference.json'
    result['input_sha256']=hashes
    result['analysis_source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    output=_safe(output,exists=False)
    if output.exists(): raise FileExistsError(output)
    output.write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    print(output)


if __name__=='__main__': main()
