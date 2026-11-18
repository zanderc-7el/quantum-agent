"""Native Arclight AllXY specification and ideal reference, without hardware I/O.

The native XYExperiment owns the 21 circuits and their order. This module does
not replace that circuit list, fabricate measurements, or infer gate fidelity.
"""
from math import cos, pi, sin

try:
    from .native_single_qubit_specs import _base
except ImportError:
    from native_single_qubit_specs import _base


def allxy_spec(*, repeat=1, **options):
    """Return meta.type='4'; XYExperiment adds its native AllXY sweep on load.

    Use an independently calibrated XY/readout config. For a no-Z run its
    ADC_Meas.global.preserve_existing_dc flag must explicitly be True.
    """
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        raise ValueError('repeat must be a positive integer')
    spec = _base('AllXY_native_21', {'unused': []}, **options)
    spec['circuit_variants'] = {}
    spec['meta'].update(type='4', repeat=repeat, expand_mode='cartesian')
    return spec


def ideal_allxy_reference(*, qubit=1, repeat=1):
    """Compute ideal P1 from the native AllXY pairs, starting in ground state.

    Call after the local Arclight bootstrap is initialized. The native AllXY
    class supplies both ordering and gate decomposition. These reference values
    are theoretical populations, never measured probabilities or gate fidelity.
    """
    allxy_spec(repeat=repeat, qubit=qubit)
    from core.circuit.allxy import AllXY
    native = AllXY(targets=[qubit], repeat=repeat)
    result = []
    for index, pair in enumerate(native.xy):
        ground, excited = 1 + 0j, 0j
        for name, _, args in native._to_gate(pair):
            if name not in ('X', 'Y', 'RX', 'RY'):
                raise ValueError('Unsupported native AllXY ideal gate: ' + name)
            angle = pi if name in ('X', 'Y') else float(args[0])
            c, s = cos(angle / 2), sin(angle / 2)
            if name in ('X', 'RX'):
                ground, excited = c*ground - 1j*s*excited, -1j*s*ground + c*excited
            else:
                ground, excited = c*ground - s*excited, s*ground + c*excited
        result.append({'index': index, 'native_pair': pair,
                       'ideal_p1': round(abs(excited)**2, 15)})
    return {'source': 'core.circuit.allxy.AllXY', 'native_meta_type': '4',
            'initial_state': '|0>', 'metric': 'ideal_excited_state_probability_P1',
            'interpretation': 'Theoretical population reference; not measured data or gate fidelity.',
            'repeat': repeat, 'points': result}
