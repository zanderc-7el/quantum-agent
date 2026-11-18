"""Offline, source-pinned F8 mathematical analysis; no instrument imports.

Raw qdat is only accepted from the controller's trusted acquisition receipt or
an explicit offline test. Native mathematical functions are copied unchanged.
Metrics describe a curve; a controller must obtain independent fresh validation.
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

MATH = Path(__file__).resolve().parent / 'f8_runtime/native_math'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


FitClass = module('_f8_pinned_fitclass', MATH / 'fitclass.py').FitClass
functions = module('_f8_pinned_functions', MATH / 'function.py')


def clean(value):
    if isinstance(value, dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):return [clean(v) for v in value]
    if isinstance(value, np.ndarray):return clean(value.tolist())
    if isinstance(value, np.generic):return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):return None
    return value


def noise(values):
    values = np.asarray(values, dtype=float)
    d = np.diff(values, n=2)
    if len(d) < 3:return float('nan')
    return max(float(np.median(np.abs(d-np.median(d)))/.67448975/np.sqrt(6)), 1e-15)


def fit_curve(x, y, kind):
    """Use original F8 FitClass and original formula, with explicit model label."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    order = np.argsort(x); x, y = x[order], y[order]
    if len(x) < 12 or np.any(np.diff(x) <= 0):
        return None, {'fit_identifiable':False, 'reason':'too few unique samples'}
    span = float(np.ptp(x)); amp = max(float(np.ptp(y))/2, 1e-15); baseline = float(np.mean(y))
    exponential = kind in ('t1','cpmg')
    if exponential:
        model = functions._T1
        seeds = [dict(A=float(y[0]-y[-1]), B=float(y[-1]), T1=t) for t in [span/10,span/3,span,2*span]]
    else:
        grid = np.linspace(.5/span, .45/float(np.median(np.diff(x))), 700)
        strength = np.abs(np.exp(-2j*np.pi*grid[:,None]*x)@(y-y.mean()))
        fs = []
        for i in np.argsort(strength)[::-1]:
            if all(abs(grid[i]-f)>.5/span for f in fs):fs.append(float(grid[i]))
            if len(fs)==3:break
        model = functions._Rabi if kind in ('rabi','power_rabi') else functions._ExpRamsey
        freq_key, tau_key = ('Omega','Tr') if kind in ('rabi','power_rabi') else ('Delta','T2')
        seeds = [dict(A=amp,B=baseline,phi=phi,**{freq_key:f,tau_key:tau}) for f in fs for phi in [0,np.pi] for tau in [span/3,span,3*span]]
    accepted = []
    for seed in seeds:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                fit = FitClass((x,y), model, p0=seed, maxfev=5000)
            if int(fit.res[-1]) not in (1,2,3,4) or not all(np.isfinite(v) for v in fit.params.values()):continue
            tau = fit.params['T1' if exponential else tau_key]
            if not 0 < tau < 100*span:continue
            if not exponential and not .5/span <= abs(fit.params[freq_key]) < .5/float(np.median(np.diff(x))):continue
            residual = y-fit.func(x); sse=float(np.sum(residual**2))
            accepted.append((sse,fit))
        except Exception:continue
    if not accepted:return None, {'fit_identifiable':False, 'reason':'native fits nonconvergent or outside model domain'}
    _, fit=min(accepted,key=lambda pair:pair[0]); residual=y-fit.func(x)
    r2=1-float(np.sum(residual**2))/max(float(np.sum((y-y.mean())**2)),1e-30)
    residual_sd=max(float(np.std(residual,ddof=min(len(fit.params),len(x)-1))),1e-15)
    tau_key = 'T1' if exponential else tau_key
    tau = float(fit.params[tau_key]); stderr=fit.error.get(tau_key)
    metric={'r_squared':r2,'contrast_snr':float(np.ptp(fit.func(x)))/residual_sd}
    if stderr is not None and np.isfinite(stderr):metric['relative_stderr']=abs(float(stderr)/tau)
    if exponential:
        metric['t1_s' if kind=='t1' else 't2_cpmg_s']=tau*1e-6
    else:
        frequency=abs(float(fit.params[freq_key])); metric['cycles']=frequency*span
        metric['rabi_frequency_hz' if kind in ('rabi','power_rabi') else 'oscillation_frequency_hz']=frequency*1e6 if kind!='power_rabi' else frequency
        if kind=='rabi':metric['pi_halfperiod_s']=.5/frequency*1e-6
        if kind in ('ramsey','echo'):metric['t2_exp_s']=tau*1e-6
    return fit, {'metrics':metric,'fit_identifiable':True,'nativeModel':model.__name__,'nativeParameters':fit.params,'nativeStandardErrors':fit.error,
                 'modelScope':'CPMG exponential envelope uses original F8 _T1 mathematical form with total-free-time axis' if kind=='cpmg' else 'Explicit F8 exponential Ramsey model; not the Gaussian fixed-T1 wrapper' if kind in ('ramsey','echo') else 'Original F8 mathematical model; half-period is a pulse seed, not gate fidelity'}


def assignment(states):
    """Fixed train/test split; direction/threshold only from training shots."""
    a,b = [np.asarray(v).ravel() for v in states]
    if min(len(a),len(b))<20:return {}, None
    at,av=a[::2],a[1::2];bt,bv=b[::2],b[1::2]
    vector=bt.mean()-at.mean()
    if abs(vector)<=1e-15:return {}, None
    direction=vector/abs(vector); threshold=float(((at.mean()+bt.mean())/2*direction.conjugate()).real)
    f0=float(np.mean((av*direction.conjugate()).real < threshold))
    f1=float(np.mean((bv*direction.conjugate()).real >= threshold))
    sigma=float(np.sqrt((np.var((at*direction.conjugate()).real)+np.var((bt*direction.conjugate()).real))/2))
    return {'assignment_fidelity':(f0+f1)/2,'assignment_fidelity_0':f0,'assignment_fidelity_1':f1,
            'training_snr':float(abs(vector)/max(sigma,1e-15))}, (direction,threshold)


def analyze(raw_path, job, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    raw_path=Path(raw_path);raw=np.load(raw_path,allow_pickle=True)
    if isinstance(raw,np.ndarray) and raw.shape==():raw=raw.item()
    data=raw['data'];ps=raw['ParaSpace'];axes=[np.asarray(x) for x in data[:-1]];labels=list(raw['info']['axis_label']);z=np.asarray(data[-1])
    if not np.isfinite(z).all():raise ValueError('Raw acquisition contains nonfinite IQ')
    if job['q'] not in ps['config']:raise ValueError('Qubit identity absent from raw configuration')
    cfg=ps['config'][job['q']]
    if abs(cfg['para']['probFreq']-job['readout_hz'])>1 and job['kind'] not in ('s21','s21_power','s21_ge','snr_freq'):raise ValueError('Readout setting disagrees with request')
    # Last axis is measured-qubit; all supported plans here acquire one qubit.
    if z.shape[-1]!=1:raise ValueError('Multi-qubit result requires explicit mapping')
    z=z[...,0]
    kind=job['kind']; metrics={};candidates={};series=[];details={}; plt.figure(figsize=(7.2,4.6))
    if kind in ('readout_cal','snr_amp','snr_freq'):
        state_axis=next((i for i,name in enumerate(labels) if name.lower() in ('ge','index')),None)
        if state_axis is None or len(axes[state_axis])!=2:raise ValueError('Missing two preparation-state axis')
        if kind=='readout_cal':
            states=np.moveaxis(z,state_axis,0).reshape(2,-1)
            metrics,trained=assignment(states)
            if trained:
                direction,threshold=trained
                for n in (0,1):plt.hist((states[n]*direction.conjugate()).real,bins=50,alpha=.5,label=f'prepared {n}')
                plt.axvline(threshold,color='black',linestyle='--');plt.legend()
        else:
            scan_name='probCarryScale' if kind=='snr_amp' else 'probFreq'
            scan_axis=labels.index(scan_name);values=axes[scan_axis]
            arranged=np.moveaxis(z,(state_axis,scan_axis),(0,1)).reshape(2,len(values),-1)
            summaries=[assignment(arranged[:,i,:])[0] for i in range(len(values))]
            scores=[v.get('training_snr',-1) for v in summaries];best=int(np.argmax(scores));metrics=summaries[best]
            if scores[best]>=0:
                candidates['readout_amp' if kind=='snr_amp' else 'readout_hz']=float(values[best])
            y=[v.get('assignment_fidelity',float('nan')) for v in summaries]
            series=[{'name':'Held-out preparation label accuracy','x':values.tolist(),'y':y,'xUnit':'normalized' if kind=='snr_amp' else 'Hz','yUnit':'fraction'}]
            plt.plot(values,y,'.-');plt.xlabel(scan_name);plt.ylabel('Held-out preparation-label accuracy')
            details['selection']='Setting selected using training SNR; threshold frozen from training shots, accuracy on interleaved held-out shots'
        details['assignmentScope']='Prepared I/X label accuracy includes SPAM/relaxation; not pure-state assignment fidelity or gate fidelity; capture/time correlations remain.'
    else:
        for i in reversed(range(len(labels))):
            if labels[i] in ('CaptureRepeat','repeat'):
                z=z.mean(axis=i);labels.pop(i);axes.pop(i)
        if len(axes)==1 and z.ndim==1:
            x=axes[0].astype(float);y=np.abs(z)
            unit='normalized' if kind=='power_rabi' else 's' if kind in ('rabi','t1','ramsey','echo','cpmg') else 'Hz'
            series=[{'name':'abs(mean complex IQ)','x':x.tolist(),'y':y.tolist(),'xUnit':unit,'yUnit':'V'}]
            plt.plot(x,y,'.',label='raw mean IQ')
            if kind in ('rabi','power_rabi','t1','ramsey','echo','cpmg'):
                factor=1 if kind=='power_rabi' else 1e6
                fit,details=fit_curve(x*factor,y,kind)
                metrics.update(details.get('metrics',{}))
                if fit is not None:
                    xx=np.linspace(x.min(),x.max(),600);plt.plot(xx,fit.func(xx*factor),'-',label='native-model fit')
                    if kind=='rabi' and 'pi_halfperiod_s' in metrics:
                        candidates['drive_length_s']=metrics['pi_halfperiod_s']
                    if kind=='power_rabi':
                        candidate=.5/abs(fit.params['Omega'])
                        if 0<candidate<=.98:candidates['drive_amp']=float(candidate)
            elif kind in ('s21','spectrum'):
                baseline=float(np.median(y));n=noise(y)
                peak=int(np.argmin(y)) if kind=='s21' else int(np.argmax(np.abs(y-baseline)))
                contrast=float(baseline-y[peak]) if kind=='s21' else float(abs(y[peak]-baseline))
                metrics['contrast_snr' if kind=='s21' else 'peak_snr']=max(0,contrast)/n
                metrics['feature_frequency_hz']=float(x[peak]);details['featureAtBoundary']=peak in (0,len(x)-1)
                if not details['featureAtBoundary']:
                    candidates['readout_hz' if kind=='s21' else 'drive_hz']=float(x[peak])
            plt.xlabel(labels[0]+' ('+unit+')');plt.ylabel('|mean IQ|');plt.legend()
        elif len(axes)==2 and z.ndim==2:
            plt.pcolormesh(axes[0],axes[1],np.abs(z).T,shading='auto');plt.colorbar(label='|mean IQ|')
            plt.xlabel(labels[0]);plt.ylabel(labels[1]);details['scope']='Two-dimensional raw response; no invented scalar gate or frequency acceptance'
        else:
            details['scope']='Raw data preserved; analysis for these multidimensional axes is not bound'
            plt.text(.5,.5,'Raw data preserved\nAnalysis not bound for these axes',ha='center')
    plt.title(job['q']+' · F8 '+kind);plt.tight_layout();figure=output/'capture_analysis.png';plt.savefig(figure,dpi=170);plt.close('all')
    value=clean({'version':1,'source':{'path':str(raw_path),'sha256':hashlib.sha256(raw_path.read_bytes()).hexdigest()},
                 'metrics':metrics,'candidateParameters':candidates,'series':series,'analysis':details,'figure':str(figure),
                 'quality':{'status':'unverified','reasons':['Curve diagnostics only; independent fresh acquisition and controller policy still required.']},
                 'mathSources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in MATH.glob('*.py')}})
    (output/'capture_analysis.json').write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--raw',required=True);parser.add_argument('--job',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    job=json.loads(Path(args.job).read_text(encoding='utf-8'));job=job['jobs'][0] if 'jobs' in job else job
    result=analyze(args.raw,job,args.output)
    print(json.dumps({'metrics':result['metrics'],'candidateParameters':result['candidateParameters']},ensure_ascii=False))
