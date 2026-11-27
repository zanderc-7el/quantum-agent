import http.client
import unittest
from tests.test_http import HTTPContracts


class StabilityReportHTTP(unittest.TestCase):
    setUp=HTTPContracts.setUp
    tearDown=HTTPContracts.tearDown
    request=HTTPContracts.request

    def test_json_report_is_get_only_and_has_no_scheduler_effect(self):
        before=self.controller.stability.list()
        status,_,body=self.request('GET','/api/stability/report?startDate=2026-11-26&endDate=2026-11-26')
        self.assertEqual(status,200,body)
        self.assertEqual(body['summary']['rounds'],0)
        self.assertEqual(self.controller.stability.list(),before)
        self.assertEqual(self.adapter.calls,[])

    def test_download_is_html_attachment_and_filters_are_validated(self):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        try:
            conn.request('GET','/api/stability/report?format=html&download=1&chipVersionId=chip-a')
            response=conn.getresponse();html=response.read().decode()
            self.assertEqual(response.status,200,html)
            self.assertIn('text/html',response.getheader('Content-Type'))
            self.assertIn('attachment;',response.getheader('Content-Disposition'))
            self.assertIn('暂无周期记录',html)
        finally:conn.close()
        status,_,body=self.request('GET','/api/stability/report?startDate=invalid')
        self.assertEqual((status,body['error']['code']),(400,'INVALID_STABILITY_REPORT_FILTER'))
        self.assertEqual(self.adapter.calls,[])


if __name__=='__main__':unittest.main()
