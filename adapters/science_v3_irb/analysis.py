"""PRE-frozen independent projection and conservative native paired IRB analysis."""
import hashlib
import math
import numpy as np
from scipy.stats import t
from adapters.science_runtime.policy import digest

def raw_hash(z):return hashlib.sha256(np.ascontiguousarray(z,dtype='<c16').tobytes()).hexdigest()

def _data(rows,z,shots):
    z=np.asarray(z)
    if z.ndim==3 and z.shape[1]==1:z=z[:,0,:]
    if z.ndim!=2 or len(z)!=len(rows) or z.shape[1]<2 or z.shape[1]%shots or not np.isfinite(z).all():raise ValueError('Reference ADC dimensions or samples invalid')
    return z

def _sem(values,shots):
    values=np.asarray(values,float);iid=float(values.std(ddof=1)/np.sqrt(len(values)))
    blocks=values.reshape(-1,shots)
    return max(iid,float(blocks.mean(axis=1).std(ddof=1)/np.sqrt(len(blocks))) if len(blocks)>1 else iid)

def _capture_stats(values,sems):
    values=np.asarray(values,float);n=len(values)
    if n<2:raise ValueError('At least two independent reference captures are required')
    sem=max(float(values.std(ddof=1)/np.sqrt(n)),float(np.linalg.norm(sems)/n))
    half=float(t.ppf(.975,n-1)*sem)
    return {'mean':float(values.mean()),'sem':sem,'captureCount':n,'values':values.tolist(),'ci95Conditional':[float(values.mean()-half),float(values.mean()+half)]}

def _reference_summary(rows,z,projection,shots,captures):
    direction=np.asarray(projection['direction']);g=np.asarray(projection['ground_iq']);e=np.asarray(projection['excited_iq'])
    contrast=float((e-g)@direction)
    projected=((z.real-g[0])*direction[0]+(z.imag-g[1])*direction[1])/contrast
    values={name:[] for name in ('ground','excited','contrast')};sems={name:[] for name in values}
    details=[]
    for capture in captures:
        states=[]
        for state in (0,1):
            match=[j for j,r in enumerate(rows) if r['capture']==capture and r['logicalState']==state]
            if len(match)!=1:raise ValueError('Reference capture is incomplete or duplicated')
            v=projected[match[0]];states.append((float(v.mean()),_sem(v,shots)))
        delta=states[1][0]-states[0][0];se=float(np.hypot(states[0][1],states[1][1]))
        details.append({'capture':capture,'ground':states[0][0],'excited':states[1][0],
                        'groundSem':states[0][1],'excitedSem':states[1][1],
                        'contrast':delta,'contrastSem':se,'contrastSnr':delta/max(se,1e-15)})
        for name,(value,error) in zip(('ground','excited','contrast'),states+[(delta,se)]):values[name].append(value);sems[name].append(error)
    return {'statistics':{k:_capture_stats(values[k],sems[k]) for k in values},'captures':details}

def freeze_pre(rows,z,*,shots,source):
    z=_data(rows,z,shots);captures=sorted({r['capture'] for r in rows})
    if len(captures)<4 or any(r['stage']!='pre' for r in rows):raise ValueError('PRE requires at least four separate captures')
    split=len(captures)//2;train=captures[:split];held=captures[split:];clouds=[]
    for state in (0,1):
        values=np.concatenate([z[j] for j,r in enumerate(rows) if r['capture'] in train and r['logicalState']==state])
        clouds.append(np.column_stack((values.real,values.imag)))
    g,e=[x.mean(axis=0) for x in clouds];cov=(np.cov(clouds[0].T)+np.cov(clouds[1].T))/2
    cov+=np.eye(2)*max(float(np.trace(cov))*1e-8,1e-30)
    direction=np.linalg.solve(cov,e-g);norm=np.linalg.norm(direction)
    if not math.isfinite(norm) or norm<=1e-15:raise ValueError('PRE training states unresolved')
    direction/=norm;contrast=float((e-g)@direction)
    if contrast<=1e-15:raise ValueError('PRE training contrast unresolved')
    projection={'independent_reference':True,'source':source,'direction':direction.tolist(),'ground_iq':g.tolist(),'excited_iq':e.tolist(),
                'training_captures':train,'validation_captures':held,'reference_raw_sha256':raw_hash(z),
                'normalization':'ground=1, excited=0 for paired analysis; no clipping',
                'reference_uncertainty_in_RB_bootstrap':False}
    summary=_reference_summary(rows,z,projection,shots,held)
    offsets=max(abs(summary['statistics']['ground']['mean']),abs(summary['statistics']['excited']['mean']-1))
    capture_offsets=max(max(abs(c['ground'])+5*c['groundSem'],abs(c['excited']-1)+5*c['excitedSem']) for c in summary['captures'])
    if summary['statistics']['contrast']['ci95Conditional'][0]<=0 or any(c['contrastSnr']<5 for c in summary['captures']) or max(offsets,capture_offsets)>.1:raise ValueError('PRE held-out contrast/offset not resolved or repeat consistent; branches not admitted')
    value={'schema':'science-v3-irb-frozen-pre/1','accepted':True,'projection':projection,'heldOut':summary,
           'rowsSha256':digest(rows),'rawSha256':raw_hash(z),'scope':'Independent prepared-state reference, not gate fidelity'}
    value['sha256']=digest(value)
    return value

def check_post(rows,z,frozen,*,shots):
    z=_data(rows,z,shots)
    if digest({k:v for k,v in frozen.items() if k!='sha256'})!=frozen['sha256']:raise ValueError('Frozen PRE changed')
    if any(r['stage']!='post' for r in rows):raise ValueError('POST stage identity differs')
    after=_reference_summary(rows,z,frozen['projection'],shots,sorted({r['capture'] for r in rows}));before=frozen['heldOut']['statistics']
    changes={};drift=0.;upper=0.
    for key in ('ground','excited','contrast'):
        old=before[key];new=after['statistics'][key];scale=abs(old['mean']) if key=='contrast' else 1.
        if scale<=1e-15:raise ValueError('PRE contrast denominator unresolved')
        delta=(new['mean']-old['mean'])/scale;sem=math.hypot(old['sem'],new['sem'])/scale
        critical=float(t.ppf(.975,min(old['captureCount'],new['captureCount'])-1))
        bound=abs(delta)+critical*sem;drift=max(drift,abs(delta));upper=max(upper,bound)
        changes[key]={'postMinusPreFraction':delta,'semFraction':sem,'absolute95UpperDiagnostic':bound}
    return {'accepted':upper<=.1,'reference_drift_fraction':drift,'reference_drift_upper_diagnostic':upper,
            'changes':changes,'post':after,'policy':'fixed PRE scale, no POST refit, maximum upper diagnostic <= 0.1',
            'uncertaintyScope':'Conditional independent capture means; t critical uses minimum PRE/POST degrees of freedom'}

def analyze(plan,i,q,frozen):
    from paired_rb_support import analyze_paired_rb
    from .sequences import verify_campaign
    verify_campaign(plan['campaign'],plan['parameters'])
    rows=plan['rows'];z=_data(rows,np.asarray(i)+1j*np.asarray(q),plan['parameters']['shots'])
    pre=[j for j,r in enumerate(rows) if r['stage']=='pre'];post=[j for j,r in enumerate(rows) if r['stage']=='post']
    if raw_hash(z[pre])!=frozen['rawSha256'] or digest([rows[j] for j in pre])!=frozen['rowsSha256']:raise ValueError('PRE reference does not match acquired rows')
    drift=check_post([rows[j] for j in post],z[post],frozen,shots=plan['parameters']['shots'])
    branches={}
    for mode in ('rb','irb'):
        ids=[j for j,r in enumerate(rows) if r['stage']==mode];axes=[]
        for j in ids:
            r=rows[j];key=r['sequenceKey'];base=plan['parameters']['rb_lengths'].index(key['clifford_m'])*plan['parameters']['rb_sequences']+key['repeat']
            axes.append({'capture':r['capture'],'base_index':base,'axes':key})
        branches[mode]=(axes,z[ids])
    p=plan['parameters'];man=plan['campaign']['nativeManifests']
    result=analyze_paired_rb(*branches['rb'],*branches['irb'],frozen['projection'],ref_manifest=man['rb'],irb_manifest=man['irb'],
                bootstrap_samples=p['rb_bootstrap_replicates'],bootstrap_seed=p['rb_master_seed'],min_seeds=20)
    metrics={'reference_drift_fraction':drift['reference_drift_fraction'],'reference_drift_upper_diagnostic':drift['reference_drift_upper_diagnostic'],
             'independent_sequences_per_length':p['rb_sequences'],'rb_captures':p['rb_captures']}
    r2=[]
    for key,name in (('reference_fit','reference'),('interleaved_fit','interleaved')):
        if key in result:
            y=np.array(result['curves'][name+'_mean']);pred=np.array(result[key]['fitted_values']);den=np.sum((y-y.mean())**2)
            if den>0:r2.append(float(1-np.sum((y-pred)**2)/den))
    reasons=list(result.get('reasons',[]))
    if not drift['accepted']:reasons.append('POST reference drift or its uncertainty exceeds frozen tolerance')
    if len(r2)!=2 or min(r2)<.95:reasons.append('Both independent exponential curves require R squared >= .95')
    if r2:metrics['r_squared']=min(r2)
    accepted=bool(result.get('reportable')) and not reasons
    if accepted:metrics.update(gate_fidelity=result['F_X_half_conditional'],clifford_fidelity=result['F_clifford'])
    else:
        result.update(fitReportableBeforeReferenceGates=bool(result.get('reportable')),reportable=False,status='not_reportable',
                      F_X_half_conditional=None,F_clifford=None)
    series=[]
    for name,fit_key in (('reference','reference_fit'),('interleaved','interleaved_fit')):
        if name+'_mean' in result.get('curves',{}):
            series.append({'name':name+' measured','x':result['lengths'],'y':result['curves'][name+'_mean'],
                'xUnit':'Cliffords','yUnit':'fixed PRE prepared-reference projection','quantity':'iq_projection',
                'standardError':result['curves'].get(name+'_seed_sem')})
        if fit_key in result:
            series.append({'name':name+' exponential fit','x':result['lengths'],'y':result[fit_key]['fitted_values'],
                'xUnit':'Cliffords','yUnit':'fixed PRE prepared-reference projection','quantity':'iq_projection'})
    return {'metrics':metrics,'candidateParameters':{},'series':series,
        'analysis':{'accepted':accepted,'reasons':reasons,'scope':'Conditional independent DRAG X/2 interleaved RB; no process-tomography/leakage claim'},
        'irb':result,'reference':{'pre':frozen,'post':drift},'targetPulseIdentity':plan['campaign']['targetPulseIdentity']}
