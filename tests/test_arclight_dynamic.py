import ast
import argparse
from copy import deepcopy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
try:
    import numpy as np
except ImportError:
    np = None
from adapters.arclight_dynamic import DynamicArcLightAdapter, build_dynamic_profile, RUNTIME
from adapters.arclight import CALIBRATION_KEYS, METHODS
from adapters.arclight_configurable_runtime.routing import receiver_route
from adapters.f8 import AdapterError

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipIf(np is None, 'Requires NumPy; use the F8 Python312 interpreter for offline math tests')
class DynamicArcLightTests(unittest.TestCase):
    def setUp(self):
        self.base = json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf-8-sig'))
        # Independent synthetic seeds: no previous acquisition is used.
        common = dict(readout_amp=.7,drive_amp=.8,half_pi_amp=.8,drive_length_s=320e-9,
            xy_delay_s=100e-9,xy_attenuation_db=3.75,xy_filter_mhz=4700,readout_width_s=2.2e-6,
            pulse_compatibility='f8_validated_short_half_dc_buff2ns_v1')
        self.chip = {'id':'chip-v1','resources':{
            'Q12':{'parameters':dict(common,readout_hz=7.1e9,drive_hz=4.7e9)},
            'Q19':{'parameters':dict(common,readout_hz=7.2e9,drive_hz=5.1e9)}}}
        self.deployment = {'id':'deployment-v1','instrument':deepcopy(self.base['instrument']), 'routes':{
            'Q12':{'xyPhysical':[1,0],'readoutPhysical':[8,0],'adcPhysical':[0,0]},
            'Q19':{'xyPhysical':[3,1],'readoutPhysical':[8,1],'adcPhysical':[0,1]}}}
        self.profile=build_dynamic_profile(ROOT,self.base,self.chip,self.deployment,['Q12','Q19'])
        self.adapter=DynamicArcLightAdapter(ROOT,self.profile)

    def plan(self,resource,procedure='sq_spectroscopy'):
        return self.adapter.build_plan({'operationId':'test-'+resource+'-'+procedure,'procedureId':procedure,
            'resourceId':resource,'parameters':{'points':9,'capture_repeats':1}})

    def test_distinct_resource_frequencies_routes_and_compiler_aliases(self):
        for resource,drive,readout,logical in [('Q12',4.7e9,7.1e9,[7,0]),('Q19',5.1e9,7.2e9,[9,1])]:
            plan=self.plan(resource);job=plan['jobs'][0]
            self.assertEqual(job['target'],resource)
            self.assertEqual(job['resourceId'],resource)
            self.assertEqual(job['routing']['xyLogical'],logical)
            self.assertEqual(job['config']['Q1']['para']['drivFreq'],drive)
            self.assertEqual(job['config']['Q1']['para']['probFreq'],readout)
            self.assertEqual(job['config']['_agent']['source_qubit'],resource)
            axis=job['spec']['sweeps'][0]['values']
            self.assertAlmostEqual((axis[0]+axis[-1])/2,drive)
        self.assertFalse(any(k=='core' or k.startswith('core.inst') for k in sys.modules))

    def test_missing_route_mapping_and_out_of_bounds_fail_closed(self):
        for mutate in [lambda d:d['routes'].pop('Q19'),
            lambda d:d['routes']['Q19'].update(xyPhysical=[8,0]),
            lambda d:d['routes']['Q19'].update(xyLogical=[6,0]),
            lambda d:d['routes']['Q19'].update(adcPhysical=[1,0]),
            lambda d:d['routes']['Q19'].update(adcSampleRateHz=3.2e9)]:
            deployment=deepcopy(self.deployment);mutate(deployment)
            with self.assertRaises(AdapterError):build_dynamic_profile(ROOT,self.base,self.chip,deployment,['Q19'])
        chip=deepcopy(self.chip);chip['resources']['Q19']['parameters'].pop('drive_hz')
        with self.assertRaises(AdapterError):build_dynamic_profile(ROOT,self.base,chip,self.deployment,['Q19'])
        chip=deepcopy(self.chip);chip['resources']['Q19']['parameters']['drive_hz']=5.7e9
        with self.assertRaises(AdapterError):build_dynamic_profile(ROOT,self.base,chip,self.deployment,['Q19'])
        with self.assertRaises(AdapterError):self.plan('Q11')

    def test_bounds_locks_disabled_and_detached_versions(self):
        self.assertEqual(self.profile['hardwareLocks'],self.base['hardwareLocks'])
        self.assertEqual(self.profile['parameterBounds'],self.base['parameterBounds'])
        self.assertFalse(self.profile['enabled'])
        self.chip['resources']['Q19']['parameters']['drive_hz']=5.4e9
        self.deployment['routes']['Q19']['xyPhysical']=[2,0]
        self.assertEqual(self.plan('Q19')['parameters']['drive_hz'],5.1e9)
        self.assertEqual(self.plan('Q19')['routing']['xyPhysical'],[3,1])

    def test_source_manifest_integrity_and_preflight_without_device(self):
        result=self.adapter.preflight()
        failures=[c for c in result['checks'] if not c['ok'] and c['name'] not in ('python','visa')]
        self.assertEqual(failures,[])
        self.assertFalse(result['hardwareConnected'])

    def test_allxy_per_operation_jobs_have_same_selected_route(self):
        plan=self.plan('Q19','sq_allxy')
        self.assertEqual(len(plan['jobs']),2)
        for job in plan['jobs']:
            self.assertEqual(receiver_route(job)[0],plan['routing'])
            self.assertEqual(job['target'],'Q19')

    def test_result_rejects_wrong_target_or_route_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);folder=d/'job';folder.mkdir();plan=self.plan('Q19')
            for name,obj in [('instrument.json',{'resourceId':'Q12','routing':plan['routing'],'address':plan['instrument']['address']}),
                             ('status.json',{'target':'Q19','actualRoute':plan['routing']})]:
                (folder/name).write_text(json.dumps(obj))
            with self.assertRaises(AdapterError) as failure:
                self.adapter._result(d,plan,{'resourceId':'Q19'}, {'folders':[str(folder)]})
            self.assertEqual(failure.exception.code,'RESULT_ROUTING_INVALID')

    def test_receiver_main_uses_selected_route_without_native_imports(self):
        # Execute only the receiver function AST with explicit stubs. No bootstrap,
        # driver, native core or hardware service is imported or launched.
        source=ROOT/RUNTIME/'run_single_qubit.py'
        tree=ast.parse(source.read_text(encoding='utf-8-sig'))
        main=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='main')
        for resource in ('Q12','Q19'):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'plans').mkdir();job=self.plan(resource)['jobs'][0]
                jobpath=root/'plans/job.json';jobpath.write_text(json.dumps(job))
                hardware=Mock();hardware.driver.handle=None
                create=Mock(return_value=hardware)
                stop=RuntimeError('test reached pure compiler boundary')
                def save(path,obj):Path(path).write_text(json.dumps(obj))
                namespace=dict(Path=Path,sys=SimpleNamespace(argv=['receiver',str(jobpath)]),ROOT=root,SOURCE_ROOT=ROOT/RUNTIME,
                    argparse=argparse,deepcopy=deepcopy,dt=dt,hashlib=hashlib,json=json,os=os,np=np,traceback=traceback,
                    receiver_route=receiver_route,save_json=save,create_hardware=create,DelegatedLock=lambda:SimpleNamespace(owned=False,release=lambda:None),
                    Experiment=SimpleNamespace(load=Mock(side_effect=stop)),progress_event=lambda s:None,
                    close_resources=lambda h,e:{'shutdown_ok':True})
                exec(compile(ast.Module(body=[main],type_ignores=[]),str(source),'exec'),namespace)
                with patch.object(sys,'argv',['receiver',str(jobpath)]),self.assertRaisesRegex(RuntimeError,'pure compiler boundary'):
                    namespace['main']()
                self.assertEqual(create.call_args.args[2],job['instrument']['address'])
                self.assertEqual(create.call_args.kwargs['visa_backend'],job['visaLibrary'])
                self.assertEqual(hardware.set_channel.call_args_list[0].args,('RO',tuple(job['routing']['readoutLogical'])))
                self.assertEqual(hardware.set_channel.call_args_list[1].args,('AD',tuple(job['routing']['adcLogical'])))
                self.assertEqual(hardware.set_channel.call_args_list[2].args,('XY',tuple(job['routing']['xyLogical'])))
                hardware.open.assert_not_called()
                status=json.loads(next((root/'results').glob('*/status.json')).read_text())
                self.assertEqual(status['resourceId'],resource)
                self.assertEqual(status['configuredRoute'],job['routing'])

    def test_stub_receiver_acquisition_receipts_and_clock_use_selected_route(self):
        # Entire entry runs against explicit in-memory stubs; no native package,
        # instrument DLL, service, worker subprocess, or physical I/O exists.
        source=ROOT/RUNTIME/'run_single_qubit.py'
        main=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='main')
        for resource in ('Q12','Q19'):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'plans').mkdir()
                job=self.plan(resource)['jobs'][0]
                job['instrument'].update(address='TCPIP::127.0.0.2::7111::SOCKET',host='127.0.0.2',port=7111)
                jobpath=root/'plans/job.json';jobpath.write_text(json.dumps(job))
                wave=lambda name:SimpleNamespace(name=name,srate=4e9,get_data=lambda:np.ones(8)*.2)
                seq=SimpleNamespace(perform_sequence_awg=lambda:None,
                    required_devices=lambda:[SimpleNamespace(name='CeyearStationF8')],
                    waves=SimpleNamespace(_waves={'xy':wave('xy_Q1'),'ro':wave('ro_B1')}),expand_pulse_sequence=lambda:[])
                experiment=SimpleNamespace(_get_scan_points=lambda:[{}],get_sequence=lambda ax:seq)
                experiment.execute_scan=lambda:experiment.runner.get_raw_data(seq,job['spec']['meta']['shots'],{})
                hardware=Mock();hardware.driver.handle.query.side_effect=lambda command:'4700' if command=='AWG5000:SELFILTERFREQ?' else 'STUB,NO_HARDWARE'
                hardware.driver.select_rf_filter.return_value={'testStub':True}
                lock=SimpleNamespace(owned=False)
                lock.acquire=lambda details:setattr(lock,'owned',True)
                lock.release=lambda:setattr(lock,'owned',False)
                osc=SimpleNamespace(oscboardSelect=Mock(),oscSamRate=lambda h:2.8)
                events=[]
                def save(path,obj):Path(path).write_text(json.dumps(obj))
                def raw(*args,**kwargs):
                    shots=args[2];blocks=kwargs['acquisitions_per_point']
                    return ([np.ones((1,shots*blocks))*.2,np.ones((1,shots*blocks))*.1,['Q1']],np.repeat(np.arange(blocks,dtype=np.int16),shots))
                namespace=dict(Path=Path,sys=SimpleNamespace(argv=['receiver',str(jobpath),'--measure']),ROOT=root,SOURCE_ROOT=ROOT/RUNTIME,
                    argparse=argparse,deepcopy=deepcopy,dt=dt,hashlib=hashlib,json=json,os=os,np=np,traceback=traceback,
                    receiver_route=receiver_route,save_json=save,create_hardware=Mock(return_value=hardware),DelegatedLock=lambda:lock,
                    Experiment=SimpleNamespace(load=lambda *a,**k:experiment),SequenceRunner=object,
                    progress_event=lambda status:events.append(deepcopy(status)),check_cancel=lambda:None,OSC=osc,
                    get_repeated_raw_data=raw,install_validated_short_half=lambda exp:{'testStub':True},
                    close_resources=lambda h,e:{'shutdown_ok':True})
                exec(compile(ast.Module(body=[main],type_ignores=[]),str(source),'exec'),namespace)
                with patch.object(sys,'argv',['receiver',str(jobpath),'--measure']):
                    folder=namespace['main']()
                receipt=json.loads((folder/'instrument.json').read_text());status=json.loads((folder/'status.json').read_text())
                self.assertEqual(receipt['resourceId'],resource)
                self.assertEqual(receipt['operationId'],job['operationId'])
                self.assertEqual(receipt['routing'],job['routing'])
                self.assertEqual(receipt['address'],'TCPIP::127.0.0.2::7111::SOCKET')
                self.assertEqual(receipt['RO'],job['routing']['readoutLogical'])
                self.assertEqual(receipt['AD'],job['routing']['adcLogical'])
                self.assertEqual(status['actualRoute'],job['routing'])
                self.assertEqual(status['resourceId'],resource)
                osc.oscboardSelect.assert_called_once_with(hardware.driver.handle,job['routing']['adcPhysical'][0])
                hardware.driver.select_rf_filter.assert_called_once_with(tuple(job['routing']['xyLogical']),4700)
                self.assertTrue(any(event.get('actualRoute')==job['routing'] for event in events))

    def test_all_native_methods_compile_selected_q19_with_frozen_route(self):
        # Pure specification compilation only: the RB evidence is a marked stub,
        # and none of these plans are submitted to a native worker.
        from adapters.arclight_configurable_runtime.platform_specs import build_job
        base=json.loads((ROOT/RUNTIME/'base_config.json').read_text())
        with patch.object(self.adapter,'_validate_snapshot',return_value={'offline_test_only':True}):
            for procedure in METHODS:
                with self.subTest(procedure=procedure):
                    plan=self.plan('Q19',procedure)
                    self.assertGreater(plan['totalRows'],0)
                    for job in plan['jobs']:
                        self.assertEqual(job['target'],'Q19')
                        self.assertEqual(job['resourceId'],'Q19')
                        self.assertEqual(job['operationId'],plan['operationId'])
                        self.assertEqual(job['routing']['xyLogical'],[9,1])
                        self.assertEqual(job['routing']['readoutLogical'],[14,1])
                        self.assertEqual(job['routing']['adcLogical'],[15,1])
                        self.assertEqual(job['config']['Q1']['para']['drivFreq'],5.1e9)
                        self.assertEqual(job['config']['Q1']['para']['probFreq'],7.2e9)
                    direct=build_job(plan['parameters'],procedure,base,'offline_direct',resource_id='Q19',
                        route=plan['routing'],instrument=plan['instrument'],visa_library=self.profile['visaLibrary'])
                    self.assertEqual(direct['target'],'Q19')
                    self.assertEqual(direct['routing'],plan['routing'])
        self.assertFalse(any(k=='core' or k.startswith('core.inst') for k in sys.modules))

if __name__=='__main__':unittest.main()
