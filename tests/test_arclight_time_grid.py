"""Offline emitted TimeRabi-grid validation; no native import, worker, or I/O."""
import ast
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

from adapters.arclight import ArcLightAdapter
from adapters.f8 import AdapterError
from backend.model_parameter_semantics import build_model_parameter_contract

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / 'adapters/arclight_runtime/validated_short_half_compat.py'


class ConfigStub:
    """Read-only dictionary view for the frozen compiler's scalar grid check."""
    def __init__(self, job, width):
        self.cfg = deepcopy(job['config'])
        self.cfg['Q1']['gate']['U_gates']['pi_len'] = width
    def raw(self): return self.cfg
    def xy_pi_len(self, q): return self.cfg['Q1']['gate']['U_gates']['pi_len']
    def xy_pi_amp(self, q): return self.cfg['Q1']['gate']['U_gates']['pi_amp']
    def xy_buff(self, q): return self.cfg['Q1']['gate']['U_gates']['buff']
    def xy_srate(self, q): return self.cfg['Q1']['gate']['sRate']['XY']
    def xy_shape(self, q): return self.cfg['Q1']['gate']['U_gates']['shape']
    def xy_drag(self, q): return self.cfg['Q1']['gate']['U_gates']['use_drag']


def native_scalar_policy():
    # Execute only this frozen function/class AST with a recording base class.
    # No bootstrap, core, native device creation, or measurements are imported.
    class CompilerStub:
        def __init__(self, config, **kwargs): self.config, self.compile_method = config, 'auto'
        def get_q_cfg(self, q, pulse): return self.config
        def get_xy_seq(self, q, **kwargs): return kwargs
    tree = ast.parse(NATIVE.read_text(encoding='utf8'))
    names = ('_checked', 'ValidatedShortHalfCompiler')
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    namespace = {'math': math, 'deepcopy': deepcopy, 'BUFFER_S': 2e-9,
                 'np': SimpleNamespace(pi=math.pi), 'Compiler': CompilerStub,
                 'CompileMethod': SimpleNamespace(AUTO='auto')}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(NATIVE), 'exec'), namespace)
    return namespace['_checked'], namespace['ValidatedShortHalfCompiler']


class TimeRabiGridTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ArcLightAdapter(ROOT, ROOT / 'adapters/arclight_profile_v2.json')

    def plan(self, parameters=None, procedure='sq_rabi_time', operation='time-grid-offline'):
        return self.adapter.build_plan({'operationId': operation, 'resourceId': 'Q11',
            'procedureId': procedure, 'parameters': parameters or {}})

    def test_unaligned_generated_time_grid_is_rejected_before_worker(self):
        for procedure in ('sq_rabi_time', 'sq_rabi_frequency'):
            with self.subTest(procedure=procedure), self.assertRaises(AdapterError) as failure:
                self.plan({'start_s': 0, 'stop_s': 5e-6, 'points': 61}, procedure)
            self.assertEqual(failure.exception.code, 'PULSE_GRID')
            detail = failure.exception.details
            self.assertEqual(detail['pointIndex'], 1)
            self.assertEqual(detail['binding'], 'cfg.Q1.gate.U_gates.pi_len')
            self.assertAlmostEqual(detail['sampleCount'], 1000 / 3)
            self.assertEqual(detail['sampleQuantumSeconds'], .25e-9)
            self.assertFalse(detail['nativeHalfRotation'])

    def test_aligned_joint_window_and_point_count_changes_keep_exact_requested_axis(self):
        checked, _ = native_scalar_policy()
        for stop, points in ((4.8e-6, 61), (5e-6, 81)):
            parameters = {'start_s': 0, 'stop_s': stop, 'points': points}
            before = deepcopy(parameters)
            plan = self.plan(parameters)
            job = plan['jobs'][0]
            values = job['spec']['sweeps'][-1]['values']
            self.assertEqual(values, [stop * index / (points - 1) for index in range(points)])
            self.assertEqual(plan['estimated_total_shots'], points * 2 * 2000)
            self.assertEqual(parameters, before)
            for width in values: checked(ConfigStub(job, width), 1)

    def test_quarter_ns_start_and_step_are_valid_but_half_sample_start_is_not(self):
        plan = self.plan({'start_s': .25e-9, 'stop_s': 20.25e-9, 'points': 81})
        values = plan['jobs'][0]['spec']['sweeps'][-1]['values']
        self.assertEqual(values[0], .25e-9)
        with self.assertRaises(AdapterError) as failure:
            self.plan({'start_s': .125e-9, 'stop_s': 20.125e-9, 'points': 81})
        self.assertEqual(failure.exception.details['pointIndex'], 0)

    def test_emitted_interior_values_are_checked_not_only_endpoints(self):
        job = self.plan()['jobs'][0]
        job['spec']['sweeps'][-1]['values'][3] += .125e-9
        before = deepcopy(job)
        with self.assertRaises(AdapterError) as failure: self.adapter._validate_scan_axes(job)
        self.assertEqual(failure.exception.details['pointIndex'], 3)
        self.assertEqual(job, before)

    def test_guard_matches_frozen_native_full_X_grid_and_tolerance(self):
        checked, compiler_type = native_scalar_policy()
        job = self.plan()['jobs'][0]
        gates = job['spec']['circuit_variants']['circuits']['main']['sequences']
        self.assertEqual([gate['gate'] for gate in gates], ['X', 'MEASURE'])
        for samples in (0, 1, 2, 32, 32 + .5e-7, 32 + 2e-7, 32.5, 333.3333333333333):
            width = samples / 4e9
            trial = deepcopy(job)
            trial['spec']['sweeps'][-1]['values'] = [width]
            native_passed = True
            try: checked(ConfigStub(job, width), 1, half=False)
            except ValueError: native_passed = False
            plan_passed = True
            try: self.adapter._validate_scan_axes(trial)
            except AdapterError: plan_passed = False
            self.assertEqual(plan_passed, native_passed, samples)
        quarter = compiler_type(ConfigStub(job, .25e-9))
        self.assertEqual(quarter.get_xy_seq(1, theta=math.pi)['theta'], math.pi)
        with self.assertRaises(ValueError): quarter.get_xy_seq(1, theta=math.pi / 2)
        half = compiler_type(ConfigStub(job, .5e-9))
        self.assertEqual(half.get_xy_seq(1, theta=math.pi / 2)['pulse']['pi_len'], .25e-9)

    def test_existing_default_plans_are_byte_identical_to_pre_guard_snapshots(self):
        # Captured before this adapter-only guard; includes all physical config,
        # emitted native specs, sampling, budgets, and operation identities.
        snapshots = {'sq_rabi_time': '3f74f5aef8c8a2a6c88aac918843b0d0df2272d6de66c757f07e630ac90d7ad8',
                     'sq_rabi_frequency': '58d10d953e6752ea54cc94633493faafbbbe476f93c7bd614c8e2d4b78e4be6e'}
        for procedure, expected in snapshots.items():
            plan = self.plan(procedure=procedure, operation='time-grid-default/' + procedure)
            digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            self.assertEqual(digest, expected)

    def test_scan_edits_are_not_rounded_and_committed_half_pulse_normalization_is_unchanged(self):
        edits = {'start_s': .125e-9, 'stop_s': 5e-6, 'points': 61}
        self.assertEqual(self.adapter.normalize_parameter_changes('sq_rabi_time', edits, {}), edits)
        self.assertEqual(self.adapter.normalize_candidate('sq_rabi_time', {'drive_length_s': 320.3e-9}),
                         {'drive_length_s': 320.5e-9})

    def test_model_contract_explains_coupled_grid_with_executable_examples_without_changing_bounds(self):
        schema = self.adapter.parameter_schema('sq_rabi_time')
        contract = build_model_parameter_contract(implementation_id='adapters.arclight.ArcLightAdapter',
            profile_id=self.adapter.profile['id'], procedure_id='sq_rabi_time', parameter_bounds=schema,
            effective_parameters=self.plan()['parameters'])
        condition = next(item for item in contract['conditions'] if item['kind'] == 'native_time_grid')
        self.assertEqual(condition['parameters'], ['start_s', 'stop_s', 'points'])
        self.assertEqual(condition['sampleQuantumSeconds'], .25e-9)
        self.assertEqual(contract['parameterBounds']['points'], schema['points'])
        for parameters in condition['examples']: self.plan(parameters)
        self.assertTrue(any('0.5 ns' in note for note in contract['notes']))


if __name__ == '__main__': unittest.main()
