"""Explicit, instance-local F8 validated-session pulse compatibility.

Import after the isolated Arclight bootstrap. No device or file I/O on import.
Native Compiler.compile/gate_to_pulse/get_measure_seq, native Sequence and
SequenceRunner are retained. Only +/-pi/2 duration/amplitude and explicit I
duration differ. This is an implementation policy, not a measured calibration.
"""
from copy import deepcopy
from dataclasses import fields
import math
from types import MethodType

import numpy as np
from core.compiler.compile import Compiler, CompileMethod
from core.compiler.decompose import create_gate
from core.circuit.allxy import AllXY
from core.experiment.sequence import Sequence
from core.experiment.utils import MetaKeySchema
from paired_rb_support import PairedSingleQubitRB


POLICY = 'f8_validated_short_half_dc_buff2ns_v1'
BUFFER_S = 2e-9


def _checked(cfg, q, *, half=False):
    u = cfg.raw()[f'Q{q}']['gate']['U_gates']
    width, amp, buff, rate = (float(cfg.xy_pi_len(q)), float(cfg.xy_pi_amp(q)),
                             float(cfg.xy_buff(q)), float(cfg.xy_srate(q)))
    if not all(math.isfinite(v) for v in (width, amp, buff, rate)):
        raise ValueError('Legacy pulse parameters must be finite')
    if width < 0 or (half and width <= 0) or not 0 <= amp <= .99 or rate <= 0:
        raise ValueError('Invalid legacy pulse width, amplitude or sample rate')
    if cfg.xy_shape(q) != 'DC' or cfg.xy_drag(q) or u.get('use_decompos', False):
        raise ValueError('Validated-session compatibility requires DC, no DRAG or decomposition')
    if not math.isclose(buff, BUFFER_S, rel_tol=0, abs_tol=1e-18):
        raise ValueError('Validated-session compatibility requires buff=2e-9, including static overrides')
    if any(float(u.get(k, 0)) != 0 for k in ('z_amp', 'detuning', 'lambda1')):
        raise ValueError('Validated-session compatibility requires zero Z, detuning and DRAG lambda')
    xy = cfg.raw()[f'Q{q}']['gate'].get('XY_gates', {})
    if float(xy.get('Y_shift', 0)) != 0:
        raise ValueError('Nonzero historical Y_shift is not supported by this explicit policy')
    for samples in ((width / 2 if half else width) * rate, buff / 2 * rate):
        if not math.isclose(samples, round(samples), rel_tol=0, abs_tol=1e-7):
            raise ValueError('Legacy envelope and each half-buffer must align to XY samples')
    return width, amp, buff


class ValidatedShortHalfCompiler(Compiler):
    """Native compiler with two explicitly selected physical-pulse policies."""

    def __init__(self, config, **kwargs):
        if kwargs.pop('opt', False):
            raise ValueError('Legacy gate timing requires opt=False')
        super().__init__(config, opt=False, **kwargs)
        if self.compile_method != CompileMethod.AUTO:
            raise ValueError('Legacy policy supports native AUTO tuple circuits only')

    def compile(self, data, compile_method=None, **kwargs):
        if compile_method not in (None, CompileMethod.AUTO):
            raise ValueError('Legacy policy supports native AUTO tuple circuits only')
        return super().compile(data, compile_method=compile_method, **kwargs)

    def compile_by_auto(self, data, ctx):
        qset, gates = super().compile_by_auto(data, ctx)
        kept = []
        for gate in gates:
            if gate.name != 'i':
                kept.append(gate)
                continue
            for q in gate.targets:
                cfg = self.get_q_cfg(q, gate.pulse)
                width, _, buff = _checked(cfg, q)
                kept.append(create_gate('blank', [q], [width + buff], id=gate.id))
        return qset, kept

    def get_xy_seq(self, q, theta=np.pi, phase=0, pulse=None):
        pulse = deepcopy(pulse or {})
        cfg = self.get_q_cfg(q, pulse)
        is_half = math.isclose(float(theta), np.pi / 2, rel_tol=0, abs_tol=1e-12)
        width, _, _ = _checked(cfg, q, half=is_half)
        if is_half:
            # Native gate_to_pulse has already folded negative rotations into
            # positive theta plus a pi phase. Preserve that phase, shorten only
            # the envelope, and ask native get_xy_seq for full amplitude.
            pulse['pi_len'] = width / 2
            return super().get_xy_seq(q, theta=np.pi, phase=phase, pulse=pulse)
        return super().get_xy_seq(q, theta=theta, phase=phase, pulse=pulse)


class _KeepIdentityTokens:
    def _to_gate(self, seq):
        gates = []
        for token in (p.strip() for p in seq.split(',')):
            if not token:
                continue
            if token == 'I':
                gates.append(('I', self.targets, []))
            else:
                gates.extend(super()._to_gate(token))
        return gates


class ValidatedShortHalfAllXY(_KeepIdentityTokens, AllXY):
    pass


class ValidatedShortHalfPairedRB(_KeepIdentityTokens, PairedSingleQubitRB):
    pulse_policy = POLICY


def _build_compatible_sequence(self, setting_config, gates):
    compiler = ValidatedShortHalfCompiler(config=setting_config, opt=False)
    qset, pulses, _ = compiler.compile(gates)
    group = self.spec.meta.get(MetaKeySchema.group, 'default')
    seq = Sequence(qset, config=setting_config, group=group)
    seq.add_pulses(*pulses)
    # In-memory provenance; caller also archives this module and policy report.
    seq.validated_short_half_policy = POLICY
    return seq


def install_validated_short_half(exp):
    """Enable only this loaded experiment, before any preflight/acquisition.

    The spec must explicitly contain meta['pulse_compatibility'] = POLICY.
    For RB call install_paired_rb first. Root must archive this source/report.
    An unmodified experiment in the same process keeps normal native behavior.
    """
    if exp.spec.meta.get('pulse_compatibility') != POLICY:
        raise ValueError('Explicit spec.meta.pulse_compatibility opt-in is required')
    if getattr(exp, '_validated_short_half_policy', None):
        raise RuntimeError('Legacy policy already installed on this experiment')
    if not callable(getattr(exp, '_build_sequence_from_gates', None)):
        raise TypeError('Expected a native ScanExperiment or CirExperiment')
    allxy = getattr(exp, 'allxy', None)
    rb = getattr(exp, 'rb', None)
    if rb is not None and not isinstance(rb, PairedSingleQubitRB):
        raise TypeError('For RB install_paired_rb before installing pulse compatibility')
    if allxy is not None:
        exp.allxy = ValidatedShortHalfAllXY(**{f.name: getattr(allxy, f.name) for f in fields(AllXY)})
    if rb is not None:
        exp.rb = ValidatedShortHalfPairedRB(**{f.name: getattr(rb, f.name) for f in fields(PairedSingleQubitRB)})
    exp._build_sequence_from_gates = MethodType(_build_compatible_sequence, exp)
    exp._validated_short_half_policy = POLICY
    return {
        'policy': POLICY, 'enabled': True, 'scope': 'this experiment instance only',
        'shape': 'DC', 'buffer_s': BUFFER_S,
        'half_rotation': 'pi_len/2 envelope at pi_amp; signed axis phase retained',
        'full_pi': 'native amplitude and envelope duration unchanged',
        'identity': 'explicit I consumes pi_len+buff; retained in AllXY and Clifford RB',
        'native_measurement_compiler_unchanged': True,
        'native_sequence_and_runner_unchanged': True,
        'optimizer': False,
        'ramsey_echo_required_phase': '-2*pi*old_rotation_hz*delay_s; phase_offset=0',
        'hardware_gain_or_attenuation_equivalence': 'not established by this software policy',
        'status': 'IMPLEMENTATION_ONLY_REQUIRES_PHYSICAL_VALIDATION',
    }


def legacy_ramsey_spec(delays_s, *, old_rotation_hz, **options):
    from native_single_qubit_specs import ramsey_spec
    spec = ramsey_spec(delays_s, phase_ramp_hz=-float(old_rotation_hz),
                       phase_offset_rad=0, **options)
    spec['meta'].update(pulse_compatibility=POLICY, old_rotation_hz=float(old_rotation_hz))
    return spec


def legacy_echo_spec(delays_s, *, old_rotation_hz, **options):
    from native_single_qubit_specs import echo_spec
    spec = echo_spec(delays_s, phase_ramp_hz=-float(old_rotation_hz),
                     phase_offset_rad=0, **options)
    spec['meta'].update(pulse_compatibility=POLICY, old_rotation_hz=float(old_rotation_hz))
    return spec
