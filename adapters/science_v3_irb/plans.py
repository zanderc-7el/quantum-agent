"""Pure bounded campaign parameters. Native sequence synthesis is isolated."""
from copy import deepcopy
import math
from adapters.science_runtime.policy import digest
from adapters.science_v3.plans import BOUNDS as PULSE_BOUNDS,COMMON_FIELDS,TARGET_FIELDS,build_rows,pulse_identity
from . import beta

POLICY='native_science_v3_drag_xhalf_paired_irb_prepost_v1'
PROCEDURE='sq_drag_irb'
ROLES=('pi01','half01','drag','anharmonicity','readout')
FIELDS=COMMON_FIELDS|TARGET_FIELDS|{'drag_lambda1','anharmonicity_hz'}|beta.FIELDS
DEFAULTS={'rb_lengths':[1,4,16,64,128],'rb_sequences':20,'rb_captures':2,'reference_captures':4,
          'rb_master_seed':20261126,'rb_bootstrap_replicates':1000,'rb_target':'X/2'}
BOUNDS={'rb_sequences':[20,100],'rb_captures':[2,4],'reference_captures':[4,12],
        'rb_master_seed':[0,2**32-1],'rb_bootstrap_replicates':[100,2000]}

def normalize_parameters(parameters):
    if not isinstance(parameters,dict):raise ValueError('IRB parameters must be an object')
    allowed=FIELDS|set(DEFAULTS)|{'pulse_compatibility'}
    if set(parameters)-allowed:raise ValueError('Unknown IRB parameters: '+','.join(sorted(set(parameters)-allowed)))
    p={**deepcopy(DEFAULTS),**deepcopy(parameters)}
    if p['rb_target']!='X/2':raise ValueError('Only independent X/2 IRB is supported')
    for key,(lo,hi) in BOUNDS.items():
        if type(p[key]) is not int or not lo<=p[key]<=hi:raise ValueError(key+' outside bounded integer range')
    lengths=p['rb_lengths']
    if not isinstance(lengths,list) or not 5<=len(lengths)<=12 or any(type(n) is not int or not 1<=n<=512 for n in lengths):raise ValueError('IRB requires 5..12 integer lengths in 1..512')
    if any(b<=a for a,b in zip(lengths,lengths[1:])):raise ValueError('RB lengths must be strictly increasing')
    # Reuse the frozen pulse grid/shape/DRAG validation, not its scan defaults.
    pulse={k:v for k,v in p.items() if k in FIELDS or k=='pulse_compatibility'}
    if beta.empirical(p):
        normalized,_=beta.normalized_pulse(pulse)
        for key in ('drag_lambda1','anharmonicity_hz'):p.pop(key,None)
    else:
        normalized,_=build_rows('sq_drag_scan',{**{k:v for k,v in pulse.items() if k!='drag_mode'},'capture_repeats':2,'scan_axis_values':[-1.,-.5,0.,.5,1.]})
    p.update({k:normalized[k] for k in FIELDS if k in normalized})
    rows=2*p['rb_sequences']*len(lengths)*p['rb_captures']+4*p['reference_captures']
    samples=rows*round((80e-6+p['readout_width_s']+1e-6)*6e9)
    shots=rows*p['shots']*p['acquisitions_per_point']
    if rows>3000 or samples>400_000_000 or shots>10_000_000:raise ValueError('IRB campaign exceeds actual row/sample/shot budget')
    return p,{'rows':rows,'samples':samples,'shots':shots}

def verification_parameters(parameters):
    p,_=normalize_parameters(parameters)
    # A bijection modulo 2**32 guarantees a different independent sequence set.
    p['rb_master_seed']=(p['rb_master_seed']+0x9e3779b9)%(2**32)
    return p

def target_pulse(parameters):
    p,_=normalize_parameters(parameters)
    if beta.empirical(p):return beta.normalized_pulse(p)[1]
    _,rows=build_rows('sq_drag_scan',{**{k:v for k,v in p.items() if k in FIELDS and k!='drag_mode'},'capture_repeats':2,'scan_axis_values':[-1.,-.5,0.,.5,1.]})
    pulse=deepcopy(rows[0]['gates'][0]['pulse']);pulse.update(lambda1=p['drag_lambda1'],anharmonicityHz=p['anharmonicity_hz'])
    return pulse

def identity(parameters,half=True):
    p,_=normalize_parameters(parameters)
    if beta.empirical(p):return beta.pulse_identity_beta(p,target_pulse(p),half)
    value=pulse_identity(target_pulse(p),{k:p[k] for k in ('readout_hz','readout_amp','readout_width_s','xy_delay_s')})
    if half:value.update(amplitude=value['amplitude']/2,rotationRad=math.pi/2)
    return {'identity':value,'sha256':digest(value)}
