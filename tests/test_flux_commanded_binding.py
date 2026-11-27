from copy import deepcopy
import unittest
from backend.common import APIError
from backend.workspace import device_key
from tests.test_flux_configuration import FluxConfigurationTests
from tests.test_science_flux_commanded import route


class CommandedBinding(FluxConfigurationTests):
    def test_published_overlay_keeps_observed_identity_and_old_versions(self):
        from backend.flux_binding import published_bias_binding
        r=route();r.update(resourceId='A',deviceKey=device_key(self.fixture.base))
        dep=self.c.save_deployment(self.deployment({'A':r}));dv=self.c.publish_deployment(dep['id'],{'operationId':'pub','expectedRevision':1})
        profile=deepcopy(self.fixture.base);profile['routingByResource']={'A':dep['routes']['A']}
        original={'profile':profile,'resources':[{'id':'A','physicalId':None}], 'observedResourceBinding':{'sha256':'observed'}}
        got=published_bias_binding(self.c,original,dv['id'],'A')
        self.assertEqual(got['observedResourceBinding'],original['observedResourceBinding'])
        self.assertNotIn('deploymentVersionId',got);self.assertNotIn('chipVersionId',got)
        self.assertEqual(got['profile']['biasRoutes'],{'A':r});self.assertNotIn('biasRoutes',original['profile'])
        self.c.save_deployment({**dep,'operationId':'edit','expectedRevision':1,'biasRoutes':{}})
        self.assertEqual(published_bias_binding(self.c,original,dv['id'],'A'),got)
        for change in ('draft','rf','device','resource','unconfirmed'):
            with self.subTest(change=change):
                body=self.deployment({'A':r});body['operationId']='new-'+change
                if change=='rf':body['routes']['A']['xyPhysical']=[2,0]
                if change=='device':body['instrument']=deepcopy(body['instrument']);body['instrument'].update(host='other',address='TCPIP::other::'+str(body['instrument']['port'])+'::SOCKET')
                if change=='resource':body['biasRoutes']={'B':{**r,'resourceId':'B'}}
                if change=='unconfirmed':body['wiringConfirmed']=False
                saved=self.c.save_deployment(body)
                version=saved if change=='draft' else self.c.publish_deployment(saved['id'],{'operationId':'publish-'+change,'expectedRevision':1})
                with self.assertRaises(APIError):published_bias_binding(self.c,original,version['id'],'A')

if __name__=='__main__':unittest.main()
