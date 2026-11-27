"""New profile through the ordinary HTTP catalog and create routes, no worker."""
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from tests import test_manual_http
from tests.test_science_repeat5 import parameters

class Repeat5HTTP(test_manual_http.ManualHTTPContracts):
    def test_explicit_profile_catalog_and_normal_manual_creation(self):
        from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter,build_repeat5_profile
        root=Path(__file__).resolve().parents[1];base=json.loads((root/'adapters/arclight_profile.json').read_text('utf8'))
        native=ScienceRepeat5Adapter(root,build_repeat5_profile(root,base))
        preview={'executionProfile':'science-shape-repeat5','manualMethods':[],'hardwareConnected':False}
        with patch.object(self.controller,'science_catalog',return_value=preview) as catalog:
            code,body=self.request('/api/catalog?executionProfile=science-shape-repeat5&sourceWorkflowId=source&resourceId=Q11')
            self.assertEqual((code,body),(200,preview))
            self.assertEqual(catalog.call_args.kwargs['execution_profile'],'science-shape-repeat5')
        self.adapter.profile.update({k:deepcopy(native.profile[k]) for k in ('sciencePolicy','scienceIRBPolicy','scienceRepeat5Policy','parameterBounds')})
        self.adapter.capabilities=native.capabilities;self.adapter.parameter_schema=native.parameter_schema
        self.adapter.validation_parameter_keys=native.validation_parameter_keys
        self.controller.adapter_profile_hash=self.controller._adapter_fingerprint()
        method='sq_shape_rabi_amplitude_repeat5'
        code,wf=self.request('/api/workflows',{'name':'TEST_ONLY normal five-X entry','mode':'real','policy':'strict',
            'resourceIds':['Q11'],'executionProfile':'science-shape-repeat5','manualProcedureId':method,
            'manualParameters':{k:v for k,v in parameters().items() if k in native.parameter_schema(method)},
            'manualScienceReferences':[{'role':r,'recordId':'test-'+r} for r in ('f01','readout')]})
        self.assertEqual(code,201,wf);self.assertEqual(wf['nodes'][0]['procedureId'],method)
        self.assertEqual(wf['executionProfile'],'science-shape-repeat5');self.assertFalse(self.adapter.calls)
        code,_=self.request('/api/workflows/'+wf['id']);self.assertEqual(code,200)
