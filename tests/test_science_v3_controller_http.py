"""Read-only profile selection HTTP contracts, using an explicit test controller."""
from unittest.mock import patch
from tests import test_manual_http


class ScienceCatalogHTTP(test_manual_http.ManualHTTPContracts):
    def test_catalog_requires_explicit_unique_profile_and_bound_identity(self):
        preview = {'executionProfile': 'science-v3', 'manualMethods': [], 'hardwareConnected': False}
        with patch.object(self.controller, 'science_catalog', return_value=preview) as catalog:
            status, body = self.request('/api/catalog?executionProfile=science-v3&chipVersionId=chip-v1&deploymentVersionId=dep-v1&resourceId=Q11')
            self.assertEqual((status, body), (200, preview))
            catalog.assert_called_once_with(chip_version_id='chip-v1', deployment_version_id='dep-v1', resource_id='Q11')
            status, body = self.request('/api/catalog?executionProfile=science-v3&executionProfile=other')
            self.assertEqual(status, 400)
            self.assertEqual(catalog.call_count, 1)
        self.assertEqual(self.controller.list(), [])
        self.assertEqual(self.adapter.calls, [])

    def test_missing_binding_is_rejected_without_state_creation(self):
        status, body = self.request('/api/catalog?executionProfile=science-v3')
        self.assertEqual(status, 409)
        self.assertEqual(body['error']['code'], 'SCIENCE_CONFIGURATION_REQUIRED')
        self.assertEqual(self.controller.list(), [])
        self.assertEqual(self.adapter.calls, [])
