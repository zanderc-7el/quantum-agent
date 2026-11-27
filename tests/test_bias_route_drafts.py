"""Wiring editor persists partial LF sheets and scopes confirmed data to a deployment."""
import unittest
from copy import deepcopy
from backend.common import APIError
from backend.bias_route_drafts import _context
from tests import test_chip_catalog
from tests.test_science_flux_commanded import route as sample_route


class WiringSheetContracts(unittest.TestCase):
    def setUp(self):
        fixture = test_chip_catalog.ChipCatalogTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.catalogue = fixture.catalog()
        self.dep = dict(name='离线验证机台', machine='test', instrument=fixture.base['instrument'],
                        routes={'A': dict(xyPhysical=[1,1],readoutPhysical=[8,1],adcPhysical=[0,1])},
                        wiringConfirmed=True, biasRouteDrafts={'A': {'fields': {}}})
        self.fields = self.dep['biasRouteDrafts']['A']['fields']

    def save(self, item, operation):
        return self.catalogue.save_deployment({**item, 'operationId':operation, 'expectedRevision':item.get('revision',0)})

    def fill(self):
        from backend.bias_route_drafts import NUMBER_FIELDS, NOTE_FIELDS
        route = sample_route()
        self.fields.update({k:str(route[k]) for k in NUMBER_FIELDS})
        self.fields.update({k:'测试夹具现场依据 '+k for k in NOTE_FIELDS})
        self.fields.update(lineId='fixture line',stationIdentity='fixture idn', moduleReplies='module success',
                           channelReplies='channel success', offsetReplies='offset success')
        self.dep['biasRouteDrafts']['A']['confirmation'] = _context(self.dep,'A',deepcopy(self.fields))

    def test_partial_draft_reopens_without_authorising_an_instrument(self):
        self.fields.update(board='0',channel='',startSettingV='')
        saved=self.save(self.dep,'partial')
        again=self.catalogue.get_deployment(saved['id'])
        self.assertEqual(again['biasRouteDrafts']['A']['fields'],self.fields)
        self.assertEqual(again['biasRoutes'],{})
        published=self.catalogue.publish_deployment(saved['id'],{'operationId':'publish-partial','expectedRevision':1})
        self.assertEqual(published['biasRoutes'],{})

    def test_complete_confirm_publish_and_revoke_on_changed_binding(self):
        self.fill()
        saved=self.save(self.dep,'positive')
        real=saved['biasRoutes']['A']
        self.assertEqual(real['schema'],'qcal-bias-command-route/1')
        self.assertEqual(real['endSettingV'],-.1)
        self.assertEqual(real['commandReplyContract']['offsetReplies'],['offset success'])
        frozen=self.catalogue.publish_deployment(saved['id'],{'operationId':'pub','expectedRevision':1})
        for key in ('physical','instrument','fields'):
            changed=deepcopy(saved)
            if key=='physical': changed['routes']['A']['xyPhysical']=[1,2]
            elif key=='instrument':changed['instrument']['port']+=1
            else:changed['biasRouteDrafts']['A']['fields']['endSettingV']='0'
            changed.pop('id')
            read=self.save(changed,key)
            self.assertEqual(read['biasRoutes'],{})
            self.assertNotIn('confirmation',read['biasRouteDrafts']['A'])
        self.assertEqual(self.catalogue.get_deployment(frozen['id'])['biasRoutes']['A'],real)

    def test_out_of_bounds_acceptance_does_not_save(self):
        self.fill()
        self.fields['endSettingV']='3'
        self.dep['biasRouteDrafts']['A']['confirmation']=_context(self.dep,'A',deepcopy(self.fields))
        with self.assertRaises(APIError): self.save(self.dep,'bad-range')

    def test_legacy_measured_route_cannot_be_silently_overwritten(self):
        from tests.test_flux_configuration import route
        self.dep['biasRoutes']={'A':route('A')}
        with self.assertRaises(APIError): self.save(self.dep,'protected')
