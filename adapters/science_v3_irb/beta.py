"""Empirical signed derivative multiplier. Seconds, never a fictitious alpha.

The numeric beta cap is the outer envelope of the existing |lambda|<=10,
|Delta|>=1 MHz policy. It is a planning bound, not a hardware voltage claim.
Actual native baseband and DAC sample limits remain independently enforced.
"""
from copy import deepcopy
import math
import random
from adapters.science_runtime.policy import digest
from adapters.science_v3.plans import COMMON_FIELDS,TARGET_FIELDS,build_rows,pulse_identity

MODE='empirical_beta'
PULSE_POLICY='native_science_v3_empirical_beta_fixed_duration_v1'
HALF_POLICY='native_science_v3_drag_xhalf_empirical_beta_v1'
IRB_POLICY='native_science_v3_drag_xhalf_empirical_beta_paired_irb_prepost_v1'
MAX_BETA_S=10/(2*math.pi*1e6)
FIELDS={'drag_mode','drag_beta_s'}
SCAN_FIELDS=FIELDS|{'beta_start_s','beta_stop_s','verify_drag_beta_s'}
BASE_FIELDS=COMMON_FIELDS|TARGET_FIELDS|{'pulse_compatibility'}


def empirical(p):
    value=p.get('drag_mode','normalized_alpha')
    if value not in ('normalized_alpha',MODE):raise ValueError('Unknown DRAG coefficient mode')
    return value==MODE


def mode_of(p):return MODE if empirical(p) else 'normalized_alpha'


def candidate_key(p):return 'drag_beta_s' if empirical(p) else 'drag_lambda1'


def required_roles(procedure,p):
    from .half_scan import ROLES as HALF_ROLES
    from .plans import ROLES
    roles=HALF_ROLES if procedure=='sq_drag_half_scan' else ROLES
    return tuple(r for r in roles if r!='anharmonicity' or not empirical(p))


def policy_for(procedure,p):
    if procedure=='sq_drag_half_scan':
        from .half_scan import POLICY
        return HALF_POLICY if empirical(p) else POLICY
    if procedure=='sq_drag_irb':
        from .plans import POLICY
        return IRB_POLICY if empirical(p) else POLICY
    raise ValueError('Unsupported empirical DRAG procedure')


def pulse_policy(p):
    from adapters.science_v3.plans import POLICY
    return PULSE_POLICY if empirical(p) else POLICY


def coefficient_evidence(p,value=None):
    if not empirical(p):return None
    return {'mode':MODE,'unit':'s','betaSeconds':finite_beta(p.get('drag_beta_s',0.) if value is None else value),
        'convention':'(I+i*beta*dI/dt)*exp(i*phase), native np.gradient at 4 GHz before 2 ns zero buffer',
        'inferredAnharmonicityHz':None,'limits':{'coefficientAbsoluteMaximumS':MAX_BETA_S,
        'basebandPeakNormalized':1.,'dacPeakNormalized':.990001,'sampleRateHz':4e9,'bufferS':2e-9,
        'sampleSlopeMaximumNormalizedPerSecond':8e9,
        'sampleSlopeScope':'Consequence of the existing unit-peak sample bound; not a calibrated voltage slew limit'}}


def finite_beta(value):
    try:ok=type(value) in (int,float) and math.isfinite(value) and abs(value)<=MAX_BETA_S
    except OverflowError:ok=False
    if not ok:raise ValueError('Empirical beta must be finite seconds within the bounded derivative policy')
    return value


def normalized_pulse(p):
    # Only the existing shape/timing validator is reused; no lambda/alpha path.
    base,rows=build_rows('sq_shape_half_verify',{k:v for k,v in p.items() if k in BASE_FIELDS})
    clean={k:v for k,v in base.items() if k in BASE_FIELDS}
    clean.update(drag_mode=MODE,drag_beta_s=finite_beta(p.get('drag_beta_s',0.)))
    pulse=deepcopy(rows[0]['gates'][0]['pulse'])
    pulse.update(dragMode=MODE,betaSeconds=clean['drag_beta_s'],lambda1=None,anharmonicityHz=None)
    return clean,pulse


def pulse_identity_beta(p,pulse,half=True):
    value=pulse_identity(pulse,{k:p[k] for k in ('readout_hz','readout_amp','readout_width_s','xy_delay_s')})
    value.update(policy=PULSE_POLICY,dragMode=MODE,betaSeconds=finite_beta(pulse['betaSeconds']))
    if half:value.update(amplitude=value['amplitude']/2,rotationRad=math.pi/2)
    return {'identity':value,'sha256':digest(value)}


def build_half(p):
    clean,pulse=normalized_pulse(p)
    count=p.get('points',21);repeats=p.get('drag_repeats',4)
    if type(count) is not int or not 5<=count<=41 or type(repeats) is not int or not 1<=repeats<=16:
        raise ValueError('Empirical half scan points/repeats outside existing policy')
    start=finite_beta(p.get('beta_start_s',-1e-9));stop=finite_beta(p.get('beta_stop_s',1e-9))
    axis=deepcopy(p.get('scan_axis_values'))
    if axis is None:
        axis=[start+(stop-start)*j/(count-1) for j in range(count)];axis[0]=start;axis[-1]=stop
    if not isinstance(axis,list) or not 5<=len(axis)<=41:raise ValueError('Empirical half scan needs 5..41 coefficients')
    for value in axis:finite_beta(value)
    if any(b<=a for a,b in zip(axis,axis[1:])) or 0. not in axis:raise ValueError('Empirical half scan requires increasing seconds and an actual zero baseline')
    clean.update(points=count,drag_repeats=repeats,beta_start_s=start,beta_stop_s=stop,scan_axis_values=axis)
    candidate=p.get('verify_drag_beta_s')
    if 'verify_drag_beta_s' in p:
        finite_beta(candidate)
        if not axis[0]<=candidate<=axis[-1]:raise ValueError('Frozen beta outside independently scanned window')
        clean['verify_drag_beta_s']=candidate
    rows=[]
    for capture in range(clean['capture_repeats']):
        subset=[]
        def add(condition,x,gates,state=None):
            if sum(g['pulse']['durationNs']+2 for g in gates)>79700:raise ValueError('Half-DRAG sequence exceeds control window')
            subset.append({'capture':capture,'stage':'half_drag_scan','condition':condition,'axisValue':x,'logicalState':state,'gates':gates})
        def diagnostic(value):
            return [{'axis':ax,'theta':math.pi/2,'pulse':{**deepcopy(pulse),'betaSeconds':value}}
                    for _ in range(repeats*3) for ax in ('x','y')]
        for x in axis:add('diagnostic',x,diagnostic(x))
        for state in (0,1):add('reference',state,[{'axis':'x','theta':math.pi*state,'pulse':{**deepcopy(pulse),'betaSeconds':0.}}],state)
        if candidate is not None:
            add('verification_candidate',candidate,diagnostic(candidate));subset[-1]['baseCondition']='diagnostic'
        random.Random(clean['order_seed']+capture).shuffle(subset);rows.extend(subset)
    for index,row in enumerate(rows):row['id']=index
    samples=len(rows)*round((80e-6+clean['readout_width_s']+1e-6)*6e9)
    shots=len(rows)*clean['shots']*clean['acquisitions_per_point']
    if len(rows)>3000 or samples>400_000_000 or shots>10_000_000:raise ValueError('Half-DRAG sample or shot budget exceeded')
    bundle={'schema':'science-v3-half-drag-sequences/1','campaignPolicy':HALF_POLICY,'rows':rows,
        'coefficient':{'mode':MODE,'unit':'s','convention':'Q=beta*dI/dt before native phase rotation'},
        'diagnostic':'(X/2 Y/2)^3 repeated; measured zero-beta half baseline and fixed zero-beta full-pi references',
        'budget':{'rows':len(rows),'samples':samples,'shots':shots}}
    bundle['sha256']=digest(bundle)
    return clean,bundle


def analyze_half(plan,i,q):
    # Coordinate scaling only for stable polynomial fitting. No pulse compiler
    # consumes this private numeric view; its axis is beta in nanoseconds.
    from .half_scan import analyze
    view=deepcopy(plan);p=view['parameters'];p.pop('drag_mode',None)
    p['scan_axis_values']=[v*1e9 for v in p['scan_axis_values']]
    if 'verify_drag_beta_s' in p:p['verify_drag_lambda1']=p['verify_drag_beta_s']*1e9
    for row in view['rows']:
        if row['condition']!='reference':row['axisValue']*=1e9
    result=analyze(view,i,q,_numeric_view=True)
    candidate=result['candidateParameters'].pop('drag_lambda1',None)
    selected=plan['parameters'].get('verify_drag_beta_s',candidate*1e-9 if candidate is not None else None)
    if candidate is not None:result['candidateParameters']['drag_beta_s']=selected
    if 'optimum_fit_stderr' in result['metrics']:result['metrics']['optimum_fit_stderr']*=1e-9
    for series in result.get('series',[]):
        series['x']=list(plan['parameters']['scan_axis_values']);series['xUnit']='s'
    comparison=result.get('candidateComparison')
    if comparison is not None:
        comparison['baselineBetaSeconds']=comparison.pop('baselineLambda1')*1e-9
        comparison.pop('candidateLambda1');comparison['candidateBetaSeconds']=plan['parameters']['verify_drag_beta_s']
        comparison.update(coefficientMode=MODE,coefficientUnit='s')
    result['analysis'].update(coefficientMode=MODE,coefficientUnit='s',
        coefficientConvention='Q=beta*dI/dt before native phase rotation; empirical beta does not measure anharmonicity')
    if candidate is not None:
        p0,pulse=normalized_pulse({**plan['parameters'],'drag_beta_s':selected})
        result['targetPulseIdentity']=pulse_identity_beta(p0,pulse)
    return result
