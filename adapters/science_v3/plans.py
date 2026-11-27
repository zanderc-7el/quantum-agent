"""Finite numeric circuit templates. Planning never imports the native runtime."""
from copy import deepcopy
import math
from adapters.science_runtime.policy import canonical,digest,normalize

POLICY='native_science_v3_fixed_duration_drag_segmented_carrier_v1'
METHODS={
 'sq_shape_rabi_amplitude':('Shape Rabi','s03_rabi_amplitude',('f01','readout')),
 'sq_shape_half_verify':('Shape half rotation','s03_rabi_amplitude',('pi01','readout')),
 'sq_shelve_spectrum12':('SHELVE conditional spectrum','s02_qubit_frequency',('pi01','f01','readout','xy_passband')),
 'sq_shelve_rabi12':('SHELVE conditional Rabi','s03_rabi_amplitude',('pi01','spectrum12','readout','xy_passband')),
 'sq_shelve_readout_compare':('SHELVE held-out assignment','s04_readout_fidelity',('pi01','spectrum12','pi12','readout','xy_passband')),
 'sq_drag_scan':('DRAG diagnostic','s03_rabi_amplitude',('pi01','half01','anharmonicity','readout'))}
BOUNDS={'pulse_shape':{'enum':['Gaussian','CosPulse']},'pulse_length_s':[4e-9,2e-6],
 'pulse_sigma_s':[.25e-9,1e-6],'shape_pi_amp':[.0001,.99],'pulse_phase_rad':[-2*math.pi,2*math.pi],
 'drag_lambda1':[-10,10],'anharmonicity_hz':[-2e9,2e9],
 'prep01_shape':{'enum':['DC','Gaussian','CosPulse']},'prep01_sigma_s':[.25e-9,1e-6],
 'probe12_shape':{'enum':['Gaussian','CosPulse']},'probe12_length_s':[4e-9,2e-6],
 'probe12_sigma_s':[.25e-9,1e-6],'probe12_amplitude':[0,.99],'probe12_phase_rad':[-2*math.pi,2*math.pi],
 'f12_hz':[1e6,10e9],'shots':[2,2047],'capture_repeats':[2,12],'acquisitions_per_point':[1,16],
 'points':[9,241],'amp_start':[0,.99],'amp_stop':[0,.99],'start_hz':[1e6,10e9],'stop_hz':[1e6,10e9],
 'lambda_start':[-10,10],'lambda_stop':[-10,10],'drag_repeats':[1,16],
 'drive_hz':[4e9,5.5e9],'drive_amp':[.0001,.99],'drive_length_s':[4e-9,2e-6],
 'half_pi_amp':[.0001,.99],'readout_hz':[6.5e9,7.6e9],'readout_amp':[.01,.98],
 'readout_width_s':[1.5e-6,10e-6],'xy_delay_s':[0,1e-6],'xy_attenuation_db':[0,30],
 'xy_filter_mhz':[100,10000],'order_seed':[0,2**32-1]}
INTEGERS={'shots','capture_repeats','acquisitions_per_point','points','drag_repeats','xy_filter_mhz','order_seed'}
VERIFY={'sq_shape_rabi_amplitude':'shape_pi_amp','sq_shelve_spectrum12':'f12_hz',
        'sq_shelve_rabi12':'probe12_amplitude','sq_drag_scan':'drag_lambda1'}
COMMON_FIELDS={'drive_hz','drive_amp','drive_length_s','readout_hz','readout_amp','readout_width_s','xy_delay_s',
               'xy_attenuation_db','xy_filter_mhz','shots','capture_repeats','acquisitions_per_point','order_seed'}
TARGET_FIELDS={'pulse_shape','pulse_length_s','pulse_sigma_s','shape_pi_amp','pulse_phase_rad'}
PROBE_FIELDS={'prep01_shape','prep01_sigma_s','probe12_shape','probe12_length_s','probe12_sigma_s','probe12_amplitude','probe12_phase_rad'}
METHOD_FIELDS={
 'sq_shape_rabi_amplitude':COMMON_FIELDS|TARGET_FIELDS|{'points','amp_start','amp_stop'},
 'sq_shape_half_verify':COMMON_FIELDS|TARGET_FIELDS,
 'sq_drag_scan':COMMON_FIELDS|TARGET_FIELDS|{'points','lambda_start','lambda_stop','drag_lambda1','anharmonicity_hz','drag_repeats'},
 'sq_shelve_spectrum12':COMMON_FIELDS|PROBE_FIELDS|{'points','start_hz','stop_hz'},
 'sq_shelve_rabi12':COMMON_FIELDS|PROBE_FIELDS|{'points','amp_start','amp_stop','f12_hz'},
 'sq_shelve_readout_compare':COMMON_FIELDS|PROBE_FIELDS|{'f12_hz'}}
DEFAULTS={'pulse_shape':'CosPulse','pulse_length_s':20e-9,'pulse_sigma_s':4e-9,'shape_pi_amp':.3,'pulse_phase_rad':0.,
          'drag_lambda1':0.,'prep01_shape':'DC','probe12_shape':'CosPulse','probe12_length_s':100e-9,
          'probe12_sigma_s':20e-9,'probe12_amplitude':.1,'probe12_phase_rad':0.,'shots':1000,'capture_repeats':4,
          'acquisitions_per_point':1,'points':61,'amp_start':0.,'amp_stop':.98,'lambda_start':-1.,'lambda_stop':1.,'drag_repeats':4}

def _finite(x,key,lo,hi):
    if type(x) not in (int,float) or not math.isfinite(x) or not lo<=x<=hi:raise ValueError(f'{key} outside [{lo}, {hi}]')

def build_rows(method,parameters):
    if method not in METHODS:raise ValueError('Unsupported science v3 method')
    if not isinstance(parameters,dict):raise ValueError('Parameters must be an object')
    allowed=set(BOUNDS)|{'scan_axis_values','pulse_compatibility'}|{'verify_'+v for v in VERIFY.values()}
    if set(parameters)-allowed:raise ValueError('Unknown science parameters: '+','.join(sorted(set(parameters)-allowed)))
    p={'pulse_shape':'CosPulse','pulse_length_s':20e-9,'shape_pi_amp':.3,'pulse_phase_rad':0.,'drag_lambda1':0.,
       'prep01_shape':'DC','probe12_shape':'CosPulse','probe12_length_s':100e-9,'probe12_amplitude':.1,'probe12_phase_rad':0.,
       'shots':1000,'capture_repeats':4,'acquisitions_per_point':1,'points':61,'amp_start':0.,'amp_stop':.98,
       'lambda_start':-1.,'lambda_stop':1.,'drag_repeats':4,'order_seed':20261126,**deepcopy(parameters)}
    if method=='sq_drag_scan' and 'points' not in parameters:p['points']=21
    for key,value in p.items():
        rule=BOUNDS.get(key)
        if isinstance(rule,dict):
            if value not in rule['enum']:raise ValueError(key+' unsupported')
        elif rule:
            _finite(value,key,*rule)
            if key in INTEGERS and type(value) is not int:raise ValueError(key+' must be integer')
        elif key.startswith('verify_'):_finite(value,key,*BOUNDS[key[7:]])
    if method=='sq_shelve_readout_compare' and p['capture_repeats']<4:raise ValueError('Held-out comparison requires at least four captures')
    def pulse(transition,amplitude=None,frequency=None):
        if transition=='probe12':
            shape=p['probe12_shape'];duration=p['probe12_length_s'];amp=p['probe12_amplitude'];freq=p.get('f12_hz')
            sigma=p.get('probe12_sigma_s');phase=p['probe12_phase_rad'];lam=0.
        elif transition=='prep01':
            shape=p['prep01_shape'];duration=p.get('drive_length_s');amp=p.get('drive_amp');freq=p.get('drive_hz')
            sigma=p.get('prep01_sigma_s');phase=0.;lam=0.
        else:
            shape=p['pulse_shape'];duration=p['pulse_length_s'];amp=p['shape_pi_amp'];freq=p.get('drive_hz')
            sigma=p.get('pulse_sigma_s');phase=p['pulse_phase_rad'];lam=p['drag_lambda1']
        if duration is None or (frequency if frequency is not None else freq) is None:raise ValueError('Pulse needs explicit current frequency and duration')
        input_={'shape':shape,'durationNs':duration*1e9,'amplitude':amp if amplitude is None else amplitude,
                'frequencyHz':freq if frequency is None else frequency,'phaseRad':phase,'lambda1':lam}
        if sigma is not None:input_['sigmaNs']=sigma*1e9
        if lam:
            input_.update(anharmonicityHz=p.get('anharmonicity_hz'),anharmonicitySource='server_authenticated_anharmonicity_dependency')
        normalized=normalize({'kind':'pulse','input':input_})['input']
        return {k:normalized[k] for k in ('shape','durationNs','amplitude','frequencyHz','phaseRad','lambda1','anharmonicityHz') } | ({'sigmaNs':normalized['sigmaNs']} if 'sigmaNs' in normalized else {}) | {'label':transition}
    # Validate every fixed shape, including parameters not swept in this method.
    target=pulse('target01')
    def gate(pulse_,theta=math.pi,axis='x'):return {'axis':axis,'theta':theta,'pulse':deepcopy(pulse_)}
    if method in ('sq_shape_half_verify','sq_shelve_readout_compare'):
        if 'scan_axis_values' in p:raise ValueError('This method has fixed preparation conditions')
        axis=[0.]
    else:
        if method=='sq_shelve_spectrum12':key='frequencyHz';low,high=1e6,10e9;start,stop=p.get('start_hz'),p.get('stop_hz')
        elif method=='sq_drag_scan':key='lambda1';low,high=-10,10;start,stop=p['lambda_start'],p['lambda_stop']
        else:key='amplitude';low,high=0,.99;start,stop=p['amp_start'],p['amp_stop']
        axis=p.get('scan_axis_values')
        if axis is None:
            if start is None or stop is None:raise ValueError('Explicit frequency search endpoints are required')
            axis=[start+(stop-start)*i/(p['points']-1) for i in range(p['points'])];axis[0]=start;axis[-1]=stop
        if not isinstance(axis,list) or not 3<=len(axis)<=241:raise ValueError('Axis requires 3..241 values')
        if method=='sq_drag_scan' and len(axis)>41:raise ValueError('DRAG maximum is 41 coefficients')
        for item in axis:_finite(item,key,low,high)
        if any(b<=a for a,b in zip(axis,axis[1:])):raise ValueError('Axis must be strictly increasing')
        p['scan_axis_values']=axis
    rows=[]
    def add(capture,condition,x,gates,state=None):
        if sum(g['pulse']['durationNs']+2 for g in gates)>79700:raise ValueError('Sequence exceeds control window')
        rows.append({'id':len(rows),'capture':capture,'condition':condition,'axisValue':x,'logicalState':state,'gates':gates})
    for capture in range(p['capture_repeats']):
        if method=='sq_shape_rabi_amplitude':
            for x in axis:add(capture,'rabi',x,[gate(pulse('target01',amplitude=x))])
        elif method=='sq_shape_half_verify':
            for angle in (0,.5,1,1.5,2):add(capture,'rotation',angle,[gate(target,math.pi*angle)])
        elif method=='sq_drag_scan':
            for x in axis:
                xp=pulse('target01');xp['lambda1']=x;xp['anharmonicityHz']=p.get('anharmonicity_hz')
                _finite(xp['anharmonicityHz'],'anharmonicity_hz',-2e9,2e9)
                if abs(xp['anharmonicityHz'])<1e6:raise ValueError('DRAG requires nonzero authenticated anharmonicity')
                add(capture,'diagnostic',x,[gate(xp,axis=ax) for _ in range(p['drag_repeats']) for ax in ('x','y')])
            reference=deepcopy(target);reference.update(lambda1=0.,anharmonicityHz=None)
            add(capture,'reference',0,[gate(reference,0)],0);add(capture,'reference',1,[gate(reference)],1)
        elif method=='sq_shelve_spectrum12':
            # Fixed preparation including an equally long zero-amplitude control.
            for condition,amp in [('prep_off',0),('prep_on',p['drive_amp'])]:
                for x in axis:add(capture,condition,x,[gate(pulse('prep01',amplitude=amp)),gate(pulse('probe12',frequency=x))])
        elif method=='sq_shelve_rabi12':
            for x in axis:add(capture,'rabi12',x,[gate(pulse('prep01')),gate(pulse('probe12',amplitude=x))])
        else:
            for condition in ('off','on'):
                for state in (0,1):add(capture,condition,float(state),[gate(pulse('prep01',amplitude=p['drive_amp']*state)),
                      gate(pulse('probe12',amplitude=p['probe12_amplitude'] if condition=='on' else 0))],state)
    verify_key=VERIFY.get(method)
    if verify_key and 'verify_'+verify_key in p:
        selected=p['verify_'+verify_key]
        if not axis[0]<=selected<=axis[-1]:raise ValueError('Frozen candidate outside independently scanned window')
        conditions=('prep_off','prep_on') if method=='sq_shelve_spectrum12' else ('diagnostic',) if method=='sq_drag_scan' else ('rabi12',) if method=='sq_shelve_rabi12' else ('rabi',)
        for capture in range(p['capture_repeats']):
            for condition in conditions:
                reference=next(r for r in rows if r['capture']==capture and r['condition']==condition)
                gates=deepcopy(reference['gates'])
                for g in gates:
                    if verify_key=='shape_pi_amp':g['pulse']['amplitude']=selected
                    elif verify_key=='drag_lambda1':g['pulse']['lambda1']=selected
                    elif g['pulse']['label']=='probe12':g['pulse']['frequencyHz' if verify_key=='f12_hz' else 'amplitude']=selected
                add(capture,'verification_candidate',selected,gates);rows[-1]['baseCondition']=condition
    if len(rows)>3000 or len(rows)*p['shots']*p['acquisitions_per_point']>10_000_000:raise ValueError('Science acquisition budget exceeded')
    return p,rows

def pulse_identity(pulse,readout_context):
    return {'policy':POLICY,'transition':pulse['label'],'shape':pulse['shape'],'durationS':pulse['durationNs']*1e-9,
            'sigmaS':pulse.get('sigmaNs',0)*1e-9,'amplitude':pulse['amplitude'],'frequencyHz':pulse['frequencyHz'],
            'phaseRad':pulse['phaseRad'],'rotationRad':math.pi,'lambda1':pulse['lambda1'],
            'anharmonicityHz':pulse['anharmonicityHz'],'bufferS':2e-9,'xySampleRateHz':4e9,
            'readoutContextHash':digest(readout_context)}
