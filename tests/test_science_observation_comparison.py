"""R09 contracts. Temporary fake acquisition only; no hardware or production DB."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import base64
import json
from pathlib import Path
import tempfile
import unittest

from backend.common import APIError
from backend.science_service import ScienceService
from tests.test_controller import make_fixture


class ReadoutObservationContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='r09-contract-');self.addCleanup(self.temp.cleanup)
        self.c,self.adapter=make_fixture(Path(self.temp.name));self.addCleanup(self.c.close)
        self.adapter.profile['resources']['Q11'].update(readout_width_s=2e-6,xy_delay_s=0)
        self.c.adapter_profile_hash=self.c._adapter_fingerprint()
        self.next_projection={'direction':[1.,0.],'origin':[.01,.02]}
        execute=self.adapter.execute
        def acquire(request,emit,cancel):
            raw=execute(request,emit,cancel)
            raw['series']=[dict(name='actual projected IQ',x=[0.,25e-9,50e-9],y=[.1,.3,.52],xUnit='s',yUnit='IQ a.u.')]
            raw['provenance']={'profileHash':'a'*64}
            content=json.dumps({'analysis':{'procedureId':request['procedureId'],'raw_sha256':raw['artifacts'][0]['sha256'],'projection':self.next_projection}}).encode()
            path=Path(self.temp.name)/('analysis-'+str(len(self.adapter.calls))+'.json');path.write_bytes(content)
            raw['artifacts'].append(dict(id='projection-'+str(len(self.adapter.calls)),name='platform_analysis.json',kind='json',localPath=str(path),sha256=hashlib.sha256(content).hexdigest()))
            return raw
        self.adapter.execute=acquire
        wf=self.c.create(dict(name='TEST FAKE ONLY',mode='real',resourceIds=['Q11']))
        self.c.action(wf['id'],dict(operationId='start',action='start',expectedRevision=wf['revision']));self.c.run_workflow(wf['id'])
        node=next(n for n in self.c.get(wf['id'])['nodes'] if n['procedureId']=='sq_rabi_time')
        if not node.get('latestAttemptId'):
            self.c._execute_node(wf['id'],node['id'])
            node=next(n for n in self.c.get(wf['id'])['nodes'] if n['procedureId']=='sq_rabi_time')
        self.service=ScienceService(self.c)
        self.source=self.save('measurement',{},'source',reference={'workflowId':wf['id'],'attemptId':node['latestAttemptId']})
        self.workflow_id=wf['id'];self.node_id=node['id']
        self.document=self.service.write('/readout-calibration-template',{'sourceRecordId':self.source['id'],'sourceSeriesId':'trace-0'},'POST')
        when=datetime.now(timezone.utc)
        self.document['source']={'document':'EXPLICIT TEST FIXTURE','origin':'unit-test only; no real calibration',
            'measuredAt':when.isoformat(),'validFrom':(when-timedelta(days=1)).isoformat(),'validUntil':(when+timedelta(days=1)).isoformat()}
        self.document['statePreparation']['evidence']='synthetic test assertion; not actual state preparation evidence'
        self.document['response'].update(groundResponse=.1,excitedResponse=.5)

    def save(self,kind,inputs,op,**extra):
        return self.service.write('/records',dict(kind=kind,input=inputs,operationId=op,scope={},**extra),'POST')

    def calibration(self,document=None,op='cal'):
        return self.save('readout_calibration',{'sourceRecordId':self.source['id'],'sourceSeriesId':'trace-0',
            'fileName':'TEST_ONLY.json','documentText':json.dumps(document or self.document)},op)

    def simulation(self,cal):
        return self.save('simulation',{'sourceRecordId':self.source['id'],'sourceSeriesId':'trace-0',
            'readoutCalibrationRecordId':cal['id'],'modelParameters':{'rabiHz':1e7,'t1Ns':1e15,'t2Ns':1e15}},'sim')

    def compare(self,sim,mask=None):
        return self.service.write('/compare',{'leftRecordId':self.source['id'],'rightRecordId':sim['id'],
            'leftSeriesId':'trace-0','rightSeriesId':'observation','excludedLeftPoints':mask or []},'POST')

    def test_external_mapping_same_units_hand_difference_mask_restore_and_reload(self):
        cal=self.calibration();sim=self.simulation(cal);calls=len(self.adapter.calls)
        self.assertEqual(cal['dataKind'],'provided_data');self.assertFalse(cal['physicalAccepted'])
        self.assertEqual(sim['dataKind'],'simulation');self.assertFalse(sim['acceptedCalibration'])
        compared=self.compare(sim)
        self.assertEqual(compared['series'][0]['unit'],'IQ a.u.')
        self.assertAlmostEqual(compared['series'][1]['values'][0],.1)
        self.assertAlmostEqual(compared['series'][1]['values'][1],.3,places=9)
        self.assertAlmostEqual(compared['series'][2]['values'][2],.02,places=9)
        hidden=self.compare(sim,[25e-9]);self.assertEqual(hidden['metrics']['missingPoints'],1)
        self.assertIsNone(hidden['series'][2]['values'][1]);self.assertEqual(hidden['view']['originalLeftValues'][1],.3)
        self.assertEqual(self.compare(sim),compared)
        reloaded=ScienceService(self.c).get('/records/'+cal['id'],{})
        self.assertEqual(reloaded['result']['documentSha256'],hashlib.sha256(cal['input']['documentText'].encode()).hexdigest())
        self.assertEqual(len(self.adapter.calls),calls)

    def test_all_points_excluded_keeps_original_table_and_can_restore(self):
        sim=self.simulation(self.calibration())
        output=self.compare(sim,[0.,25e-9,50e-9])
        self.assertEqual(output['metrics'],{'comparedPoints':0,'missingPoints':3,'rmse':None})
        self.assertEqual(output['series'][2]['values'],[None,None,None])
        self.assertEqual(output['view']['originalLeftValues'],[.1,.3,.52])
        self.assertEqual(self.compare(sim)['metrics']['comparedPoints'],3)

    def test_bom_file_hash_uses_original_bytes_and_rejects_invalid_utf8(self):
        original=b'\xef\xbb\xbf'+json.dumps(self.document,ensure_ascii=False).encode('utf-8')
        payload={'sourceRecordId':self.source['id'],'sourceSeriesId':'trace-0','fileName':'BOM_TEST.json',
                 'documentBase64':base64.b64encode(original).decode('ascii')}
        cal=self.save('readout_calibration',payload,'bom')
        self.assertEqual(cal['result']['documentSha256'],hashlib.sha256(original).hexdigest())
        self.assertEqual(self.compare(self.simulation(cal))['metrics']['comparedPoints'],3)
        payload['documentBase64']=base64.b64encode(b'\xff{}').decode('ascii')
        with self.assertRaises(APIError):self.save('readout_calibration',payload,'badutf8')

    def test_missing_or_mismatched_units_projection_offset_and_conditions_are_rejected(self):
        for field,value in [('unit','unknown'),('quantity','excited_probability'),('adapterProfileHash','b'*64),
                            ('projection',{'direction':[1.,0.],'origin':[0.,0.]})]:
            doc=deepcopy(self.document);doc['appliesTo'][field]=value
            with self.subTest(field=field),self.assertRaises(APIError):self.calibration(doc,'bad-'+field)
        doc=deepcopy(self.document);doc['appliesTo']['readoutParameters']['readout_amp']=.9
        with self.assertRaises(APIError):self.calibration(doc,'bad-readout')

    def test_prepared_labels_expired_and_blank_pure_state_evidence_are_rejected(self):
        for field,value in [('kind','prepared_labels'),('evidence','')]:
            doc=deepcopy(self.document);doc['statePreparation'][field]=value
            with self.subTest(field=field),self.assertRaises(APIError):self.calibration(doc,'bad-'+field)
        doc=deepcopy(self.document);doc['source']['validUntil']='2000-01-01T00:00:00Z'
        with self.assertRaises(APIError):self.calibration(doc,'expired')

    def test_bad_mask_and_changed_projection_file_cannot_be_compared(self):
        cal=self.calibration();sim=self.simulation(cal)
        with self.assertRaises(APIError):self.compare(sim,[123.])
        artifact=self.document['appliesTo']['projectionArtifact']['id']
        with self.c.store.connect() as rt:path=rt.db.execute('SELECT path FROM platform_artifacts WHERE id=?',(artifact,)).fetchone()[0]
        Path(path).write_text('{}')
        with self.assertRaises(APIError):self.compare(sim)

    def test_default_probability_model_still_cannot_compare_with_iq(self):
        sim=self.save('simulation',{'sourceRecordId':self.source['id']},'unmapped')
        with self.assertRaises(APIError):self.service.write('/compare',{'leftRecordId':self.source['id'],'rightRecordId':sim['id']},'POST')

    def test_real_iq_independent_pca_axes_or_origins_cannot_be_subtracted(self):
        same=self.save('measurement',{},'same',reference=self.source['reference'])
        self.assertEqual(self.service.write('/compare',{'leftRecordId':self.source['id'],'rightRecordId':same['id']},'POST')['metrics']['rmse'],0)
        for i,projection in enumerate([{'direction':[-1.,0.],'origin':[.01,.02]},
                                       {'direction':[1.,0.],'origin':[0.,0.]},{}]):
            self.next_projection=projection
            self.c._execute_node(self.workflow_id,self.node_id)
            node=next(n for n in self.c.get(self.workflow_id)['nodes'] if n['id']==self.node_id)
            other=self.save('measurement',{},'other-'+str(i),reference={'workflowId':self.workflow_id,'attemptId':node['latestAttemptId']})
            with self.subTest(projection=projection),self.assertRaises(APIError):
                self.service.write('/compare',{'leftRecordId':self.source['id'],'rightRecordId':other['id']},'POST')


if __name__=='__main__':unittest.main()
