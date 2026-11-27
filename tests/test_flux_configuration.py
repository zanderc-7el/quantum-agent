"""Bias configuration is explicit, version-frozen and cannot inherit a route."""
from copy import deepcopy
import unittest
from backend.common import APIError
from tests import test_chip_catalog as fixtures


def route(resource='A'):
    return dict(schema='qcal-bias-route/1',resourceId=resource,deviceKey='f8-lf',lineId='flux-'+resource,
        board=0,channel=0,unit='V',polarity=1,measurementPlane='chip input',
        stationIdentity='fixture-station-idn',measurementInstrumentIdentity='fixture-meter-idn',
        minimumV=-.1,maximumV=.1,minimumMeasuredV=-1,maximumMeasuredV=1,maxStepV=.1,
        maxSlewVPerSecond=1,maxRampSeconds=2,settleSeconds=.01,stabilitySamples=2,
        readbackIntervalSeconds=.01,readTimeoutSeconds=.01,toleranceV=.001,
        maximumUncertaintyV=.00001,restoreTimeoutSeconds=2,restorePolicy='restore_initial_verified',
        preparedStaticDcEvidenceSha256='1'*64,confirmationEvidenceSha256='2'*64,
        providerId='not-installed',providerProtocolSha256='3'*64,
        offsetToOutput=dict(gain=1,offsetV=0,uncertaintyV=.00001,evidenceSha256='4'*64))


class FluxConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ChipCatalogTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.c=self.fixture.catalog();self.c.root=__import__('pathlib').Path(__file__).resolve().parents[1]
    def deployment(self,bias):
        return dict(operationId='dep',name='explicit deployment',instrument=self.fixture.base['instrument'],
            routes={'A':dict(xyPhysical=[1,1],readoutPhysical=[8,1],adcPhysical=[0,1])},
            wiringConfirmed=True,biasRoutes=bias)
    def binding(self,dep):
        chip=self.c.save_chip(dict(operationId='chip',name='chip',qubits=[self.fixture.qubit()]))
        cv=self.c.publish_chip(chip['id'],dict(operationId='cp',expectedRevision=1))
        dv=self.c.publish_deployment(dep['id'],dict(operationId='dp',expectedRevision=1))
        return self.c.resolve_binding(cv['id'],dv['id'],['A'])
    def test_incomplete_unknown_and_mismatched_routes_are_rejected_before_save(self):
        for index,bad in enumerate(({'resourceId':'A'}, {**route(),'unknown':1},route('OTHER'))):
            with self.subTest(route=bad),self.assertRaises(APIError):
                self.c.save_deployment({**self.deployment({'A':bad}),'operationId':'bad-'+str(index)})
    def test_selected_route_is_frozen_from_published_deployment(self):
        source=self.deployment({'A':route(),'B':route('B')});original=deepcopy(source)
        dep=self.c.save_deployment(source);binding=self.binding(dep)
        self.assertEqual(binding['profile'].get('biasRoutes'),{'A':route()})
        self.assertEqual(source,original)
        self.assertFalse(binding['profile']['enabled'])
        self.assertEqual(self.c.get_deployment(dep['id'])['biasRoutes'],original['biasRoutes'])
    def test_legacy_base_bias_route_is_never_inherited_without_deployment_field(self):
        self.c.base_profile['biasRoutes']={'A':route()}
        body=self.deployment({});body.pop('biasRoutes')
        binding=self.binding(self.c.save_deployment(body))
        self.assertNotIn('biasRoutes',binding['profile'])


if __name__=='__main__':unittest.main()
