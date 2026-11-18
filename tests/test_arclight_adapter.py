import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from adapters.arclight import ArcLightAdapter,CALIBRATION_KEYS,METHODS,build_job
from adapters.f8 import AdapterError

ROOT=Path(__file__).resolve().parents[1]
class ArcLightPlanTests(unittest.TestCase):
    def setUp(self):self.adapter=ArcLightAdapter(ROOT,ROOT/'adapters/arclight_profile.json')
    def request(self,procedure,parameters=None):return {'operationId':'test-'+procedure,'procedureId':procedure,'resourceId':'Q11','runId':'r','parameters':parameters or {}}
    def test_import_planning_does_not_load_native_core(self):
        import sys
        self.adapter.build_plan(self.request('sq_cpmg'))
        self.assertFalse(any(k=='core' or k.startswith('core.inst') for k in sys.modules))
    def test_all_default_methods_and_scan_budgets(self):
        with patch.object(self.adapter,'_validate_snapshot',return_value={'offline_fixture':True}):
            for procedure in METHODS:
                with self.subTest(procedure=procedure):
                    plan=self.adapter.build_plan(self.request(procedure))
                    self.assertGreater(plan['totalRows'],0);self.assertLessEqual(plan['estimated_total_shots'],self.adapter.profile['maximumTotalShots'])
                    self.assertEqual(plan['jobs'][-1]['config']['ADC_Meas']['global']['preserve_existing_dc'],True)
    def test_generated_drive_frequency_axis_rejects_legal_center_crossing_bounds(self):
        for center in self.adapter.profile['parameterBounds']['drive_hz']:
            with self.subTest(center=center),self.assertRaises(AdapterError) as failure:
                self.adapter.build_plan(self.request('sq_spectroscopy',{'drive_hz':center,'span_hz':100e6,'points':9}))
            self.assertEqual(failure.exception.code,'PARAMETER_OUT_OF_RANGE')
            self.assertEqual(failure.exception.details['parameter'],'drive_hz')
            self.assertEqual(failure.exception.details['binding'],'cfg.Q1.para.drivFreq')
    def test_generated_readout_frequency_axes_reject_legal_center_crossing_bounds(self):
        for procedure in ('ro_resonator_survey','ro_power_shift','ro_optimize_frequency','ro_s21_ge'):
            for center in self.adapter.profile['parameterBounds']['readout_hz']:
                with self.subTest(procedure=procedure,center=center),self.assertRaises(AdapterError) as failure:
                    self.adapter.build_plan(self.request(procedure,{'readout_hz':center,'span_hz':100e6,'points':9}))
                self.assertEqual(failure.exception.code,'PARAMETER_OUT_OF_RANGE')
                self.assertEqual(failure.exception.details['parameter'],'readout_hz')
    def test_generated_frequency_endpoints_at_bounds_are_accepted(self):
        for procedure,field in (('sq_spectroscopy','drive_hz'),('ro_resonator_survey','readout_hz'),
                ('ro_power_shift','readout_hz'),('ro_optimize_frequency','readout_hz'),('ro_s21_ge','readout_hz')):
            low,high=self.adapter.profile['parameterBounds'][field]
            for center,endpoint,index in ((low+50e6,low,0),(high-50e6,high,-1)):
                with self.subTest(procedure=procedure,endpoint=endpoint):
                    plan=self.adapter.build_plan(self.request(procedure,{field:center,'span_hz':100e6,'points':9}))
                    values=plan['jobs'][0]['spec']['sweeps'][0]['values']
                    self.assertEqual(values[index],endpoint)
                    self.assertTrue(all(low<=value<=high for value in values))
    def test_explicit_frequency_axis_overrides_unused_center_span(self):
        for procedure,field in (('sq_spectroscopy','drive_hz'),('sq_rabi_frequency','drive_hz'),
                ('ro_resonator_survey','readout_hz'),('ro_power_shift','readout_hz'),
                ('ro_optimize_frequency','readout_hz'),('ro_s21_ge','readout_hz')):
            low,high=self.adapter.profile['parameterBounds'][field]
            with self.subTest(procedure=procedure):
                plan=self.adapter.build_plan(self.request(procedure,{field:high,'span_hz':100e6,'frequencies_hz':[low,high],'points':9}))
                self.assertEqual(plan['jobs'][0]['spec']['sweeps'][0]['values'],[low,high])
        plan=self.adapter.build_plan(self.request('sq_spectroscopy',{'drive_hz':5.5e9,'span_hz':100e6,'start_hz':5.4e9,'stop_hz':5.5e9,'points':9}))
        self.assertEqual(plan['jobs'][0]['spec']['sweeps'][0]['values'][-1],5.5e9)
    def test_generated_amplitude_axes_use_physical_parameter_bounds(self):
        for procedure,field,parameters in (('ro_optimize','readout_amp',{'amp_stop':.99}),
                ('ro_optimize','readout_amp',{'amp_start':0}),('sq_rabi_amplitude','drive_amp',{'amp_start':0})):
            with self.subTest(procedure=procedure,parameters=parameters),self.assertRaises(AdapterError) as failure:
                self.adapter.build_plan(self.request(procedure,parameters))
            self.assertEqual(failure.exception.code,'PARAMETER_OUT_OF_RANGE')
            self.assertEqual(failure.exception.details['parameter'],field)
        for procedure,field in (('ro_optimize','readout_amp'),('sq_rabi_amplitude','drive_amp')):
            low,high=self.adapter.profile['parameterBounds'][field]
            with self.subTest(procedure=procedure,boundary='inclusive'):
                plan=self.adapter.build_plan(self.request(procedure,{'amp_start':low,'amp_stop':high,'points':9}))
                values=plan['jobs'][0]['spec']['sweeps'][0]['values']
                self.assertEqual((values[0],values[-1]),(low,high))
    def test_emitted_axes_validate_interior_points_and_carrier_bindings(self):
        for binding,field,bad in (('cfg.Q1.para.probCarryFreq','readout_hz',7.6e9+1),
                ('cfg.Q1.para.drivCarryFreq','drive_hz',5.5e9+1),
                ('cfg.Q1.para.probCarryScale','readout_amp',.99),
                ('cfg.Q1.gate.U_gates.pi_amp','drive_amp',float('nan')),
                ('cfg.Q1.gate.U_gates.pi_amp','drive_amp',float('inf')),
                ('cfg.Q1.gate.U_gates.pi_amp','drive_amp',True)):
            def emitted(*args,**kwargs):
                job=build_job(*args,**kwargs);low,high=self.adapter.profile['parameterBounds'][field]
                job['spec']['sweeps']=[{'name':'physical_axis','bindings':binding,'values':[low,bad,high]}]
                return job
            with self.subTest(binding=binding,value=bad),patch('adapters.arclight.build_job',side_effect=emitted),self.assertRaises(AdapterError) as failure:
                self.adapter.build_plan(self.request('ro_resonator_survey'))
            self.assertEqual(failure.exception.details['parameter'],field)
            self.assertEqual(failure.exception.details['pointIndex'],1)
    def test_five_x_amplitude_and_short_half_policy(self):
        job=self.adapter.build_plan(self.request('sq_rabi_amplitude'))['jobs'][0]
        gates=job['spec']['circuit_variants']['circuits']['main']['sequences']
        self.assertEqual(sum(g['gate']=='X' for g in gates),5)
        self.assertEqual(job['gate_repeats'],5)
        with self.assertRaises(AdapterError):self.adapter.build_plan(self.request('sq_rabi_time',{'half_pi_amp':.49}))
        self.assertEqual(self.adapter.normalize_candidate('sq_rabi_time',{'drive_length_s':320.12e-9}),{'drive_length_s':320e-9})
    def test_cpmg_wait_sum_is_exact_total(self):
        plan=self.adapter.build_plan(self.request('sq_cpmg',{'pi_pulses':4}));job=plan['jobs'][0]
        columns=[s['values'] for s in job['spec']['sweeps']]
        for n,total in enumerate(job['spec']['meta']['total_delays_s']):self.assertAlmostEqual(sum(c[n] for c in columns),total,places=15)
        self.assertEqual(job['total_delay_multiplier'],8)
        self.assertEqual(sum(g['gate']=='Y' for g in job['spec']['circuit_variants']['circuits']['main']['sequences']),4)
    def test_allxy_uses_new_separate_reference(self):
        p=self.adapter.build_plan(self.request('sq_allxy'))
        self.assertEqual(p['jobs'][0]['rb_role'],'independent_reference');self.assertEqual(p['jobs'][0]['captures'],6)
        self.assertEqual(p['expectedJobRows'],[12,42])
    def test_invalid_timing_target_and_unknown_parameter_rejected(self):
        for parameters in ({'stop_s':100e-6},{'drive_length_s':320.1e-9},{'execute_shell':'anything'}):
            with self.assertRaises(AdapterError):self.adapter.build_plan(self.request('sq_cpmg',parameters))
        req=self.request('sq_t1');req['resourceId']='Q18'
        with self.assertRaises(AdapterError):self.adapter.build_plan(req)
    def test_historical_calibration_cannot_enable_rb(self):
        with self.assertRaises(AdapterError):self.adapter.build_plan(self.request('sq_rb'))
    def test_scoped_enable_does_not_mutate_profile(self):
        enabled=self.adapter.with_execution_enabled();self.assertTrue(enabled.physical_hardware);self.assertTrue(enabled.profile['enabled']);self.assertFalse(self.adapter.profile['enabled'])
    def test_profile_source_hashes_and_ast(self):
        report=self.adapter.preflight();self.assertTrue(report['ready'],report)
        for file in (ROOT/'adapters/arclight_runtime').glob('*.py'):ast.parse(file.read_text(encoding='utf8'))
    def test_frozen_rb_plan_has_new_pre_post_and_native_pair(self):
        with patch.object(self.adapter,'_validate_snapshot',return_value={'offline_fixture':True}):
            p=self.adapter.build_plan(self.request('sq_rb'))
        self.assertEqual([j['rb_role'] for j in p['jobs']],['pre','reference','interleaved','post'])
        self.assertEqual(p['jobs'][1]['spec'],p['jobs'][2]['spec']);self.assertEqual(p['jobs'][2]['interleaved_gate'],'X/2')
        self.assertNotEqual(p['jobs'][0]['name'],p['jobs'][3]['name'])
    def test_existing_chassis_locks_are_never_reported_free(self):
        with tempfile.TemporaryDirectory() as temp:
            lock=Path(temp)/'chassis.lock';lock.write_text('not valid json',encoding='utf8')
            self.adapter.profile['hardwareLocks']=[str(lock),str(Path(temp)/'other.lock')]
            with patch('adapters.f8.F8Adapter.hardware_ownership',return_value={'verified':True,'owners':[]}):
                state=self.adapter.hardware_ownership()
            self.assertEqual(len(state['owners']),1);self.assertEqual(state['owners'][0]['kind'],'chassis_lock')
            self.assertTrue(lock.exists())
    def test_progress_is_actual_completed_percentage(self):
        from adapters.arclight_runtime.platform_hooks import progress_event
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'QCAL_ARCLIGHT_RUN_DIR':temp,'QCAL_ARCLIGHT_JOB_INDEX':'2','QCAL_ARCLIGHT_JOB_COUNT':'4','QCAL_ARCLIGHT_COMPLETED_ROWS':'20','QCAL_ARCLIGHT_TOTAL_ROWS':'100'}):
            progress_event({'job':'native','points_expected':40,'points_completed':10})
            event=json.loads((Path(temp)/'events.jsonl').read_text(encoding='utf8'))
            self.assertEqual(event['progress'],30);self.assertEqual(event['rowNumber'],10);self.assertEqual(event['totalRows'],40)
    def test_cooperative_stop_preserves_partial_data_and_does_not_quarantine(self):
        for status,code in [('cancelled','CANCELLED_AFTER_SHUTDOWN'),('failed_after_shutdown','FAILED_AFTER_SHUTDOWN')]:
            with self.subTest(status=status),tempfile.TemporaryDirectory() as temp:
                adapter=ArcLightAdapter(temp,{**self.adapter.profile,'enabled':True})
                def launch(*args,**kwargs):
                    folder=Path(kwargs['cwd']);raw=folder/'partial_raw_scan.npz';raw.write_bytes(b'partial-read-only-test')
                    (folder/'completion.json').write_text(json.dumps({'state':status,'shutdown_ok':True,'files':[str(raw)],'pointsCompleted':3}),encoding='utf8')
                    class Ended:
                        pid=12345;returncode=1
                        def poll(self):return 1
                    return Ended()
                with patch.object(adapter,'preflight',return_value={'ready':True}),patch.object(adapter,'build_plan',return_value={'max_seconds':5}),patch('adapters.arclight.subprocess.Popen',side_effect=launch):
                    with self.assertRaises(AdapterError) as failure:adapter.execute(self.request('sq_t1'),lambda event:None,lambda:False)
                self.assertEqual(failure.exception.code,code)
                receipt=adapter.reconcile('test-sq_t1');self.assertEqual(receipt['status'],status);self.assertTrue(receipt['shutdownConfirmed'])
                self.assertEqual(receipt['pointsCompleted'],3);self.assertEqual(receipt['partialArtifacts'][0]['kind'],'raw')
                self.assertNotIn('result',receipt)
    def test_rb_publication_returns_stable_calibration_version_and_rechecks_raw(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter=ArcLightAdapter(temp,self.adapter.profile)
            params={k:adapter.profile['resources']['Q11'][k] for k in CALIBRATION_KEYS}
            snapshot={'version':1,'runId':'fresh','resourceId':'Q11','parameters':params,'acceptedEvidence':[
                {'procedureId':'sq_rb','status':'passed','selectionOperationId':'rb-a','verificationOperationId':'rb-b'}]}
            for operation in ('rb-a','rb-b'):
                folder=adapter._operation_dir(operation);folder.mkdir(parents=True);artifacts=[]
                for role in ('pre','reference','interleaved','post'):
                    raw=folder/(role+'.npz');raw.write_bytes((operation+role).encode())
                    artifacts.append({'kind':'raw','sourcePath':str(raw),'sha256':hashlib.sha256(raw.read_bytes()).hexdigest()})
                (folder/'adapter_intent.json').write_text(json.dumps({'request':{'runId':'fresh','resourceId':'Q11','procedureId':'sq_rb'}}),encoding='utf8')
                (folder/'adapter_receipt.json').write_text(json.dumps({'status':'completed','result':{'dataKind':'real','provenance':{'freshAcquisition':True},'analysisQuality':{'accepted':True},'parameters':params,'artifacts':artifacts}}),encoding='utf8')
            with patch.object(adapter,'_validate_snapshot',return_value={'offline_fixture':True}):
                result=adapter.publish_calibration(snapshot);again=adapter.publish_calibration(snapshot)
                self.assertTrue(result['calibrationVersion'].startswith('arclight-'))
                self.assertEqual(result,again)
                (adapter._operation_dir('rb-b')/'pre.npz').write_bytes(b'changed')
                with self.assertRaises(AdapterError):adapter.publish_calibration(snapshot)

if __name__=='__main__':unittest.main()
