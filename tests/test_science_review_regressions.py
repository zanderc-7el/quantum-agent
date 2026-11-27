"""Scientific review regressions: temporary SQLite and explicit fake acquisition only."""
import math
from pathlib import Path
import tempfile
import unittest

from backend.common import APIError
from backend.science_models import bloch, predistortion
from backend.science_service import ScienceService
from tests.test_controller import make_fixture


class ScientificReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='science-review-')
        self.addCleanup(self.temp.cleanup)
        self.c, self.adapter = make_fixture(Path(self.temp.name))
        self.addCleanup(self.c.close)
        self.service = ScienceService(self.c)

    def save(self, kind, inputs, operation, scope=None, reference=None):
        body = dict(kind=kind, input=inputs, operationId=operation, scope=scope or {})
        if reference:
            body['reference'] = reference
        return self.service.write('/records', body, 'POST')

    def acquired_series(self, procedure='sq_t1', native=False, unit='probability'):
        # Native I/O is replaced only by the existing explicit fake adapter.
        # The controller, registered evidence and science service are real.
        self.adapter.profile['resources']['Q11'].update(pi_pulses=12,readout_width_s=2e-6,xy_delay_s=0)
        self.c.adapter_profile_hash = self.c._adapter_fingerprint()
        original = self.adapter.execute
        def execute(request, emit, cancel):
            raw = original(request, emit, cancel)
            if native:
                raw['parameters'].update(pulse_compatibility='f8_validated_short_half_dc_buff2ns_v1',rotation_hz=1e6)
            raw['series'] = [dict(name='calibrated population '+str(i),x=[0,25e-9,50e-9],
                y=[1,.9995,.999] if i==0 else [0,.2,.4],xUnit='s',yUnit=unit,quantity='excited_probability') for i in (0,1)]
            return raw
        self.adapter.execute = execute
        wf = self.c.create(dict(name='source',mode='real',resourceIds=['Q11'],coverage='full' if procedure in ('sq_ramsey','sq_echo') else 'main'))
        self.c.action(wf['id'],dict(operationId='start',action='start',expectedRevision=wf['revision']))
        self.c.run_workflow(wf['id'])
        wf = self.c.get(wf['id'])
        node = next(n for n in wf['nodes'] if n['procedureId']==procedure)
        if not node.get('latestAttemptId'):
            # The fake fixture omits unrelated full-graph auxiliaries. Exercise
            # the selected supported acquisition directly, as controller tests do.
            self.c._execute_node(wf['id'],node['id'])
            wf=self.c.get(wf['id'])
            node=next(n for n in wf['nodes'] if n['procedureId']==procedure)
        record = self.save('measurement',{},'source',reference=dict(workflowId=wf['id'],attemptId=node['latestAttemptId']))
        return wf,node,record

    def test_native_cpmg_preserves_its_same_direction_analysis_pulse(self):
        _,_,record = self.acquired_series('sq_cpmg',native=True)
        sim = self.save('simulation',dict(sourceRecordId=record['id'],modelParameters=dict(sequence='cpmg',t1Ns=1e15,t2Ns=1e15)),'sim')
        self.assertAlmostEqual(sim['result']['series'][0]['values'][0],1.,places=9)
        self.assertAlmostEqual(sim['result']['series'][0]['values'][-1],1.,places=9)

    def test_analysis_phase_is_applied_after_echo_refocusing(self):
        output = bloch(dict(sequence='echo',durationNs=500,analysisPhaseHz=-1e6,t1Ns=1e15,t2Ns=1e15,points=3))
        self.assertAlmostEqual(output['series'][0]['values'][-1],1.,places=9)
        self.assertAlmostEqual(output['trajectory'][-1]['x'],1.,places=9)

    def test_native_echo_transverse_frame_keeps_intermediate_rotation(self):
        output=bloch(dict(sequence='echo',durationNs=1000,detuningHz=1e6,refocusAxis='y',
                          readoutSign=-1,t1Ns=1e15,t2Ns=1e15,timesNs=[0,500,1000]))
        # Native RX(pi/2) prepares -y. In the +x model frame, native X is Y;
        # at the midpoint z-rotation has taken +x to -x, then Y takes it to +x.
        self.assertAlmostEqual(output['trajectory'][1]['x'],1.,places=9)
        self.assertAlmostEqual(output['trajectory'][-1]['x'],-1.,places=9)
        self.assertAlmostEqual(output['series'][0]['values'][-1],0.,places=9)

    def test_native_echo_zero_wait_still_applies_its_refocusing_pulse(self):
        _,_,record=self.acquired_series('sq_echo',native=True)
        sim=self.save('simulation',dict(sourceRecordId=record['id'],modelParameters=dict(sequence='echo',t1Ns=1e15,t2Ns=1e15)),'sim')
        values=sim['result']['series'][0]['values']
        self.assertAlmostEqual(values[0],0.,places=9)
        self.assertAlmostEqual(values[-1],(1-math.cos(.1*math.pi))/2,places=9)

    def test_real_comparison_rejects_changed_drive_frequency(self):
        wf,node,record = self.acquired_series()
        with self.c.store.transaction() as rt:
            current=self.c.store.get(rt,wf['id'])
            current.update(status='paused',engineState='paused')
            self.c.store.save(rt,current,'fixture_pause',{})
        current=self.c.get(wf['id'])
        self.c.set_parameters(wf['id'],node['id'],dict(operationId='change-drive',expectedRevision=current['revision'],parameters=dict(drive_hz=4.5e9)))
        self.c._execute_node(wf['id'],node['id'])
        latest=next(n for n in self.c.get(wf['id'])['nodes'] if n['id']==node['id'])
        repeat=self.save('measurement',{},'repeat',reference=dict(workflowId=wf['id'],attemptId=latest['latestAttemptId']))
        with self.assertRaises(APIError) as caught:
            self.service.write('/compare',dict(leftRecordId=record['id'],rightRecordId=repeat['id']),'POST')
        self.assertEqual(caught.exception.code,'SCIENCE_CONDITIONS_INCOMPATIBLE')

    def test_acquired_t1_cannot_be_compared_to_an_x_gate_as_same_experiment(self):
        _,_,record = self.acquired_series()
        with self.assertRaises(APIError) as caught:
            self.save('simulation',dict(sourceRecordId=record['id'],modelParameters=dict(sequence='x')),'wrong-model')
        self.assertEqual(caught.exception.code,'SCIENCE_CONDITIONS_INCOMPATIBLE')

    def test_cpmg_simulation_freezes_acquired_pulse_count_and_identity(self):
        _,_,record = self.acquired_series('sq_cpmg')
        sim = self.save('simulation',dict(sourceRecordId=record['id'],modelParameters=dict(sequence='cpmg')),'sim')
        self.assertEqual(sim['result']['modelParameters'].get('piPulses'),12)
        self.assertEqual(sim['result']['experimentBinding']['reference'],record['reference'])
        self.assertEqual(sim['result']['experimentBinding']['parameters'],record['result']['parameters'])
        with self.assertRaises(APIError):
            self.save('simulation',dict(sourceRecordId=record['id'],modelParameters=dict(sequence='cpmg',piPulses=4)),'wrong-pulses')

    def test_model_comparison_cannot_switch_to_another_capture_series(self):
        _,_,record = self.acquired_series()
        sim = self.save('simulation',dict(sourceRecordId=record['id'],sourceSeriesId='trace-0',modelParameters=dict(sequence='t1')),'sim')
        with self.assertRaises(APIError):
            self.service.write('/compare',dict(leftRecordId=record['id'],leftSeriesId='trace-1',rightRecordId=sim['id']),'POST')
        output = self.service.write('/compare',dict(leftRecordId=record['id'],leftSeriesId='trace-0',rightRecordId=sim['id']),'POST')
        self.assertEqual(output['metrics']['comparedPoints'],3)

    def test_derived_model_inherits_source_currentness_after_invalidation(self):
        wf,node,record = self.acquired_series()
        sim = self.save('simulation',dict(sourceRecordId=record['id'],modelParameters=dict(sequence='t1')),'sim')
        with self.c.store.transaction() as rt:
            current = self.c.store.get(rt,wf['id'])
            self.c._invalidate(current,node['id'],include_target=True)
            self.c.store.save(rt,current,'test_upstream_invalidated',{})
        loaded = self.service.get('/records/'+sim['id'],{})
        self.assertFalse(loaded['evidenceCurrent'])
        self.assertTrue(loaded['evidenceIntact'])
        self.assertEqual(loaded['validity'],'historical')

    def test_admission_failure_cannot_restore_invalidated_evidence_currentness(self):
        wf,node,record = self.acquired_series()
        with self.c.store.transaction() as rt:
            current = self.c.store.get(rt,wf['id'])
            self.c._invalidate(current,node['id'],include_target=True)
            changed = next(n for n in current['nodes'] if n['id']==node['id'])
            changed['engineState']='plan_rejected'
            self.c.store.save(rt,current,'fixture_rejected_admission',{})
        loaded = self.service.get('/records/'+record['id'],{})
        self.assertIs(loaded['evidenceCurrent'],False)

    def test_unknown_observable_units_cannot_be_subtracted(self):
        _,_,record = self.acquired_series(unit='unknown')
        with self.assertRaises(APIError):
            self.service.write('/compare',dict(leftRecordId=record['id'],rightRecordId=record['id']),'POST')

    def test_multiqubit_attempt_cannot_be_relabelled_as_another_resource(self):
        self.c.history['resources'].append({'id':'Q18','label':'Q18','engineKey':'Q18'})
        self.adapter.profile['resources']['Q18'] = {**self.adapter.profile['resources']['Q11'], 'q':18}
        self.c.adapter_profile_hash = self.c._adapter_fingerprint()
        wf = self.c.create(dict(name='identity',mode='real',policy='strict',resourceIds=['Q11','Q18']))
        self.c.action(wf['id'], dict(action='run_step',nodeId=wf['nodes'][0]['id'],operationId='step',expectedRevision=wf['revision']))
        self.c.run_workflow(wf['id'])
        wf = self.c.get(wf['id'])
        ref = dict(workflowId=wf['id'],attemptId=wf['nodes'][0]['latestAttemptId'])
        with self.assertRaises(APIError) as caught:
            self.save('measurement', {}, 'wrong', dict(workflowId=wf['id'],resourceId='Q18'), ref)
        self.assertEqual(caught.exception.code, 'SCIENCE_SCOPE_MISMATCH')
        record = self.save('measurement', {}, 'derived', reference=ref)
        self.assertEqual(record['scope']['resourceId'], 'Q11')

    def test_predistortion_response_is_linear_not_wrapped_for_pure_delay(self):
        result = predistortion(dict(target=[1,0,0,0],impulseResponse=[0,1],regularization=1e-12,maxGain=5))
        traces = {s['id']:s['values'] for s in result['series']}
        self.assertAlmostEqual(traces['reconstructed'][0], 0., places=10)
        self.assertGreater(result['metrics']['rmse'], .1)
        for index in range(1, len(traces['reconstructed'])):
            self.assertAlmostEqual(traces['reconstructed'][index], traces['predistorted'][index-1], places=10)
        self.assertTrue(all(len(v)==len(result['axes']['x']['values']) for v in traces.values()))

    def test_explicit_latency_makes_a_pure_delay_target_causal(self):
        result = predistortion(dict(target=[1,0,0,0],impulseResponse=[0,1],regularization=1e-12,maxGain=5,latencySamples=1,sampleIntervalNs=2))
        traces = {s['id']:s['values'] for s in result['series']}
        self.assertAlmostEqual(traces['predistorted'][0], 1., places=9)
        self.assertAlmostEqual(traces['target'][0], 0., places=9)
        self.assertAlmostEqual(traces['target'][1], 1., places=9)
        self.assertLess(result['metrics']['rmse'], 1e-9)
        self.assertEqual(result['metrics']['latencyNs'], 2)


if __name__ == '__main__':
    unittest.main()
