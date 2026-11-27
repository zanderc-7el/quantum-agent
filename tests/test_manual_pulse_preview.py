"""Read-only draft preview, using actual ArcLight plans and no native/device load."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from backend.common import APIError,dumps
from tests.test_controller import make_fixture
from tests import test_manual_http as http_contract

ROOT=Path(__file__).resolve().parents[1]


class ManualPulsePreviewContracts(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.c,self.adapter=make_fixture(Path(temp.name));self.addCleanup(self.c.close)
        from adapters.arclight import ArcLightAdapter
        profile=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf-8'))
        self.native=ArcLightAdapter(ROOT,profile)
        self.adapter.profile['parameterBounds']=deepcopy(profile['parameterBounds'])
        self.adapter.build_plan=self.native.build_plan
        self.adapter.normalize_parameter_changes=self.native.normalize_parameter_changes
        self.adapter.capabilities=lambda:{'procedures':list(self.native.capabilities()['procedures'])}
        self.c.adapter_profile_hash=self.c._adapter_fingerprint()

    def create(self,method='sq_rabi_amplitude'):
        return self.c.create({'name':'pulse draft fixture','mode':'real','policy':'strict','resourceIds':['Q11'],
                             'manualProcedureId':method})

    def preview(self,wf,changes=None,**extra):
        return self.c.pulse_preview(wf['id'],wf['manualExperiment']['nodeId'],
            {'expectedRevision':wf['revision'],'parameters':changes or {},**extra})

    def rows(self):
        with self.c.store.connect() as rt:
            return {t:[tuple(r) for r in rt.db.execute('SELECT * FROM '+t)] for t in (
                'platform_workflows','platform_attempts','platform_effects','platform_leases','platform_commands','platform_decisions')}

    def test_actual_five_x_draft_timing_and_normalization_do_not_write_state(self):
        wf=self.create();before=self.rows()
        out=self.preview(wf,{'drive_length_s':80.3e-9,'readout_width_s':2e-6,'amp_start':.12,'amp_stop':.9,'points':11})
        self.assertEqual(out['status'],'ready')
        self.assertEqual(out['normalizedChanges']['drive_length_s'],80.5e-9)
        xy=[p for p in out['segments'] if p['channel']=='XY']
        self.assertEqual(len(xy),5)
        for p in xy:
            self.assertAlmostEqual(p['envelopeDurationS'],80.5e-9)
            self.assertAlmostEqual(p['durationS'],82.5e-9)
            self.assertEqual(p['amplitude'],.12)
            self.assertEqual(p['phaseRad'],0)
        self.assertAlmostEqual(xy[-1]['startS']+xy[-1]['durationS'],out['readoutStartS']-out['controlReadoutGapS'])
        self.assertAlmostEqual(sum(p['durationS'] for p in out['segments'] if p['channel']=='Read in'),2e-6)
        self.assertIsNone(out['referenceClockHz'])
        self.assertIsNone(out['zSetting'])
        self.assertEqual(self.rows(),before);self.assertEqual(self.adapter.calls,[])

    def test_preview_saved_parameters_match_existing_save_path_and_stale_preview_rejects(self):
        wf=self.create();changes={'drive_length_s':90.3e-9,'readout_width_s':2e-6,'points':11}
        before=self.preview(wf,changes)
        saved=self.c.set_parameters(wf['id'],wf['manualExperiment']['nodeId'],{
            'operationId':'save-draft','expectedRevision':wf['revision'],'parameters':changes})
        after=self.preview(saved)
        self.assertEqual(before['segments'],after['segments'])
        self.assertEqual(before['effectiveParameters'],after['effectiveParameters'])
        with self.assertRaises(APIError) as stale:self.preview(wf)
        self.assertEqual(stale.exception.code,'STALE_REVISION')
        self.assertEqual(self.adapter.calls,[])

    def test_scan_point_and_unsupported_methods_never_fabricate_a_sequence(self):
        wf=self.create('sq_rabi_time')
        out=self.preview(wf,{'start_s':20e-9,'stop_s':80e-9,'points':11},scanIndices=[10])
        self.assertEqual(out['axes'][0]['index'],10)
        self.assertAlmostEqual(next(p for p in out['segments'] if p['channel']=='XY')['envelopeDurationS'],80e-9)
        for changes in ({'drive_length_s':float('nan')},{'unknownPhase':1}):
            with self.assertRaises(APIError):self.preview(wf,changes)
        unsupported=self.create('sq_rb')
        result=self.preview(unsupported)
        self.assertEqual(result['status'],'unavailable');self.assertEqual(result['segments'],[])
        self.assertIn('未覆盖',result['reason']);self.assertEqual(self.adapter.calls,[])

    def test_saved_preview_and_existing_single_step_dispatch_use_the_same_parameters(self):
        # Explicit FakeAdapter only: use the existing controller command/effect
        # lifecycle to test the handoff, never the ArcLight execute method.
        from backend.manual_pulse_preview import summarize
        wf=self.create('ro_resonator_survey');changes={'readout_width_s':2e-6,'readout_hz':7.01e9,'points':11}
        preview=self.preview(wf,changes)
        saved=self.c.set_parameters(wf['id'],wf['manualExperiment']['nodeId'],{
            'operationId':'save-then-run','expectedRevision':wf['revision'],'parameters':changes})
        self.c.action(wf['id'],{'action':'run_step','nodeId':wf['manualExperiment']['nodeId'],
            'operationId':'existing-single-step','expectedRevision':saved['revision']})
        self.c.run_workflow(wf['id'])
        self.assertGreaterEqual(len(self.adapter.calls),1)
        request=self.adapter.calls[0]
        self.assertEqual(request['parameters']['readout_width_s'],preview['normalizedChanges']['readout_width_s'])
        actual_plan=self.native.build_plan(request)
        self.assertEqual(summarize(actual_plan)['segments'],preview['segments'])

    def test_readout_off_on_and_t1_wait_are_actual_variant_and_selected_axis(self):
        wf=self.create('sq_spectroscopy')
        on=self.preview(wf,{'points':11},scanIndices=[1,1])
        self.assertEqual(on['variant'],'on')
        self.assertGreater(next(s for s in on['segments'] if s['channel']=='XY')['amplitude'],0)
        off=self.preview(wf,{'points':11},scanIndices=[1,0])
        self.assertEqual(off['variant'],'off')
        self.assertEqual(next(s for s in off['segments'] if s['channel']=='XY')['amplitude'],0)
        t1=self.create('sq_t1')
        result=self.preview(t1,{'start_s':0.,'stop_s':1e-6,'points':11},scanIndices=[10])
        self.assertAlmostEqual(next(s for s in result['segments'] if s['channel']=='Wait')['durationS'],1e-6)

    def test_stale_readout_marker_is_reported_without_invalidating_it_or_writing_a_decision(self):
        wf=self.create()
        with self.c.store.transaction() as rt:
            wf=self.c.get(wf['id'],rt)
            wf['readoutRevalidations']={'Q11':{'id':'fixture-marker','state':'active',
                'values':{'readout_hz':1.},'parameterVersions':{'readout_hz':99}}}
            self.c.store.save(rt,wf,'fixture_marker',{'message':'temporary test only'})
        wf=self.c.get(wf['id']);before=self.rows()
        with self.assertRaises(APIError) as error:self.preview(wf)
        self.assertEqual(error.exception.code,'PULSE_PREVIEW_REFERENCE_CHANGED')
        self.assertEqual(self.rows(),before)


class ManualPulsePreviewHTTP(unittest.TestCase):
    setUp=http_contract.ManualHTTPContracts.setUp
    tearDown=http_contract.ManualHTTPContracts.tearDown
    request=http_contract.ManualHTTPContracts.request

    def test_http_preview_is_authenticated_revision_bound_and_has_no_persisted_effect(self):
        from adapters.arclight import ArcLightAdapter
        profile=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf-8'))
        real_plan=ArcLightAdapter(ROOT,profile)
        self.adapter.profile['parameterBounds']=deepcopy(profile['parameterBounds'])
        self.adapter.build_plan=real_plan.build_plan
        self.adapter.normalize_parameter_changes=real_plan.normalize_parameter_changes
        self.controller.adapter_profile_hash=self.controller._adapter_fingerprint()
        status,wf=self.request('/api/workflows',{'name':'HTTP timing fixture','mode':'real','policy':'strict',
            'resourceIds':['Q11'],'manualProcedureId':'sq_rabi_amplitude'})
        self.assertLess(status,300,wf)
        path=f"/api/workflows/{wf['id']}/nodes/{wf['manualExperiment']['nodeId']}/pulse-preview"
        def snapshot():
            with self.controller.store.connect() as rt:
                return {r[0]:[tuple(row) for row in rt.db.execute('SELECT * FROM '+r[0])]
                        for r in rt.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        before=snapshot();payload={'expectedRevision':wf['revision'],'parameters':{'drive_length_s':70e-9,'points':11}}
        status,result=self.request(path,payload)
        self.assertEqual(status,200,result);self.assertEqual(result['status'],'ready')
        self.assertFalse(result['dispatched']);self.assertFalse(result['hardwareConnected'])
        self.assertEqual(self.request(path,{**payload,'expectedRevision':wf['revision']+1})[0],409)
        self.assertEqual(self.request(path,{**payload,'action':'run_step'})[0],400)
        self.assertEqual(snapshot(),before);self.assertEqual(self.adapter.calls,[])


class ManualPulsePreviewNative(unittest.TestCase):
    def test_timing_matches_actual_native_sequence_in_guarded_subprocess(self):
        import subprocess
        profile=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf-8'))
        script='''import sys,types,unittest,tempfile,os,importlib.util
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root));sys.path.insert(0,str(root/'adapters/science_runtime'));sys.path.insert(0,str(root/'adapters/arclight_configurable_runtime'))
import compile_only_worker as guard
sys.addaudithook(guard.audit);sys.meta_path.insert(0,guard.NoDrivers())
native=root/'adapters/arclight_configurable_runtime/work/server/core'
for name,path in [('core',native),('core.compiler',native/'compiler'),('core.experiment',native/'experiment')]:guard.namespace(name,path)
inst=types.ModuleType('core.inst');inst.get_hardware=guard.deny;sys.modules['core.inst']=inst
spec=importlib.util.spec_from_file_location('core.inst.utils',native/'inst/utils.py')
utility=importlib.util.module_from_spec(spec);sys.modules[spec.name]=utility;spec.loader.exec_module(utility)
base=types.ModuleType('core.inst.instrument_base');base.InstrumentBase=type('ForbiddenInstrument',(),{'__init__':guard.deny});sys.modules[base.__name__]=base
from tests.test_manual_pulse_preview import native_parity
with tempfile.TemporaryDirectory(prefix='timing-native-') as directory:
    previous=os.getcwd()
    try:os.chdir(directory);native_parity()
    finally:os.chdir(previous)
print('native timing parity; zero device resolution')
'''
        result=subprocess.run([profile['python'],'-I','-B','-X','utf8','-c',script,str(ROOT)],capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('zero device resolution',result.stdout)


def native_parity():
    from core.experiment.experiment import Experiment
    from core.experiment.pulse import PulseType
    from validated_short_half_compat import install_validated_short_half
    fixture=ManualPulsePreviewContracts();fixture.setUp()
    try:
        plans=[];builder=fixture.native.build_plan
        def capture(request):
            plan=builder(request);plans.append(plan);return plan
        fixture.adapter.build_plan=capture
        cases=[('sq_rabi_amplitude',{'drive_length_s':80e-9,'points':11},[3]),
            ('sq_rabi_time',{'start_s':20e-9,'stop_s':80e-9,'points':11},[10]),
            ('sq_spectroscopy',{'points':11},[4,0]),('sq_spectroscopy',{'points':11},[4,1]),
            ('sq_t1',{'start_s':0.,'stop_s':1e-6,'points':11},[8]),
            ('sq_ramsey',{'start_s':0.,'stop_s':1e-6,'points':11},[8,8]),
            ('sq_echo',{'start_s':0.,'stop_s':1e-6,'points':11},None),
            ('sq_cpmg',{'start_s':0.,'stop_s':1e-6,'points':11},None),
            ('ro_resonator_survey',{'points':11},None),('ro_power_shift',{'points':11},None),
            ('ro_iq01',{},None)]
        for method,changes,indices in cases:
            if indices is None:
                initial=fixture.preview(fixture.create(method),changes)
                indices=[min(8,len(a['values'])-1) for a in initial['axes']]
            wf=fixture.create(method);preview=fixture.preview(wf,changes,scanIndices=indices)
            fixture.assertEqual(preview['status'],'ready',preview)
            job=plans[-1]['jobs'][0]
            exp=Experiment.load(deepcopy(job['config']),deepcopy(job['spec']),exp_id='timing-fixture')
            if job['spec']['meta'].get('pulse_compatibility'):install_validated_short_half(exp)
            axes={axis['binding']:axis['values'][axis['index']] for axis in preview['axes']}
            seq=exp.get_sequence(axes)
            native=[(times,pulse) for items in seq.pulse_list_per_qubit.values() for times,pulse in items
                    if pulse.pulse_type in (PulseType.XY,PulseType.RO)]
            shown=[s for s in preview['segments'] if s['channel'] in ('XY','Read in')]
            fixture.assertEqual(len(native),len(shown),method)
            for (times,pulse),segment in zip(native,shown):
                with fixture.subTest(method=method,segment=segment['id']):
                    start=times[0]+(seq.gate_start if pulse.pulse_type==PulseType.XY else 0)
                    fixture.assertAlmostEqual(start,segment['startS'],places=14)
                    fixture.assertAlmostEqual(times[1]-times[0],segment['durationS'],places=14)
                    fixture.assertAlmostEqual(pulse.A,segment['amplitude'],places=14)
                    fixture.assertAlmostEqual(pulse.phase,segment['phaseRad'],places=14)
            exp.close()
    finally:fixture.doCleanups()


if __name__=='__main__':unittest.main()
