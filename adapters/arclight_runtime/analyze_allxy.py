"""AllXY diagnostics from saved real IQ and an independent prepared-state reference.

No native runtime, core, or hardware imports. Native sequence order is read from
the literal AllXY.xy circuit list and propagated with ideal one-qubit unitaries.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).absolute().parent))
import argparse
import ast
import hashlib
import json
import os
import numpy as np
from scipy.stats import t
from readout_reference_support import ROOT,_safe,_load_run,_mean_sem
from analyze_single_qubit import normalized_acquisition_blocks, projected_standard_error


def native_ideal_reference():
    path=Path(__file__).absolute().parent/'work/server/core/circuit/allxy.py'
    source=path.read_text(encoding='utf-8-sig'); tree=ast.parse(source)
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AllXY')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='xy')
    assignments=[n for n in ast.walk(method) if isinstance(n,ast.Assign)
                 and any(isinstance(a,ast.Name) and a.id=='cirs' for a in n.targets)]
    if len(assignments)!=1: raise ValueError('Native AllXY ordering is no longer a single literal list')
    pairs=ast.literal_eval(assignments[0].value)
    if len(pairs)!=21 or len(set(pairs))!=21: raise ValueError('Expected 21 distinct native AllXY pairs')
    rows=[]
    for index,pair in enumerate(pairs):
        state=np.array([1+0j,0j])
        for token in pair.split(','):
            token=token.strip()
            if token=='I': continue
            if token not in ('X','Y','X/2','Y/2','-X/2','-Y/2'):
                raise ValueError('Unsupported native AllXY token '+token)
            theta=(np.pi/2 if '/2' in token else np.pi)*(-1 if token.startswith('-') else 1)
            c,s=np.cos(theta/2),np.sin(theta/2)
            u=np.array([[c,-1j*s],[-1j*s,c]]) if 'X' in token else np.array([[c,-s],[s,c]])
            state=u@state
        rows.append({'index':index,'native_pair':pair,'ideal_p1':float(np.round(abs(state[1])**2,15))})
    return {'source':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'method':'Native AllXY.xy literal order; independent ideal unitary propagation from |0>',
            'metric':'Theoretical P1, not measured probability','points':rows}


def analyze_arrays(records,iq,job,*,ideal=None,acquisition_block_index=None):
    ideal=native_ideal_reference() if ideal is None else ideal
    reference=job.get('calibration',{}).get('projection',{})
    if reference.get('independent_reference') is not True or not reference.get('source'):
        raise ValueError('AllXY requires an independently acquired identified IQ reference')
    g,e=np.asarray(reference['ground_iq'],float),np.asarray(reference['excited_iq'],float)
    if g.shape!=(2,) or e.shape!=(2,) or not np.isfinite([g,e]).all():
        raise ValueError('Finite [I,Q] prepared-reference centroids required')
    difference=complex(*(e-g)); ground=complex(*g)
    if abs(difference)<1e-15: raise ValueError('Independent reference has zero contrast')
    direction=np.asarray(reference.get('direction',e-g),float)
    if direction.shape!=(2,) or not np.isfinite(direction).all() or np.linalg.norm(direction)==0:
        raise ValueError('Independent fixed direction must be a finite nonzero [I,Q] vector')
    direction=direction/np.linalg.norm(direction)
    contrast=float((e-g)@direction)
    if contrast<=1e-15: raise ValueError('Independent fixed direction requires positive prepared-state contrast')
    if job['spec']['meta'].get('repeat',1)!=1:
        raise ValueError('This diagnostic requires the unique native 21-sequence order (repeat=1)')
    q='Q'+str(job['spec']['meta']['qubit']); para=job['config'][q]['para']
    if 'frequency_hz' in reference and abs(reference['frequency_hz']-para['probFreq'])>1:
        raise ValueError('Readout frequency differs from independent reference')
    if 'amplitude' in reference and abs(reference['amplitude']-para['probCarryScale'])>1e-12:
        raise ValueError('Readout amplitude differs from independent reference')
    z=np.asarray(iq)
    if z.ndim==3 and z.shape[1]==1: z=z[:,0,:]
    if z.ndim!=2 or z.shape[0]!=len(records) or z.shape[1]<64 or not np.iscomplexobj(z) or not np.isfinite(z).all():
        raise ValueError('Finite complex IQ [point,1,shots>=64] or [point,shots>=64] required')
    # Deliberately do not clip or refit centroids/axis using AllXY data.
    projected=((z.real-ground.real)*direction[0]+(z.imag-ground.imag)*direction[1])/contrast
    acquisition_count=job.get('acquisitions_per_point',1)
    actual_blocks=normalized_acquisition_blocks(acquisition_block_index,projected.shape,acquisition_count)
    acquisition_sem,acquisition_stats=projected_standard_error(projected,acquisition_count,acquisition_block_index)
    labels=[r['native_pair'] for r in ideal['points']]
    theory=np.array([r['ideal_p1'] for r in ideal['points']])
    if len(labels)!=21 or len(set(labels))!=21: raise ValueError('Invalid native ideal reference')
    point_rows=[]; by_capture={}; base_map={}; capture_bases={}
    for point,(record,shots) in enumerate(zip(records,projected)):
        capture=record['capture']; base=record['base_index']; axes=record['axes']
        if isinstance(capture,bool) or not isinstance(capture,int) or capture<0 or isinstance(base,bool) or not isinstance(base,int) or base<0:
            raise ValueError('capture/base_index must be nonnegative integers')
        if set(axes)!={'circ'} or axes['circ'] not in labels:
            raise ValueError('Expected the native AllXY circ axis only')
        index=labels.index(axes['circ'])
        if base in base_map and base_map[base]!=index: raise ValueError('base_index changes AllXY circuit')
        base_map[base]=index
        if base in capture_bases.setdefault(capture,set()): raise ValueError('Repeated base_index within capture')
        capture_bases[capture].add(base)
        if index in by_capture.setdefault(capture,{}): raise ValueError('Duplicate native pair in capture')
        sem,meta=_mean_sem(shots)
        if acquisition_stats is not None:
            sem=max(sem,float(acquisition_sem[point]))
            meta.update(acquisition_block_sem=acquisition_stats['acquisition_block_sem'][point],
                actual_acquisition_count=acquisition_count,
                actual_acquisition_block_means=[float(shots[actual_blocks[point]==b].mean()) for b in range(acquisition_count)])
        row={'acquisition_point':point,'capture':capture,'base_index':base,'native_index':index,
            'native_pair':labels[index],'ideal_p1':float(theory[index]),
            'mean_iq':[float(z[point].real.mean()),float(z[point].imag.mean())],
            'prepared_reference_iq_mean_unclipped':float(shots.mean()),
            'prepared_reference_iq_sem':sem,'sem_diagnostics':meta,'shots':len(shots)}
        by_capture[capture][index]=row; point_rows.append(row)
    captures=sorted(by_capture)
    if len(captures)<2 or any(set(v)!=set(range(21)) for v in by_capture.values()):
        raise ValueError('At least two complete captures of all 21 native circuits required')
    if any(v!=capture_bases[captures[0]] for v in capture_bases.values()):
        raise ValueError('Capture base-index coverage differs')
    values=np.array([[by_capture[c][i]['prepared_reference_iq_mean_unclipped'] for i in range(21)] for c in captures])
    errors=np.array([[by_capture[c][i]['prepared_reference_iq_sem'] for i in range(21)] for c in captures])
    mean=values.mean(axis=0)
    within=np.sqrt((errors**2).sum(axis=0))/len(captures)
    between=values.std(axis=0,ddof=1)/np.sqrt(len(captures))
    sem=np.maximum(within,between)
    width=t.ppf(.975,len(captures)-1)*sem
    aggregate=[{'native_index':i,'native_pair':labels[i],'ideal_p1':float(theory[i]),
        'mean_unclipped':float(mean[i]),'sem':float(sem[i]),
        'within_capture_combined_sem':float(within[i]),'between_capture_sem':float(between[i]),
        'ci95_capture_t_conditional_unclipped':[float(mean[i]-width[i]),float(mean[i]+width[i])],
        'residual_to_ideal':float(mean[i]-theory[i])} for i in range(21)]
    result={'schema':'arclight_allxy_independent_reference_v1','target':job['target'],
        'diagnostic_only':True,'native_ideal_reference':ideal,'independent_reference':reference,
        'captures':captures,'points':point_rows,'aggregate':aggregate,
        'rms_deviation_from_ideal':float(np.sqrt(np.mean((mean-theory)**2))),
        'rms_mean_standard_error':float(np.sqrt(np.mean(sem**2))),
        'rms_by_capture':{str(c):float(np.sqrt(np.mean((values[j]-theory)**2))) for j,c in enumerate(captures)},
        'normalization':{'fixed_direction':direction.tolist(),'prepared_state_projected_contrast':contrast,
            'formula':'((IQ-ground_reference) dot fixed_direction)/((excited_reference-ground_reference) dot fixed_direction)',
            'allxy_used_to_train_axis_or_scale':False},
        'acquisition_statistics':acquisition_stats,
        'level_summary':[{'ideal_p1':float(level),'circuits':int(np.count_nonzero(theory==level)),
            'mean_unclipped':float(mean[theory==level].mean()),
            'mean_by_capture':[float(row[theory==level].mean()) for row in values],
            'point_mean_range':[float(mean[theory==level].min()),float(mean[theory==level].max())]}
            for level in (0,.5,1)],
        'calibration_uncertainty_included_in_sem':False,
        'interpretation':'Unclipped prepared-reference IQ scale; not independently verified pure-state probability. RMS deviation is not gate fidelity.',
        'uncertainty':'SEM is max(combined within-capture max(IID,contiguous-subblock,actual-rearmed-acquisition-block) SEM, between-capture SEM); rearmed blocks remain within each outer capture. t intervals condition on fixed calibration and do not include reference/model uncertainty.'}
    return result,{'projected_shots_unclipped':projected,'capture_means':values,
                   'capture_sem':errors,'aggregate_mean':mean,'aggregate_sem':sem,'ideal':theory}


def render(result,views,output):
    os.environ.setdefault('MPLBACKEND','Agg')
    os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'runtime_state'/'matplotlib'))
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig=Figure(figsize=(12,8),dpi=160); FigureCanvasAgg(fig)
    gs=fig.add_gridspec(2,1,height_ratios=[2.25,1],left=.09,right=.98,top=.88,bottom=.17,hspace=.24)
    ax=fig.add_subplot(gs[0]); residual=fig.add_subplot(gs[1],sharex=ax)
    x=np.arange(1,22); labels=[r['native_pair'] for r in result['native_ideal_reference']['points']]
    for j,c in enumerate(result['captures']):
        offset=(j-(len(result['captures'])-1)/2)*.04
        ax.errorbar(x+offset,views['capture_means'][j],yerr=views['capture_sem'][j],fmt='.',
                    alpha=.32,elinewidth=.6,markersize=3,label=f'capture {c}')
    ax.step(x,views['ideal'],where='mid',color='black',ls='--',lw=1.8,label='Ideal native reference P1')
    ax.errorbar(x,views['aggregate_mean'],yerr=views['aggregate_sem'],fmt='o-',color='#1261a0',
                lw=1.3,markersize=4,capsize=2,label='Measured mean +/- SEM')
    ax.set_ylabel('Prepared-reference IQ scale (unclipped)'); ax.grid(alpha=.2)
    ax.legend(loc='best',ncol=3,fontsize=7); ax.tick_params(labelbottom=False)
    residual.axhline(0,color='black',ls='--',lw=1)
    residual.errorbar(x,views['aggregate_mean']-views['ideal'],yerr=views['aggregate_sem'],
                      fmt='o',color='#8b1d2c',capsize=2,markersize=4)
    residual.set_ylabel('Mean - ideal reference'); residual.grid(alpha=.2)
    residual.set_xticks(x,[f'{i}\n{label}' for i,label in zip(x,labels)],rotation=45,ha='right',fontsize=7)
    residual.set_xlim(.5,21.5)
    fig.suptitle(f'{result["target"]} | Arclight native AllXY (21 sequences)',fontsize=18,y=.975)
    fig.text(.5,.925,'Fixed independently acquired IQ reference; all captures and raw scale retained',ha='center',fontsize=11)
    fig.text(.09,.048,'Prepared-reference IQ scale is not independently verified pure-state probability.\n'
        f'RMS deviation = {result["rms_deviation_from_ideal"]:.4f} (diagnostic, not gate fidelity); '
        f'RMS SEM = {result["rms_mean_standard_error"]:.4f}. Reference uncertainty excluded.',fontsize=9)
    fig.savefig(output,dpi=160)


def save_analysis(folder,result,views):
    folder=_safe(folder)
    for name in ('allxy_diagnostic.json','allxy_unclipped_iq_scale.npz','allxy_independent_reference.png','allxy_points.csv'):
        if (folder/name).exists(): raise FileExistsError(folder/name)
    result['image']='allxy_independent_reference.png'
    result['image_size_pixels']=[1920,1280]
    np.savez_compressed(folder/'allxy_unclipped_iq_scale.npz',**views,
        point_native_index=np.array([p['native_index'] for p in result['points']]),
        point_capture=np.array([p['capture'] for p in result['points']]))
    table=np.array([[r['acquisition_point'],r['capture'],r['native_index'],*r['mean_iq'],
        r['prepared_reference_iq_mean_unclipped'],r['prepared_reference_iq_sem'],r['ideal_p1']] for r in result['points']])
    np.savetxt(folder/'allxy_points.csv',table,delimiter=',',comments='',
        header='acquisition_point,capture,native_index,mean_i,mean_q,prepared_reference_iq_mean_unclipped,sem,ideal_p1')
    render(result,views,folder/result['image'])
    (folder/'allxy_diagnostic.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    return result


def analyze_directory(folder):
    folder=_safe(folder)
    axes,iq,job,hashes=_load_run(folder)
    if str(Path(job.get('calibration',{}).get('projection',{}).get('source','')).absolute())==str(folder):
        raise ValueError('AllXY itself cannot serve as its independent reference')
    with np.load(folder/'raw_scan.npz',allow_pickle=False) as raw:
        blocks=raw['acquisition_block_index'].copy() if 'acquisition_block_index' in raw.files else None
    result,views=analyze_arrays(axes,iq,job,acquisition_block_index=blocks)
    result['input_sha256']=hashes
    result['analysis_source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return save_analysis(folder,result,views)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path)
    r=analyze_directory(p.parse_args().folder)
    print(json.dumps({'target':r['target'],'rms_diagnostic_not_fidelity':r['rms_deviation_from_ideal'],
                      'image':r['image'],'image_size_pixels':r['image_size_pixels']}))
