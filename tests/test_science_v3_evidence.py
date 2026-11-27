"""Receipt fixtures exercise authentication; never grant physical eligibility."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from adapters.science_runtime.policy import digest,sha
from adapters.science_v3.evidence import authenticate_dependency,result_content,check_pulse_dependencies
from adapters.science_v3.plans import POLICY,pulse_identity,build_rows

class EvidenceContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='v3-proof-fixture-');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.binding={'chipVersionId':'chip1','deploymentVersionId':'dep1','chipHash':'c'*64,'deploymentHash':'d'*64}
        self.route={'xyPhysical':[0,0],'readoutPhysical':[8,0],'adcPhysical':[0,0],
                    'xyLogical':[6,0],'readoutLogical':[14,0],'adcLogical':[15,0],'adcSampleRateHz':2.8e9}
        self.instrument={'id':'CeyearStationF8','host':'fixture.invalid','port':7110,'address':'TCPIP::fixture.invalid::7110::SOCKET'}
        self.consumer={'runId':'target','resourceId':'Q11','adapterProfileHash':'new','parameterVersions':{'readout_hz':0},'parameters':{'readout_hz':7e9}}
        self.dependency={'recordId':'fixture-source','sourceRoles':['f01'],'adapterProfileHash':'old-source','scope':{'workflowId':'source','resourceId':'Q11',
            'chipVersionId':'chip1','deploymentVersionId':'dep1'},'parameterVersions':{'readout_hz':2},'operations':[]}
        for stage in ('selection','verification'):
            operation='source-attempt/'+('measure' if stage=='selection' else 'verify')
            folder=self.root/'data/runs/arclight_operations'/hashlib.sha256(operation.encode()).hexdigest();folder.mkdir(parents=True)
            raw=folder/'raw_scan.npz';raw.write_bytes(('EXPLICIT TEST FIXTURE '+stage).encode())
            artifact={'id':stage,'name':'raw_scan.npz','kind':'raw','sha256':sha(raw),'localPath':str(raw)}
            req={'runId':'source','resourceId':'Q11','operationId':operation,'parameters':{'readout_hz':7e9},'adapterProfileHash':'old-source'}
            result={'dataKind':'real','acquiredAt':'TEST FIXTURE TIME','artifacts':[artifact],'analysisQuality':{'accepted':True},
                    'provenance':{'freshAcquisition':True,'nativeHalfPolicy':'f8_validated_short_half_dc_buff2ns_v1'}}
            documents={'adapter_intent.json':{'request':req},'adapter_receipt.json':{'status':'completed','result':result},
                'completion.json':{'state':'completed','shutdown_ok':True},
                'plan.json':{'operationId':operation,'configurationBinding':self.binding,'routing':self.route,
                             'hardwareLocks':['fixture-lock1','fixture-lock2'],'instrument':self.instrument}}
            for name,doc in documents.items():(folder/name).write_text(json.dumps(doc),encoding='utf-8')
            self.dependency['operations'].append({'stage':stage,'operationId':operation,'adapterRequestSha256':digest(req),
                'resultContentSha256':digest(result_content(result)),'rawArtifacts':[{'id':stage,'sha256':sha(raw)}]})
    def authenticate(self,**changes):
        return authenticate_dependency(self.root,self.consumer,self.dependency,expected_route=changes.get('route',self.route),
            expected_binding=changes.get('binding',self.binding),expected_locks=['fixture-lock1','fixture-lock2'],expected_instrument=self.instrument)
    def test_cross_workflow_uses_source_identity_and_separate_revision_namespace(self):
        result=self.authenticate()
        self.assertTrue(result['crossWorkflow']);self.assertEqual(result['sourceWorkflowId'],'source');self.assertEqual(result['consumerWorkflowId'],'target')
        self.assertEqual(result['sourceParameterVersions']['readout_hz'],2)
        self.assertEqual(result['consumerParameterVersions']['readout_hz'],0)
    def test_cross_deployment_route_changed_raw_and_nonseed_policy_reject(self):
        with self.assertRaises(ValueError):self.authenticate(binding={**self.binding,'deploymentHash':'changed'})
        with self.assertRaises(ValueError):self.authenticate(route={**self.route,'xyPhysical':[1,0]})
        self.dependency['sourceRoles']=['half01']
        with self.assertRaises(ValueError):self.authenticate()
        self.dependency['sourceRoles']=['f01'];operation=self.dependency['operations'][0]['operationId']
        folder=self.root/'data/runs/arclight_operations'/hashlib.sha256(operation.encode()).hexdigest();(folder/'raw_scan.npz').write_bytes(b'tampered')
        with self.assertRaises(ValueError):self.authenticate()
    def test_result_projection_matches_controller_contract(self):
        from backend.science_dependencies import result_content as controller_projection
        value={'dataKind':'real','metrics':{},'comparison':{'off':{'n0':100}},'classifierProtocol':{'heldOutCaptures':[2,3]},
               'artifacts':[{'id':'a','name':'raw','kind':'raw','sha256':'a'*64,'url':'/ignored','localPath':'ignored'}]}
        self.assertEqual(result_content(value),controller_projection(value))
        self.assertIn('comparison',result_content(value))
        self.assertIn('classifierProtocol',result_content(value))
    def test_legacy_observed_lineage_never_claims_design_identity(self):
        self.dependency['scope']={'workflowId':'source','resourceId':'Q11'}
        binding={'schema':'qcal-observed-resource/1','kind':'observed_resource','resourceId':'Q11',
            'sourceWorkflowId':'source','sourceAdapterId':'legacy','sourceAdapterProfileHash':'old-source',
            'sourceProfileSnapshotSha256':'p'*64,'routeSha256':digest(self.route),'deviceKey':'fixture-device',
            'physicalDesignId':None,'identityClaim':'measured_resource_not_design_identity'}
        binding['sha256']=digest(binding)
        with self.assertRaises(ValueError):self.authenticate()
        result=authenticate_dependency(self.root,self.consumer,self.dependency,expected_route=self.route,expected_observed=binding)
        self.assertTrue(result['crossWorkflow'])
        changed={**binding,'physicalDesignId':'Q11'};changed['sha256']=digest({k:v for k,v in changed.items() if k!='sha256'})
        with self.assertRaises(ValueError):authenticate_dependency(self.root,self.consumer,self.dependency,expected_route=self.route,expected_observed=changed)
        self.dependency['adapterProfileHash']='forged'
        with self.assertRaises(ValueError):authenticate_dependency(self.root,self.consumer,self.dependency,expected_route=self.route,expected_observed=binding)

class PulseIdentityContracts(unittest.TestCase):
    def test_full_half_identity_and_hidden_drag_change(self):
        import math
        p={'drive_hz':4.86e9,'drive_amp':.3,'drive_length_s':20e-9,'readout_hz':7.01e9,'readout_amp':.4,
            'readout_width_s':2e-6,'xy_delay_s':100e-9,'xy_filter_mhz':4700,'xy_attenuation_db':3.75,
            'pulse_shape':'CosPulse','pulse_length_s':20e-9,'shape_pi_amp':.3,'anharmonicity_hz':-200e6,'scan_axis_values':[-1,-.5,0,.5,1]}
        p,rows=build_rows('sq_drag_scan',p);pulse=deepcopy(rows[0]['gates'][0]['pulse']);pulse.update(lambda1=0.,anharmonicityHz=None)
        identity=pulse_identity(pulse,{k:p[k] for k in ('readout_hz','readout_amp','readout_width_s','xy_delay_s')})
        half={**identity,'rotationRad':math.pi/2,'amplitude':.15}
        deps={role:{'parameters':p,'targetPulseIdentity':{'identity':v,'sha256':digest(v)}} for role,v in [('pi01',identity),('half01',half)]}
        check_pulse_dependencies('sq_drag_scan',p,rows,deps)
        altered=deps['half01']['targetPulseIdentity'];altered['identity']['lambda1']=.7;altered['sha256']=digest(altered['identity'])
        with self.assertRaisesRegex(ValueError,'lambda1'):check_pulse_dependencies('sq_drag_scan',p,rows,deps)

if __name__=='__main__':unittest.main()
