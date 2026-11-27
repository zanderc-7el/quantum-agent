"""External channel imports use only an isolated store; all samples TEST_ONLY."""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from backend.common import APIError, digest, dumps
from backend.science_service import ScienceService
from tests.test_controller import make_fixture


def fixture(scope):
    context = {'channelId':'TEST_ONLY_XY', 'measurementPoint':'TEST_ONLY room-temperature port',
               'quantity':'voltage', 'unit':'V', 'conditions':{'loadOhm':50., 'rangeV':2.}}
    target = {'kind':'specified_target', 'context':deepcopy(context), 'windowS':[0.,3e-9],
              'timeS':[0.,1e-9,2e-9,3e-9], 'values':[0.,1.,1.,0.]}
    common = {'dataOrigin':'test_fixture', 'context':deepcopy(context), 'calibrationId':'TEST_ONLY_H',
              'acquisitionId':'TEST_ONLY_H_CAPTURE', 'acquiredAt':'2026-11-20T00:00:00Z',
              'source':{'document':'TEST_ONLY container.h5','provider':'contract tests, never acquired',
                        'sha256':'a'*64,'recordPath':'/calibration'}}
    calibration = {**deepcopy(common), 'version':'v1', 'validFrom':'2026-11-19T00:00:00Z',
                   'validUntil':'2026-11-21T00:00:00Z', 'frequencyHz':[0.,1e8,2e8],
                   'real':[1.,.8,.6], 'imag':[0.,-.6,-.8], 'unit':'ratio'}
    responses = {}
    for name,minute,values in [('baseline',1,[0.,1.4,.8,-.2]), ('compensated',2,[0.,1.1,.9,0.])]:
        responses[name] = {**deepcopy(common), 'calibrationVersion':'v1',
            'acquisitionId':'TEST_ONLY_'+name, 'acquiredAt':f'2026-11-20T00:0{minute}:00Z',
            'windowS':target['windowS'][:], 'timeS':target['timeS'][:], 'values':values}
        responses[name]['source']['recordPath']='/captures/'+name
    return {'schema':'qcal-external-channel-comparison/1','dataOrigin':'test_fixture',
            'scope':deepcopy(scope),'context':context,'target':target,'calibration':calibration,**responses}


class ChannelComparisonContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='r05-contract-');self.addCleanup(self.temp.cleanup)
        self.c,self.adapter=make_fixture(Path(self.temp.name));self.addCleanup(self.c.close)
        self.wf=self.c.create({'name':'TEST ONLY external channel','mode':'real','policy':'strict','resourceIds':['Q11']})
        self.scope={'workflowId':self.wf['id'],'resourceId':'Q11'}
        self.service=ScienceService(self.c);self.document=fixture(self.scope)

    def body(self, document=None, operation='import'):
        return {'kind':'predistortion_comparison','operationId':operation,'scope':self.scope,
                'input':{'fileName':'TEST_ONLY.json','documentText':json.dumps(document or self.document)}}

    def save(self, document=None, operation='import'):
        return self.service.write('/records',self.body(document,operation),'POST')

    def test_template_preview_save_query_recover_and_independent_source_hashes(self):
        template=self.service.write('/channel-comparison-template',{'scope':self.scope},'POST')
        self.assertEqual(template['scope'],self.scope)
        with self.assertRaises(APIError):self.save(template,'empty')
        body=self.body();preview=self.service.write('/channel-comparison-preview',
            {'scope':self.scope,'input':body['input']},'POST')
        record=self.save();result=record['result'];self.assertEqual(result,preview)
        self.assertAlmostEqual(result['metrics']['baselineRmse'],.06**.5)
        self.assertAlmostEqual(result['metrics']['compensatedRmse'],.005**.5)
        self.assertAlmostEqual(result['metrics']['baselineOvershoot'],.4)
        self.assertAlmostEqual(result['metrics']['baselineUndershoot'],.2)
        self.assertAlmostEqual(result['transfer']['magnitude'][1],1.)
        self.assertAlmostEqual(result['transfer']['phaseRad'][1],-.6435011087932844)
        self.assertEqual(result['documentSha256'],hashlib.sha256(body['input']['documentText'].encode()).hexdigest())
        self.assertEqual(record['dataKind'],'provided_data');self.assertFalse(record['physicalAccepted'])
        self.assertEqual(result['qualification'],'test_fixture_not_measurement')
        self.assertFalse(record['dependencyAssessment']['executionEligible'])
        self.assertEqual(self.save(),record)
        recovered=ScienceService(self.c).get('/records/'+record['id'],{})
        self.assertEqual(recovered['result'],result)
        listed=self.service.get('/records',{'kind':'predistortion_comparison','summary':'true'})['records']
        self.assertEqual(listed[0]['result']['documentSha256'],result['documentSha256'])
        self.assertEqual(self.adapter.calls,[])
        self.assertEqual(self.c.get(self.wf['id'])['revision'],self.wf['revision'])
        sources=result['sourceSummary']
        self.assertEqual(sources['baseline']['source']['sha256'],sources['compensated']['source']['sha256'])
        self.assertNotEqual(sources['baseline']['contentSha256'],sources['compensated']['contentSha256'])

    def test_scope_point_conditions_axis_unit_window_and_calibration_version_must_match(self):
        mutations=[lambda d:d['scope'].update(resourceId='Q99'),
                   lambda d:d['compensated']['context'].update(measurementPoint='different port'),
                   lambda d:d['calibration']['context'].update(channelId='different channel'),
                   lambda d:d['baseline']['context']['conditions'].update(loadOhm=75),
                   lambda d:d['target']['context'].update(unit='mV'),
                   lambda d:d['compensated']['timeS'].__setitem__(1,1.1e-9),
                   lambda d:d['baseline']['windowS'].__setitem__(1,4e-9),
                   lambda d:d['compensated'].update(calibrationVersion='v2')]
        for i,mutate in enumerate(mutations):
            doc=deepcopy(self.document);mutate(doc)
            with self.subTest(i=i),self.assertRaises(APIError):self.save(doc,'bad-'+str(i))

    def test_self_comparison_missing_provenance_expired_and_renamed_same_capture_rejected(self):
        mutations=[lambda d:d['compensated'].update(acquisitionId=d['baseline']['acquisitionId']),
            lambda d:d['compensated']['source'].update(recordPath=d['baseline']['source']['recordPath']),
            lambda d:d['compensated'].update(acquiredAt=d['baseline']['acquiredAt']),
            lambda d:d['compensated']['source'].update(provider=''),
            lambda d:d['baseline']['source'].update(sha256='not a sha'),
            lambda d:d['calibration'].update(validUntil='2026-11-19T12:00:00Z')]
        for i,mutate in enumerate(mutations):
            doc=deepcopy(self.document);mutate(doc)
            with self.subTest(i=i),self.assertRaises(APIError):self.save(doc,'bad-source-'+str(i))

    def test_transfer_calibration_may_be_derived_from_baseline_capture_not_self_comparison(self):
        doc=deepcopy(self.document)
        for key in ('acquisitionId','acquiredAt','source'):doc['calibration'][key]=deepcopy(doc['baseline'][key])
        result=self.save(doc,'shared-calibration-capture')['result']
        self.assertEqual(result['sourceSummary']['calibration']['acquisitionId'],result['sourceSummary']['baseline']['acquisitionId'])
        self.assertFalse(result['physicalAccepted'])

    def test_model_fake_flags_unknown_fields_and_nonfinite_cannot_promote(self):
        for i,mutate in enumerate([
            lambda d:d.update(dataOrigin='model'),lambda d:d.update(physicalAccepted=True),
            lambda d:d['baseline'].update(dataOrigin='external_measurement'),
            lambda d:d['calibration']['real'].__setitem__(0,float('nan')),
            lambda d:d['calibration']['real'].__setitem__(0,10**400),
            lambda d:d['target']['timeS'].__setitem__(1,0.),
            lambda d:d['compensated']['values'].__setitem__(0,True)]):
            doc=deepcopy(self.document);mutate(doc)
            with self.subTest(i=i),self.assertRaises(APIError):self.save(doc,'untrusted-'+str(i))
        doc=deepcopy(self.document)
        for item in [doc,doc['calibration'],doc['baseline'],doc['compensated']]:item['dataOrigin']='external_measurement'
        result=self.save(doc,'claimed')['result']
        self.assertEqual(result['qualification'],'external_claim_not_platform_verified')
        self.assertFalse(result['physicalAccepted']);self.assertFalse(result['acceptedCalibration'])

    def test_bom_exact_bytes_size_duplicate_json_keys_and_operation_conflict(self):
        original=b'\xef\xbb\xbf'+json.dumps(self.document,ensure_ascii=False,indent=2).encode()
        body=self.body();body['input']={'fileName':'TEST_ONLY_BOM.json','documentBase64':base64.b64encode(original).decode()}
        result=self.service.write('/records',body,'POST')['result']
        self.assertEqual(result['documentSha256'],hashlib.sha256(original).hexdigest())
        with self.assertRaises(APIError):self.save()
        for i,content in enumerate(['{"schema":"x","schema":"y"}', ' '*524289, '{"x":NaN}']):
            body=self.body(operation='parse-'+str(i));body['input']['documentText']=content
            with self.subTest(i=i),self.assertRaises(APIError):self.service.write('/records',body,'POST')
        body=self.body(operation='invalidutf8');body['input']={'fileName':'bad.json','documentBase64':'/w=='}
        with self.assertRaises(APIError):self.service.write('/records',body,'POST')

    def test_all_external_rows_are_immutable_and_never_eligible_as_transfer_calibration(self):
        record=self.save()
        body={'kind':'predistortion','operationId':'reuse','scope':self.scope,
              'input':{'target':[0,1,0],'impulseResponse':[1], 'transferCalibrationRecordId':record['id']}}
        with self.assertRaises(APIError):self.service.write('/records',body,'POST')
        with self.c.store.transaction() as rt:
            changed=deepcopy(record);changed['result']['metrics']['baselineRmse']=0
            rt.db.execute('UPDATE platform_science_records SET payload=?,payload_hash=? WHERE id=?',
                          (dumps(changed),digest(changed),record['id']))
        with self.assertRaises(APIError):self.service.get('/records/'+record['id'],{})

    def test_zero_transfer_phase_and_extreme_percentage_are_not_false_numbers(self):
        doc=deepcopy(self.document);doc['calibration']['real'][0]=0.
        result=self.save(doc,'zero-h')['result']
        self.assertIsNone(result['transfer']['phaseRad'][0])
        self.assertFalse(result['transfer']['phaseDefined'][0])
        doc['target']['values']=[0.]*4;doc['baseline']['values']=[1e-320]*4;doc['compensated']['values']=[1.]*4
        result=self.save(doc,'small-baseline')['result']
        self.assertEqual(result['metrics']['baselineRmse'],1e-320)
        self.assertIsNone(result['metrics']['rmseReductionPercent'])
        self.assertEqual(result['metrics']['rmseReductionPercentUnavailableReason'],'numeric_range')

    def test_boolean_condition_is_not_numeric_one_but_int_and_float_are_same_quantity(self):
        doc=deepcopy(self.document)
        for item in [doc,doc['target'],doc['calibration'],doc['baseline'],doc['compensated']]:
            item['context']['conditions']['gain']=1
        doc['compensated']['context']['conditions']['gain']=True
        with self.assertRaises(APIError):self.save(doc,'boolean-condition')
        doc['compensated']['context']['conditions']['gain']=1.0
        self.assertEqual(self.save(doc,'numeric-condition')['result']['context']['conditions']['gain'],1)

    def test_optional_platform_anchor_reuses_registered_bytes_and_cannot_cross_scope(self):
        self.c.action(self.wf['id'],{'action':'run_step','nodeId':self.wf['nodes'][0]['id'],
                      'operationId':'TEST_ONLY_measure','expectedRevision':self.wf['revision']})
        self.c.run_workflow(self.wf['id'])
        current=self.c.get(self.wf['id']);node=current['nodes'][0]
        source=self.service.write('/records',{'kind':'measurement','operationId':'anchor','scope':self.scope,'input':{},
            'reference':{'workflowId':current['id'],'attemptId':node['latestAttemptId']}},'POST')
        calls=len(self.adapter.calls)
        body=self.body(operation='anchored');body['input']['sourceRecordId']=source['id']
        saved=self.service.write('/records',body,'POST')
        self.assertEqual(saved['sourceRecordId'],source['id'])
        self.assertEqual(saved['dependencyProof']['rootReferences'],[{'role':'source_record','recordId':source['id']}])
        self.assertFalse(saved['physicalAccepted']);self.assertEqual(len(self.adapter.calls),calls)
        other=self.c.create({'name':'TEST_ONLY other Q11','mode':'real','resourceIds':['Q11']})
        bad=deepcopy(body);bad['operationId']='cross';bad['scope']={'workflowId':other['id'],'resourceId':'Q11'}
        bad['input']['documentText']=json.dumps(fixture(bad['scope']))
        with self.assertRaises(APIError):self.service.write('/records',bad,'POST')
        model=self.service.write('/records',{'kind':'pulse','operationId':'model','scope':self.scope,'input':{}},'POST')
        bad=deepcopy(body);bad['operationId']='model-anchor';bad['input']['sourceRecordId']=model['id']
        with self.assertRaises(APIError):self.service.write('/records',bad,'POST')
        with self.c.store.connect() as rt:
            path=rt.db.execute('SELECT path FROM platform_artifacts WHERE id=?',(source['evidence'][0]['id'],)).fetchone()[0]
        Path(path).write_bytes(b'TEST_ONLY changed acquisition bytes')
        stale=self.service.get('/records/'+saved['id'],{})
        self.assertFalse(stale['evidenceIntact']);self.assertEqual(stale['result'],saved['result'])
        body['operationId']='changed-anchor'
        with self.assertRaises(APIError):self.service.write('/records',body,'POST')

    def test_import_errors_name_operator_fields_in_chinese_without_changing_codes(self):
        empty=self.service.write('/channel-comparison-template',{'scope':self.scope},'POST')
        for key,expected in [('channelId','通道名称'),('measurementPoint','物理测点')]:
            with self.subTest(empty=key),self.assertRaises(APIError) as caught:self.save(empty,'empty-'+key)
            self.assertEqual(caught.exception.code,'SCIENCE_CHANNEL_INVALID')
            self.assertIn('请填写'+expected,caught.exception.message)
            self.assertNotIn(key,caught.exception.message)
            empty['context'][key]='TEST_ONLY filled'
        mutations=[
            (lambda d:d['calibration']['source'].update(document=''),'来源文件名称'),
            (lambda d:d['calibration']['source'].update(provider=''),'数据提供方'),
            (lambda d:d['calibration']['source'].update(recordPath=''),'文件内采集记录路径'),
            (lambda d:d['calibration']['source'].update(sha256='invalid'),'来源文件内容摘要'),
            (lambda d:d['calibration'].update(calibrationId=''),'校准编号'),
            (lambda d:d['calibration'].update(version=''),'校准版本'),
            (lambda d:d['baseline'].pop('values'),'未补偿基线时域采集'),
            (lambda d:d['compensated'].pop('values'),'补偿后时域采集')]
        for i,(mutate,expected) in enumerate(mutations):
            doc=deepcopy(self.document);mutate(doc)
            with self.subTest(expected=expected),self.assertRaises(APIError) as caught:self.save(doc,'wording-'+str(i))
            self.assertEqual(caught.exception.code,'SCIENCE_CHANNEL_INVALID')
            self.assertIn(expected,caught.exception.message)
            for internal in ('channelId','measurementPoint','document','provider','recordPath','sha256',
                             'calibrationId','version','baseline','compensated'):
                self.assertNotIn(internal,caught.exception.message)


if __name__=='__main__':unittest.main()
