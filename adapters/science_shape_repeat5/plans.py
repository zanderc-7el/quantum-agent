"""Pure finite five-X scan. Individual pulse identity stays unchanged."""
from copy import deepcopy
from adapters.science_v3.plans import build_rows,METHODS as ORIGINAL_METHODS,METHOD_FIELDS

PROCEDURE='sq_shape_rabi_amplitude_repeat5'
POLICY='native_shape_rabi_five_x_fixed_duration_v1'
PROFILE_POLICY='native_science_repeat5_source_chain_v1'
N=5
METHODS={**ORIGINAL_METHODS,PROCEDURE:('Shape Rabi: five identical X pulses','s03_rabi_amplitude',('f01','readout'))}

def build_repeat5(parameters):
    if 'gate_repeats' in parameters:raise ValueError('Five repetitions are frozen, not a configurable parameter')
    if parameters.get('drag_lambda1',0.)!=0.:raise ValueError('Shape calibration requires zero DRAG coefficient')
    p,rows=build_rows('sq_shape_rabi_amplitude',parameters)
    if 'verify_shape_pi_amp' in p and not p['scan_axis_values'][0]<p['verify_shape_pi_amp']<p['scan_axis_values'][-1]:
        raise ValueError('Single-gate pi amplitude must be strictly inside the acquired axis')
    for row in rows:
        assert len(row['gates'])==1
        row['gates']=[deepcopy(row['gates'][0]) for _ in range(N)]
        row['gateRepeats']=N
        row['controlDurationS']=sum(g['pulse']['durationNs']+2 for g in row['gates'])*1e-9
        if row['controlDurationS']>79.7e-6:raise ValueError('Five-gate sequence exceeds actual control window')
    return p,rows

def method_fields(method):
    return METHOD_FIELDS['sq_shape_rabi_amplitude' if method==PROCEDURE else method]
