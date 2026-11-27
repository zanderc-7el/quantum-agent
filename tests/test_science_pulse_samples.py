"""Read-only registered pulse samples; all files and acquisitions are fixtures."""
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from backend.common import APIError,digest,dumps
from backend.science_service import ScienceService
from tests.test_controller import make_fixture


def npy(values):
    header=repr({'descr':'<f8','fortran_order':False,'shape':(len(values),)}).encode('ascii')
    header+=b' ' * ((16-(10+len(header)+1)%16)%16)+b'\n'
    return b'\x93NUMPY\x01\x00'+struct.pack('<H',len(header))+header+struct.pack('<'+'d'*len(values),*values)


class PulseSamples(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.c,self.adapter=make_fixture(Path(self.temp.name));self.addCleanup(self.c.close)
        self.service=ScienceService(self.c)
        wf=self.c.create({'name':'TEST_ONLY pulse archive','mode':'real','policy':'strict','resourceIds':['Q11']})
        self.c.action(wf['id'],{'action':'run_step','nodeId':wf['nodes'][0]['id'],'expectedRevision':wf['revision'],'operationId':'fixture-run'})
        self.c.run_workflow(wf['id']);self.wf=self.c.get(wf['id']);self.node=self.wf['nodes'][0]
        self.folder=self.c.root/'data/pulse-fixture';self.folder.mkdir(parents=True)
        self.raw_i=[0.,.1,.2,0.];self.raw_q=[0.,-.1,0.,0.];self.rf=[0.,0.,.1,.2,.1,0.,0.,0.]
        def sha(v):return hashlib.sha256(struct.pack('<'+'d'*len(v),*v)).hexdigest()
        self.segment={'id':'xy0','label':'fixture half','channel':'xy','startSample':2,'endSample':6,
            'sampleCount':4,'sampleRateHz':4e9,'basebandI':{'array':'xy0_i','sha256':sha(self.raw_i),'dtype':'<f8','samples':4},
            'basebandQ':{'array':'xy0_q','sha256':sha(self.raw_q),'dtype':'<f8','samples':4}}
        self.wave={'name':'xy_1','dtype':'<f8','shape':[8],'sampleRateHz':4e9,'startAt':0.,'fill':True,'sha256':sha(self.rf)}
        self.files={}
        self.archive=self.folder/'preflight_0000.npz'
        with zipfile.ZipFile(self.archive,'w',zipfile.ZIP_DEFLATED) as z:
            for name,value in {'xy0_i':self.raw_i,'xy0_q':self.raw_q,'xy_1':self.rf}.items():z.writestr(name+'.npy',npy(value))
        self.compiled=[{'rowId':0,'file':str(self.archive),'sha256':hashlib.sha256(self.archive.read_bytes()).hexdigest(),
            'waves':{'xy_1':self.wave},'segments':[self.segment]}]
        self.axes=[{'id':0,'stage':'half_drag_scan','condition':'verification_candidate','capture':0,'axisValue':-.3e-9}]
        self.upload={'rowId':0,'channel':'XY',**self.wave,'uploadCommandsCompleted':True,'deviceWaveformReadback':False}
        for name,value in [('compiled_plan.json',self.compiled),('axes.json',self.axes)]:
            (self.folder/name).write_text(json.dumps(value),encoding='utf8')
        (self.folder/'uploads.jsonl').write_text(json.dumps(self.upload)+'\n',encoding='utf8')
        self.install_files()

    def install_files(self):
        with self.c.store.transaction() as rt:
            row=rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?',(self.node['latestAttemptId'],)).fetchone()
            attempt=json.loads(row[0]);raw=attempt['result'];artifacts=[]
            for path in self.folder.iterdir():
                data=path.read_bytes();item={'id':digest(str(path)+hashlib.sha256(data).hexdigest()),'name':path.name,'kind':'raw' if path.suffix=='.npz' else 'metadata',
                    'localPath':str(path),'sha256':hashlib.sha256(data).hexdigest()}
                artifacts.append(item);self.files[path.name]=item
                self.c._artifact(rt,item)
            raw['artifacts']=artifacts;raw['sourceRunId']=attempt['verificationOperationId']
            raw.setdefault('provenance',{})['operationId']=attempt['verificationOperationId']
            self.c.store.attempt(rt,self.wf['id'],attempt)
        self.record=self.service.write('/records',{'kind':'measurement','scope':{},'input':{},'operationId':'save-'+digest(self.compiled),
            'reference':{'workflowId':self.wf['id'],'attemptId':self.node['latestAttemptId']}},'POST')

    def get(self,**query):return self.service.get('/records/'+self.record['id']+'/pulse-samples',query)

    def test_registered_arrays_and_upload_are_bound_without_dispatch_or_writes(self):
        with self.c.store.connect() as rt:before=list(rt.db.iterdump())
        calls=len(self.adapter.calls);value=self.get()
        self.assertEqual(value['status'],'available');self.assertEqual(value['samples']['i'],self.raw_i)
        self.assertEqual(value['samples']['q'],self.raw_q);self.assertEqual(value['samples']['rf'],self.rf[2:6])
        self.assertEqual(value['upload']['status'],'matched');self.assertFalse(value['upload']['deviceWaveformReadback'])
        self.assertEqual(value['selected'],{'rowId':0,'segmentId':'xy0'})
        with self.c.store.connect() as rt:self.assertEqual(list(rt.db.iterdump()),before)
        self.assertEqual(len(self.adapter.calls),calls)

    def test_view_checks_selected_files_but_never_claims_full_archive_validation(self):
        with self.c.store.connect() as rt:
            attempt=json.loads(rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?',(self.node['latestAttemptId'],)).fetchone()[0])
            old=next(a for a in attempt['result']['selectionResult']['artifacts'] if a['kind']=='raw')
            path=rt.db.execute('SELECT path FROM platform_artifacts WHERE id=?',(old['id'],)).fetchone()[0]
        Path(path).write_bytes(b'damaged unrelated selection acquisition')
        with patch.object(self.service,'_reference',side_effect=AssertionError('full archive read is not the pulse view contract')):
            result=self.get()
        self.assertEqual(result['status'],'available')
        self.assertEqual(result['verifiedEvidenceScope'],'selected_stage_pulse_artifacts')
        full=self.service.get('/records/'+self.record['id'],{})
        self.assertFalse(full['evidenceIntact'],'full scientific source validation must still find unrelated corruption')

    def test_wrong_scope_selector_and_changed_archive_are_rejected(self):
        for query in ({'workflowId':'other'},{'rowId':'9'},{'segmentId':'missing'},{'stage':'invalid'},{'rowId':['0','1']}):
            with self.subTest(query=query),self.assertRaises(APIError):self.get(**query)
        self.archive.write_bytes(b'changed')
        with self.assertRaises(APIError):self.get()

    def test_missing_legacy_iq_and_missing_upload_do_not_invent_data(self):
        self.segment.pop('basebandI');self.segment.pop('basebandQ')
        (self.folder/'compiled_plan.json').write_text(json.dumps(self.compiled),encoding='utf8')
        (self.folder/'uploads.jsonl').unlink();self.install_files()
        value=self.get();self.assertEqual(value['status'],'unavailable');self.assertIsNone(value['samples'])
        self.assertEqual(value['upload']['status'],'not_recorded')

    def test_legacy_base_index_metadata_is_unavailable_but_not_accepted_for_half_irb(self):
        self.compiled=[{'base_index':0,'axes':{'cfg.Q1.gate.U_gates.pi_len':0.},
            'waves':[{'name':'xy_1','samples':344000,'srate':4e9,'peak':0.,'is_complex':False,'sha256':'a'*64}],
            'pulse_timing':[{'channel':'Q1','start_s':0.,'duration_s':2e-9,'pulse':"<PulseType.XY: 'XY'>"}]}]
        (self.folder/'compiled_plan.json').write_text(json.dumps(self.compiled),encoding='utf8');self.install_files()
        value=self.get()
        self.assertEqual(value['status'],'unavailable');self.assertIsNone(value['samples'])
        self.assertIn('旧版',value['reason']);self.assertIn('ADC',value['reason'])
        from backend.science_pulse_samples import _source_binding
        for method in ('sq_drag_half_scan','sq_drag_irb'):
            def changed(service,rt,record):
                source=_source_binding(service,rt,record);source['attempt']['procedureId']=method;return source
            with self.subTest(method=method),patch('backend.science_pulse_samples._source_binding',side_effect=changed),self.assertRaises(APIError):
                self.get()

    def test_legacy_detection_does_not_hide_mixed_or_malformed_modern_archive(self):
        from copy import deepcopy
        modern=deepcopy(self.compiled[0])
        legacy={'base_index':1,'axes':{},'waves':[],'pulse_timing':[]}
        no_row=deepcopy(modern);no_row.pop('rowId')
        invalid_legacy={**legacy,'waves':'not-an-array'}
        for rows in ([modern,legacy],[no_row],[invalid_legacy]):
            with self.subTest(rows=rows):
                self.compiled=rows
                (self.folder/'compiled_plan.json').write_text(json.dumps(rows),encoding='utf8');self.install_files()
                with self.assertRaises(APIError):self.get()

    def test_rehashed_metadata_cannot_replace_array_or_upload_identity(self):
        self.segment['basebandI']['sha256']='0'*64
        (self.folder/'compiled_plan.json').write_text(json.dumps(self.compiled),encoding='utf8');self.install_files()
        with self.assertRaises(APIError):self.get()

    def test_precompiled_samples_without_upload_remain_explicitly_not_uploaded(self):
        (self.folder/'uploads.jsonl').unlink();self.install_files()
        value=self.get();self.assertEqual(value['status'],'available')
        self.assertEqual(value['upload']['status'],'not_recorded')
        self.assertEqual(value['upload']['receipts'],[])

    def test_changed_upload_cannot_borrow_preflight_digest(self):
        self.upload['sha256']='f'*64
        (self.folder/'uploads.jsonl').write_text(json.dumps(self.upload)+'\n',encoding='utf8')
        self.install_files()
        with self.assertRaises(APIError):self.get()

    def test_other_stage_does_not_reuse_verification_waveform(self):
        value=self.get(stage='selection')
        self.assertEqual(value['stage'],'selection');self.assertEqual(value['status'],'unavailable')
        self.assertEqual(value['artifacts'],[])
        self.assertNotEqual(value['operationId'],self.get()['operationId'])

    def test_exact_index_subsampling_keeps_ends_and_never_interpolates(self):
        from backend.science_pulse_samples import _sample_indices
        indices=_sample_indices(4001)
        self.assertEqual((indices[0],indices[-1],len(indices)),(0,4000,2048))
        self.assertEqual(indices,sorted(set(indices)))

    def test_npy_rejects_object_payload_and_wrong_shape(self):
        from backend.science_pulse_samples import _array
        bad=npy([1.]).replace(b"'<f8'",b"'|O8'")
        memory=io.BytesIO()
        with zipfile.ZipFile(memory,'w') as z:z.writestr('x.npy',bad)
        with zipfile.ZipFile(io.BytesIO(memory.getvalue())) as z,self.assertRaises(APIError):_array(z,'x',{})
        memory=io.BytesIO()
        with zipfile.ZipFile(memory,'w') as z:z.writestr('x.npy',npy([.1]))
        with zipfile.ZipFile(io.BytesIO(memory.getvalue())) as z,self.assertRaises(APIError):
            _array(z,'x',{'dtype':'<f8','samples':2,'sha256':hashlib.sha256(struct.pack('<d',.1)).hexdigest()})

    def test_unregistered_row_file_and_overrange_real_samples_are_rejected(self):
        original=self.compiled[0]['file'];self.compiled[0]['file']=str(self.folder/'other-operation.npz')
        (self.folder/'compiled_plan.json').write_text(json.dumps(self.compiled),encoding='utf8');self.install_files()
        with self.assertRaises(APIError):self.get()
        self.compiled[0]['file']=original;self.raw_i[1]=1.01
        payload=struct.pack('<'+'d'*len(self.raw_i),*self.raw_i)
        self.segment['basebandI']['sha256']=hashlib.sha256(payload).hexdigest()
        with zipfile.ZipFile(self.archive,'w',zipfile.ZIP_DEFLATED) as z:
            for name,values in {'xy0_i':self.raw_i,'xy0_q':self.raw_q,'xy_1':self.rf}.items():z.writestr(name+'.npy',npy(values))
        self.compiled[0]['sha256']=hashlib.sha256(self.archive.read_bytes()).hexdigest()
        (self.folder/'compiled_plan.json').write_text(json.dumps(self.compiled),encoding='utf8');self.install_files()
        with self.assertRaises(APIError):self.get()

    def test_native_legal_baseband_peak_and_filled_rf_contract(self):
        self.raw_i[1]=.995
        self.segment['basebandI']['sha256']=hashlib.sha256(struct.pack('<4d',*self.raw_i)).hexdigest()
        self.raw_q[1]=0.
        self.segment['basebandQ']['sha256']=hashlib.sha256(struct.pack('<4d',*self.raw_q)).hexdigest()
        with zipfile.ZipFile(self.archive,'w',zipfile.ZIP_DEFLATED) as z:
            for name,values in {'xy0_i':self.raw_i,'xy0_q':self.raw_q,'xy_1':self.rf}.items():z.writestr(name+'.npy',npy(values))
        self.compiled[0]['sha256']=hashlib.sha256(self.archive.read_bytes()).hexdigest()
        (self.folder/'compiled_plan.json').write_text(json.dumps(self.compiled),encoding='utf8');self.install_files()
        self.assertEqual(self.get()['samples']['i'][1],.995)
        self.wave['fill']=False;self.upload['fill']=False
        (self.folder/'compiled_plan.json').write_text(json.dumps(self.compiled),encoding='utf8')
        (self.folder/'uploads.jsonl').write_text(json.dumps(self.upload)+'\n',encoding='utf8');self.install_files()
        with self.assertRaises(APIError):self.get()

    def test_http_read_endpoint_uses_real_service_and_does_not_mutate(self):
        import threading
        from backend.server import Server
        from tests.test_manual_http import ManualHTTPContracts
        server=Server(('127.0.0.1',0),self.c);server.science_service=self.service
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.base=f'http://127.0.0.1:{server.server_address[1]}'
        try:
            status,value=ManualHTTPContracts.request(self,'/api/science/records/'+self.record['id']+'/pulse-samples?rowId=0&segmentId=xy0')
            self.assertEqual(status,200,value);self.assertEqual(value['samples']['i'],self.raw_i)
            status,_=ManualHTTPContracts.request(self,'/api/science/records/'+self.record['id']+'/pulse-samples?rowId=0&rowId=1')
            self.assertEqual(status,409)
        finally:server.shutdown();server.server_close();thread.join(2)

    def test_actual_native_compiler_archive_and_injected_upload_receipt(self):
        # Actual native arrays, never device output. Only this temporary DB is
        # registered; TEST FAKE upload success is not physical evidence.
        fixture=Path(__file__).parent/'fixtures/r19_native_pulse_samples'
        envelope=json.loads((fixture/'fixture.json').read_text(encoding='utf8'))
        self.assertTrue(envelope['fixtureOnly']);self.assertFalse(envelope['realUploadOccurred'])
        self.assertFalse(envelope['physicalAccepted']);self.assertFalse(envelope['hardwareConnected'])
        for path in self.folder.iterdir():path.unlink()
        for name in ('compiled_plan.json','preflight_0007.npz','uploads.jsonl','plan.json'):
            data=(fixture/name).read_bytes();self.assertEqual(hashlib.sha256(data).hexdigest(),envelope['files'][name])
            (self.folder/name).write_bytes(data)
        self.compiled=json.loads((self.folder/'compiled_plan.json').read_text(encoding='utf8'))
        self.compiled[0]['file']=str(self.folder/'preflight_0007.npz')
        (self.folder/'compiled_plan.json').write_text(json.dumps(self.compiled),encoding='utf8')
        plan=json.loads((self.folder/'plan.json').read_text(encoding='utf8'))
        (self.folder/'axes.json').write_text(json.dumps(plan['rows']),encoding='utf8')
        self.install_files()
        value=self.get(rowId='7',segmentId='xy0')
        self.assertEqual(value['status'],'available');self.assertEqual(value['upload']['status'],'matched')
        self.assertEqual(value['segment']['coefficientMode'],'empirical_beta')
        self.assertEqual(len(value['samples']['i']),value['segment']['sampleCount'])
        self.assertTrue(any(v!=0 for v in value['samples']['q']))
        self.assertEqual(value['samples']['rfSampleCount'],value['segment']['endSample']-value['segment']['startSample'])


if __name__=='__main__':unittest.main()
