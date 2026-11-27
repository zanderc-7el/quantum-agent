"""Staged, pure model-bound projection for the audited ArcLight implementation.

Wired to the controller in staged source; not deployed to the running service.
No files, plans, hardware, or operator schemas are read or changed by this helper.
The caller must first verify its actual adapter implementation and
profile binding; a user-supplied name is not proof of that identity.

effective_parameters means the NEXT request's committed values plus overrides,
not result.parameters: normal verification may contain a temporary single-point
axis that the next ordinary retry does not inherit.

fixed_rescan_eligible is a controller attestation, not inferred from a state
string. It may be true only after checking the current optimizer identity,
readout values/versions, current nonstale failed result, and matching latest and
lastFixedAttemptId. Original controller/build_plan checks remain authoritative.
"""
from copy import deepcopy
import math


SUPPORTED_IDENTITY = ('adapters.arclight.ArcLightAdapter', 'arclight-private-f8-q11-v1')
SUPPORTED_IDENTITIES = frozenset((SUPPORTED_IDENTITY,
    ('adapters.arclight.ArcLightAdapter', 'arclight-private-f8-q11-v2'),
    ('adapters.arclight.ArcLightAdapter', 'arclight-private-f8-q11-v3')))
SAMPLING = frozenset(('shots', 'capture_repeats', 'acquisitions_per_point'))
METHOD_KNOBS = {
    'ro_resonator_survey': {'readout_hz', 'span_hz', 'points'},
    'sq_spectroscopy': {'drive_hz', 'span_hz', 'points', 'spectroscopy_width_s'},
    'sq_rabi_amplitude': {'amp_start', 'amp_stop', 'points'},
    'sq_rabi_time': {'start_s', 'stop_s', 'points'},
    'ro_iq01': set(),
    'ro_optimize': {'amp_start', 'amp_stop', 'points'},
    'sq_t1': {'start_s', 'stop_s', 'points'},
    'sq_ramsey': {'start_s', 'stop_s', 'points', 'rotation_hz'},
    'sq_echo': {'start_s', 'stop_s', 'points', 'rotation_hz'},
    'sq_cpmg': {'start_s', 'stop_s', 'points', 'pi_pulses'},
    'sq_allxy': set(),
    'sq_rb': set(),
}
MINIMUM_CAPTURES = {'ro_resonator_survey': 2, 'sq_spectroscopy': 2, 'sq_rabi_amplitude': 2, 'sq_rabi_time': 2,
                    'ro_iq01': 1, 'ro_optimize': 1, 'sq_t1': 2, 'sq_ramsey': 2,
                    'sq_echo': 2, 'sq_cpmg': 2, 'sq_allxy': 2, 'sq_rb': 1}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def build_model_parameter_contract(*, implementation_id, profile_id, procedure_id,
                                   parameter_bounds, effective_parameters,
                                   fixed_readout_state=None, fixed_rescan_eligible=False):
    """Return model bounds and conditions; never apply parameters or grant a retry.

    Unknown identities/methods receive an unchanged deep copy with applied=False.
    Cross-field conditions are explanations for the existing controller/plan
    validators, not a second physical validator or permission to bypass them.
    """
    source = deepcopy(parameter_bounds)
    contract = {'schema': 'qcal-model-parameter-semantics/1', 'applied': False,
        'implementationId': implementation_id, 'profileId': profile_id,
        'procedureId': procedure_id, 'parameterBounds': source, 'notes': [],
        'conditions': [], 'issues': [], 'excludedParameters': [],
        'isExecutionApproval': False,
        'source': 'audited_static_semantics_not_measurement_or_acceptance',
        'finalValidation': 'Existing controller context, budget and adapter.build_plan checks remain required.'}
    if (implementation_id, profile_id) not in SUPPORTED_IDENTITIES or procedure_id not in METHOD_KNOBS:
        return contract
    contract['applied'] = True
    allowed = set(SAMPLING) | METHOD_KNOBS[procedure_id]
    params = effective_parameters
    rescan = (procedure_id == 'ro_optimize' and fixed_readout_state == 'fixed_failed'
              and fixed_rescan_eligible is True)
    if procedure_id == 'ro_optimize' and fixed_readout_state is not None:
        if fixed_readout_state not in ('active', 'fixed_failed'):
            contract['issues'].append({'code': 'UNKNOWN_FIXED_READOUT_STATE', 'parameter': 'fixed_readout_state'})
        if not rescan:
            allowed = set(SAMPLING)
            contract['notes'].append('Current readout candidate stays fixed; failed state alone does not authorize a new scan.')
        else:
            contract['conditions'].append({'parameters': ['amp_start', 'amp_stop'], 'kind': 'required_together',
                'meaning': 'Explicit full-scan request after a verified eligible fixed-point failure; original controller must recheck eligibility.'})
            contract['conditions'].append({'parameters': ['points'], 'kind': 'effective_only_with',
                'requires': ['amp_start', 'amp_stop'],
                'requiresCallerSemanticCheck': True,
                'meaning': 'points alone does not change the fixed amplitudes axis.'})
            contract['notes'].append('The existing controller removes amplitudes only for a validated paired endpoint rescan request.')

    # Adapter validates an axis whenever its key exists, even when a particular
    # method does not use it. Do not reinterpret []/None as a missing default.
    for key in ('frequencies_hz', 'amplitudes', 'powers'):
        if key in params:
            value = params[key]
            if not isinstance(value, list) or not 1 <= len(value) <= 161 or not all(_finite(v) for v in value):
                contract['issues'].append({'code': 'INVALID_EXISTING_EXPLICIT_AXIS', 'parameter': key,
                    'meaning': 'Existing adapter rejects this axis; repair effective parameters instead of treating it as absent.'})

    if procedure_id == 'ro_resonator_survey':
        if 'frequencies_hz' in params:
            allowed -= {'readout_hz', 'span_hz', 'points'}
            contract['notes'].append('Explicit frequencies_hz determines the S21 frequency axis; center/span/points do not rebuild it.')
        contract['notes'].append('S21 survey can submit only readout_hz. readout_amp changes the physical readout but this method cannot commit that calibration-context change, so it is excluded from model edits.')
        contract['notes'].append('At least two complete captures must reproduce the cavity position; the original analysis and independent verification still determine acceptance.')
    if procedure_id == 'sq_spectroscopy':
        if 'frequencies_hz' in params:
            allowed -= {'drive_hz', 'span_hz', 'points'}
            contract['notes'].append('Explicit frequencies_hz determines the frequency axis; center/span/points do not rebuild it.')
        elif 'start_hz' in params and 'stop_hz' in params:
            allowed -= {'drive_hz', 'span_hz'}
            contract['notes'].append('Both explicit frequency endpoints fix the window; points still changes the grid.')
        contract['notes'].append('drive_amp changes the physical long drive but its temporary diagnostic context is not yet supported; it is not an ineffective hardware control.')
    if procedure_id in ('sq_rabi_amplitude', 'ro_optimize') and 'amplitudes' in params and not rescan:
        allowed -= {'amp_start', 'amp_stop', 'points'}
        contract['notes'].append('Explicit amplitudes overrides endpoint/points construction and is not removed by an ordinary endpoint edit.')

    bounds = {key: deepcopy(rule) for key, rule in source.items() if key in allowed}

    def intersect(key, lower=None, upper=None):
        if key not in bounds:
            return
        rule = bounds[key]
        if not isinstance(rule, dict) or not _finite(rule.get('min')) or not _finite(rule.get('max')):
            bounds.pop(key)
            contract['issues'].append({'code': 'INVALID_PARAMETER_BOUNDS', 'parameter': key})
            return
        if lower is not None:
            rule['min'] = max(rule['min'], lower)
        if upper is not None:
            rule['max'] = min(rule['max'], upper)
        if rule['min'] > rule['max']:
            bounds.pop(key)
            contract['issues'].append({'code': 'EMPTY_PARAMETER_INTERVAL', 'parameter': key})

    intersect('capture_repeats', lower=MINIMUM_CAPTURES[procedure_id])
    if procedure_id in ('sq_rabi_amplitude', 'ro_optimize'):
        physical = source.get('drive_amp' if procedure_id == 'sq_rabi_amplitude' else 'readout_amp', {})
        if _finite(physical.get('min')) and _finite(physical.get('max')):
            for key in ('amp_start', 'amp_stop'):
                intersect(key, physical['min'], physical['max'])
        else:
            for key in ('amp_start', 'amp_stop'):
                bounds.pop(key, None)
            contract['issues'].append({'code': 'MISSING_PHYSICAL_AMPLITUDE_BOUNDS', 'parameter': 'amplitude_axis'})
        if 'amp_start' in bounds or 'amp_stop' in bounds:
            contract['conditions'].append({'parameters': ['amp_start', 'amp_stop'], 'kind': 'strictly_increasing',
                'meaning': 'Merged effective endpoints must satisfy amp_start < amp_stop; adapter.build_plan validates the actual axis.'})
    if procedure_id == 'sq_rabi_time':
        intersect('start_s', upper=79.7e-6)
        intersect('stop_s', upper=79.7e-6)
        contract['conditions'].append({'parameters': ['start_s', 'stop_s'], 'kind': 'strictly_increasing',
            'meaning': 'Merged start_s < stop_s <= 79.7 us; the existing native plan remains the final check.'})
        contract['conditions'].append({'parameters': ['start_s', 'stop_s', 'points'], 'kind': 'native_time_grid',
            'sampleRateHz': 4e9, 'sampleQuantumSeconds': 0.25e-9,
            'formula': 'start_s * 4e9 and ((stop_s - start_s) / (points - 1)) * 4e9 must be integers',
            'enforcedBy': 'adapter.build_plan on every emitted pi_len value; native absolute sample tolerance 1e-7',
            'examples': [{'start_s': 0, 'stop_s': 4.8e-6, 'points': 61},
                         {'start_s': 0, 'stop_s': 5e-6, 'points': 81}],
            'meaning': 'Jointly choose a uniform single-X duration grid with aligned start and step. 5 us / 61 points is invalid. No silent rounding or waveform replacement.'})
        contract['notes'].append('TimeRabi scans a full X pulse, so its duration axis uses 0.25 ns samples. The separately committed drive_length_s still requires 0.5 ns alignment for later X/2 pulses; that candidate normalization is unchanged.')
    if procedure_id in ('sq_t1', 'sq_ramsey', 'sq_echo', 'sq_cpmg'):
        intersect('start_s', upper=79.7e-6)
        intersect('stop_s', upper=79.7e-6)
        overhead = '(pi_pulses + 2) * (drive_length_s + 2e-9)' if procedure_id == 'sq_cpmg' else '4 * (drive_length_s + 2e-9)'
        contract['conditions'].extend([
            {'parameters': ['start_s', 'stop_s'], 'kind': 'strictly_increasing',
             'meaning': 'Merged start_s < stop_s; points reconstructs the actual uniform total-delay grid.'},
            {'parameters': ['stop_s', 'drive_length_s'] + (['pi_pulses'] if procedure_id == 'sq_cpmg' else []),
             'kind': 'native_sequence_window', 'formula': 'stop_s + ' + overhead + ' <= 79.7e-6',
             'enforcedBy': 'adapter.build_plan',
             'meaning': 'The interval alone is not executable permission: calibrated pulse length and merged pulse count must fit the complete sequence window.'}])
        contract['notes'].append('Explicit frequencies_hz/amplitudes/powers do not replace this method\'s time grid; invalid stored arrays still require repair.')
        contract['notes'].append('Existing native per-capture fits, repeat agreement and installed supplemental acceptance remain required; longer windows or more samples do not imply acceptance.')
    if procedure_id in ('sq_ramsey', 'sq_echo'):
        contract['notes'].append('rotation_hz changes virtual Z phase as -2*pi*rotation_hz*total_delay_s; it does not change the calibrated drive frequency.')
        if procedure_id == 'sq_echo':
            contract['notes'].append('Echo uses half the total delay on each side of refocusing; zero phase ramp permits exponential analysis, nonconstant phase uses oscillatory analysis.')
    if procedure_id == 'sq_cpmg':
        intersect('pi_pulses', lower=1)
        contract['notes'].append('pi_pulses changes the Y-refocusing pulse count and redistributes the same total delay; rotation_hz is unused. Joint pulse-count/window edits remain subject to the native sequence check.')
    if procedure_id in ('sq_allxy', 'sq_rb'):
        roles = ['independent_reference'] if procedure_id == 'sq_allxy' else ['pre', 'post']
        contract['samplingSemantics'] = {
            'shotsScope': 'all_jobs_including_fixed_references',
            'captureRepeatsScope': 'main_jobs_only',
            'acquisitionsPerPointScope': 'main_jobs_only',
            'fixedReferences': [{'role': role, 'preparedStates': 2, 'captures': 6, 'acquisitionsPerPoint': 2} for role in roles],
            'independentVerification': 'A separate verification operation repeats all jobs and is budgeted separately.'}
        contract['notes'].append('Reference jobs stay at 6 captures x 2 acquisitions per point; shots applies to references and main jobs, while ordinary capture/acquisition edits only change main jobs.')
        if procedure_id == 'sq_allxy':
            contract['notes'].append('AllXY always scans its 21 native circuits with a fresh preceding independent IQ reference; points and ordinary amplitude/time endpoints do not alter these circuits.')
        else:
            contract['notes'].append('RB retains the existing paired reference/interleaved length and seed grids with fresh PRE/POST IQ references. More captures average the same seeds, not new independent Clifford seeds.')
            contract['notes'].append('RB array/sequence/seed controls are not exposed here; current independently accepted Rabi/readout evidence, paired bootstrap statistics and reference drift checks remain mandatory.')
    if any(issue['code'] == 'INVALID_EXISTING_EXPLICIT_AXIS' for issue in contract['issues']):
        bounds = {}
        contract['requiresEffectiveParameterRepair'] = True
    contract['parameterBounds'] = bounds
    contract['excludedParameters'] = sorted(set(source) - set(bounds))
    contract['minimumCaptures'] = MINIMUM_CAPTURES[procedure_id]
    contract['notes'].append('Sampling bounds do not replace independent selection and verification acquisitions or the frozen quality gates.')
    return contract
