"""Compare PRE/POST reference captures on the frozen PRE axis and scale.

Offline only. No POST axis training, no RB correction, no clipping. The six
captures in each job remain the independent outer repeats; two ADC trains are
within each point and only enter its conservative SEM.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).absolute().parent))
import argparse
import datetime
import hashlib
import json
import os
import numpy as np
from scipy.stats import t
from readout_reference_support import ROOT,_safe,_mean_sem
from prepare_rb_from_fresh_reference import load_reference,assert_conditions
from export_paired_lda_reference import build_reference
from analyze_single_qubit import projected_standard_error,normalized_acquisition_blocks


def mean_stats(values,point_sem):
    y=np.asarray(values,float);s=np.asarray(point_sem,float);n=len(y)
    if n<2 or s.shape!=y.shape or not np.isfinite([y,s]).all(): raise ValueError('At least two finite captures are required')
    between=float(y.std(ddof=1)/np.sqrt(n));within=float(np.sqrt(np.sum(s*s))/n)
    sem=max(between,within);half=float(t.ppf(.975,n-1)*sem)
    return {'capture_count':n,'capture_values':y.tolist(),'capture_point_sem':s.tolist(),
        'mean':float(y.mean()),'between_capture_sem':between,'combined_within_capture_sem':within,
        'effective_sem':sem,'ci95_t_conditional':[float(y.mean()-half),float(y.mean()+half)],
        'observed_range':[float(y.min()),float(y.max())],'t_degrees_of_freedom':n-1}


def compare_mean(pre,post,*,relative=False):
    a,b=pre['mean'],post['mean'];sa,sb=pre['effective_sem'],post['effective_sem']
    va,vb=sa*sa,sb*sb;se=float(np.sqrt(va+vb));den=va*va/(pre['capture_count']-1)+vb*vb/(post['capture_count']-1)
    df=float((va+vb)**2/den) if den>0 else float(min(pre['capture_count'],post['capture_count'])-1)
    half=float(t.ppf(.975,df)*se);delta=float(b-a)
    out={'pre_mean':a,'post_mean':b,'post_minus_pre':delta,'difference_sem':se,
        'difference_ci95_welch_t_conditional':[delta-half,delta+half],'welch_degrees_of_freedom':df,
        'difference_ci_excludes_zero':bool(delta-half>0 or delta+half<0)}
    if relative:
        ratio=float(b/a) if abs(a)>1e-15 else None
        critical=float(t.ppf(.975,min(pre['capture_count'],post['capture_count'])-1))
        A=a*a-critical*critical*va;B=-2*a*b;C=b*b-critical*critical*vb
        discriminant=B*B-4*A*C
        interval=None
        if A>0 and discriminant>=0:
            interval=sorted([float((-B-np.sqrt(discriminant))/(2*A)),float((-B+np.sqrt(discriminant))/(2*A))])
        out.update(post_over_pre=ratio,relative_change_fraction=None if ratio is None else ratio-1,
            relative_change_percent=None if ratio is None else 100*(ratio-1),
            ratio_ci95_fieller_t_conditional=interval,
            relative_change_ci95=None if interval is None else [v-1 for v in interval],
            ratio_interval_bounded=interval is not None,
            ratio_interval_method='Independent capture means; Fieller quadratic with t critical at min(n_pre-1,n_post-1). Unbounded denominator cases are not clipped.')
    return out


def capture_diagnostics(axes,iq,blocks,projection):
    z=np.asarray(iq)
    if z.ndim==3 and z.shape[1]==1:z=z[:,0]
    if z.ndim!=2 or len(z)!=len(axes) or not np.isfinite(z).all(): raise ValueError('Invalid real reference IQ dimensions')
    d=np.asarray(projection['direction'],float);g=np.asarray(projection['ground_iq'],float);e=np.asarray(projection['excited_iq'],float)
    if d.shape!=(2,) or not np.isfinite([d,g,e]).all() or not np.isclose(np.linalg.norm(d),1,atol=1e-10):
        raise ValueError('Use the finite frozen PRE unit direction and prepared-state centroids')
    contrast=float((e-g)@d)
    if contrast<=1e-15: raise ValueError('PRE reference contrast must be positive')
    projected=((z.real-g[0])*d[0]+(z.imag-g[1])*d[1])/contrast
    actual=normalized_acquisition_blocks(blocks,projected.shape,2)
    acquisition_sem,acquisition_stats=projected_standard_error(projected,2,blocks)
    rowlist=[]
    for capture in sorted({a['capture'] for a in axes}):
        row={'capture':capture}
        for label in ('ground','excited'):
            matches=[i for i,a in enumerate(axes) if a['capture']==capture and a['axes']['exp.cir.default']==label]
            if len(matches)!=1: raise ValueError('Each capture must include exactly one ground and excited point')
            index=matches[0];y=projected[index];sem,detail=_mean_sem(y)
            sem=max(sem,float(acquisition_sem[index]))
            row[label]={'mean_prepared_reference_scale':float(y.mean()),'sem':sem,
                'raw_mean_iq':[float(z[index].real.mean()),float(z[index].imag.mean())],
                'raw_fixed_axis_mean':float(z[index].real.mean()*d[0]+z[index].imag.mean()*d[1]),
                'actual_block_means':[float(y[actual[index]==b].mean()) for b in range(2)],
                'acquisition_block_sem':acquisition_stats['acquisition_block_sem'][index],
                'within_point_sem_diagnostics':detail}
        row['contrast']={'mean_prepared_reference_scale':row['excited']['mean_prepared_reference_scale']-row['ground']['mean_prepared_reference_scale'],
            'sem':float(np.hypot(row['excited']['sem'],row['ground']['sem']))}
        rowlist.append(row)
    return rowlist,projected


def summarize_rows(rows):
    return {key:mean_stats([r[key]['mean_prepared_reference_scale'] for r in rows],[r[key]['sem'] for r in rows])
            for key in ('ground','excited','contrast')}


def render(result,output):
    os.environ.setdefault('MPLBACKEND','Agg');os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'runtime_state/matplotlib'))
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig=Figure(figsize=(12,8),dpi=160);FigureCanvasAgg(fig)
    gs=fig.add_gridspec(2,1,left=.09,right=.98,top=.87,bottom=.18,hspace=.24)
    top=fig.add_subplot(gs[0]);bottom=fig.add_subplot(gs[1],sharex=top)
    labels=[];positions=[]
    for stage,offset,color in [('pre',0,'#1261a0'),('post',8,'#b94720')]:
        rows=result[stage+'_captures'];x=np.arange(len(rows))+offset;positions.extend(x)
        labels.extend([stage.upper()+' c'+str(r['capture']) for r in rows])
        for state,marker in [('ground','o'),('excited','^')]:
            top.errorbar(x,[r[state]['mean_prepared_reference_scale'] for r in rows],
                yerr=[r[state]['sem'] for r in rows],fmt=marker+'-',color=color,lw=1.2,capsize=2,
                alpha=.85,label=stage.upper()+' '+state)
        bottom.errorbar(x,[r['contrast']['mean_prepared_reference_scale'] for r in rows],
            yerr=[r['contrast']['sem'] for r in rows],fmt='o-',color=color,capsize=2,label=stage.upper()+' contrast')
    top.axhline(0,color='black',ls='--',lw=.8);top.axhline(1,color='black',ls='--',lw=.8)
    bottom.axhline(1,color='black',ls='--',lw=.8)
    top.set_ylabel('Prepared-reference IQ scale');bottom.set_ylabel('Excited - ground contrast')
    top.tick_params(labelbottom=False);bottom.set_xticks(positions,labels,rotation=45,ha='right')
    for ax in (top,bottom):ax.grid(alpha=.2);ax.legend(fontsize=8,ncol=2)
    ratio=result['comparison_pre_all_vs_post']['contrast']['relative_change_percent']
    fig.suptitle(f'{result["target"]} | PRE / POST independent-reference drift',fontsize=18,y=.97)
    fig.text(.5,.915,f'PRE direction and training scale retained | Mean contrast change: {ratio:.2f}%',ha='center',fontsize=11)
    fig.text(.09,.055,'Points show capture mean +/- conservative SEM; six outer captures per reference.\n'
        'No POST axis fitting, no RB renormalization. Prepared-reference IQ scale is not pure-state probability.\n'
        '95% t / Fieller intervals are in the JSON report and condition on PRE calibration; this is not gate fidelity.',fontsize=9)
    fig.savefig(output,dpi=160)


def analyze(pre_folder,post_folder):
    pre_folder=_safe(pre_folder);post_folder=_safe(post_folder)
    if pre_folder==post_folder: raise ValueError('PRE and POST must be different independently acquired runs')
    sources={name:(ROOT/name).read_bytes() for name in ('analyze_rb_reference_drift.py','prepare_rb_from_fresh_reference.py',
        'export_paired_lda_reference.py','analyze_historical_allxy.py','analyze_allxy.py','readout_reference_support.py','analyze_single_qubit.py')}
    pa,pz,pjob,ph,pblocks,pproof=load_reference(pre_folder,role='pre')
    qa,qz,qjob,qh,qblocks,qproof=load_reference(post_folder,role='post')
    if ph['raw_scan.npz']==qh['raw_scan.npz']:
        raise ValueError('PRE and POST raw files are identical; a copied reference is not a new acquisition')
    pre_status=json.loads((pre_folder/'status.json').read_text(encoding='utf-8-sig'))
    post_status=json.loads((post_folder/'status.json').read_text(encoding='utf-8-sig'))
    if datetime.datetime.fromisoformat(post_status['started'])<=datetime.datetime.fromisoformat(pre_status['ended']):
        raise ValueError('POST must start after the PRE acquisition has ended')
    assert_conditions(pjob,qjob)
    exported=build_reference(pre_folder);projection=exported['paired_rb_projection']
    pre,pre_shots=capture_diagnostics(pa,pz,pblocks,projection)
    post,post_shots=capture_diagnostics(qa,qz,qblocks,projection)
    pre_summary=summarize_rows(pre);post_summary=summarize_rows(post)
    heldout=[r for r in pre if r['capture'] in projection['validation_captures']]
    held_summary=summarize_rows(heldout)
    compare=lambda lhs:{key:compare_mean(lhs[key],post_summary[key],relative=key=='contrast') for key in lhs}
    result={'schema':'arclight_rb_pre_post_fixed_lda_drift_v1','target':pjob['target'],
        'pre_folder':str(pre_folder),'post_folder':str(post_folder),'fixed_PRE_projection':projection,
        'pre_acquisition_ended_local':pre_status['ended'],'post_acquisition_started_local':post_status['started'],
        'normalization_convention':'Prepared ground=0, excited=1 on PRE training reference; no clipping',
        'pre_captures':pre,'post_captures':post,'pre_all_summary':pre_summary,'post_summary':post_summary,
        'pre_heldout_summary':held_summary,'comparison_pre_all_vs_post':compare(pre_summary),
        'comparison_pre_heldout_vs_post':compare(held_summary),
        'pre_raw_input_sha256':ph,'post_raw_input_sha256':qh,
        'pre_real_block_verification':pproof,'post_real_block_verification':qproof,
        'post_used_to_train_reference':False,'RB_data_corrected_or_renormalized':False,
        'stationarity_proven':False,'gate_fidelity_reported_by_this_tool':False,
        'analysis_source_sha256':{name:hashlib.sha256(data).hexdigest() for name,data in sources.items()},
        'uncertainty':'Capture mean SEM=max(between-capture SEM, combined within-point conservative SEM). Difference uses Welch t; contrast ratio uses Fieller with conservative min capture df. Conditional on fixed PRE direction/centroids; excludes calibration/model systematic uncertainty.',
        'limits':['PRE training captures helped choose the direction/scale; compare PRE held-out vs POST as a complementary independent check.',
            'Two real ADC trains per point remain within-capture blocks, not additional outer repeats.',
            'Reference drift can include preparation, relaxation, thermal and readout changes; it cannot uniquely identify an electronics mechanism.',
            'Observed PRE/POST agreement does not prove stationarity throughout RB; disagreement is reported without altering RB data or fidelity estimates.']}
    for name,data in sources.items():
        if (ROOT/name).read_bytes()!=data: raise RuntimeError('Analysis source changed during computation: '+name)
    outputs=[post_folder/name for name in ('rb_reference_drift.json','rb_reference_drift.png','rb_reference_drift_unclipped.npz')]
    if any(path.exists() for path in outputs): raise FileExistsError('Drift output already exists; refusing overwrite')
    snapshot=post_folder/'rb_drift_analysis_sources';snapshot.mkdir(exist_ok=False)
    for name,data in sources.items():(snapshot/name).write_bytes(data)
    result.update(image=outputs[1].name,image_size_pixels=[1920,1280])
    np.savez_compressed(outputs[2],pre_projected_shots_unclipped=pre_shots,post_projected_shots_unclipped=post_shots,
        pre_acquisition_block_index=pblocks,post_acquisition_block_index=qblocks)
    render(result,outputs[1]);outputs[0].write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('pre_folder',type=Path);p.add_argument('post_folder',type=Path);args=p.parse_args()
    result=analyze(args.pre_folder,args.post_folder)
    print(json.dumps({'target':result['target'],'comparison_pre_all_vs_post':result['comparison_pre_all_vs_post'],
        'image':str(Path(result['post_folder'])/result['image'])},ensure_ascii=False))
