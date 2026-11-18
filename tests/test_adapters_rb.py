"""Offline native-RB planning/evidence/paired-statistics tests; no hardware."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest

from adapters.f8 import F8Adapter, AdapterError, _dump
from adapters.rb_suite import CAL_KEYS

try:
    import numpy as np
    NUMPY=True
except ImportError:
    NUMPY=False

ROOT=Path(__file__).resolve().parents[1]


class CurrentCalibrationEvidence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data');self.directory=Path(self.temp.name)
        profile=json.loads((ROOT/'adapters/f8_profile.json').read_text(encoding='utf-8'))
        self.adapter=F8Adapter(ROOT,profile);self.adapter.operations=self.directory/'operations'
        params={k:profile['resources']['Q11'][k] for k in CAL_KEYS}
        self.request={'runId':'offline-rb-test','nodeId':'rb','attemptId':'a','operationId':'offline-rb-suite','resourceId':'Q11','procedureId':'sq_rb','parameters':params,'parameterVersions':{k:2 for k in params},'deadline':time.time()+300}
        evidence=[]
        for procedure in ('sq_rabi_time','ro_iq01'):
            operations=[]
            for stage in ('select','verify'):
                operation=procedure+'/'+stage;operations.append(operation);folder=self.adapter._operation_dir(operation);folder.mkdir(parents=True)
                raw=folder/'raw.qdat';raw.write_bytes(('OFFLINE TEST FIXTURE '+operation).encode())
                original={**self.request,'procedureId':procedure,'operationId':operation}
                result={'dataKind':'real','provenance':{'freshAcquisition':True},'parameters':params,
                        'artifacts':[{'kind':'raw','sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),'sourcePath':str(raw),'localPath':str(raw)}]}
                _dump(folder/'adapter_intent.json',{'request':original})
                _dump(folder/'adapter_receipt.json',{'status':'completed','result':result})
            evidence.append({'procedureId':procedure,'status':'passed','selectionOperationId':operations[0],'verificationOperationId':operations[1],'ruleVersion':'offline-test/1'})
        self.request['validationSnapshot']={'version':1,'runId':self.request['runId'],'resourceId':'Q11','parameters':params,'parameterVersions':self.request['parameterVersions'],'acceptedEvidence':evidence}

    def tearDown(self):self.temp.cleanup()

    def test_plan_requires_current_receipts_not_trial_file(self):
        plan=self.adapter.build_plan(self.request)
        self.assertEqual(plan['suite'],'native_paired_rb')
        self.assertEqual(plan['estimated_total_shots'],976000)
        self.assertEqual(len(plan['validatedCalibration']['acceptedEvidence']),2)
        bad=copy.deepcopy(self.request);bad.pop('validationSnapshot')
        with self.assertRaises(AdapterError):self.adapter.build_plan(bad)

    def test_stale_actual_parameters_rejected(self):
        self.request['parameters']['drive_hz']+=100000
        with self.assertRaises(AdapterError) as err:self.adapter.build_plan(self.request)
        self.assertIn(err.exception.code,('RB_VALIDATION_PARAMETERS','RB_STALE_CALIBRATION'))

    def test_historical_or_reused_evidence_rejected(self):
        item=self.request['validationSnapshot']['acceptedEvidence'][0]
        path=self.adapter._operation_dir(item['verificationOperationId'])/'adapter_receipt.json'
        data=json.loads(path.read_text(encoding='utf-8'));data['result']['dataKind']='historical';_dump(path,data)
        with self.assertRaises(AdapterError) as err:self.adapter.build_plan(self.request)
        self.assertEqual(err.exception.code,'RB_FRESH_DATA_REQUIRED')

    def test_final_publication_requires_complete_independent_rb(self):
        with self.assertRaises(AdapterError) as err:self.adapter.publish_calibration(self.request['validationSnapshot'])
        self.assertEqual(err.exception.code,'RB_PUBLICATION_REQUIRED')

    def test_scan_versions_do_not_invalidate_physical_calibration(self):
        self.request['validationSnapshot']['parameterVersions']=copy.deepcopy(self.request['parameterVersions'])
        self.request['parameterVersions']['points']=7
        self.adapter.build_plan(self.request)
        self.request['parameterVersions']['drive_hz']=3
        with self.assertRaises(AdapterError) as err:self.adapter.build_plan(self.request)
        self.assertEqual(err.exception.code,'RB_VALIDATION_IDENTITY')

    def test_complete_independent_suite_publication_is_immutable(self):
        snapshot=copy.deepcopy(self.request['validationSnapshot']);operations=[]
        for stage in ('selection','verification'):
            operation='rb/'+stage;operations.append(operation);folder=self.adapter._operation_dir(operation);folder.mkdir(parents=True)
            artifacts=[]
            for index in range(62):
                path=folder/(str(index)+'.qdat');path.write_bytes(('OFFLINE RB PUBLICATION TEST '+stage+str(index)).encode())
                artifacts.append({'kind':'raw','sourcePath':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
            original={**self.request,'procedureId':'sq_rb','operationId':operation}
            result={'dataKind':'real','parameters':snapshot['parameters'],'artifacts':artifacts,'metrics':{'gate_fidelity':.95},
                    'provenance':{'freshAcquisition':True,'publicationChecks':{'offline_fixture':True},'frozenPlanHash':'offline-'+stage}}
            _dump(folder/'adapter_intent.json',{'request':original});_dump(folder/'adapter_receipt.json',{'status':'completed','result':result})
        snapshot['acceptedEvidence'].append({'procedureId':'sq_rb','status':'passed','selectionOperationId':operations[0],'verificationOperationId':operations[1],'ruleVersion':'offline-test/1'})
        self.adapter.root=self.directory
        first=self.adapter.publish_calibration(snapshot);second=self.adapter.publish_calibration(snapshot)
        self.assertEqual(first['calibrationVersion'],second['calibrationVersion'])
        self.assertEqual(first['sha256'],second['sha256'])
        self.assertEqual(first['status'],'VALIDATED_SESSION')

    @unittest.skipUnless(NUMPY,'Requires F8 offline math environment')
    def test_full_native_schedule_recovers_all_ideal_sequences(self):
        folder=self.directory/'schedule';folder.mkdir()
        plan=self.adapter.build_plan(self.request,folder)
        self.assertEqual(len(plan['jobs']),62)
        self.assertEqual(plan['jobs'][0]['kind'],'readout_cal')
        self.assertEqual(plan['jobs'][-1]['kind'],'readout_cal')
        self.assertEqual(sum(j['kind']=='rb_iq' for j in plan['jobs']),60)
        audit=json.loads((folder/'frozen_rb_validation.json').read_text(encoding='utf-8'))
        self.assertEqual(audit['decision'],'PASS')
        self.assertEqual(audit['checked_sequences'],480)
        cal=json.loads((folder/'calibration.json').read_text(encoding='utf-8'))
        self.assertEqual(cal['source'],'current_controller_independent_validation')
        self.assertEqual(cal['status'],'FROZEN_FOR_NATIVE_RB')


@unittest.skipUnless(NUMPY,'Requires F8 offline math environment')
class PairedStatistics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('_rb_analysis_offline_test',ROOT/'adapters/rb_analysis.py')
        cls.analysis=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.analysis)

    def test_known_synthetic_decay_recovers_conditional_fidelity_and_drift(self):
        # Explicit synthetic oracle, not a platform measurement or UI artifact.
        rng=np.random.default_rng(515);lengths=[1,2,3,4,6,8,12,16];x=np.array(lengths)
        ref=.18*(.88**x)[None,:,None]+.03+rng.normal(0,.002,(30,8,2))
        irb=.18*(.82**x)[None,:,None]+.03+rng.normal(0,.002,(30,8,2))
        ref=ref+0j;irb=irb+0j
        pre=np.empty((2,4,100),complex);pre[0]=-.2+rng.normal(0,.005,(4,100));pre[1]=.2+rng.normal(0,.005,(4,100))
        post=pre*.95
        result=self.analysis.summarize(ref,irb,lengths,pre,post,200,999)
        self.assertAlmostEqual(result['metrics']['gate_fidelity'],.5*(1+.82/.88),delta=.015)
        self.assertAlmostEqual(result['metrics']['reference_drift_fraction'],.05,delta=.01)
        self.assertEqual(result['metrics']['paired_seed_count'],30)
        self.assertIn('Whole paired seed',result['scope']['statistics'])


if __name__=='__main__':unittest.main()
