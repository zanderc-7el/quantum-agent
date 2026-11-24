"""Freeze an independently acquired IQ direction; offline, no instrument imports."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.stats import t

ROOT=Path(__import__('os').environ.get('QCAL_ARCLIGHT_RUN_DIR',str(Path(__file__).absolute().parent))).absolute()

def read(p): return json.loads(p.read_text(encoding='utf8'))
def sha(p):
    with p.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,d): p.write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')

def _attach_plan(plan, report):
    if plan is None:return
    plan=Path(plan).absolute()
    if not plan.is_relative_to(ROOT/'plans'):raise ValueError('Only this task plans may be changed.')
    # Re-read at mutation, preserving current configuration edits.
    p=read(plan)
    assert p['target']==report['target'] and p['kind']=='rabi_time'
    for candidate in (ROOT/'results').glob('*_'+p['name']):
        if (candidate/'status.json').exists():raise RuntimeError('Target plan has already been used; refusing to edit it.')
    p.setdefault('calibration',{})['projection']=report['projection']
    save(plan,p)

def freeze(folder, plan=None):
    """Return frozen report; optionally attach its direction to an unused Rabi plan.

    With no plan this is a reference-only operation, suitable before RB or any
    subsequent independent acquisition. Existing reports are verified and reused,
    never overwritten.
    """
    folder=Path(folder).absolute()
    if not folder.is_relative_to(ROOT/'results'):raise ValueError('Only task results are accepted.')
    status=read(folder/'status.json');job=read(folder/'job.json');axes=read(folder/'axes.json')
    assert status['mode']=='real_hardware' and status['state']=='acquired'
    assert status.get('hardware_connected') is False
    destination=folder/'independent_projection.json'
    if destination.exists():
        existing=read(destination)
        for item in existing['sources']:
            p=Path(item['path'])
            if p.parent!=folder or sha(p)!=item['sha256']:
                raise ValueError('Existing frozen reference source changed; refusing to rewrite.')
        _attach_plan(plan,existing)
        return existing
    raw=np.load(folder/'raw_scan.npz',allow_pickle=False)
    iq=np.asarray(raw['i'][:,0,:])+1j*np.asarray(raw['q'][:,0,:])
    assert len(iq)==len(axes) and np.isfinite(iq).all()
    caps=sorted({a['capture'] for a in axes});assert len(caps)>=6
    traincaps=caps[:len(caps)//2];testcaps=caps[len(caps)//2:]
    labels=np.array([a['axes']['exp.cir.default'] for a in axes]);captures=np.array([a['capture'] for a in axes])
    clouds=[]
    for label in ('ground','excited'):
        z=iq[(labels==label)&np.isin(captures,traincaps)].reshape(-1)
        clouds.append(np.column_stack((z.real,z.imag)))
    c0,c1=clouds;cov=(np.cov(c0.T)+np.cov(c1.T))/2
    cov+=np.eye(2)*max(np.trace(cov)*1e-8,1e-30)
    direction=np.linalg.solve(cov,c1.mean(axis=0)-c0.mean(axis=0));direction/=np.linalg.norm(direction)
    origin=(c0.mean(axis=0)+c1.mean(axis=0))/2
    v0,v1=c0@direction,c1@direction
    values=np.sort(np.r_[v0,v1]);thresholds=(values[1:]+values[:-1])/2
    accuracy=(np.searchsorted(np.sort(v0),thresholds,side='right')/len(v0)+1-np.searchsorted(np.sort(v1),thresholds,side='right')/len(v1))/2
    threshold=float(thresholds[np.argmax(accuracy)])
    rows=[]
    for cap in caps:
        ps=[]
        for label in ('ground','excited'):
            ids=np.flatnonzero((labels==label)&(captures==cap));assert len(ids)==1
            z=iq[ids[0]];ps.append(z.real*direction[0]+z.imag*direction[1])
        p0,p1=ps;delta=float(p1.mean()-p0.mean());sem=float(np.sqrt(p0.var(ddof=1)/len(p0)+p1.var(ddof=1)/len(p1)))
        rows.append({'capture':cap,'role':'training' if cap in traincaps else 'held_out_capture',
                     'ground_mean':float(p0.mean()),'excited_mean':float(p1.mean()),'contrast':delta,
                     'shot_iid_contrast_sem':sem,'contrast_shot_sigma':delta/sem,
                     'snr_dprime':delta/np.sqrt((p0.var(ddof=1)+p1.var(ddof=1))/2),
                     'prepared_label_accuracy':float((np.mean(p0<=threshold)+np.mean(p1>threshold))/2)})
    held=[r for r in rows if r['role']=='held_out_capture'];ds=np.array([r['contrast'] for r in held]);se=float(ds.std(ddof=1)/np.sqrt(len(ds)));half=float(t.ppf(.975,len(ds)-1)*se)
    ci=[float(ds.mean()-half),float(ds.mean()+half)]
    valid=ci[0]>0 and all(d>0 for d in ds)
    projection={'direction':direction.tolist(),'origin':origin.tolist(),'independent_reference':True,
                'source':str(folder/'independent_projection.json'),'reference_raw':str(folder/'raw_scan.npz'),
                'reference_raw_sha256':sha(folder/'raw_scan.npz'),
                'method':'LDA direction trained only on first half of separately acquired reference captures; frozen before subsequent measurement',
                'training_captures':traincaps,'validation_captures':testcaps,
                'reference_contrast_repeated':bool(valid),'interpretation':'Projected IQ (a.u.), not probability or pure-state fidelity'}
    report={'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'target':job['target'],
            'projection':projection,'capture_results':rows,'training_threshold':threshold,
            'held_out_capture_mean_contrast':float(ds.mean()),'held_out_capture_contrast_sem':se,
            'held_out_capture_contrast_ci95_t':ci,'held_out_contrast_positive_and_repeat_consistent':bool(valid),
            'held_out_prepared_label_accuracy_mean':float(np.mean([r['prepared_label_accuracy'] for r in held])),
            'limitations':['Prepared-state labels include preparation and readout errors; assignment accuracy is not pure-state or gate fidelity.',
                           f'Capture-level t interval uses {len(testcaps)} independent held-out captures and assumes stable independent capture means.',
                           'Projection is frozen before subsequent measurement; reference validity does not establish calibrated gates or measurement fidelity.'],
            'sources':[{ 'path':str(folder/n),'sha256':sha(folder/n)} for n in ('job.json','axes.json','raw_scan.npz','status.json')]}
    save(destination,report)
    _attach_plan(plan,report)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder');p.add_argument('plan',nargs='?')
    p.add_argument('--reference-only',action='store_true');a=p.parse_args()
    if a.reference_only and a.plan:p.error('--reference-only does not accept a plan.')
    if not a.reference_only and not a.plan:p.error('Supply a plan or --reference-only.')
    report=freeze(a.folder,None if a.reference_only else a.plan)
    print(json.dumps({'target':report['target'],'plan':a.plan,'direction':report['projection']['direction'],
                      'held_out_contrast_ci95':report['held_out_capture_contrast_ci95_t'],
                      'repeat_consistent':report['held_out_contrast_positive_and_repeat_consistent'],
                      'held_out_prepared_label_accuracy':report['held_out_prepared_label_accuracy_mean'],
                      'reference_report':str(Path(a.folder)/'independent_projection.json')},ensure_ascii=False))
