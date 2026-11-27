"""Versioned passband and actual planning contracts. No device/model calls."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.common import APIError, dumps
from tests import test_chip_catalog as catalog_fixtures
from adapters.f8 import AdapterError
from adapters.science_runtime.policy import digest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = '台站 XY 路由校准报告 2026-11-26，第 3 页'


def confirmed(low=4.5e9, high=5.1e9, source=SOURCE):
    return {'xyPassbandHz': [low, high], 'xyPassbandSource': source, 'xyPassbandConfirmed': True}


def proof(request, roles, parameters):
    nodes = [{'recordId': 'fixture-' + role, 'parameters': deepcopy(parameters),
              'resolvedOutputs': {'candidateParameters': {}}} for role in roles]
    value = {'schema': 'qcal-science-dependencies/1', 'policyVersion': 'fixture-no-physical-claim',
             'kind': 'fixture', 'scope': {'workflowId': request['runId'], 'resourceId': request['resourceId']},
             'inputs': {}, 'rootReferences': [{'role': role, 'recordId': 'fixture-' + role} for role in roles],
             'nodes': nodes, 'edges': [{'role': role, 'toRecordId': 'fixture-' + role} for role in roles]}
    value['closureSha256'] = digest(value)
    value['assessment'] = {'executionEligible': True, 'consumerSha256': digest(request)}
    return value


class PassbandConfiguration(unittest.TestCase):
    def setUp(self):
        self.fixture = catalog_fixtures.ChipCatalogTests(); self.fixture.setUp(); self.addCleanup(self.fixture.tearDown)
        self.catalog = self.fixture.catalog(); self.catalog.root = ROOT
        self.index = 0

    def save(self, fields, resource='A'):
        self.index += 1
        return self.catalog.save_deployment({'operationId': 'dep-' + str(self.index), 'name': '可读接线版本',
            'instrument': self.fixture.base['instrument'], 'routes': {resource: {
                'xyPhysical': [1, 1], 'readoutPhysical': [8, 1], 'adcPhysical': [0, 1], **fields}},
            'wiringConfirmed': True})

    def test_normal_save_rejects_bad_values_or_confirmed_placeholder_source(self):
        bad = [confirmed(0), confirmed(-1), confirmed(True), confirmed(5e9, 5e9), confirmed(6e9, 5e9),
               confirmed(float('nan')), confirmed(1, float('inf')), confirmed(10**400), confirmed('4500000000'),
               confirmed(None), confirmed(source='  \t\n '), confirmed(source='待核实'), confirmed(source=' TBD '),
               confirmed(source='ＴＢＤ'),
               confirmed(source='???'), confirmed(source='已确认'), confirmed(source='x' * 2001),
               {**confirmed(), 'xyPassbandConfirmed': 'true'}, {'xyPassbandHz': [None, -2]},
               {'xyPassbandHz': '4.5..5.1'}, {'xyPassbandHz': [1, 2, 3]}]
        for fields in bad:
            with self.subTest(fields=fields), self.assertRaises(APIError): self.save(fields)

    def test_pending_partial_null_and_legacy_save_publish_without_authorization(self):
        for fields in ({}, {'xyPassbandHz': [4e9, None]}, {'xyPassbandHz': [None, 5e9]},
                       {'xyPassbandHz': [None, None], 'xyPassbandSource': '', 'xyPassbandConfirmed': False},
                       {'xyPassbandHz': None, 'xyPassbandSource': None},
                       {'xyPassbandHz': [4e9, 5e9], 'xyPassbandSource': '待核实', 'xyPassbandConfirmed': False}):
            with self.subTest(fields=fields):
                saved = self.save(fields)
                version = self.catalog.publish_deployment(saved['id'], {'operationId': 'pub-' + str(self.index), 'expectedRevision': 1})
                self.assertIsNot(version['routes']['A'].get('xyPassbandConfirmed'), True)
                for key, value in fields.items(): self.assertEqual(version['routes']['A'][key], value)

    def test_legacy_invalid_draft_cannot_publish_a_confirmed_band(self):
        saved = self.save({})
        saved['routes']['A'].update(confirmed(source='未提供'))
        with self.fixture.store.transaction() as rt:
            rt.db.execute('UPDATE config_entities SET payload=? WHERE id=?', (dumps(saved), saved['id']))
        with self.assertRaises(APIError):
            self.catalog.publish_deployment(saved['id'], {'operationId': 'legacy-publish', 'expectedRevision': 1})

    def binding(self):
        chip = self.catalog.save_chip({'operationId': 'chip', 'name': '两个独立路由',
                                      'qubits': [self.fixture.qubit('A'), self.fixture.qubit('B')]})
        cv = self.catalog.publish_chip(chip['id'], {'operationId': 'cp', 'expectedRevision': 1})
        saved = self.save(confirmed(source='未知频段调查与实测结论.docx 第 8 页'))
        saved['routes']['B'] = {'xyPhysical': [2, 0], 'readoutPhysical': [8, 0], 'adcPhysical': [0, 0],
                                'xyPassbandHz': [None, 4.9e9], 'xyPassbandConfirmed': False}
        saved = self.catalog.save_deployment({**saved, 'operationId': 'two-routes', 'expectedRevision': 1})
        dv = self.catalog.publish_deployment(saved['id'], {'operationId': 'dp', 'expectedRevision': 2})
        return self.catalog.resolve_binding(cv['id'], dv['id'], ['A', 'B']), saved, cv, dv

    def test_actual_binding_preserves_resource_bands_and_frozen_version_after_edits(self):
        from adapters.science_v3_adapter import ScienceV3Adapter, build_science_profile
        binding, saved, cv, dv = self.binding()
        frozen = deepcopy(binding)
        self.assertEqual(binding['profile']['routingByResource']['A']['xyPassbandHz'], [4.5e9, 5.1e9])
        self.assertEqual(binding['profile']['routingByResource']['B']['xyPassbandHz'], [None, 4.9e9])
        adapter = ScienceV3Adapter(ROOT, build_science_profile(ROOT, binding['profile']))
        self.assertEqual(adapter._routing('A')['xyPassbandSource'], '未知频段调查与实测结论.docx 第 8 页')
        caps = adapter.capabilities()['procedures']
        self.assertEqual(caps['sq_shelve_spectrum12']['missingPassbandResources'], ['B'])
        self.assertTrue(caps['sq_shape_rabi_amplitude']['available'])
        _, request = PassbandAdapters().v3({})
        with patch.object(adapter, 'authenticate_dependency', return_value={'fixtureOnly': True}):
            self.assertEqual(adapter.build_plan(request)['routing']['xyPassbandHz'], [4.5e9, 5.1e9])
            request['resourceId'] = 'B'
            request['scienceDependencies'] = proof(request, ['f01','pi01','readout'], request['parameters'])
            with self.assertRaises(AdapterError): adapter.build_plan(request)
        saved['routes']['A'].update(confirmed(3e9, 4e9))
        self.catalog.save_deployment({**saved, 'operationId': 'edit-after-publish', 'expectedRevision': 2})
        self.assertEqual(self.catalog.resolve_binding(cv['id'], dv['id'], ['A', 'B']), frozen)


class PassbandAdapters(unittest.TestCase):
    def base(self):
        base = json.loads((ROOT / 'adapters/arclight_profile.json').read_text(encoding='utf-8'))
        base['resources'] = {'A': deepcopy(base['resources']['Q11']), 'B': deepcopy(base['resources']['Q11'])}
        base['routingByResource'] = {r: {'xyPhysical': [i, 0], 'readoutPhysical': [8, 0], 'adcPhysical': [0, 0]}
                                     for i, r in enumerate(('A', 'B'))}
        return base

    def v3(self, fields, resource='A', method='sq_shelve_spectrum12'):
        from adapters.science_v3_adapter import ScienceV3Adapter, build_science_profile
        from adapters.science_v3.plans import METHODS
        base = self.base(); profile = build_science_profile(ROOT, base)
        profile['routingByResource'][resource].update(fields)
        adapter = ScienceV3Adapter(ROOT, profile)
        parameters = {'drive_hz': 4.86e9, 'drive_amp': .3, 'drive_length_s': 20e-9,
            'readout_hz': 7.01e9, 'readout_amp': .4, 'readout_width_s': 2e-6, 'xy_delay_s': 100e-9,
            'xy_filter_mhz': 4700, 'xy_attenuation_db': 3.75, 'shots': 16, 'capture_repeats': 2,
            'acquisitions_per_point': 1, 'pulse_compatibility': 'f8_validated_short_half_dc_buff2ns_v1',
            'scan_axis_values': [4.6e9, 4.65e9, 4.7e9] if method.startswith('sq_shelve') else [.1, .2, .3]}
        request = {'operationId': 'fixture/measure', 'runId': 'fixture-run', 'nodeId': 'fixture-node',
                   'attemptId': 'fixture-attempt', 'resourceId': resource, 'procedureId': method, 'parameters': parameters,
                   'parameterVersions': {}, 'leaseToken': 'fixture-no-physical-lease'}
        request['scienceDependencies'] = proof(request, sorted(set(METHODS[method][2]) - {'xy_passband'}), parameters)
        return adapter, request

    def v3_plan(self, fields, **kwargs):
        adapter, request = self.v3(fields, **kwargs)
        # Source files, rows and native pulse-matching are real. Only source
        # acceptance is injected; these fixtures do not represent measurements.
        with patch.object(adapter, 'authenticate_dependency', return_value={'fixtureOnly': True}):
            return adapter, adapter.build_plan(request)

    def test_v3_capabilities_use_same_strict_source_and_range_contract(self):
        for fields in ({}, confirmed(source=' '), confirmed(source='未知'), confirmed(1, float('inf')),
                       confirmed(True), confirmed(5e9, 4e9), {'xyPassbandHz': [4e9, None]}):
            with self.subTest(fields=fields):
                adapter, _ = self.v3(fields)
                capability=adapter.capabilities()['procedures']['sq_shelve_spectrum12']
                self.assertFalse(capability['available'])
                self.assertIn('A',capability['missingPassbandResources'])

    def test_actual_shelve_plan_rejects_invalid_band_and_both_probe_and_prep_outside(self):
        for fields in ({}, confirmed(source=' '), confirmed(source='pending'), confirmed(1, float('inf')),
                       confirmed(True), confirmed(4.7e9, 5e9), confirmed(4.5e9, 4.8e9)):
            with self.subTest(fields=fields), self.assertRaises(AdapterError): self.v3_plan(fields)
        adapter, plan = self.v3_plan(confirmed(4.6e9, 4.86e9))
        self.assertEqual(plan['routing']['xyPassbandHz'], [4.6e9, 4.86e9])
        self.assertEqual({g['pulse']['frequencyHz'] for row in plan['rows'] for g in row['gates']}, {4.6e9, 4.65e9, 4.7e9, 4.86e9})
        self.assertFalse(adapter.profile['enabled'])

    def test_actual_wrapper_worker_rechecks_band_and_frozen_route_before_any_device(self):
        from adapters.science_v3.execute_worker import validate_admission
        class BeforeSpawn(BaseException): pass
        adapter, request = self.v3(confirmed())
        adapter.profile['enabled'] = True
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            adapter.operations = Path(temporary)
            stack.enter_context(patch.object(adapter, 'authenticate_dependency', return_value={'fixtureOnly': True}))
            stack.enter_context(patch('adapters.science_v3.evidence.authenticate_dependency', return_value={'fixtureOnly': True}))
            stack.enter_context(patch.object(adapter, 'preflight', return_value={'ready': True}))
            stack.enter_context(patch('adapters.arclight.subprocess.Popen', side_effect=BeforeSpawn))
            with self.assertRaises(BeforeSpawn): adapter.execute(request, lambda _: None, lambda: False)
            folder = adapter._operation_dir(request['operationId'])
            plan = json.loads((folder / 'plan.json').read_text(encoding='utf-8'))
            self.assertEqual(validate_admission(plan, folder), request)
            for change in (confirmed(source=' '), confirmed(4.7e9, 5e9), {'xyPhysical':[3,1]}):
                changed = deepcopy(plan); changed['routing'].update(change)
                with self.subTest(change=change), self.assertRaises(ValueError): validate_admission(changed, folder)
            changed = deepcopy(plan)
            changed['profileSnapshot']['routingByResource']['A'].update(confirmed(source='TBD'))
            changed['profileHash'] = digest(changed['profileSnapshot'])
            with self.assertRaises(ValueError): validate_admission(changed, folder)

    def test_nonpassband_shape_method_and_standard_method_work_with_unknown_band(self):
        self.v3_plan({}, method='sq_shape_rabi_amplitude')
        from adapters.arclight_dynamic import DynamicArcLightAdapter
        adapter = DynamicArcLightAdapter(ROOT, self.base())
        request = {'operationId': 'legacy/measure', 'runId': 'legacy-run', 'nodeId': 'legacy-node',
                   'resourceId': 'A', 'procedureId': 'sq_spectroscopy',
                   'parameters': {'drive_hz': 4.86e9, 'span_hz': 10e6, 'points': 41}}
        plan = adapter.build_plan(request)
        self.assertNotIn('xyPassbandHz', plan['routing'])
        self.assertTrue(plan['jobs'])

    def flux(self, fields, *, with_f01=True):
        from adapters.science_flux_adapter import ScienceFluxAdapter, build_flux_profile
        from tests.test_science_flux import route, Provider
        from tests.test_science_flux_plans import parameters
        base = self.base(); base['resources'] = {'A': base['resources']['A']}; base['routingByResource'] = {'A': base['routingByResource']['A']}
        bias = route(); bias['resourceId'] = 'A'
        bias['deviceKey'] = 'device:' + base['instrument']['host'] + ':' + str(base['instrument']['port'])
        base['biasRoutes'] = {'A': bias}
        profile = build_flux_profile(ROOT, base);profile['routingByResource']['A'].update(fields)
        adapter = ScienceFluxAdapter(ROOT, profile)
        p = parameters(); p.pop('bias_values_v'); p['local_readout_frequencies_hz'] = p.pop('readout_frequencies_hz')[:5]
        if with_f01: p.update(drive_frequencies_hz=[4.8e9 + i * 1e6 for i in range(5)], drive_amp=.1, spectroscopy_width_s=2e-6)
        point = {'resourceId': 'A', 'requestedSettingV': .2, 'usableReadoutBandHz': [6.98e9, 7.02e9],
                 'pointId': 'point-1', 'recordId': 'fixture-flux_scan', 'routeSha256': digest(bias)}
        request = {'operationId': 'flux-fixture/measure', 'runId': 'fixture-run', 'nodeId': 'fixture-node',
                   'attemptId': 'fixture-attempt', 'resourceId': 'A', 'procedureId': 'sq_flux_point_recheck',
                   'parameters': p, 'parameterVersions': {}, 'leaseToken': 'fixture-no-physical-lease',
                   'fluxPointEvidence': point, 'configurationBinding': {}}
        request['scienceDependencies'] = proof(request, ['cavity_seed', 'flux_scan'], p)
        with ExitStack() as stack:
            stack.enter_context(patch('adapters.science_flux_adapter.provider_for', return_value=Provider))
            stack.enter_context(patch('adapters.science_flux.evidence.validate_point', return_value=point))
            stack.enter_context(patch.object(adapter, 'authenticate_dependency', return_value={'fixtureOnly': True}))
            return adapter.build_plan(request)

    def test_actual_flux_campaign_requires_band_only_for_optional_f01(self):
        no_drive = self.flux({}, with_f01=False)
        self.assertEqual({row['phase'] for row in no_drive['rows']}, {'local_cavity'})
        for fields in ({}, confirmed(source='\t '), confirmed(source='TBD'), confirmed(1, float('inf')),
                       confirmed(True), confirmed(4.8001e9, 5e9), confirmed(4e9, 4.803e9)):
            with self.subTest(fields=fields), self.assertRaises(AdapterError): self.flux(fields)
        plan = self.flux(confirmed(4.8e9, 4.804e9))
        self.assertEqual({row['phase'] for row in plan['rows']}, {'local_cavity', 'f01'})
        self.assertEqual(plan['profileSnapshot']['routingByResource']['A']['xyPassbandHz'], [4.8e9, 4.804e9])


if __name__ == '__main__': unittest.main()
