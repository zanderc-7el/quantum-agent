"""Controller-only lifecycle fixtures: no network, devices or accepted sources."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.common import APIError,digest
from backend.workspace import device_key
from tests.test_controller import make_fixture
from tests.test_science_flux_commanded import route


class CommandedController(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.c,self.fake=make_fixture(Path(self.tmp.name));self.addCleanup(self.c.close)
        self.root=Path(__file__).resolve().parents[1]

    def test_published_route_reaches_catalog_and_creation_without_design_identity(self):
        base=json.loads((self.root/'adapters/arclight_profile.json').read_text(encoding='utf8'))
        r=route();r.update(resourceId='Q11',deviceKey=device_key(base))
        rf={k:base['instrument'][k] for k in ('xyPhysical','readoutPhysical','adcPhysical')}
        chips=self.c.workspace.chips
        dep=chips.save_deployment({'operationId':'save-route','name':'fixture declared bias','instrument':base['instrument'],
            'routes':{'Q11':rf},'wiringConfirmed':True,'biasRoutes':{'Q11':r}})
        published=chips.publish_deployment(dep['id'],{'operationId':'publish-route','expectedRevision':1})
        binding={'profile':base,'resources':[{'id':'Q11','label':'fixture observed','physicalId':None}], 'source':{}}
        manifest=self.c.root/'adapters/science_flux/source_manifest.json';manifest.parent.mkdir(parents=True);manifest.write_text('[]')
        origin_manifest=self.c.root/base['sourceManifest'];origin_manifest.parent.mkdir(parents=True,exist_ok=True);origin_manifest.write_text('[]')
        with patch.object(self.c,'_observed_science_binding',return_value=binding),patch('adapters.science_flux_adapter.sha',return_value='fixture-runner'),patch('adapters.science_flux_adapter.verify_sources',return_value={}):
            catalog=self.c.science_catalog(source_workflow_id='fixture-source',resource_id='Q11',execution_profile='science-flux',bias_deployment_version_id=published['id'])
            self.assertEqual(catalog['configurationBinding']['biasDeploymentVersionId'],published['id'])
            self.assertTrue(all(m['capability']=='available' for m in catalog['manualMethods']))
            wf=self.c.create({'name':'declared voltage fixture','mode':'real','policy':'strict','executionProfile':'science-flux',
                'sourceWorkflowId':'fixture-source','biasDeploymentVersionId':published['id'],'resourceIds':['Q11'],
                'manualProcedureId':'ro_flux_scan','manualParameters':{'bias_values_v':[.2],'readout_frequencies_hz':[7e9+i*1e6 for i in range(5)]},
                'manualScienceReferences':[{'role':'cavity_seed','recordId':'fixture-not-accepted'}]})
        self.assertEqual(wf['gatesVersion'],'science-flux-commanded-return-gates/1')
        self.assertNotIn('chipVersionId',wf);self.assertNotIn('deploymentVersionId',wf)
        self.assertEqual(wf['configurationBinding']['profile']['biasRoutes'],{'Q11':r})
        self.assertEqual(wf['status'],'draft');self.assertEqual(self.fake.calls,[])
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],0)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],0)

    def test_server_generates_operation_authorization_before_consumer_digest_and_rejects_client_proof(self):
        from tests.test_flux_controller import FluxControllerContracts
        helper=FluxControllerContracts();helper.c,helper.fake=self.c,self.fake
        body=helper.fixture();r=route();r.update(resourceId='Q11')
        self.fake.profile['biasRoutes']={'Q11':r};self.c.adapter_profile_hash=self.c._adapter_fingerprint()
        with self.assertRaises(APIError) as error:self.c.create({**body,'biasCommandAuthorization':{'accepted':True}})
        self.assertEqual(error.exception.code,'SCIENCE_CLIENT_PROOF_FORBIDDEN')
        wf=self.c.create(body);node=wf['nodes'][0]
        request={'operationId':'fixture-plan','resourceId':'Q11','procedureId':'ro_flux_scan','parameters':{}}
        consumers=[]
        def resolve(rt,**kwargs):
            consumers.append(deepcopy(kwargs['consumer']));return {'closureSha256':'fixture'}
        with patch('backend.science_dependencies.ScienceDependencyResolver.resolve',side_effect=resolve),self.c.store.connect() as rt:
            self.c._science_request_dependencies(rt,wf,node,request)
            self.assertEqual(consumers[0]['biasCommandAuthorization']['operationId'],'fixture-plan')
            self.assertEqual(consumers[0]['biasCommandAuthorization']['routeSha256'],digest(r))
            request['biasCommandAuthorization']['endSettingV']=99
            with self.assertRaises(APIError):self.c._science_request_dependencies(rt,wf,node,request)

if __name__=='__main__':unittest.main()
