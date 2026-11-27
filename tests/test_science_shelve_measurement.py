"""Display projection contracts; explicit fake transport, temporary SQLite only."""
from copy import deepcopy
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backend.common import APIError, dumps
from backend.science_service import ScienceService
from tests.test_controller import make_fixture


METHOD = 'sq_shelve_readout_compare'


def comparison():
    def condition(counts, fidelities):
        n0, n1 = map(sum, counts)
        return dict(counts=counts, n0=n0, n1=n1, wrongAs1=counts[0][1], wrongAs0=counts[1][0],
                    fidelity=1-.5*(counts[0][1]/n0+counts[1][0]/n1),
                    standardErrorBinomial=.01, perCaptureFidelity=fidelities,
                    classifier={'direction':[1.,0.], 'threshold':.5})
    return {'off':condition([[180,20],[10,190]], {'2':.92,'3':.93}),
            'on':condition([[196,4],[8,192]], {'2':.96,'3':.98})}


def fields():
    return dict(comparison=comparison(),
        classifierProtocol={'type':'separate condition LDA; equal priors; fixed training/validation capture split',
            'trainingCaptures':[0,1], 'validationCaptures':[2,3], 'logicalLabelsRetained':True},
        pulseIdentities={'probe12':{'identity':{'transition':'probe12','shape':'Gaussian','frequencyHz':4.6e9,
            'amplitude':.2,'durationS':20e-9,'sigmaS':5e-9,'phaseRad':0.,'rotationRad':3.141592653589793,
            'policy':'fixture-only','bufferS':2e-9,'xySampleRateHz':4e9,'readoutContextHash':'f'*64},'sha256':'a'*64}},
        metrics={'assignment_fidelity':.97,'assignment_gain_pp':4.5,'heldout_captures':2,
            'paired_capture_gain_stderr_pp':.5,'conditional_shift_snr':8.,'ground_disturbance_ratio':.1})


class ShelveMeasurementContracts(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.c,self.adapter=make_fixture(Path(temp.name));self.addCleanup(self.c.close)
        self.service=ScienceService(self.c)

    def acquired(self):
        from adapters.science_v3.plans import POLICY, BOUNDS
        self.adapter.profile['sciencePolicy']=POLICY
        self.adapter.profile['parameterBounds'].update(deepcopy(BOUNDS))
        self.adapter.capabilities=lambda:{'procedures':{METHOD:{'available':True}}}
        self.c.adapter_profile_hash=self.c._adapter_fingerprint()
        execute=self.adapter.execute
        def sample(request,emit,cancel):
            raw=execute(request,emit,cancel);raw.update(fields());raw['analysisQuality']={'accepted':True}
            if request['operationId'].endswith('/measure'):
                raw['comparison']['on']['counts']=[[194,6],[8,192]]
                raw['comparison']['on'].update(wrongAs1=6,fidelity=.965)
            return raw
        self.adapter.execute=sample
        wf=self.c.create({'name':'SHELVE display fixture only','mode':'real','policy':'strict','resourceIds':['Q11'],
            'manualProcedureId':METHOD,'manualScienceReferences':[{'role':r,'recordId':'fixture-'+r}
                for r in ('pi01','spectrum12','pi12','readout')]})
        with patch('backend.science_dependencies.ScienceDependencyResolver.resolve',return_value={
                'closureSha256':'fixture-only','assessment':{'executionEligible':True}}):
            self.c.action(wf['id'],{'action':'run_step','nodeId':wf['nodes'][0]['id'],
                'operationId':'fixture-step','expectedRevision':wf['revision']})
            self.c.run_workflow(wf['id'])
        wf=self.c.get(wf['id']);node=wf['nodes'][0]
        self.assertEqual(len(self.adapter.calls),2)
        return wf,node

    def test_service_saves_both_controller_acquisitions_and_loads_full_structured_counts(self):
        wf,node=self.acquired()
        body={'kind':'measurement','input':{},'scope':{},'operationId':'save-real-reference',
              'reference':{'workflowId':wf['id'],'attemptId':node['latestAttemptId']}}
        saved=self.service.write('/records',body,'POST')
        loaded=self.service.get('/records/'+saved['id'],{})
        result=loaded['result']
        self.assertEqual(result['comparison'],fields()['comparison'])
        self.assertEqual(result['classifierProtocol'],fields()['classifierProtocol'])
        self.assertEqual(result['pulseIdentities'],fields()['pulseIdentities'])
        self.assertEqual(result['shelveSelection']['comparison']['on']['counts'],[[194,6],[8,192]])
        self.assertEqual(result['shelveStage'],'verification')
        self.assertFalse(saved['physicalAccepted'])
        self.assertEqual(len(self.adapter.calls),2,'saving/rendering never starts another measurement')
        # Preserve authority: clients cannot replace native structured output.
        with self.assertRaises(APIError):self.service.write('/records',{**body,'operationId':'forged',
            'comparison':{'on':{'fidelity':1}}},'POST')
        with self.c.store.connect() as rt:
            artifact=rt.db.execute('SELECT path FROM platform_artifacts LIMIT 1').fetchone()[0]
        Path(artifact).write_bytes(b'tampered')
        self.assertFalse(self.service.get('/records/'+saved['id'],{})['evidenceIntact'])

    def project(self,**raw):
        return ScienceService._measurement({'raw':{'dataKind':'real','acquiredAt':'fixture-time',**raw},
            'attempt':{'procedureId':METHOD,'verificationOperationId':'verify'}})

    def test_old_record_missing_counts_stays_missing_and_never_invents_classifier_or_repeats(self):
        result=self.project(metrics={'assignment_fidelity':.97})
        self.assertNotIn('comparison',result)
        self.assertNotIn('classifierProtocol',result)
        self.assertNotIn('shelveSelection',result)
        self.assertEqual(result['metrics']['assignment_fidelity'],.97)

    def test_actual_native_numeric_output_survives_projection_without_changing_counts(self):
        # Numerical arrays only, explicitly synthetic. No native transport,
        # receipt, provider, or physical acceptance is constructed by this test.
        root=Path(__file__).resolve().parents[1]
        python=json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf-8'))['python']
        code='''import sys,json
sys.path.insert(0,sys.argv[1])
from tests.test_science_v3_analysis import AnalysisContracts,analyze
t=AnalysisContracts();t.setUp()
plan,i,q=t.data('sq_shelve_readout_compare',{},lambda r:float(r['logicalState'])*(.4 if r['condition']=='off' else .8))
print(json.dumps(analyze(plan,i,q),allow_nan=False))
'''
        result=subprocess.run([python,'-I','-B','-c',code,str(root)],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        native=json.loads(result.stdout);projected=self.project(**native)
        self.assertEqual(projected['comparison'],native['comparison'])
        self.assertEqual(projected['classifierProtocol'],native['classifierProtocol'])
        self.assertEqual(projected['comparison']['on']['n0'],200)

    def test_projection_is_bounded_and_does_not_promote_nested_or_nonfinite_client_shaped_fields(self):
        raw=fields();raw['comparison']['off']['unknown']='x'*100000
        raw['classifierProtocol']['unknown']={'accepted':True}
        raw['pulseIdentities']['probe12']['identity']['unknown']='x'*100000
        result=self.project(**raw)
        self.assertLess(len(dumps(result)),20000)
        self.assertNotIn('unknown',result['comparison']['off'])
        self.assertNotIn('unknown',result['classifierProtocol'])
        self.assertNotIn('unknown',result['pulseIdentities']['probe12']['identity'])
        for mutate in (lambda r:r['comparison']['off'].update(counts=[[0,float('nan')],[1,2]]),
                       lambda r:r['comparison']['off'].update(perCaptureFidelity={str(i):.9 for i in range(100)})):
            raw=fields();mutate(raw)
            self.assertNotIn('comparison',self.project(**raw))
        # This whitelist is procedure scoped, not enabled by an arbitrary field.
        source={'raw':{'acquiredAt':'fixture',**fields()},'attempt':{'procedureId':'sq_rabi_time'}}
        self.assertNotIn('comparison',ScienceService._measurement(source))


if __name__=='__main__':unittest.main()
