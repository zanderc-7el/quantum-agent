"""Independent coefficient selection for the exact X/2 gate later characterized."""
from copy import deepcopy
import math
import random
from adapters.science_runtime.policy import digest
from adapters.science_v3.plans import build_rows
from .plans import FIELDS,identity
from . import beta

PROCEDURE='sq_drag_half_scan'
POLICY='native_science_v3_drag_xhalf_diagnostic_v1'
ROLES=('pi01','half01','anharmonicity','readout','rabi_amplitude','rabi_time')
SCAN_FIELDS=FIELDS|beta.SCAN_FIELDS|{'points','lambda_start','lambda_stop','drag_repeats','capture_repeats','scan_axis_values','verify_drag_lambda1','pulse_compatibility'}

def build_half_scan(parameters):
    if set(parameters)-SCAN_FIELDS:raise ValueError('Unknown half-DRAG parameters')
    if beta.empirical(parameters):return beta.build_half(parameters)
    p,rows=build_rows('sq_drag_scan',{k:v for k,v in parameters.items() if k!='drag_mode'})
    if 'drag_mode' in parameters:p['drag_mode']=parameters['drag_mode']
    if 0. not in p['scan_axis_values']:raise ValueError('Half-DRAG scan must acquire an actual zero coefficient baseline')
    for row in rows:
        row['stage']='half_drag_scan'
        if row['condition'] in ('diagnostic','verification_candidate'):
            # (X/2 Y/2)^3 closes ideally. Repeating this block amplifies errors
            # of the same half rotations; no full-pi substitution is performed.
            row['gates']=deepcopy(row['gates']*3)
            for gate in row['gates']:gate['theta']=math.pi/2
        if sum(g['pulse']['durationNs']+2 for g in row['gates'])>79700:raise ValueError('Half-DRAG sequence exceeds control window')
    ordered=[]
    for capture in range(p['capture_repeats']):
        subset=[r for r in rows if r['capture']==capture]
        random.Random(p['order_seed']+capture).shuffle(subset);ordered.extend(subset)
    rows=ordered
    for index,row in enumerate(rows):row['id']=index
    p={k:v for k,v in p.items() if k in SCAN_FIELDS}
    samples=len(rows)*round((80e-6+p['readout_width_s']+1e-6)*6e9);shots=len(rows)*p['shots']*p['acquisitions_per_point']
    if samples>400_000_000 or shots>10_000_000:raise ValueError('Half-DRAG sample or shot budget exceeded')
    bundle={'schema':'science-v3-half-drag-sequences/1','campaignPolicy':POLICY,'rows':rows,
            'diagnostic':'(X/2 Y/2)^3 repeated; fixed full-pi baseline references; independent coefficient scan then frozen candidate reacquisition',
            'budget':{'rows':len(rows),'samples':samples,'shots':shots}}
    bundle['sha256']=digest(bundle)
    return p,bundle

def target_identity(parameters,coefficient):
    if beta.empirical(parameters):
        p,pulse=beta.normalized_pulse({**parameters,'drag_beta_s':coefficient})
        return beta.pulse_identity_beta(p,pulse)
    return identity({k:(coefficient if k=='drag_lambda1' else v) for k,v in {**parameters,'drag_lambda1':coefficient}.items() if k in FIELDS})

def analyze(plan,i,q,_numeric_view=False):
    if not _numeric_view and beta.empirical(plan['parameters']):return beta.analyze_half(plan,i,q)
    from adapters.science_v3.analysis import analyze as analyze_diagnostic
    result=analyze_diagnostic({**plan,'procedureId':'sq_drag_scan'},i,q)
    result['analysis']['scope']='Exact fixed-duration X/2 coefficient diagnostic; no gate-fidelity claim until independent same-pulse IRB'
    result['analysis']['metricSemantics']={'diagnostic_improvement':'Observed zero-axis mean minus minimum acquired scan-axis mean'}
    for series in result.get('series',[]):series['xUnit']='dimensionless lambda1'
    if 'verify_drag_lambda1' in plan['parameters']:
        import numpy as np
        rows=plan['rows'];z=np.asarray(i)[:,0,:]+1j*np.asarray(q)[:,0,:];means=z.mean(axis=1)
        cloud=np.column_stack((means.real,means.imag));_,_,axes=np.linalg.svd(cloud-cloud.mean(axis=0),full_matrices=False)
        values=cloud@axes[0];differences=[]
        for capture in range(plan['parameters']['capture_repeats']):
            def index(condition,state=None,coefficient=None):
                return next(j for j,r in enumerate(rows) if r['capture']==capture and r['condition']==condition
                    and (state is None or r['logicalState']==state) and (coefficient is None or r['axisValue']==coefficient))
            delta=values[index('reference',1)]-values[index('reference',0)]
            if abs(delta)<=1e-15:raise ValueError('Half-DRAG baseline reference contrast unresolved')
            differences.append(float((values[index('diagnostic',coefficient=0.)]-values[index('verification_candidate')])/delta))
        mean=float(np.mean(differences));sem=float(np.std(differences,ddof=1)/np.sqrt(len(differences)))
        result['metrics'].update(candidate_improvement_vs_zero=mean,candidate_improvement_sem=sem,candidate_improvement_upper_5sem=mean+5*sem)
        result['candidateComparison']={'baselineLambda1':0.,'candidateLambda1':plan['parameters']['verify_drag_lambda1'],
            'pairedCaptureDifferences':differences,'mean':mean,'captureSem':sem,
            'scope':'Measured zero-coefficient half-gate minus dedicated frozen-candidate half-gate on each capture prepared-reference scale'}
        if mean+5*sem<0:
            result['analysis']['accepted']=False;result['analysis']['reasons'].append('Frozen half-gate candidate is worse than the actual zero baseline beyond five capture SEM')
    if not _numeric_view and 'drag_lambda1' in result['candidateParameters']:result['targetPulseIdentity']=target_identity(plan['parameters'],result['candidateParameters']['drag_lambda1'])
    return result
