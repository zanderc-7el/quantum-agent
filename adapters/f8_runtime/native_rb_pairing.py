"""Offline F8-native paired RB sequence preparation and deterministic dispatch.

No QOS package import, connection, acquisition, configuration writes, waveform
patching or fitting. The only source executed by load_native_library is the
inspected standalone Clifford algebra module (numpy and functools imports).
The caller constructs RB/RB_interleaved using the native F8 factories, then
binds a frozen gateMap. The native Clifford recovery and XY gates are retained.
"""
from copy import deepcopy
from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import numpy as np

DEFAULT_NATIVE_SOURCE = Path('D:/coding_tgqos_test/MeasModule/QOS/Gate/utils/clifford_RB.py')


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def load_native_library(path=DEFAULT_NATIVE_SOURCE):
    path = Path(path).resolve()
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(x.name for x in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module)
    if set(imports) != {'numpy', 'functools'}:
        raise ValueError('Reinspect native Clifford source imports before executing')
    spec = importlib.util.spec_from_file_location('_f8_native_clifford_algebra', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.indexlist_clifford_singlequbit[4] != ['X/2']:
        raise ValueError('Native X/2 Clifford index changed')
    return module


def _indices(gates):
    result = []
    for gate in gates:
        if not isinstance(gate, str) or not gate.startswith('clif_'):
            raise ValueError('Expected native clif_N gate tokens')
        n = int(gate[5:])
        if gate != f'clif_{n}' or not 0 <= n < 24:
            raise ValueError('Invalid Clifford token')
        result.append(n)
    return result


def _native_sequence(module, length, interleaved, seed):
    state = np.random.get_state()
    try:
        np.random.seed(seed)
        # This is the original F8 function, including its own recovery gate.
        gates = module.rb_gateidx(length, interleaved=interleaved, group=None)
    finally:
        np.random.set_state(state)
    return list(gates)


def make_plan(q, lengths, repeats=30, master_seed=2026111701, native_source=DEFAULT_NATIVE_SOURCE):
    if q not in ('Q11', 'Q18'):
        raise ValueError('This campaign is scoped to Q11/Q18')
    lengths = list(lengths)
    if repeats != 30 or not lengths or lengths != sorted(set(lengths)):
        raise ValueError('Require 30 paired sequences and sorted distinct lengths')
    if any(type(x) is not int or x <= 0 or x > 32 for x in lengths):
        raise ValueError('Only Clifford lengths 1..32 are prepared')
    source = Path(native_source).resolve()
    module = load_native_library(source)
    rows = []
    for sequence_index in range(repeats):
        for length in lengths:
            seed_bytes = hashlib.sha256(f'{master_seed}|{q}|{sequence_index}|{length}'.encode()).digest()
            seed = int.from_bytes(seed_bytes[:4], 'little')
            reference = _native_sequence(module, length, [], seed)
            interleaved = _native_sequence(module, length, [4], seed)
            ref_indices, irb_indices = _indices(reference), _indices(interleaved)
            if ref_indices[:-1] != irb_indices[:-1:2] or irb_indices[1:-1:2] != [4]*length:
                raise AssertionError('Native RNG did not produce paired Clifford bases')
            counts = {name: sum(len(module.indexlist_clifford_singlequbit[i]) for i in seq)
                      for name, seq in [('reference', ref_indices), ('interleaved', irb_indices)]}
            rows.append({'sequence_index': sequence_index, 'length': length, 'numpy_seed': seed,
                         'base_indices': ref_indices[:-1], 'reference_gate_map': reference,
                         'interleaved_gate_map': interleaved, 'native_primitive_count': counts})
    return {'schema_version': 'f8_native_rb_gateidx_paired.v1', 'q': q, 'repeats': repeats,
            'lengths': lengths, 'master_seed': master_seed, 'interleaved_indices': [4],
            'target': 'X/2', 'native_source': str(source),
            'native_source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'rng': 'numpy legacy global MT19937; saved/restored around every native rb_gateidx call',
            'pairing': 'same per-coordinate seed, native RB/IRB generation, independently computed native recovery',
            'pulse_policy': 'unmodified native full-duration XY/2 with explicitly calibrated half_pi_amp',
            'status': 'FROZEN_SEQUENCE_DRAFT_NOT_ACQUIRED_NATIVE_HALF_PI_VALIDATION_REQUIRED', 'rows': rows}


def validate_plan(plan, pi_len_s, buff_s=2e-9, prob_t0_s=80e-6, gap_time_s=300e-9,
                  xy_delay_s=0, waveform_length_s=86e-6):
    source = Path(plan['native_source'])
    if hashlib.sha256(source.read_bytes()).hexdigest() != plan['native_source_sha256']:
        raise ValueError('Native sequence source changed')
    native = load_native_library(source)
    expected = {(r, m) for r in range(plan['repeats']) for m in plan['lengths']}
    observed = set()
    worst = {'reference': 0, 'interleaved': 0}
    errors = []
    for row in plan['rows']:
        coordinate = (row['sequence_index'], row['length'])
        if coordinate in observed:
            errors.append(['duplicate', coordinate])
        observed.add(coordinate)
        ref = _indices(row['reference_gate_map'])
        irb = _indices(row['interleaved_gate_map'])
        if ref[:-1] != row['base_indices'] or irb[:-1:2] != ref[:-1] or irb[1:-1:2] != [4]*row['length']:
            errors.append(['pairing', coordinate])
        for variant, seq, target in [('reference', ref, []), ('interleaved', irb, [4])]:
            replay = _native_sequence(native, row['length'], target, row['numpy_seed'])
            if replay != row[f'{variant}_gate_map']:
                errors.append(['replay', coordinate, variant])
            total = np.eye(2, dtype=complex)
            for i in seq:
                total = native.matrixlist_clifford_singlequbit[i] @ total
            if not native.matrix_compare(total, np.eye(2), phase=True):
                errors.append(['recovery', coordinate, variant])
            count = sum(len(native.indexlist_clifford_singlequbit[i]) for i in seq)
            if count != row['native_primitive_count'][variant]:
                errors.append(['primitive_count', coordinate, variant])
            worst[variant] = max(worst[variant], count)
    if observed != expected:
        errors.append(['coordinates', sorted(expected-observed), sorted(observed-expected)])
    if len({r['numpy_seed'] for r in plan['rows']}) != len(plan['rows']):
        errors.append(['seed_collision'])
    duration = {k: v*(pi_len_s+buff_s) for k, v in worst.items()}
    for variant, width in duration.items():
        end = prob_t0_s-gap_time_s
        if width > end+1e-15 or end-width+xy_delay_s < -1e-15 or end+xy_delay_s > waveform_length_s+1e-15:
            errors.append(['window_overflow', variant, width])
    return {'decision': 'PASS' if not errors else 'FAIL', 'errors': errors, 'plan_sha256': digest(plan),
            'checked_pairs': len(plan['rows']), 'checked_sequences': 2*len(plan['rows']),
            'maximum_primitive_count': worst, 'maximum_native_gate_block_s': duration,
            'timing_inputs': dict(pi_len_s=pi_len_s, buff_s=buff_s, prob_t0_s=prob_t0_s,
                                  gap_time_s=gap_time_s, xy_delay_s=xy_delay_s,
                                  waveform_length_s=waveform_length_s),
            'scope': 'Native algebra, frozen replay and time-window check; not a physical calibration'}


def bind_frozen_gate_map(experiment, plan, variant, sequence_index, dispatch_log=None):
    """Bind already-constructed native RB/RB_interleaved; do not edit cfg/gates.

    Construct the native experiment only after binding meas.ini to the run copy.
    Pass interleaved=[4], not ['X/2'], to native RB_interleaved.
    """
    if variant not in ('reference', 'interleaved') or not 0 <= sequence_index < plan['repeats']:
        raise ValueError('Invalid RB variant or sequence index')
    expected_name = 'RB' if variant == 'reference' else 'RB_interleaved'
    if experiment['name'] != expected_name or experiment['measQ'] != plan['q']:
        raise ValueError('Expected corresponding original F8 RB factory output')
    if list(experiment['sweep_setting']['gate_num']) != plan['lengths']:
        raise ValueError('Native factory lengths differ from frozen plan')
    result = deepcopy(experiment)
    q = plan['q']
    lookup = {(r['length'], r['sequence_index']): list(r[f'{variant}_gate_map']) for r in plan['rows']}
    if len(lookup) != plan['repeats']*len(plan['lengths']):
        raise ValueError('Incomplete frozen plan')
    plan_hash = digest(plan)
    def resolved(length, repeat_index):
        key = (int(length), int(repeat_index))
        gates = lookup[key]
        if dispatch_log is not None:
            path = Path(dispatch_log)
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = {'event': 'native_frozen_gate_map_resolved_not_RF_verified',
                     'q': q, 'variant': variant, 'length': key[0], 'sequence_index': key[1],
                     'plan_sha256': plan_hash, 'gateMap': {q: gates}}
            with path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False)+'\n')
        return {q: list(gates)}
    constraints = result['constrains']
    kept = [c for c in constraints if not (len(c) == 3 and c[2] == 'st.gateMap')]
    if len(constraints)-len(kept) != 1:
        raise ValueError('Expected exactly one native random gateMap constraint')
    kept.append((('st.gate_num', 'st.repeat_index'), resolved, 'st.gateMap'))
    result['constrains'] = kept
    result['sweep_setting']['repeat_index'] = [sequence_index]
    result['setting']['repeat_index'] = sequence_index
    result['setting']['gate_num'] = plan['lengths'][0]
    result['setting']['native_paired_rb'] = {'plan_sha256': plan_hash, 'q': q, 'variant': variant,
        'sequence_index': sequence_index, 'native_source_sha256': plan['native_source_sha256'],
        'adapter_scope': 'deterministic lookup of native rb_gateidx output only; no pulse/config change'}
    return result


def verify_dispatched_gate_map(st, plan, variant, q, expected_sequence_index):
    """Call from after_set with kw['st']; compare actual resolved map to freeze.

    after_set means the set stage has completed. This proves software sequence
    dispatch only; associate it with the runner's waveform hashes and actual
    after_get receipt for acquisition evidence.
    """
    length, seed = int(st['gate_num']), int(st['repeat_index'])
    if q != plan['q'] or seed != expected_sequence_index:
        raise ValueError('Unexpected actual RB target/index')
    matches = [r for r in plan['rows'] if r['length'] == length and r['sequence_index'] == seed]
    if len(matches) != 1:
        raise ValueError('Actual RB coordinate absent/duplicated in plan')
    actual = st['gateMap']
    expected = {q: matches[0][f'{variant}_gate_map']}
    if actual != expected:
        raise ValueError('Actual resolved gateMap differs from native frozen sequence')
    return {'q': q, 'variant': variant, 'length': length, 'sequence_index': seed,
            'plan_sha256': digest(plan), 'actual_gateMap': deepcopy(actual),
            'actual_gateMap_sha256': digest(actual), 'matches_native_frozen_plan': True}
