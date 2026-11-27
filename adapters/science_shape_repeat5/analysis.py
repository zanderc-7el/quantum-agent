"""Real ADC five-gate fit; no amplitude extrapolation or fidelity inference."""
import math
import numpy as np
from .plans import N,PROCEDURE,POLICY,build_repeat5

def analyze(plan,i,q):
    from analyze_single_qubit import fit_signal
    p,expected=build_repeat5(plan['parameters'])
    if plan['procedureId']!=PROCEDURE or plan['rows']!=expected:raise ValueError('Five-X analysis row identity differs')
    z=np.asarray(i)[:,0,:]+1j*np.asarray(q)[:,0,:]
    if z.shape[0]!=len(expected) or z.shape[1]<2 or not np.isfinite(z).all():raise ValueError('Five-X raw ADC shape differs')
    means=z.mean(axis=1);cloud=np.column_stack((means.real,means.imag))
    _,_,v=np.linalg.svd(cloud-cloud.mean(axis=0),full_matrices=False)
    projected=(z*np.conj(v[0,0]+1j*v[0,1])).real
    ys=projected.mean(axis=1);errors=projected.std(axis=1,ddof=1)/math.sqrt(z.shape[1])
    axis=p['scan_axis_values'];values=[];sigma=[]
    for x in axis:
        selected=[j for j,r in enumerate(expected) if r['condition']=='rabi' and r['axisValue']==x]
        values.append(float(np.mean(ys[selected])))
        sigma.append(float(max(np.sqrt(np.mean(errors[selected]**2)),1e-15)))
    fit,diagnostics,curve=fit_signal(axis,values,sigma,oscillatory=True)
    reasons=[];candidate={};metrics={};calibration={'policy':POLICY,'gateRepeats':N,
        'amplitudeAxisUnit':'waveform_scale','frequencyUnit':'cycles per waveform_scale',
        'piAmplitudeFormula':'5 / (2 * fittedFrequency)','singleGateRotation':'X(pi)',
        'fit':fit,'independentFixedCandidate':p.get('verify_shape_pi_amp')}
    if fit is None:reasons.extend(diagnostics.get('rejection_reasons',['Five-gate oscillation unresolved']))
    else:
        frequency=float(fit['value']);selected=N/(2*frequency)
        stderr=N*float(fit['standard_error_model'])/(2*frequency*frequency)
        calibration.update(singleGatePiAmplitude=selected,singleGatePiAmplitudeModelStandardError=stderr)
        predicted=np.interp(axis,curve[0],curve[1]);observed=np.asarray(values)
        metrics.update(r_squared=float(1-np.sum((observed-predicted)**2)/max(np.sum((observed-observed.mean())**2),1e-30)),
            contrast_snr=float(np.ptp(values)/max(np.median(sigma),1e-15)),cycles=float(fit['cycles_in_window']),
            amplitude_oscillation_frequency=frequency,relative_stderr=float(fit['standard_error_model']/frequency),
            gate_repeats=N,pi_amplitude_model_stderr=stderr)
        if not axis[0]<selected<axis[-1] or not 0<selected<=.99:
            reasons.append('Single-gate pi amplitude lies outside acquired axis or native amplitude bound')
        else:candidate['shape_pi_amp']=selected
        if 'verify_shape_pi_amp' in p:
            frozen=p['verify_shape_pi_amp'];indices=[j for j,r in enumerate(expected) if r['condition']=='verification_candidate']
            tolerance=max(5*np.median(sigma),5*np.sqrt(np.mean((observed-predicted)**2)),.05*np.ptp(values),1e-15)
            predicted_fixed=float(np.interp(frozen,curve[0],curve[1]));captures=ys[indices]
            metrics.update(candidate_points_acquired=len(indices),
                candidate_agreement_fraction=abs(selected-frozen)/(.1*abs(frozen)),
                candidate_signal_agreement=float(abs(captures.mean()-predicted_fixed)/tolerance),
                candidate_capture_agreement=float(np.max(np.abs(captures-predicted_fixed))/tolerance))
            if len(indices)!=p['capture_repeats']:reasons.append('Each capture must acquire the frozen five-gate candidate')
            for key in ('candidate_agreement_fraction','candidate_signal_agreement','candidate_capture_agreement'):
                if not math.isfinite(metrics[key]) or metrics[key]>1:reasons.append('Independent five-gate '+key+' failed')
            if 'shape_pi_amp' not in candidate:reasons.append('Independent scan cannot validate a physically reachable single-gate pi amplitude')
            candidate={'shape_pi_amp':frozen} if not reasons else {}
    return {'metrics':metrics,'candidateParameters':candidate,'series':[{'name':'five_X_amplitude_Rabi',
        'x':list(axis),'y':values,'xUnit':'waveform_scale','yUnit':'ADC projection','quantity':'iq_projection'}],
        'shapeRabiCalibration':calibration,'analysis':{'accepted':not reasons,'reasons':reasons,
        'scope':'Five-X amplitude calibration of a single shaped pi pulse; not gate fidelity or leakage',
        'policy':POLICY,'gateRepeats':N}}
