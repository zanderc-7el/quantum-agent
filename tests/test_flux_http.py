"""Temporary loopback server only; no instrument transport is installed."""
from unittest.mock import patch
from tests import test_manual_http

class FluxHTTPContracts(test_manual_http.ManualHTTPContracts):
    def test_flux_catalog_is_explicit_readonly_and_reports_provider_block(self):
        value={'executionProfile':'science-flux','hardwareConnected':False,
            'manualMethods':[{'procedureId':'ro_flux_scan','capability':'unavailable'}],
            'preflight':{'staticReady':True,'missingFields':{'Q11':['biasRoutes.Q11']}}}
        with patch.object(self.controller,'science_catalog',return_value=value) as read:
            status,body=self.request('/api/catalog?executionProfile=science-flux&sourceWorkflowId=source&resourceId=Q11')
            self.assertEqual((status,body),(200,value))
            read.assert_called_once_with(chip_version_id=None,deployment_version_id=None,resource_id='Q11',source_workflow_id='source',execution_profile='science-flux')
        self.assertEqual(self.controller.list(),[]);self.assertEqual(self.adapter.calls,[])
    def test_unbound_flux_request_cannot_create_effects_or_dispatch(self):
        status,body=self.request('/api/workflows',{'name':'blocked flux','mode':'real','policy':'strict','resourceIds':['Q11'],
            'executionProfile':'science-flux','manualProcedureId':'ro_flux_scan','manualParameters':{'bias_values_v':[0]}})
        self.assertEqual(status,409,body);self.assertEqual(self.controller.list(),[]);self.assertEqual(self.adapter.calls,[])
        with self.controller.store.connect() as rt:self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],0)
