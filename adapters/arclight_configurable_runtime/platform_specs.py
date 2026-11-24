"""Pure dictionary builders for bounded platform methods, no native imports."""
from copy import deepcopy
from math import pi
from .routing import apply_job_routing
from .native_single_qubit_specs import (spectrum_spec,rabi_time_spec,rabi_amplitude_spec,
    singleshot_spec,t1_spec,ramsey_spec,echo_spec,rb_spec,_gate,_base,_sweep)
from .native_allxy_spec import allxy_spec

POLICY='f8_validated_short_half_dc_buff2ns_v1'

def linspace(a,b,n):return [a+(b-a)*i/(n-1) for i in range(n)]

def build_job(parameters,procedure,base_config,name,*,resource_id,route,instrument,visa_library):
    p=parameters;cfg=deepcopy(base_config);q=cfg['Q1'];q['para'].update(
        probFreq=p['readout_hz'],probCarryFreq=p['readout_hz'],probCarryScale=p['readout_amp'],
        drivFreq=p['drive_hz'],drivCarryFreq=p['drive_hz'])
    q['gate']['U_gates'].update(pi_len=p['drive_length_s'],pi_amp=p['drive_amp'],buff=2e-9,shape='DC',use_drag=False)
    cfg['ADC_Meas']['B1']['para']['width']=p['readout_width_s']
    common={'shots':p['shots']};n=p['points'];widths=linspace(p['start_s'],p['stop_s'],n)
    freqs=p.get('frequencies_hz') or linspace(p['readout_hz']-p['span_hz']/2,p['readout_hz']+p['span_hz']/2,n)
    amplitudes=p.get('amplitudes') or linspace(p['amp_start'],p['amp_stop'],n)
    extra={}
    if procedure in ('ro_resonator_survey','ro_power_shift'):
        kind='s21' if procedure=='ro_resonator_survey' else 's21_power'
        spec=_base(kind,{'off':[_gate('zero_xy','RX',0),_gate('readout','MEASURE')]},**common)
        spec['sweeps']=[_sweep('readout_frequency_hz','cfg.Q1.para.probFreq',freqs)]
        if kind=='s21_power':spec['sweeps'].append(_sweep('readout_amplitude','cfg.Q1.para.probCarryScale',p.get('powers',[.1,.2,.4,.6,.8])))
    elif procedure=='sq_spectroscopy':
        kind='spectrum';values=p.get('frequencies_hz') or linspace(p.get('start_hz',p['drive_hz']-p['span_hz']/2),p.get('stop_hz',p['drive_hz']+p['span_hz']/2),n)
        spec=spectrum_spec(values,drive_width_s=p['spectroscopy_width_s'],drive_amplitude=p['drive_amp'],**common)
    elif procedure in ('sq_rabi_time','sq_rabi_frequency'):
        kind='rabi_time';spec=rabi_time_spec(widths,drive_amplitude=p['drive_amp'],frequencies_hz=p.get('frequencies_hz') if procedure=='sq_rabi_frequency' else None,**common)
        spec['static_config']['cfg.Q1.gate.U_gates.buff']=2e-9
    elif procedure=='sq_rabi_amplitude':
        kind='rabi_amplitude';spec=rabi_amplitude_spec(amplitudes,gate_repeats=5,**common);extra['gate_repeats']=5
    elif procedure in ('ro_iq01','ro_optimize','ro_optimize_frequency','ro_s21_ge'):
        kind='readout';spec=singleshot_spec(readout_frequencies_hz=freqs if procedure in ('ro_optimize_frequency','ro_s21_ge') else None,
            readout_amplitudes=amplitudes if procedure=='ro_optimize' else None,**common)
    elif procedure=='sq_t1':kind='t1';spec=t1_spec(widths,**common)
    elif procedure=='sq_ramsey':kind='ramsey';spec=ramsey_spec(widths,phase_ramp_hz=-p['rotation_hz'],phase_offset_rad=0,**common)
    elif procedure=='sq_echo':kind='echo';spec=echo_spec(widths,phase_ramp_hz=-p['rotation_hz'],phase_offset_rad=0,**common)
    elif procedure=='sq_cpmg':
        kind='cpmg';count=p['pi_pulses'];gates=[_gate('first_half','RX',pi/2),_gate('wait_a','BLANK',0)]
        for i in range(count):
            gates.append(_gate(f'refocus_{i}','Y'));gates.append(_gate(f'wait_{i}','BLANK',0))
        gates.extend([_gate('last_half','RX',pi/2),_gate('readout','MEASURE')])
        spec=_base('CPMG_native_circuit',{'main':gates},mode='zip',**common)
        spec['sweeps']=[_sweep('first_half_gap','exp.cir.main.wait_a.params',[x/(2*count) for x in widths])]
        for i in range(count):spec['sweeps'].append(_sweep(f'gap_{i}',f'exp.cir.main.wait_{i}.params',[x/(2*count) if i==count-1 else x/count for x in widths]))
        spec['meta'].update(total_delays_s=widths,pi_pulses=count);extra.update(axis_key='exp.cir.main.wait_a.params',total_delay_multiplier=2*count)
    elif procedure=='sq_allxy':kind='allxy';spec=allxy_spec(**common)
    elif procedure=='sq_rb':
        kind='rb_reference';spec=rb_spec(p['rb_lengths'],random_sequences=p['rb_sequences'],seed=p['rb_master_seed'],**common)
    else:raise ValueError('Unknown native method '+procedure)
    if kind not in ('spectrum','s21','s21_power'):
        spec['meta']['pulse_compatibility']=POLICY
    job = {'name':name,'target':resource_id,'kind':kind,'config':cfg,'spec':spec,'parameters':deepcopy(p),
        'xy_physical_channel':[0,0],'xy_attenuation_db':p['xy_attenuation_db'],'xy_delay_s':p['xy_delay_s'],
        'xy_filter_mhz':p['xy_filter_mhz'],'captures':p['capture_repeats'],'acquisitions_per_point':p['acquisitions_per_point'],
        'randomize':True,'order_seed':p['order_seed'],'pulse_policy_target':POLICY,**extra}

    return apply_job_routing(job,resource_id,route,instrument,visa_library)
