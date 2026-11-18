"""Native Arclight experiment dictionaries; no instrument imports or execution.

All frequencies are Hz, times seconds, gate angles radians, amplitudes waveform
scale. Returned specifications retain raw I/Q (no sample postprocessor). Q1 is
the local software name; preserve the physical/source qubit mapping separately.
"""
from copy import deepcopy
from math import isfinite, pi


def _values(values, name, *, minimum=None, integer=False):
    result = list(values)
    if not result:
        raise ValueError(name + ' must not be empty')
    for value in result:
        if isinstance(value, bool) or not isfinite(value):
            raise ValueError(name + ' must contain finite numbers')
        if minimum is not None and value < minimum:
            raise ValueError(name + ' contains an out-of-range value')
        if integer and int(value) != value:
            raise ValueError(name + ' must contain integers')
    return [int(v) if integer else float(v) for v in result]


def extend_readout_config(base, *, drive_frequency_hz, xy_channel,
                          xy_sample_rate_hz, pi_length_s, pi_amplitude,
                          readout_start_s, acquisition_delay_s,
                          z_channel=None, z_sample_rate_hz=2e9,
                          shape='DC', buff_s=0, period_s=None, qubit=1):
    """Copy a verified readout config and add drive fields and explicit timing.

    Acquisition delay is required: it is not inferred from the readout start or
    cable latency. z_channel=None intentionally leaves a missing mapping rather
    than inventing hardware; the executor must support XY without unused Z.
    Delta2 is a nonzero placeholder needed by the compiler, with DRAG disabled.
    """
    cfg = deepcopy(base)
    q = cfg[f'Q{qubit}']
    q.setdefault('inst', {})['xyGateChI'] = xy_channel
    if z_channel is not None:
        q['inst']['fluxCH'] = z_channel
    q.setdefault('para', {}).update(drivFreq=float(drive_frequency_hz), drivLoFreq=0,
                                    drivCarryFreq=float(drive_frequency_hz))
    # Retain an existing working-point record. preserve_existing_dc controls
    # actual Z dispatch; adding XY fields must not rewrite historical DC metadata.
    q['para'].setdefault('fluxValue', 0)
    q.setdefault('gate', {})['sRate'] = {'XY': float(xy_sample_rate_hz), 'Z': float(z_sample_rate_hz)}
    q['gate']['U_gates'] = {'pi_len': float(pi_length_s), 'pi_amp': float(pi_amplitude),
                          'shape': shape, 'buff': float(buff_s), 'use_drag': False,
                          'lambda1': 0, 'Delta2': -200e6, 'z_amp': 0}
    profile = cfg['ADC_Meas'][q['inst']['ADC_PROB']]['para']
    time = cfg['ADC_Meas']['global']['timeSeq']
    time.update(probT0=float(readout_start_s), gapTime=0,
                wdLen=float(readout_start_s)+profile['width'], fill=False)
    if period_s is not None:
        time['period'] = float(period_s)
    if time['period'] <= time['wdLen']:
        raise ValueError('Repetition period must exceed the whole pulse sequence.')
    profile['triggerDelay'] = float(acquisition_delay_s)
    return cfg


def _gate(identity, name, *params):
    return {'id': identity, 'gate': name, 'ctrls': [], 'targets': [0], 'params': list(params)}


def _base(name, circuits, *, shots=128, qubit=1, mode='cartesian', group='agent_f8', sync='CeyearStationF8'):
    if not isinstance(shots, int) or not 1 <= shots <= 2047:
        raise ValueError('F8 entry supports 1..2047 shots per acquisition.')
    return {'meta': {'name': name, 'type': '1', 'qubit': qubit, 'shots': shots,
                     'readout_mode': 'IQ', 'sync': sync, 'group': group,
                     'expand_mode': mode, 'time_to_read': .08, 'batch_size': 1},
            'circuit_variants': {'default': next(iter(circuits)),
                'circuits': {key: {'mapping': {'0': qubit}, 'sequences': gates}
                             for key, gates in circuits.items()}},
            'static_config': {}, 'post_process': [], 'analysis': [], 'sweeps': []}


def _sweep(name, binding, values):
    return {'name': name, 'type': 'global_config' if binding.startswith('cfg.') else 'gate_param',
            'bindings': binding, 'values': list(values)}


def spectrum_spec(frequencies_hz, *, drive_width_s, drive_amplitude, **options):
    """Paired drive OFF/ON at each frequency. RX(0) uploads an explicit zero XY."""
    spec = _base('Spectrum_paired', {
        'off': [_gate('drive', 'RX', 0), _gate('readout', 'MEASURE')],
        'on': [_gate('drive', 'X'), _gate('readout', 'MEASURE')]}, **options)
    q = spec['meta']['qubit']
    spec['static_config'] = {f'cfg.Q{q}.gate.U_gates.pi_len': float(drive_width_s),
        f'cfg.Q{q}.gate.U_gates.pi_amp': float(drive_amplitude),
        f'cfg.Q{q}.gate.U_gates.shape': 'DC', f'cfg.Q{q}.gate.U_gates.buff': 0,
        f'cfg.Q{q}.gate.U_gates.use_drag': False}
    spec['sweeps'] = [_sweep('drive_frequency_hz', f'cfg.Q{q}.para.drivFreq',
                            _values(frequencies_hz, 'frequencies', minimum=0)),
                      _sweep('drive_state', 'exp.cir.default', ['off', 'on'])]
    return spec


def rabi_time_spec(widths_s, *, drive_amplitude, frequencies_hz=None, **options):
    """Optional native frequency x duration scan; length binding is the real U_gates path."""
    spec = _base('Rabi_time', {'main': [_gate('drive', 'X'), _gate('readout', 'MEASURE')]}, **options)
    q = spec['meta']['qubit']
    spec['static_config'] = {f'cfg.Q{q}.gate.U_gates.pi_amp': float(drive_amplitude),
        f'cfg.Q{q}.gate.U_gates.shape': 'DC', f'cfg.Q{q}.gate.U_gates.buff': 0,
        f'cfg.Q{q}.gate.U_gates.use_drag': False}
    if frequencies_hz is not None:
        spec['sweeps'].append(_sweep('drive_frequency_hz', f'cfg.Q{q}.para.drivFreq',
                                   _values(frequencies_hz, 'frequencies', minimum=0)))
    spec['sweeps'].append(_sweep('drive_duration_s', f'cfg.Q{q}.gate.U_gates.pi_len',
                               _values(widths_s, 'widths', minimum=0)))
    return spec


def rabi_amplitude_spec(amplitudes, *, gate_repeats=1, **options):
    if not isinstance(gate_repeats, int) or gate_repeats < 1:
        raise ValueError('gate_repeats must be a positive integer')
    gates = [_gate(f'drive_{n}', 'X') for n in range(gate_repeats)] + [_gate('readout', 'MEASURE')]
    spec = _base('Rabi_amplitude', {'main': gates}, **options)
    q = spec['meta']['qubit']
    spec['sweeps'] = [_sweep('drive_amplitude', f'cfg.Q{q}.gate.U_gates.pi_amp',
                            _values(amplitudes, 'amplitudes', minimum=0))]
    return spec


def singleshot_spec(*, readout_frequencies_hz=None, readout_amplitudes=None, **options):
    """Ground/excited I/Q pairs; this assumes the X gate has already been calibrated."""
    spec = _base('SingleShot_IQ', {
        'ground': [_gate('prep', 'RX', 0), _gate('readout', 'MEASURE')],
        'excited': [_gate('prep', 'X'), _gate('readout', 'MEASURE')]}, **options)
    q = spec['meta']['qubit']
    if readout_frequencies_hz is not None:
        spec['sweeps'].append(_sweep('readout_frequency_hz', f'cfg.Q{q}.para.probFreq',
            _values(readout_frequencies_hz, 'readout frequencies', minimum=0)))
    if readout_amplitudes is not None:
        spec['sweeps'].append(_sweep('readout_amplitude', f'cfg.Q{q}.para.probCarryScale',
            _values(readout_amplitudes, 'readout amplitudes', minimum=0)))
    spec['sweeps'].append(_sweep('prepared_state', 'exp.cir.default', ['ground', 'excited']))
    return spec


def t1_spec(delays_s, **options):
    spec = _base('T1', {'main': [_gate('prep', 'X'), _gate('wait', 'BLANK', 0),
                              _gate('readout', 'MEASURE')]}, **options)
    spec['sweeps'] = [_sweep('delay_s', 'exp.cir.main.wait.params', _values(delays_s, 'delays', minimum=0))]
    return spec


def ramsey_spec(delays_s, *, phase_ramp_hz=0, phase_offset_rad=pi/2, **options):
    delays = _values(delays_s, 'delays', minimum=0)
    options['mode'] = 'zip'
    spec = _base('Ramsey', {'main': [_gate('first_half', 'RX', pi/2), _gate('wait', 'BLANK', 0),
        _gate('phase', 'RZ', 0), _gate('last_half', 'RX', pi/2), _gate('readout', 'MEASURE')]}, **options)
    spec['sweeps'] = [_sweep('delay_s', 'exp.cir.main.wait.params', delays),
        _sweep('virtual_phase_rad', 'exp.cir.main.phase.params',
               [phase_offset_rad + 2*pi*phase_ramp_hz*t for t in delays])]
    spec['meta']['phase_ramp_hz'] = phase_ramp_hz
    spec['meta']['phase_offset_rad'] = phase_offset_rad
    return spec


def echo_spec(total_delays_s, *, phase_ramp_hz=0, phase_offset_rad=0, **options):
    delays = _values(total_delays_s, 'total delays', minimum=0)
    options['mode'] = 'zip'
    spec = _base('HahnEcho', {'main': [_gate('first_half', 'RX', pi/2),
        _gate('wait_a', 'BLANK', 0), _gate('refocus', 'X'), _gate('wait_b', 'BLANK', 0),
        _gate('phase', 'RZ', 0), _gate('last_half', 'RX', pi/2), _gate('readout', 'MEASURE')]}, **options)
    spec['sweeps'] = [_sweep('half_delay_a_s', 'exp.cir.main.wait_a.params', [t/2 for t in delays]),
        _sweep('half_delay_b_s', 'exp.cir.main.wait_b.params', [t/2 for t in delays]),
        _sweep('virtual_phase_rad', 'exp.cir.main.phase.params',
               [phase_offset_rad + 2*pi*phase_ramp_hz*t for t in delays])]
    spec['meta']['total_delays_s'] = delays
    spec['meta']['phase_ramp_hz'] = phase_ramp_hz
    return spec


def rb_spec(clifford_lengths, *, random_sequences=20, seed=20261117, **options):
    """Reference Clifford RB. Retain raw IQ and classify with current measured IQ calibration.

    For IRB after Experiment.load: exp.rb = dataclasses.replace(exp.rb,
    mode='irb', interleaved='X/2'). Native spec does not wire those fields.
    """
    lengths = _values(clifford_lengths, 'Clifford lengths', minimum=0, integer=True)
    if not isinstance(random_sequences, int) or random_sequences < 1:
        raise ValueError('random_sequences must be a positive integer')
    spec = _base('Clifford_RB', {'unused': []}, **options)
    spec['circuit_variants'] = {}
    spec['meta'].update(type='3', repeat=random_sequences, run_seed=seed, expand_mode='cartesian')
    spec['sweeps'] = [_sweep('clifford_length', 'clifford_m', lengths)]
    return spec
