"""Third profile stays explicit at the real HTTP boundary; temporary DB only."""
from unittest.mock import patch
from tests import test_manual_http


class ScienceIRBHTTP(test_manual_http.ManualHTTPContracts):
    def test_explicit_third_catalog_identity_and_no_hardware_mutation(self):
        preview={'executionProfile':'science-v3-irb','manualMethods':[],'hardwareConnected':False}
        with patch.object(self.controller,'science_catalog',return_value=preview) as catalog:
            status,body=self.request('/api/catalog?executionProfile=science-v3-irb&sourceWorkflowId=origin&resourceId=Q11')
            self.assertEqual((status,body),(200,preview))
            catalog.assert_called_once_with(chip_version_id=None,deployment_version_id=None,resource_id='Q11',source_workflow_id='origin',execution_profile='science-v3-irb')
            status,_=self.request('/api/catalog?executionProfile=science-v3-irb&executionProfile=science-v3')
            self.assertEqual(status,400);self.assertEqual(catalog.call_count,1)
        self.assertEqual(self.controller.list(),[]);self.assertEqual(self.adapter.calls,[])

    def test_new_profile_without_configuration_cannot_create_or_execute(self):
        status,body=self.request('/api/workflows',{'name':'unbound IRB','mode':'real','policy':'strict','resourceIds':['Q11'],
            'executionProfile':'science-v3-irb','manualProcedureId':'sq_drag_irb'})
        self.assertEqual(status,409,body);self.assertEqual(self.controller.list(),[])
        self.assertEqual(self.adapter.calls,[])
