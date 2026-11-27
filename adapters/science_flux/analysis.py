"""Fresh raw-IQ cavity cuts and independent same-bias paired spectroscopy."""
import numpy as np
from platform_analysis import _cavity,clean,native_module

def analyze_cut(cut,rows,i,q,p,blocks):
    spectrum=cut['phase']=='f01';key='cfg.Q1.para.drivFreq' if spectrum else 'cfg.Q1.para.probFreq'
    axes=[{'axes':{key:r['frequencyHz'],'exp.cir.default':r['condition']},'capture':r['capture'],
        'base_index':r['frequencyIndex']*(2 if spectrum else 1)+(r['condition']=='on')} for r in rows]
    native=native_module('analyze_single_qubit');iq=native.validate_arrays(axes,i,q)
    if not spectrum:
        metrics,candidates,diagnostic,traces=_cavity(axes,iq,blocks,{'kind':'s21','acquisitions_per_point':p['acquisitions_per_point']})
        # Multiple comparable separate dips are unresolved, not a chosen point.
        reasons=list(diagnostic['reasons']);centers=[];snrs=[];captures=[]
        for capture,trace in enumerate(traces):
            y=np.asarray(trace['y']);edge=max(2,len(y)//8);base=np.linspace(np.median(y[:edge]),np.median(y[-edge:]),len(y));depth=base-y
            k=int(np.argmax(depth));noise=max(float(np.median(trace['sem'])),float(np.median(np.abs(np.diff(y,n=2)))/1.65),1e-30)
            snr=max(0,float(depth[k]))/noise
            secondary=[n for n in range(1,len(y)-1) if abs(n-k)>2 and depth[n]>depth[n-1] and depth[n]>=depth[n+1] and depth[n]>=.8*depth[k]]
            if secondary:reasons.append('Multiple comparable cavity features require a bounded local rescan')
            centers.append(float(trace['x'][k]));snrs.append(snr)
            captures.append({'capture':capture,'candidateHz':float(trace['x'][k]),'peakIndex':k,'snr':snr,
                'interior':0<k<len(y)-1,'ambiguous':bool(secondary),'pointCount':len(y)})
        frequency=cut['frequenciesHz'];response=np.mean([np.asarray(t['y']) for t in traces],axis=0).tolist()
        accepted=diagnostic['accepted'] and not reasons
        return clean({'accepted':accepted,'reasons':sorted(set(reasons)),'candidateHz':candidates.get('readout_hz') if accepted else None,
            'contrast_snr':min(snrs,default=0),'captureCandidates':captures,'frequencyAgreementHz':float(np.ptp(centers)),
            'frequencyToleranceHz':max(5e4,2*float(np.median(np.diff(frequency)))),'frequenciesHz':frequency,
            'responseMagnitude':response,'series':[{'label':'Raw cavity |mean IQ| capture '+str(n+1),'x':t['x'],'y':t['y'],'sem':t['sem']} for n,t in enumerate(traces)],
            'scope':'Acquired readout-IQ response, not normalized S21; separate minima remain unresolved'})
    result,views=native.analyze_arrays(axes,i,q,{'kind':'spectrum','acquisitions_per_point':p['acquisitions_per_point']},blocks)
    group=result['groups'][0];repeats=group['repeats'];curves=views['plots'][0]['comparison']
    accepted=group['repeat_validated'];frequency=cut['frequenciesHz']
    complex_sem,_=native.projected_standard_error(iq,p['acquisitions_per_point'],blocks);means=iq.mean(axis=1);signals=[]
    for capture in range(p['capture_repeats']):
        off=sorted([n for n,r in enumerate(rows) if r['capture']==capture and r['condition']=='off'],key=lambda n:rows[n]['frequencyHz'])
        on=sorted([n for n,r in enumerate(rows) if r['capture']==capture and r['condition']=='on'],key=lambda n:rows[n]['frequencyHz'])
        signals.append({'capture':capture,'frequenciesHz':[rows[n]['frequencyHz'] for n in off],
            'offMeanI':means[off].real,'offMeanQ':means[off].imag,'onMeanI':means[on].real,'onMeanQ':means[on].imag,
            'offComplexSem':complex_sem[off],'onComplexSem':complex_sem[on],
            'backgroundComplex':repeats[capture]['background_complex'],'excursionMagnitude':curves[capture][2]})
    return clean({'accepted':accepted,'reasons':[] if accepted else ['Same-bias OFF/ON response lacks independent interior agreement'],
        'candidateHz':group['candidate_frequency_hz'] if accepted else None,
        'contrast_snr':min(r['candidate_significance'] for r in repeats),'captureCandidates':repeats,
        'frequenciesHz':frequency,'responseMagnitude':np.mean([c[2] for c in curves],axis=0),
        'signalCaptures':signals,'uncertaintyMethod':'max(shot IID complex SEM, independently rearmed block-mean complex SEM)',
        'series':[{'label':'Same-bias OFF/ON excursion capture '+str(n+1),'x':c[0],'y':c[2]} for n,c in enumerate(curves)],
        'scope':'Paired response candidate, not proof of qubit identity or coherent control; no f01 inferred from cavity'})
