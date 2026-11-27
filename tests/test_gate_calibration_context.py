"""Actual ArcLight plans and controller lifecycle, synthetic transport in a temp DB.

No instrument, native worker, socket or production data is opened. The injected
transport creates explicit fixture bytes in the real bridge's archive contract.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from adapters.arclight import ArcLightAdapter
from adapters.f8 import _digest, _dump
from backend.common import APIError, ROOT, now
from backend.controller import Controller


class GateContextContracts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='gate-context-test-only-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        folder = self.root / 'data/imports'; folder.mkdir(parents=True)
        (folder/'history.json').write_text(json.dumps({'version':1,'resources':[{'id':'Q11','label':'Q11'}],'records':[]}),encoding='utf-8')
        profile = json.loads((ROOT/'adapters/arclight_profile_v2.json').read_text(encoding='utf-8'))
        profile.update(enabled=True, connectionConfirmedAt=now())
        self.adapter = ArcLightAdapter(ROOT, profile)
        self.adapter.operations = self.root/'data/runs/arclight_operations'
        self.calls = []
        self.adapter.execute = self.transport
        self.adapter.with_execution_enabled = lambda: self.adapter
        self.c = Controller(self.root, adapter=self.adapter, start_worker=False)
        self.addCleanup(self.c.close)
        self.c.hardware_ownership = lambda **kwargs: {'verified':True,'owners':[]}
        self.c._brain = Mock(return_value={'status':'waiting_model','message':'OFFLINE FIXTURE'})
        self.counter = 0

    def transport(self, request, emit, cancel):
        self.calls.append(deepcopy(request))
        plan = self.adapter.build_plan(request)
        directory = self.adapter._operation_dir(request['operationId']); directory.mkdir(parents=True)
        raw = directory/'fixture.npz'; raw.write_bytes(('TEST_ONLY/no ADC/'+request['operationId']).encode())
        candidate = {'drive_amp': .8655218444566908 if request['operationId'].endswith('/measure') else .8448499519798696} if request['procedureId']=='sq_rabi_amplitude' else {}
        if request['procedureId']=='sq_rabi_time': candidate={'drive_length_s':330.5e-9}
        if request['procedureId']=='ro_optimize': candidate={'readout_amp':request['parameters']['readout_amp']}
        analysis = {'metrics':{'contrast_snr':8,'peak_snr':8,'r_squared':.95,'cycles':2,'assignment_fidelity':.92,'relative_stderr':.1,'t1_s':40e-6,'scan_coverage':1},
                    'candidateParameters':candidate,'series':[],'analysis':{'accepted':True,'fixture':'TEST_ONLY'}}
        completion = {'state':'completed','shutdown_ok':True,'acquiredAt':now(),'analysis':analysis,'files':[str(raw),str(directory/'plan.json')]}
        canonical={k:v for k,v in request.items() if k!='outputDir'}
        _dump(directory/'adapter_intent.json',{'operationId':request['operationId'],'requestHash':_digest(canonical),'request':canonical,'profileHash':_digest(self.adapter.profile)})
        _dump(directory/'plan.json',plan); _dump(directory/'completion.json',completion)
        result=self.adapter._result(directory,plan,request,completion)
        _dump(directory/'adapter_receipt.json',{'status':'completed','operationId':request['operationId'],'requestHash':_digest(canonical),'result':result})
        return result

    def create(self, *, coverage='main', policy='strict', length=330.5e-9):
        wf=self.c.create({'name':'TEST_ONLY gate context','resourceIds':['Q11'],'mode':'real','policy':policy,'coverage':coverage,
            'budget':{'maxTotalShots':100000000}})
        rabi=next(n for n in wf['nodes'] if n['procedureId']=='sq_rabi_amplitude')
        return self.c.set_parameters(wf['id'],rabi['id'],{'operationId':'fixture-parameters','expectedRevision':wf['revision'],'parameters':{'drive_length_s':length}})

    def command(self,wf,action,**extra):
        self.counter+=1
        return self.c.action(wf['id'],{'operationId':'fixture-'+str(self.counter),'expectedRevision':wf['revision'],'action':action,**extra})

    def accepted_rabi(self, *, length=330.5e-9, coverage='main'):
        wf=self.create(length=length,coverage=coverage)
        procedures = ['ro_resonator_survey'] + (['ro_power_shift'] if coverage=='full' else []) + ['sq_spectroscopy','sq_rabi_amplitude']
        for procedure in procedures:
            node=next(n for n in wf['nodes'] if n['procedureId']==procedure)
            self.command(wf,'run_step',nodeId=node['id']); self.c.run_workflow(wf['id']); wf=self.c.get(wf['id'])
            self.assertEqual(self.c._node(wf,node['id'])['engineState'],'awaiting_acceptance',self.c._node(wf,node['id']).get('result'))
            wf=self.command(wf,'approve',nodeId=node['id'])
        return wf

    def test_manual_main_rejects_accepted_330_5ns_amplitude_before_320ns_readout_without_side_effects(self):
        wf=self.accepted_rabi(); iq=next(n for n in wf['nodes'] if n['procedureId']=='ro_iq01')
        before=deepcopy(wf); calls=len(self.calls)
        with self.assertRaises(APIError) as error:self.command(wf,'run_step',nodeId=iq['id'])
        self.assertEqual(error.exception.code,'GATE_CALIBRATION_CONTEXT_MISMATCH')
        self.assertIn('330.5',error.exception.message);self.assertIn('320',error.exception.message)
        self.assertIn('幅度 Rabi 验证门长',error.exception.message)
        self.assertNotIn('drive_length_s',error.exception.message)
        self.assertEqual(len(self.calls),calls)
        after=self.c.get(wf['id']); self.assertEqual(after['revision'],before['revision'])
        self.assertEqual(self.c._node(after,iq['id'])['attempt'],0)
        with self.c.store.connect() as rt:self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],0)

    def test_matching_gate_runs_both_independent_readout_acquisitions(self):
        wf=self.accepted_rabi(length=320e-9); iq=next(n for n in wf['nodes'] if n['procedureId']=='ro_iq01')
        self.command(wf,'run_step',nodeId=iq['id']); self.c.run_workflow(wf['id'])
        self.assertEqual([r['operationId'].rsplit('/',1)[1] for r in self.calls if r['procedureId']=='ro_iq01'],['measure','verify'])
        self.assertEqual(self.c._node(self.c.get(wf['id']),iq['id'])['engineState'],'awaiting_acceptance')

    def test_automatic_main_rejects_before_any_readout_dispatch(self):
        wf=self.create(policy='threshold'); self.command(wf,'start'); self.c.run_workflow(wf['id'])
        current=self.c.get(wf['id']); iq=next(n for n in current['nodes'] if n['procedureId']=='ro_iq01')
        self.assertEqual(iq.get('lastAdmissionError',{}).get('code'),'GATE_CALIBRATION_CONTEXT_MISMATCH')
        self.assertFalse(any(r['procedureId']=='ro_iq01' for r in self.calls));self.assertEqual(iq['attempt'],0)

    def test_full_still_requires_time_rabi_and_allows_the_time_sweep(self):
        wf=self.accepted_rabi(coverage='full'); iq=next(n for n in wf['nodes'] if n['procedureId']=='ro_iq01')
        with self.assertRaises(APIError) as error:self.command(wf,'run_step',nodeId=iq['id'])
        self.assertEqual(error.exception.code,'PREREQUISITES_MISSING')
        time_rabi=next(n for n in wf['nodes'] if n['procedureId']=='sq_rabi_time')
        self.command(wf,'run_step',nodeId=time_rabi['id']);self.c.run_workflow(wf['id'])
        current=self.c.get(wf['id']);self.assertEqual(self.c._node(current,time_rabi['id'])['engineState'],'awaiting_acceptance')
        current=self.command(current,'approve',nodeId=time_rabi['id'])
        self.command(current,'run_step',nodeId=iq['id']);self.c.run_workflow(wf['id'])
        self.assertEqual(self.c._node(self.c.get(wf['id']),iq['id'])['engineState'],'awaiting_acceptance')
        self.assertTrue(all(r['parameters']['drive_length_s']==330.5e-9 for r in self.calls if r['procedureId']=='ro_iq01'))

    def test_missing_or_changed_native_receipt_blocks_even_matching_scalar_context(self):
        wf=self.accepted_rabi(length=320e-9); iq=next(n for n in wf['nodes'] if n['procedureId']=='ro_iq01')
        rabi=next(n for n in wf['nodes'] if n['procedureId']=='sq_rabi_amplitude')
        directory=self.adapter._operation_dir(rabi['latestAttemptId']+'/verify')
        cases = [
            ('adapter_receipt.json', lambda data: {**data,'requestHash':'0'*64}),
            ('adapter_intent.json', lambda data: {**data,'profileHash':'0'*64}),
            ('completion.json', lambda data: {**data,'shutdown_ok':False}),
            ('plan.json', lambda data: {**data,'parameters':{**data['parameters'],'drive_length_s':330.5e-9}}),
        ]
        for name,change in cases:
            with self.subTest(name=name):
                path=directory/name; original=path.read_bytes(); _dump(path,change(json.loads(original)))
                try:
                    with self.assertRaises(APIError) as error:self.command(wf,'run_step',nodeId=iq['id'])
                    self.assertEqual(error.exception.code,'GATE_CALIBRATION_EVIDENCE_REQUIRED')
                finally:path.write_bytes(original)
        raw=directory/'fixture.npz'; original=raw.read_bytes(); raw.write_bytes(b'changed TEST_ONLY raw')
        try:
            with self.assertRaises(APIError) as error:self.command(wf,'run_step',nodeId=iq['id'])
            self.assertEqual(error.exception.code,'GATE_CALIBRATION_EVIDENCE_REQUIRED')
        finally:raw.write_bytes(original)
        receipt=directory/'adapter_receipt.json'; original=receipt.read_bytes(); receipt.unlink()
        try:
            with self.assertRaises(APIError) as error:self.command(wf,'run_step',nodeId=iq['id'])
            self.assertEqual(error.exception.code,'GATE_CALIBRATION_EVIDENCE_REQUIRED')
        finally:receipt.write_bytes(original)
        self.assertEqual(len(self.calls),6)

    def test_time_method_name_or_quality_boolean_alone_cannot_bypass_gate_context(self):
        wf=self.accepted_rabi(coverage='full')
        with self.c.store.transaction() as rt:
            current=self.c.get(wf['id'],rt)
            timing=next(n for n in current['nodes'] if n['procedureId']=='sq_rabi_time')
            timing.update(engineState='verified',status='success',result={'dataKind':'real','quality':{'status':'passed'},'parameters':{'drive_length_s':320e-9}})
            self.c.store.save(rt,current,'TEST_ONLY invalid timing evidence')
        wf=self.c.get(wf['id']);iq=next(n for n in wf['nodes'] if n['procedureId']=='ro_iq01')
        with self.assertRaises(APIError) as error:self.command(wf,'run_step',nodeId=iq['id'])
        self.assertEqual(error.exception.code,'GATE_CALIBRATION_EVIDENCE_REQUIRED')

    def test_replay_and_other_adapter_contracts_do_not_gain_or_lose_execution_qualification(self):
        from backend.gate_calibration_context import require_current_gate
        from tests.test_controller import make_fixture
        wf=self.accepted_rabi();iq=next(n for n in wf['nodes'] if n['procedureId']=='ro_iq01')
        with self.c.store.connect() as rt:
            self.assertIsNone(require_current_gate(self.c,rt,{**wf,'mode':'replay'},iq,{}))
        other=tempfile.TemporaryDirectory();self.addCleanup(other.cleanup)
        fake,adapter=make_fixture(other.name);self.addCleanup(fake.close)
        with fake.store.connect() as rt:self.assertIsNone(require_current_gate(fake,rt,wf,iq,{}))

    def test_full_gate_allows_later_readout_optimization_without_requiring_old_readout_parameters(self):
        wf=self.accepted_rabi(coverage='full')
        for procedure in ('sq_rabi_time','ro_iq01'):
            node=next(n for n in wf['nodes'] if n['procedureId']==procedure)
            self.command(wf,'run_step',nodeId=node['id']);self.c.run_workflow(wf['id'])
            wf=self.c.get(wf['id']);self.assertEqual(self.c._node(wf,node['id'])['engineState'],'awaiting_acceptance')
            wf=self.command(wf,'approve',nodeId=node['id'])
        optimizer=next(n for n in wf['nodes'] if n['procedureId']=='ro_optimize')
        wf=self.c.set_parameters(wf['id'],optimizer['id'],{'operationId':'change-only-readout','expectedRevision':wf['revision'],
            'parameters':{'readout_hz':7.05e9,'readout_amp':.3,'readout_width_s':2.2e-6}})
        self.command(wf,'run_step',nodeId=optimizer['id']);self.c.run_workflow(wf['id'])
        current=self.c.get(wf['id']);self.assertEqual(self.c._node(current,optimizer['id'])['engineState'],'awaiting_acceptance')
        operations=[r for r in self.calls if r['procedureId']=='ro_optimize'];self.assertEqual(len(operations),2)
        self.assertTrue(all(r['parameters']['drive_length_s']==330.5e-9 and r['parameters']['readout_hz']==7.05e9 for r in operations))

    def test_only_real_fixed_gate_consumers_are_checked_and_mismatch_does_not_read_large_raw(self):
        from backend.gate_calibration_context import require_current_gate
        wf=self.accepted_rabi();iq=next(n for n in wf['nodes'] if n['procedureId']=='ro_iq01')
        with self.c.store.connect() as rt:
            p,v=self.c._parameters(rt,wf,iq)
            with patch('backend.gate_calibration_context.file_hash',side_effect=AssertionError('must reject before raw I/O')):
                with self.assertRaises(APIError) as error:require_current_gate(self.c,rt,wf,iq,p)
                self.assertEqual(error.exception.code,'GATE_CALIBRATION_CONTEXT_MISMATCH')
                for procedure in ('ro_resonator_survey','ro_power_shift','sq_spectroscopy','sq_rabi_time','sq_rabi_amplitude'):
                    self.assertIsNone(require_current_gate(self.c,rt,wf,{**iq,'procedureId':procedure},p))
                p.update(drive_length_s=330.5e-9,drive_hz=p['drive_hz']+1e6)
                with self.assertRaises(APIError) as error:require_current_gate(self.c,rt,wf,iq,p)
                self.assertIn('驱动频率不一致',error.exception.message)
                self.assertNotIn('drive_hz',error.exception.message)
                self.assertNotIn('验证门长',error.exception.message)

    def test_matching_source_hashes_only_each_raw_and_plan_once_not_all_plot_files(self):
        from backend.gate_calibration_context import require_current_gate
        from backend.common import file_hash
        wf=self.accepted_rabi(length=320e-9);iq=next(n for n in wf['nodes'] if n['procedureId']=='ro_iq01')
        with self.c.store.connect() as rt:
            p,_=self.c._parameters(rt,wf,iq)
            with patch('backend.gate_calibration_context.file_hash',wraps=file_hash) as hashing:
                proof=require_current_gate(self.c,rt,wf,iq,p)
                self.assertEqual(proof['sourceProcedureId'],'sq_rabi_amplitude')
                self.assertEqual(hashing.call_count,4)

