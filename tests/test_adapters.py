"""Offline adapter checks: no instrument imports, subprocesses, or connections."""
import ast
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from adapters.f8 import AdapterError, F8Adapter, PROCEDURES, _dump
from adapters.history import METHODS

ROOT = Path(__file__).resolve().parents[1]


class AdapterContracts(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads((ROOT/'adapters/f8_profile.json').read_text(encoding='utf-8'))
        self.adapter = F8Adapter(ROOT, self.profile)
        self.request = {'runId':'offline', 'nodeId':'s03', 'attemptId':'a1', 'operationId':'offline-test', 'resourceId':'Q11',
                        'procedureId':'sq_rabi_time', 'parameters':{}, 'parameterVersions':{'drive_hz':1}, 'deadline':time.time()+60}

    def test_every_historical_kind_has_owner_and_method(self):
        history=json.loads((ROOT/'data/imports/history.json').read_text(encoding='utf-8'))
        self.assertEqual({'Q11','Q18'}, {x['id'] for x in history['resources']})
        self.assertGreater(len(history['records']), 40)
        for record in history['records']:
            self.assertEqual(record['result']['dataKind'], 'historical')
            self.assertEqual(record['result']['quality']['status'], 'unverified')
            self.assertFalse(record['result']['provenance']['freshAcquisition'])
            self.assertTrue(record['ownerId'] and record['procedureId'])
            self.assertTrue(all(len(x['sha256'])==64 for x in record['sourceFiles'] if 'sha256' in x))

    def test_real_factory_mapping_and_cpmg(self):
        plan=self.adapter.build_plan(self.request)
        self.assertEqual(plan['jobs'][0]['kind'],'rabi')
        self.assertEqual(plan['jobs'][0]['stop_s'],2.4e-6)
        self.request['procedureId']='sq_cpmg'
        plan=self.adapter.build_plan(self.request)
        self.assertEqual(plan['jobs'][0]['kind'],'cpmg')
        self.assertEqual(plan['jobs'][0]['pi_pulses'],4)
        self.assertIn('CPMG',PROCEDURES['sq_cpmg'][2])

    def test_bounds_nan_unknown_parameters(self):
        for values in ({'shots':2001}, {'drive_amp':1.1}, {'readout_amp':float('nan')}, {'use_compiler':True}, {'points':1}, {'pi_pulses':1.5}):
            self.request['parameters']=values
            with self.assertRaises(AdapterError):self.adapter.build_plan(self.request)

    def test_adc_power_is_normalized_not_dbm(self):
        self.request['procedureId']='ro_power_shift'
        self.request['parameters']={'powers':[-45,-20]}
        with self.assertRaises(AdapterError):self.adapter.build_plan(self.request)

    def test_readout_frequency_scan_selects_native_snr_freq(self):
        self.request['procedureId']='ro_optimize'
        self.request['parameters']={'scan_axis':'frequency','span_hz':2e6,'points':31}
        job=self.adapter.build_plan(self.request)['jobs'][0]
        self.assertEqual(job['kind'],'snr_freq')
        self.assertNotIn('amp_start',job)

    def test_unfrozen_rb_is_not_callable_by_default(self):
        self.request['procedureId']='sq_rb'
        with self.assertRaises(AdapterError) as error:self.adapter.build_plan(self.request)
        self.assertEqual(error.exception.code,'RB_VALIDATION_REQUIRED')

    def test_disabled_execute_does_not_spawn_or_connect(self):
        with self.assertRaises(AdapterError) as error:self.adapter.execute(self.request,lambda x:None,lambda:False)
        self.assertEqual(error.exception.code,'HARDWARE_DISABLED')

    def test_static_preflight_is_static(self):
        report=self.adapter.preflight()
        self.assertFalse(report['hardwareConnected'])
        self.assertFalse(report['ownershipVerified'])
        self.assertTrue(report['ready'],report['checks'])

    def test_reconcile_never_reissues_unknown_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=F8Adapter(directory,self.profile)
            self.assertEqual(adapter.reconcile('a')['status'],'not_started')
            path=adapter._operation_dir('a');path.mkdir(parents=True)
            _dump(path/'adapter_intent.json',{'operationId':'a'})
            self.assertEqual(adapter.reconcile('a')['status'],'unknown')
            _dump(path/'adapter_receipt.json',{'status':'completed','result':{'dataKind':'real'}})
            self.assertEqual(adapter.reconcile('a')['status'],'completed')

    def test_no_hardware_import_in_api_adapter(self):
        tree=ast.parse((ROOT/'adapters/f8.py').read_text(encoding='utf-8'))
        modules=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):modules.extend(x.name for x in node.names)
            elif isinstance(node,ast.ImportFrom):modules.append(node.module)
        self.assertFalse(any(x and x.split('.')[0] in ('QOS','Meas','meas','pyvisa') for x in modules))

    def test_completed_operation_replays_without_second_process(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as temporary:
            adapter=F8Adapter(ROOT,{**self.profile,'enabled':True})
            adapter.operations=Path(temporary)/'operations'
            def fake_process(args,**kwargs):
                directory=Path(kwargs['cwd']);plan=json.loads((directory/'plan.json').read_text(encoding='utf-8'))
                _dump(directory/(plan['phase']+'_complete.json'),{'completed':True})
                _dump(directory/(plan['phase']+'_hardware_after.json'),{'shutdown_failures':[]})
                class Process:
                    pid=123456
                    returncode=0
                    def poll(self):return 0
                return Process()
            result={'dataKind':'real','quality':{'status':'unverified','reasons':[]}}
            with patch('adapters.f8.subprocess.Popen',side_effect=fake_process) as spawn,patch.object(adapter,'_result',return_value=result):
                first=adapter.execute(self.request,lambda x:None,lambda:False)
                second=adapter.execute(self.request,lambda x:None,lambda:False)
                self.assertEqual(first,second)
                self.assertEqual(spawn.call_count,1)
                changed={**self.request,'parameters':{'points':61}}
                with self.assertRaises(AdapterError) as error:adapter.execute(changed,lambda x:None,lambda:False)
                self.assertEqual(error.exception.code,'IDEMPOTENCY_CONFLICT')

    def test_pending_operation_cannot_be_relaunched(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as temporary:
            adapter=F8Adapter(ROOT,{**self.profile,'enabled':True});adapter.operations=Path(temporary)/'operations'
            from adapters.f8 import _digest
            directory=adapter._operation_dir(self.request['operationId']);directory.mkdir(parents=True)
            _dump(directory/'adapter_intent.json',{'requestHash':_digest(self.request)})
            with patch('adapters.f8.subprocess.Popen') as spawn:
                with self.assertRaises(AdapterError) as error:adapter.execute(self.request,lambda x:None,lambda:False)
                self.assertEqual(error.exception.code,'NEEDS_RECONCILIATION');spawn.assert_not_called()

    def test_lost_event_sink_stops_worker_and_keeps_unknown(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as temporary:
            adapter=F8Adapter(ROOT,{**self.profile,'enabled':True,'cancelGraceSeconds':0});adapter.operations=Path(temporary)/'operations'
            class Process:
                pid=123456;returncode=None;killed=False
                def poll(self):return self.returncode
                def kill(self):self.killed=True;self.returncode=-9
                def wait(self,timeout=None):return self.returncode
            process=Process()
            def emit(event):
                if event['event']=='worker_started':raise RuntimeError('simulated lease loss')
            with patch('adapters.f8.subprocess.Popen',return_value=process):
                with self.assertRaises(AdapterError) as error:adapter.execute(self.request,emit,lambda:False)
            self.assertEqual(error.exception.code,'NEEDS_RECONCILIATION')
            self.assertTrue(process.killed)
            self.assertEqual(adapter.reconcile(self.request['operationId'])['status'],'unknown')
            self.assertTrue((adapter._operation_dir(self.request['operationId'])/'STOP_ADC_AFTER_ROW').exists())


if __name__=='__main__':unittest.main()
