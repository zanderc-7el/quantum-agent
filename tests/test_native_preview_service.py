from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.common import APIError
from tests.test_controller import make_fixture


class NativePreviewServiceTests(unittest.TestCase):
    def setUp(self):
        from backend.native_preview import NativePreviewService
        self.temp=tempfile.TemporaryDirectory(prefix='native-service-');self.addCleanup(self.temp.cleanup)
        self.c,self.adapter=make_fixture(Path(self.temp.name));self.addCleanup(self.c.close)
        self.wf=self.c.create({'name':'native compile test','mode':'real','policy':'strict','resourceIds':['Q11']})
        self.service=NativePreviewService(self.c)
        self.body={'kind':'drag','scope':{'workflowId':self.wf['id'],'resourceId':'Q11'},
                   'nodeId':self.wf['nodes'][0]['id'],'input':{'shape':'Gaussian','durationNs':20,'amplitude':.2,
                   'sigmaNs':4,'lambda1':.3,'anharmonicityHz':-200e6,'anharmonicitySource':'unvalidated supplied model'}}

    def test_native_result_saved_with_actual_task_evidence_and_download_hash(self):
        result=self.service.preview(self.body)
        self.assertEqual(result['source'],'native_compiler');self.assertEqual(result['dataKind'],'compiled')
        self.assertEqual(result['scope']['workflowId'],self.wf['id'])
        self.assertEqual(result['scope']['nodeId'],self.body['nodeId'])
        self.assertEqual(result['input']['frequencyHz'],4.4e9)
        self.assertEqual(result['sourceEvidence']['parameterSources']['sigmaNs'],'explicit_compile_input')
        self.assertFalse(result['acceptedCalibration']);self.assertEqual(self.adapter.calls,[])
        for artifact in result['artifacts']:
            path,metadata=self.c.artifact(artifact['id'])
            self.assertTrue(path.is_file());self.assertEqual(artifact['sha256'],metadata['sha256'])
        saved=json.loads((Path(self.temp.name)/'data/science_compiles'/result['id']/'record.json').read_text(encoding='utf-8'))
        self.assertEqual(saved,result)

    def test_wrong_scope_and_claimed_evidence_reject_before_spawn(self):
        cases=[{'scope':{'workflowId':self.wf['id'],'resourceId':'Q18'}},
               {'scope':{'workflowId':self.wf['id'],'chipVersionId':'forged'}},
               {'nodeId':'foreign'}, {'sourceHash':'forged'}, {'dispatched':True}, {'scope':{}}]
        with patch('subprocess.Popen',side_effect=AssertionError('spawned')):
            for changes in cases:
                with self.subTest(changes=changes),self.assertRaises(APIError):self.service.preview({**self.body,**changes})

    def test_multi_resource_node_cannot_borrow_other_resource(self):
        with self.c.store.transaction() as rt:
            wf=self.c.store.get(rt,self.wf['id']);wf['resourceIds'].append('Q18')
            foreign=deepcopy(wf['nodes'][0]);foreign['id']='q18-node';foreign['resourceId']='Q18';wf['nodes'].append(foreign)
            self.c.store.save(rt,wf,'fixture_multi',{})
        with patch('subprocess.Popen',side_effect=AssertionError('spawned')):
            with self.assertRaises(APIError):self.service.preview({**self.body,'nodeId':'q18-node'})
            with self.assertRaises(APIError):self.service.preview({k:v for k,v in self.body.items() if k!='nodeId'})

    def test_changed_parameter_evidence_rejected_after_child(self):
        from backend.native_preview import compile_offline
        def change(*args,**kwargs):
            result=compile_offline(*args,**kwargs)
            with self.c.store.transaction() as rt:
                wf=self.c.store.get(rt,self.wf['id']);wf['nodes'][0]['parameterOverrides']={'drive_hz':4.5e9}
                self.c.store.save(rt,wf,'fixture_change',{})
            return result
        with patch('backend.native_preview.compile_offline',side_effect=change):
            with self.assertRaises(APIError) as error:self.service.preview(self.body)
        self.assertEqual(error.exception.code,'NATIVE_SOURCE_CHANGED')

if __name__=='__main__':unittest.main()
