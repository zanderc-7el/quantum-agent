"""Offline native paired RB schedule/algebra and paired-seed analysis.

No workspaces, drivers, VISA, instrument modules, or hardware entry points.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import warnings
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE=Path(__file__).resolve().parent


def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


PAIR=load_module('_qcal_native_pair',HERE/'f8_runtime/native_rb_pairing.py')
MATH=load_module('_qcal_native_rb_math',HERE/'f8_runtime/native_math/function.py')
FitClass=load_module('_qcal_native_rb_fit',HERE/'f8_runtime/native_math/fitclass.py').FitClass


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def clean(value):
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [clean(v) for v in value]
    if isinstance(value,np.ndarray):return clean(value.tolist())
    if isinstance(value,np.generic):return clean(value.item())
    if isinstance(value,float) and not np.isfinite(value):return None
    return value


def write(path,value):Path(path).write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def prepare(spec_path):
    spec=read(spec_path)
    plan=PAIR.make_plan(spec['q'],spec['lengths'],repeats=spec['repeats'],master_seed=spec['masterSeed'],native_source=spec['nativeSource'])
    plan.update(status='FROZEN_FOR_NATIVE_ACQUISITION',calibrationVersion=spec['calibrationVersion'])
    audit=PAIR.validate_plan(plan,spec['piLength'],xy_delay_s=spec['xyDelay'],prob_t0_s=spec['probT0'],waveform_length_s=spec['probT0']+6e-6)
    output=Path(spec['output']);write(output.with_name('frozen_rb_validation.json'),audit)
    if audit['decision']!='PASS':raise ValueError('Native paired algebra/time window validation failed: '+str(audit['errors']))
    write(output,plan)
    print(json.dumps({'status':'prepared','pairs':len(plan['rows']),'planHash':digest(plan)}))


def native_fit(x,y):
    x=np.asarray(x,dtype=float);y=np.asarray(y,dtype=float);fits=[]
    for p in (.45,.7,.9,.98):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                fit=FitClass((x,y),MATH._RB,p0=[float(y[0]-y[-1]),float(y[-1]),p],maxfev=5000)
            if fit.res[-1] in (1,2,3,4) and all(np.isfinite(v) for v in fit.params.values()) and 0<fit.params['p']<1:
                sse=float(np.sum((y-fit.func(x))**2));fits.append((sse,fit))
        except Exception:pass
    if not fits:raise ValueError('Native RB decay fit unresolved')
    fit=min(fits,key=lambda v:v[0])[1]
    den=float(np.sum((y-y.mean())**2));r2=1-float(np.sum((y-fit.func(x))**2))/den if den>0 else float('nan')
    return fit,r2


def raw_from_receipt(directory,job):
    receipt_path=directory/(job['id']+'_complete.json');receipt=read(receipt_path)
    if receipt.get('event')!='job_complete' or receipt.get('job')!=job['id']:raise ValueError('Missing unique completed job receipt')
    paths=[Path(p).resolve() for p in receipt['files'] if str(p).endswith('.qdat')]
    if len(paths)!=1 or not paths[0].is_relative_to(directory) or not paths[0].is_file():raise ValueError('Raw RB evidence outside operation or missing')
    raw=np.load(paths[0],allow_pickle=True)
    ps=raw['ParaSpace'];q=job['q'];cfg=ps['config'][q]
    for field,value in [('probFreq',job['readout_hz']),('probCarryScale',job['readout_amp']),('drivFreq',job['drive_hz'])]:
        if not np.isclose(cfg['para'][field],value,rtol=1e-12,atol=1e-12):raise ValueError('RB/readout configuration changed: '+field)
    for field,value in [('pi_len',job['drive_length_s']),('pi_amp',job['drive_amp'])]:
        if not np.isclose(cfg['gate']['U_gates'][field],value,rtol=1e-12,atol=1e-12):raise ValueError('Pulse configuration changed: '+field)
    bus=cfg['inst']['ADC_PROB']
    if ps['config']['ADC_Meas'][bus]['para']['shots']!=job['shots']:raise ValueError('Unexpected shots count')
    if ps['config']['ADC_Meas'][bus]['Multi_Readout']['Qubits']!=[q]:raise ValueError('Readout object mapping changed')
    if not np.isfinite(np.asarray(raw['data'][-1])).all():raise ValueError('Nonfinite native IQ')
    return raw,{'receipt':str(receipt_path),'raw':str(paths[0]),'rawSha256':hashlib.sha256(paths[0].read_bytes()).hexdigest()}


def collect_branches(directory,plan,frozen):
    result={name:{} for name in ('reference','interleaved')};sources=[];fingerprints=set();seen_raw=set()
    captures=plan['suiteOptions']['rb_capture_repeats'];lengths=frozen['lengths'];q=frozen['q']
    for job in plan['jobs']:
        if job['kind']!='rb_iq':continue
        raw,source=raw_from_receipt(directory,job);ps=raw['ParaSpace'];setting=ps['setting'];policy=setting['native_paired_rb']
        variant,index=job['rb_variant'],job['sequence_index']
        if policy['q']!=q or policy['variant']!=variant or policy['sequence_index']!=index or policy['plan_sha256']!=digest(frozen):raise ValueError('Frozen pairing identity mismatch')
        rep,axis,cap,z=map(np.asarray,raw['data'])
        if rep.tolist()!=[index] or axis.tolist()!=lengths or cap.tolist()!=list(range(captures)) or z.shape!=(1,len(lengths),captures,1):raise ValueError('RB axes/shape mismatch')
        if source['rawSha256'] in seen_raw or index in result[variant]:raise ValueError('Raw data or seed reused')
        expected={'readout_mode':'IQ','preprocess_mode':'original','shots_avg':True,'singleShot_mode':None,'populationConvert_mode':None}
        if any(setting.get(k)!=v for k,v in expected.items()):raise ValueError('Expected unclassified native complex IQ')
        cfg=ps['config'][q];bus=cfg['inst']['ADC_PROB']
        invariant={'qubit':cfg,'adc':ps['config']['ADC_Meas'][bus],'global':ps['config']['ADC_Meas']['global'],'policy':setting.get('F8_native_replication')}
        fingerprints.add(digest(clean(invariant)));seen_raw.add(source['rawSha256']);sources.append(source);result[variant][index]=z[0,:,:,0]
    if len(fingerprints)!=1:raise ValueError('Paired RB invariant physical/configuration settings changed')
    if any(set(v)!=set(range(30)) for v in result.values()):raise ValueError('Incomplete 30 paired seed set')
    return np.asarray([result['reference'][i] for i in range(30)]),np.asarray([result['interleaved'][i] for i in range(30)]),sources


def reference_states(directory,job):
    raw,source=raw_from_receipt(directory,job);labels=list(raw['info']['axis_label']);z=np.asarray(raw['data'][-1])
    if labels!=['index','CaptureRepeat'] or z.shape!=(2,job['capture_repeats'],job['shots'],1):raise ValueError('Expected state × independent captures × shots × qubit readout reference')
    return z[:,:,:,0],source


def summarize(ref,irb,lengths,pre,post,bootstrap_replicates=300,seed=20261117):
    """Frozen pre-only projection; whole paired seeds are bootstrap units."""
    x=np.asarray(lengths,dtype=float)
    # First two pre captures define direction; held-out pre2/3 and all post
    # captures monitor the same contrast, independent of RB selection/results.
    i_mean=complex(pre[0,:2].mean());x_mean=complex(pre[1,:2].mean());delta=x_mean-i_mean
    if abs(delta)<=1e-15:raise ValueError('Independent pre reference has zero contrast')
    direction=delta/abs(delta);origin=(i_mean+x_mean)/2
    reference_y=((ref.mean(axis=(0,2))-origin)*direction.conjugate()).real
    interleaved_y=((irb.mean(axis=(0,2))-origin)*direction.conjugate()).real
    metrics={};checks={};fit_a=fit_b=None;primary_error=None;boot=[]
    try:
        fit_a,r2a=native_fit(x,reference_y);fit_b,r2b=native_fit(x,interleaved_y)
        pa,pb=fit_a.params['p'],fit_b.params['p'];estimate=.5*(1+pb/pa)
        metrics.update(r_squared=min(r2a,r2b),reference_r_squared=r2a,interleaved_r_squared=r2b,clifford_fidelity=.5*(1+pa),reference_decay_p=pa,interleaved_decay_p=pb)
        checks['primaryPhysical']=0<=estimate<=1
        for name,fit in [('reference',fit_a),('interleaved',fit_b)]:
            stderr=fit.error.get('A');checks[name+'AmplitudeIdentifiable']=bool(stderr is not None and np.isfinite(stderr) and abs(fit.params['A'])>3*abs(stderr))
        rng=np.random.default_rng(seed)
        for _ in range(bootstrap_replicates):
            ix=rng.integers(0,len(ref),len(ref))
            a=((ref[ix].mean(axis=(0,2))-origin)*direction.conjugate()).real
            b=((irb[ix].mean(axis=(0,2))-origin)*direction.conjugate()).real
            try:
                fa,_=native_fit(x,a);fb,_=native_fit(x,b);boot.append([fa.params['p'],fb.params['p'],.5*(1+fb.params['p']/fa.params['p'])])
            except ValueError:pass
        ci=np.percentile(boot,[2.5,50,97.5],axis=0) if boot else None
        checks['bootstrapSuccess']=len(boot)>=.95*bootstrap_replicates
        checks['decayIntervalsInterior']=bool(ci is not None and np.all(ci[0,:2]>.02) and np.all(ci[2,:2]<.995))
        checks['decayWidthsBounded']=bool(ci is not None and np.all(ci[2,:2]-ci[0,:2]<.35))
        checks['gateIntervalPhysical']=bool(ci is not None and 0<=ci[0,2]<=ci[2,2]<=1 and ci[2,2]-ci[0,2]<.25)
        if all(checks.values()):
            metrics.update(gate_fidelity=estimate,gate_fidelity_ci_low=float(ci[0,2]),gate_fidelity_ci_high=float(ci[2,2]))
    except Exception as exc:
        primary_error=repr(exc);ci=None;checks['primaryFit']=False
    pre_contrasts=((pre[1,2:].mean(axis=1)-pre[0,2:].mean(axis=1))*direction.conjugate()).real
    post_contrasts=((post[1].mean(axis=1)-post[0].mean(axis=1))*direction.conjugate()).real
    pre_value,post_value=float(pre_contrasts.mean()),float(post_contrasts.mean())
    if pre_value>1e-15:metrics['reference_drift_fraction']=abs(post_value/pre_value-1)
    metrics.update(pre_reference_contrast=pre_value,post_reference_contrast=post_value,bootstrap_success_fraction=len(boot)/bootstrap_replicates,paired_seed_count=len(ref))
    return {'metrics':metrics,'publicationChecks':checks,'bootstrapCI':ci,'bootstrapSuccesses':len(boot),'bootstrapRequested':bootstrap_replicates,
            'primaryError':primary_error,'projection':{'direction':[direction.real,direction.imag],'origin':[origin.real,origin.imag],
                                                    'training':'pre captures 0/1 only','heldOutPre':'pre captures 2/3','post':'all independent post captures'},
            'series':[{'name':'Reference RB mean-IQ','x':lengths,'y':reference_y,'xUnit':'Cliffords','yUnit':'IQ'},
                      {'name':'X/2 interleaved RB mean-IQ','x':lengths,'y':interleaved_y,'xUnit':'Cliffords','yUnit':'IQ'}],
            'fitParameters':{'reference':fit_a.params if fit_a else None,'interleaved':fit_b.params if fit_b else None},
            'scope':{'estimate':'Conditional single-qubit IRB F=(1+p_interleaved/p_reference)/2; Clifford F=(1+p_reference)/2',
                     'statistics':'Whole paired seed and all lengths resampled together. Inner shots/captures are not additional random sequences.',
                     'limits':'Standard stationary approximately gate-independent Markov RB model; CI excludes unknown SPAM drift/model errors. Endpoint drift checks do not prove stationarity.',
                     'projection':'Mean complex IQ projected onto a pre-only frozen direction, free A/B. No weak confusion inversion, clipping, or fixed pulse-count conversion.'}}


def analyze(plan_path):
    plan_path=Path(plan_path).resolve();directory=plan_path.parent;plan=read(plan_path);frozen=read(directory/'frozen_rb.json')
    ref,irb,sources=collect_branches(directory,plan,frozen)
    pre_job=next(j for j in plan['jobs'] if j['id']==plan['readoutBefore']);post_job=next(j for j in plan['jobs'] if j['id']==plan['readoutAfter'])
    pre,pre_src=reference_states(directory,pre_job);post,post_src=reference_states(directory,post_job)
    result=summarize(ref,irb,frozen['lengths'],pre,post,plan['suiteOptions']['rb_bootstrap_replicates'],plan['suiteOptions']['rb_master_seed'])
    result.update(version=1,acquisition='new_measurement',sources=sources+[pre_src,post_src],calibrationVersion=plan['calibrationVersion'],frozenPlanHash=digest(frozen))
    output=directory/'rb_analysis';output.mkdir(exist_ok=True)
    plt.figure(figsize=(7.4,5));xx=np.linspace(min(frozen['lengths']),max(frozen['lengths']),400)
    for item,name in zip(result['series'],('reference','interleaved')):
        plt.plot(item['x'],item['y'],'o',label=item['name']);parameters=result['fitParameters'][name]
        if parameters:plt.plot(xx,MATH._RB(xx,**parameters),'-')
    plt.xlabel('Random Clifford count (excluding recovery)');plt.ylabel('Frozen projection of mean IQ');plt.title(frozen['q']+' · native paired RB / X/2 IRB');plt.legend();plt.tight_layout();plt.savefig(output/'rb_suite.png',dpi=180);plt.close('all')
    write(output/'rb_suite_analysis.json',result)
    print(json.dumps({'metrics':clean(result['metrics']),'checks':result['publicationChecks']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['prepare','analyze']);parser.add_argument('path');args=parser.parse_args()
    prepare(args.path) if args.command=='prepare' else analyze(args.path)
