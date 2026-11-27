"""Analysis of fresh ADC arrays; no synthetic samples or gate-fidelity inference."""
import math
import numpy as np
from scipy.optimize import curve_fit
from .plans import VERIFY

def _series(name,x,y,unit='normalized ADC projection'):
    return {'name':name,'x':[float(v) for v in x],'y':[float(v) for v in y],
            'xUnit':'Hz' if 'spectrum' in name else 'waveform_scale','yUnit':unit,'quantity':'iq_projection'}

def analyze(plan,i,q):
    rows=plan['rows'];p=plan['parameters'];method=plan['procedureId']
    z=np.asarray(i)[:,0,:]+1j*np.asarray(q)[:,0,:]
    if z.shape[0]!=len(rows) or not np.isfinite(z).all():raise ValueError('Raw data do not match frozen row table')
    if method=='sq_shelve_readout_compare':return compare_counts(rows,z)
    means=z.mean(axis=1);cloud=np.column_stack((means.real,means.imag))
    _,_,v=np.linalg.svd(cloud-cloud.mean(axis=0),full_matrices=False);direction=v[0,0]+1j*v[0,1]
    projected=(z*np.conj(direction)).real;ys=projected.mean(axis=1)
    errors=projected.std(axis=1,ddof=1)/math.sqrt(z.shape[1])
    metrics={};candidate={};reasons=[];series=[];candidate_signal=None
    if method=='sq_shape_half_verify':
        values=[]
        for capture in range(p['capture_repeats']):
            selected=[j for j,r in enumerate(rows) if r['capture']==capture];base=ys[selected[0]];scale=ys[selected[2]]-base
            if abs(scale)<5*max(errors[selected]):reasons.append('Half-gate reference contrast is unresolved');continue
            values.extend((ys[j]-base)/scale-np.sin(np.pi*rows[j]['axisValue']/2)**2 for j in selected)
        metrics['half_rotation_rms']=float(np.sqrt(np.mean(np.square(values)))) if values else 1.
        metrics['contrast_snr']=float(np.ptp(ys)/max(np.median(errors),1e-15))
        if metrics['half_rotation_rms']>.15:reasons.append('Independent half-rotation diagnostic failed')
        series=[_series('half_rotation',[r['axisValue'] for r in rows],ys)]
    elif method=='sq_shelve_spectrum12':
        axis=p['scan_axis_values'];contrast=[];noise=[]
        for x in axis:
            on=[j for j,r in enumerate(rows) if r['condition']=='prep_on' and r['axisValue']==x]
            off=[j for j,r in enumerate(rows) if r['condition']=='prep_off' and r['axisValue']==x]
            contrast.append(abs(means[on].mean()-means[off].mean()))
            noise.append(float(np.sqrt(np.mean(errors[on]**2)+np.mean(errors[off]**2))))
        x=np.array(axis);y=np.array(contrast);span=np.ptp(x);scaled=(x-x[0])/span
        def peak(t,b,a,c,w):return b+a*np.exp(-.5*((t-c)/w)**2)
        try:
            fits=[]
            for sign in (1,-1):
                try:
                    fit,cov=curve_fit(peak,scaled,y,p0=[float(np.median(np.r_[y[:5],y[-5:]])),sign*float(np.ptp(y)),float(scaled[y.argmax() if sign==1 else y.argmin()]),.1],
                        bounds=([-np.inf,-np.inf,0,.005],[np.inf,np.inf,1,.5]),maxfev=10000)
                    fits.append((float(np.sum((y-peak(scaled,*fit))**2)),fit,cov))
                except (ValueError,RuntimeError):pass
            if not fits:raise ValueError('No resolved resonance fit')
            _,fit,cov=min(fits,key=lambda item:item[0])
            predicted=peak(scaled,*fit);center=x[0]+fit[2]*span
            metrics.update(peak_snr=float(abs(fit[1])/max(np.median(noise),1e-15)),
                r_squared=float(1-np.sum((y-predicted)**2)/max(np.sum((y-y.mean())**2),1e-30)),
                center_stderr_hz=float(np.sqrt(max(0,cov[2,2]))*span))
            if not np.isfinite(cov).all() or not .05<fit[2]<.95 or metrics['peak_snr']<5 or metrics['r_squared']<.8:
                reasons.append('Conditional resonance is unresolved, poorly fit or at the search boundary')
            candidate['f12_hz']=float(center)
            if 'verify_f12_hz' in p:
                on=[j for j,r in enumerate(rows) if r['condition']=='verification_candidate' and r['baseCondition']=='prep_on']
                off=[j for j,r in enumerate(rows) if r['condition']=='verification_candidate' and r['baseCondition']=='prep_off']
                if on and off:
                    observed=abs(means[on].mean()-means[off].mean());expected=float(peak((p['verify_f12_hz']-x[0])/span,*fit))
                    tolerance=max(5*np.median(noise),5*np.sqrt(np.mean((y-predicted)**2)),.05*abs(fit[1]),1e-15)
                    captures=[abs(means[next(j for j in on if rows[j]['capture']==capture)]-means[next(j for j in off if rows[j]['capture']==capture)]) for capture in range(p['capture_repeats'])]
                    candidate_signal=(float(observed),expected,float(tolerance),captures)
        except (ValueError,RuntimeError):reasons.append('Conditional spectrum fit failed')
        series=[_series('conditional_spectrum',axis,y)]
    elif method=='sq_drag_scan':
        axis=p['scan_axis_values'];values=[];scatter=[]
        for x in axis:
            observed=[]
            for capture in range(p['capture_repeats']):
                ground=next(j for j,r in enumerate(rows) if r['capture']==capture and r['condition']=='reference' and r['logicalState']==0)
                excited=next(j for j,r in enumerate(rows) if r['capture']==capture and r['condition']=='reference' and r['logicalState']==1)
                idx=next(j for j,r in enumerate(rows) if r['capture']==capture and r['condition']=='diagnostic' and r['axisValue']==x)
                delta=ys[excited]-ys[ground]
                if abs(delta)<5*max(errors[ground],errors[excited]):raise ValueError('DRAG reference contrast unresolved')
                observed.append((ys[idx]-ys[ground])/delta)
            values.append(float(np.mean(observed)));scatter.append(float(np.std(observed,ddof=1)/np.sqrt(len(observed))))
        if len(axis)<5:raise ValueError('DRAG fit needs at least five distinct coefficients')
        (a,b,c),cov=np.polyfit(axis,values,2,cov=True);selected=-b/(2*a) if a>0 else float('nan')
        predicted=np.polyval([a,b,c],axis);residual=float(np.sqrt(np.mean((np.array(values)-predicted)**2)))
        curvature_sigma=float(np.sqrt(max(0,cov[0,0])));jac=np.array([b/(2*a*a),-1/(2*a),0]) if a>0 else np.zeros(3)
        vertex_sigma=float(np.sqrt(max(0,jac@cov@jac)))
        metrics.update(r_squared=float(1-np.sum((np.array(values)-predicted)**2)/max(np.sum((np.array(values)-np.mean(values))**2),1e-30)),
                       curvature_z=float(a/max(curvature_sigma,1e-15)),optimum_fit_stderr=vertex_sigma)
        metrics['diagnostic_improvement']=float(values[min(range(len(axis)),key=lambda k:abs(axis[k]))]-min(values))
        if (not np.isfinite(cov).all() or not math.isfinite(selected) or not axis[0]<selected-1.96*vertex_sigma<selected+1.96*vertex_sigma<axis[-1]
                or metrics['r_squared']<.8 or metrics['curvature_z']<3 or np.ptp(values)<5*max(np.median(scatter),1e-15)):
            reasons.append('DRAG minimum is unresolved, noisy or outside scan; curvature/fit/uncertainty failed')
        else:candidate['drag_lambda1']=float(selected)
        if 'verify_drag_lambda1' in p:
            observed=[]
            for capture in range(p['capture_repeats']):
                ground=next(j for j,r in enumerate(rows) if r['capture']==capture and r['condition']=='reference' and r['logicalState']==0)
                excited=next(j for j,r in enumerate(rows) if r['capture']==capture and r['condition']=='reference' and r['logicalState']==1)
                target=next(j for j,r in enumerate(rows) if r['capture']==capture and r['condition']=='verification_candidate')
                observed.append((ys[target]-ys[ground])/(ys[excited]-ys[ground]))
            candidate_signal=(float(np.mean(observed)),float(np.polyval([a,b,c],p['verify_drag_lambda1'])),
                              float(max(5*residual,5*np.median(scatter),.05*np.ptp(values),1e-15)),observed)
        series=[_series('drag_diagnostic',axis,values,'relative excited-state projection')]
    else:
        from analyze_single_qubit import fit_signal
        condition='rabi' if method=='sq_shape_rabi_amplitude' else 'rabi12';axis=p['scan_axis_values']
        value=[];sigma=[]
        for x in axis:
            select=[j for j,r in enumerate(rows) if r['condition']==condition and r['axisValue']==x]
            value.append(float(np.mean(ys[select])));sigma.append(float(max(np.sqrt(np.mean(errors[select]**2)),1e-15)))
        fit,diagnostics,curve=fit_signal(axis,value,sigma,oscillatory=True)
        if fit is None:reasons.extend(diagnostics.get('rejection_reasons',['Rabi oscillation is unresolved']))
        else:
            key='shape_pi_amp' if method=='sq_shape_rabi_amplitude' else 'probe12_amplitude'
            selected=.5/fit['value']
            if not axis[0]<selected<axis[-1]:reasons.append('Pi amplitude lies outside acquired scan')
            else:candidate[key]=float(selected)
            predicted=np.interp(axis,curve[0],curve[1]);observed=np.array(value)
            metrics.update(r_squared=float(1-np.sum((observed-predicted)**2)/max(np.sum((observed-observed.mean())**2),1e-30)),
                contrast_snr=float(np.ptp(value)/max(np.median(sigma),1e-15)),cycles=float(fit['cycles_in_window']))
            if 'verify_'+key in p:
                selected_rows=[j for j,r in enumerate(rows) if r['condition']=='verification_candidate']
                if selected_rows:
                    tolerance=max(5*np.median(sigma),5*np.sqrt(np.mean((observed-predicted)**2)),.05*np.ptp(value),1e-15)
                    candidate_signal=(float(np.mean(ys[selected_rows])),float(np.interp(p['verify_'+key],curve[0],curve[1])),float(tolerance),ys[selected_rows])
        series=[_series(condition,axis,value)]
    key=VERIFY.get(method)
    if key and 'verify_'+key in p:
        frozen=p['verify_'+key];estimated=candidate.get(key)
        if estimated is None:reasons.append('Independent scan cannot validate the frozen candidate')
        else:
            span=p['scan_axis_values'][-1]-p['scan_axis_values'][0]
            tolerance=.1*abs(frozen) if key in ('shape_pi_amp','probe12_amplitude') else .05*span
            metrics['candidate_agreement_fraction']=float(abs(estimated-frozen)/max(tolerance,1e-15))
            if abs(estimated-frozen)>tolerance:reasons.append('Independent candidate agreement failed')
        metrics['candidate_points_acquired']=sum(r['condition']=='verification_candidate' for r in rows)
        if not metrics['candidate_points_acquired']:reasons.append('Frozen candidate not actually reacquired')
        if candidate_signal is None:reasons.append('Actual fixed candidate ADC signal could not be validated')
        else:
            observed,expected,tolerance,captures=candidate_signal
            metrics['candidate_signal_agreement']=abs(observed-expected)/tolerance
            metrics['candidate_capture_agreement']=float(np.max(np.abs(np.asarray(captures)-expected))/tolerance)
            if not math.isfinite(metrics['candidate_signal_agreement']) or metrics['candidate_signal_agreement']>1:
                reasons.append('Actual fixed candidate ADC signal disagrees with independent scan prediction')
            if not math.isfinite(metrics['candidate_capture_agreement']) or metrics['candidate_capture_agreement']>1:
                reasons.append('Fixed candidate ADC captures are inconsistent with independent scan prediction')
        candidate={key:frozen}
    return {'metrics':metrics,'candidateParameters':candidate,'series':series,
            'analysis':{'accepted':not reasons,'reasons':reasons,'scope':'Native ADC diagnostic; no gate fidelity or leakage claim',
                        **({'uncertaintyScope':'Quadratic fit covariance diagnostic only; not a calibrated repeated-capture confidence interval'} if method=='sq_drag_scan' else {})}}

def compare_counts(rows,z):
    captures=sorted({r['capture'] for r in rows});split=len(captures)//2;training=set(captures[:split]);validation=captures[split:]
    if len(training)<2 or len(validation)<2:raise ValueError('Separate training and held-out captures required')
    conditions={};capture_f={};response={}
    for condition in ('off','on'):
        classes=[]
        for state in (0,1):
            values=np.concatenate([z[j] for j,r in enumerate(rows) if r['capture'] in training and r['condition']==condition and r['logicalState']==state])
            classes.append(np.column_stack((values.real,values.imag)))
        means=[a.mean(axis=0) for a in classes];pooled=(np.cov(classes[0].T)+np.cov(classes[1].T))/2
        ridge=max(float(np.trace(pooled))*1e-6,1e-20);direction=np.linalg.solve(pooled+np.eye(2)*ridge,means[1]-means[0]);threshold=float(direction@(means[0]+means[1])/2)
        if np.linalg.norm(means[1]-means[0])<=1e-15:raise ValueError('Classifier training states are unresolved')
        counts=np.zeros((2,2),dtype=int);per_capture={}
        for capture in validation:
            table=np.zeros((2,2),dtype=int)
            for state in (0,1):
                values=np.concatenate([z[j] for j,r in enumerate(rows) if r['capture']==capture and r['condition']==condition and r['logicalState']==state])
                pred=np.column_stack((values.real,values.imag))@direction>threshold
                table[state]=[int((~pred).sum()),int(pred.sum())]
            counts+=table;per_capture[capture]=float(1-.5*(table[0,1]/table[0].sum()+table[1,0]/table[1].sum()))
        n0,n1=map(int,counts.sum(axis=1));e0=counts[0,1]/n0;e1=counts[1,0]/n1;f=float(1-(e0+e1)/2)
        conditions[condition]={'counts':counts.tolist(),'n0':n0,'n1':n1,'wrongAs1':int(counts[0,1]),'wrongAs0':int(counts[1,0]),
            'fidelity':f,'standardErrorBinomial':float(.5*np.sqrt(e0*(1-e0)/n0+e1*(1-e1)/n1)),
            'perCaptureFidelity':per_capture,'classifier':{'direction':direction.tolist(),'threshold':threshold}}
        capture_f[condition]=per_capture
        for state in (0,1):
            selected=[z[j] for j,r in enumerate(rows) if r['capture'] in validation and r['condition']==condition and r['logicalState']==state]
            capture_means=np.array([x.mean() for x in selected]);all_samples=np.concatenate(selected)
            variance=max(float(np.var(capture_means.real,ddof=1)+np.var(capture_means.imag,ddof=1))/len(capture_means),
                         float(np.var(all_samples.real,ddof=1)+np.var(all_samples.imag,ddof=1))/len(all_samples))
            response[condition,state]=(capture_means.mean(),variance)
    differences=np.array([capture_f['on'][c]-capture_f['off'][c] for c in validation])
    gain=conditions['on']['fidelity']-conditions['off']['fidelity']
    shift=abs(response['on',1][0]-response['off',1][0]);ground_shift=abs(response['on',0][0]-response['off',0][0])
    shift_snr=float(shift/max(np.sqrt(response['on',1][1]+response['off',1][1]),1e-15))
    disturbance=float(ground_shift/max(shift,1e-15))
    reasons=[]
    if shift_snr<5:reasons.append('Conditional logical-1 ADC response shift is unresolved')
    if disturbance>.2:reasons.append('Logical-0 ADC disturbance exceeds frozen conditional-response diagnostic limit')
    return {'metrics':{'assignment_fidelity':conditions['on']['fidelity'],'assignment_gain_pp':100*gain,
                       'paired_capture_gain_stderr_pp':float(100*differences.std(ddof=1)/np.sqrt(len(differences))),
                       'heldout_captures':len(validation),'conditional_shift_snr':shift_snr,'ground_disturbance_ratio':disturbance},'candidateParameters':{},'series':[],
            'comparison':conditions,'classifierProtocol':{'type':'separate condition LDA; equal priors; fixed training/validation capture split',
            'trainingCaptures':sorted(training),'validationCaptures':validation,'logicalLabelsRetained':True},
            'analysis':{'accepted':not reasons,'reasons':reasons,'diagnosticPolicy':'conditional-response-assignment-v1',
                        'criteria':{'conditional_shift_snr':{'min':5},'ground_disturbance_ratio':{'max':.2}},
                        'scope':'Held-out logical assignment and conditional ADC response diagnostic; not proof of |2> population, transfer fidelity, three-state selectivity or leakage'}}
