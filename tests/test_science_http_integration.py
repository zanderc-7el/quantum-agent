from tests.test_manual_http import ManualHTTPContracts
from unittest.mock import Mock


class ScienceHTTPIntegration(ManualHTTPContracts):
    def test_native_preview_route_keeps_compilation_separate_from_dispatch(self):
        self.server.native_preview_service = Mock()
        self.server.native_preview_service.preview.return_value = {'dataKind':'compiled','dispatched':False}
        payload = {'kind':'drag','scope':{'workflowId':'w','resourceId':'Q18'},'input':{'lambda1':0}}
        status, result = self.request('/api/native-preview', payload)
        self.assertEqual(status, 200)
        self.assertFalse(result['dispatched'])
        self.server.native_preview_service.preview.assert_called_once_with(payload)
        self.assertEqual(self.adapter.calls, [])

    def test_stability_overview_is_durable_and_does_not_acquire(self):
        status, result = self.request('/api/stability')
        self.assertEqual(status, 200)
        self.assertEqual(result, {'schedules': []})
        self.assertEqual(self.adapter.calls, [])

    def test_science_preview_returns_model_not_real_acquisition(self):
        status, result = self.request('/api/science/preview', {'kind':'pulse', 'input':{}})
        self.assertEqual(status, 200, result)
        self.assertFalse(result['dispatched'])
        self.assertNotEqual(result.get('dataKind'), 'real')
        self.assertEqual(self.adapter.calls, [])
