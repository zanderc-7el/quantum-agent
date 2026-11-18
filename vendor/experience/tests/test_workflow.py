"""Actual control-plane dispatch tests; scenario evidence is never physics proof."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from quantum_agents.__main__ import main
from quantum_agents.advisor import catalog
from quantum_agents.runtime import Budget, ContractError, Runtime, StepSpec
from quantum_agents.scenario import ScenarioBackend
from quantum_agents.twin import twin_steps
from quantum_agents.workflow import FlowAgent, WorkflowSupervisor, ownership

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.runtime=Runtime(Path(self.tmp.name)/'state.sqlite',[
            StepSpec(s.id,s.depends_on,s.reads,s.writes,s.resources,Budget(shots=8),{'quality':{'min':.9}})
            for s in twin_steps((0,1))])
        self.addCleanup(self.runtime.close)
        self.runtime.set_parameter('readout.frequency_hz',6.5e9,'initialize')
        self.backend=ScenarioBackend();self.agent=FlowAgent('s01_readout_frequency')

    def test_public_cli_only_sixteen_roles_and_fifteen_plan_nodes(self):
        def call(args):
            out=io.StringIO();err=io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err): code=main(args)
            return code,json.loads(out.getvalue()) if out.getvalue() else None
        code,roles=call(['list']);self.assertEqual(code,0);self.assertEqual(len(roles),16)
        code,result=call(['plan']);self.assertEqual(code,0);self.assertEqual(len(result['plan']),15)
        self.assertEqual(call(['show','sq_rabi_time'])[0],2)
        self.assertEqual(call(['show','s03_rabi_amplitude'])[1]['main_procedure_id'],'sq_rabi_amplitude')
        context=Path(self.tmp.name)/'context.json';context.write_text('{}')
        code,result=call(['propose','s03_rabi_amplitude','--context',str(context)])
        self.assertEqual(code,0);self.assertEqual(result['proposal']['decision'],'escalate')
        self.assertTrue(set(result['proposal']['target_agents'])<=set(catalog()))

    def test_all_internal_procedures_have_one_owner(self):
        owners=ownership();procedures=[p for a in owners.values() for p in a['procedure_ids']]
        self.assertEqual(len(owners),15);self.assertEqual(len(procedures),44);self.assertEqual(len(set(procedures)),44)
        self.assertEqual(FlowAgent('s07_t2').main_procedure_id,'sq_cpmg')

    def test_unbound_main_and_cross_owner_dispatch_fail_before_acquisition(self):
        for owner in ('s03_rabi_amplitude','s07_t2','d06_cz_xeb'):
            with self.assertRaisesRegex(ContractError,'UNSUPPORTED_MAIN_METHOD'):
                FlowAgent(owner).run(self.runtime,self.backend,'unbound-'+owner)
        with self.assertRaisesRegex(ContractError,'PROCEDURE_NOT_OWNED'):
            self.agent.run(self.runtime,self.backend,'wrong-owner',procedure_id='sq_ramsey')
        self.assertEqual(self.backend.calls,{})

    def test_real_dispatch_replay_and_fresh_operation(self):
        first=self.agent.run(self.runtime,self.backend,'readout-1');before=dict(self.backend.calls)
        replay=self.agent.run(self.runtime,self.backend,'readout-1')
        self.assertEqual(first['runs'],replay['runs']);self.assertEqual(before,self.backend.calls)
        fresh=self.agent.run(self.runtime,self.backend,'readout-2')
        self.assertNotEqual(first['runs'][0]['run_id'],fresh['runs'][0]['run_id'])
        self.assertNotEqual(first['runs'][0]['verification']['acquisition_id'],fresh['runs'][0]['verification']['acquisition_id'])
        self.assertEqual(fresh['state'],'partial_completed');self.assertFalse(fresh['original_step_complete'])
        self.assertFalse(fresh['workflow_complete']);self.assertEqual(self.backend.calls['measure'],2)

    def test_operation_selection_frozen_and_stale_evidence_not_current_success(self):
        self.agent.run(self.runtime,self.backend,'op')
        with self.assertRaisesRegex(ContractError,'IDEMPOTENCY_CONFLICT'):
            FlowAgent('s02_qubit_frequency').run(self.runtime,self.backend,'op',step_ids=['sq_spectroscopy:q0'])
        before=dict(self.backend.calls)
        self.runtime.set_parameter('readout.frequency_hz',6.6e9,'external-change')
        replay=self.agent.run(self.runtime,self.backend,'op')
        self.assertEqual(replay['state'],'partial_evidence_stale');self.assertEqual(before,self.backend.calls)

    def test_missing_prerequisite_does_not_run_other_owner(self):
        with self.assertRaisesRegex(ContractError,'MISSING_PREREQUISITE_EVIDENCE'):
            FlowAgent('s06_t1').run(self.runtime,self.backend,'t1',step_ids=['sq_t1:q0'])
        self.assertEqual(self.backend.calls,{})
        self.assertEqual(self.runtime.list_runs(),[])

    def test_changed_runtime_scope_and_out_of_range_candidate_rejected(self):
        bad=Runtime(Path(self.tmp.name)/'bad.sqlite',[StepSpec('ro_resonator_survey',writes=('q0.unowned',),criteria={'quality':{'min':.9}})])
        try:
            with self.assertRaisesRegex(ContractError,'SCOPE_MISMATCH'):self.agent.run(bad,self.backend,'bad')
        finally:bad.close()
        self.runtime.set_parameter('readout.frequency_hz',0.,'bad-seed')
        result=self.agent.run(self.runtime,self.backend,'range')
        self.assertNotEqual(result['state'],'partial_completed')
        self.assertNotIn('apply_trial',self.backend.calls)
        reconcile=self.backend.reconcile
        self.backend.reconcile=lambda op: reconcile(op) if op in self.backend.receipts else {'status':'not_started'}
        resumed=WorkflowSupervisor().resume_run(self.runtime,self.backend,result['runs'][0]['run_id'])
        self.assertNotEqual(resumed['state'],'completed')
        self.assertNotIn('apply_trial',self.backend.calls)

    def test_eleven_procedures_only_produce_five_partial_owner_groups(self):
        specs=twin_steps((0,1))
        summary={'data_kind':'scenario','runs':[{'step_id':s.id,'run_id':'fixture-'+s.id,'state':'completed'} for s in specs],
                 'valid_evidence':{s.id:True for s in specs}}
        result=WorkflowSupervisor().group_report(summary)
        self.assertEqual(len(result['agents']),15);self.assertEqual(result['observed_owner_count'],5)
        self.assertEqual(result['physical_procedure_runs'],11);self.assertEqual(result['original_steps_completed'],0)
        self.assertFalse(result['overall_workflow_complete'])
        groups={a['agent_id']:a for a in result['agents']}
        for owner in ('s03_rabi_amplitude','s07_t2','d06_cz_xeb'):
            self.assertFalse(groups[owner]['main_procedure_executed']);self.assertFalse(groups[owner]['original_step_complete'])
