"""独立检查持久化、故障恢复和禁止静默放行的不变量。"""
import tempfile
import os
import subprocess
import sys
import unittest
from pathlib import Path

from quantum_agents.runtime import Budget, ContractError, ResourceBusy, Runtime, StepSpec
from quantum_agents.scenario import ScenarioBackend, demo_steps, run_demo


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "runtime.sqlite"
        self.runtime = Runtime(self.path, demo_steps())

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def run_one(self, faults=None, operation_id="test"):
        backend = ScenarioBackend(faults)
        return self.runtime.run_step("frequency", backend, operation_id), backend

    def test_success_and_independent_evidence(self):
        result, backend = self.run_one()
        self.assertEqual(result["state"], "completed")
        self.assertNotEqual(result["measurement"]["acquisition_id"], result["verification"]["acquisition_id"])
        self.assertEqual(self.runtime.get_parameters()["frequency_hz"], {"value": 1.0, "revision": 1})
        self.assertEqual(result["used_shots"], 32)
        self.assertEqual(result["used_acquisitions"], 2)
        self.assertEqual(backend.calls, {"measure": 1, "apply_trial": 1, "verify": 1})

    def test_idempotent_replay_survives_restart(self):
        result, backend = self.run_one()
        calls = dict(backend.calls)
        self.runtime.close()
        self.runtime = Runtime(self.path)
        replay = self.runtime.run_step("frequency", backend, "test")
        self.assertEqual(replay, result)
        self.assertEqual(calls, backend.calls)
        self.assertEqual(self.runtime.get_parameters()["frequency_hz"]["revision"], 1)
        with self.assertRaises(ContractError):
            self.runtime.run_step("rabi", backend, "test")

    def test_retest_is_bounded_and_fresh(self):
        result, backend = self.run_one({"fail_verify_once": True})
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["attempt"], 2)
        self.assertEqual(result["used_shots"], 64)
        self.assertEqual(backend.calls["rollback"], 1)
        evidence = self.runtime.db.execute("SELECT acquisition_id FROM evidence").fetchall()
        self.assertEqual(len(evidence), 4)
        self.assertEqual(len(set(r[0] for r in evidence)), 4)

    def test_failed_verification_rolls_back_without_revision(self):
        result, backend = self.run_one({"fail_verify": True})
        self.assertEqual(result["state"], "rolled_back")
        self.assertEqual(result["attempt"], 2)
        self.assertEqual(backend.actual_values["frequency_hz"], None)
        self.assertNotIn("frequency_hz", self.runtime.get_parameters())
        self.assertIsNone(self.runtime.evidence("frequency"))

    def test_required_data_fail_closed(self):
        for fault in ("missing_evidence", "nan", "bad_counts", "stale_evidence", "reused_evidence", "missing_metric"):
            with self.subTest(fault=fault):
                runtime = Runtime(Path(self.temp.name) / (fault + ".sqlite"), demo_steps()[:1])
                result = runtime.run_step("frequency", ScenarioBackend({fault: True}), fault)
                self.assertNotEqual(result["state"], "completed")
                self.assertEqual(runtime.get_parameters(), {})
                self.assertIsNone(runtime.evidence("frequency"))
                runtime.close()

    def test_backend_passed_does_not_override_frozen_threshold(self):
        result, backend = self.run_one({"low_metric": True})
        self.assertEqual(result["state"], "rolled_back")
        self.assertEqual(result["attempt"], 2)
        self.assertEqual(backend.calls["verify"], 2)

    def test_unknown_capability_and_real_hardware_rejected_before_measure(self):
        for fault in ({"unknown_capability": True}, {}):
            backend = ScenarioBackend(fault)
            if not fault:
                backend.data_kind = "real"
            with self.assertRaises(ContractError):
                self.runtime.run_step("frequency", backend, "cap" + str(bool(fault)))
            self.assertEqual(backend.calls, {})

    def test_lost_receipt_holds_resource_and_reconciles_without_reapply(self):
        result, backend = self.run_one({"lose_receipt": "apply_trial"})
        self.assertEqual(result["state"], "needs_reconciliation")
        with self.assertRaises(ResourceBusy):
            self.runtime.run_step("frequency", ScenarioBackend(), "competing")
        self.runtime.close()
        self.runtime = Runtime(self.path)
        resumed = self.runtime.resume(result["run_id"], backend)
        self.assertEqual(resumed["state"], "completed")
        self.assertEqual(backend.calls["apply_trial"], 1)
        self.assertEqual(backend.calls["measure"], 1)
        self.assertEqual(backend.calls["reconcile"], 1)
        self.assertEqual(self.runtime.db.execute("SELECT count(*) FROM locks").fetchone()[0], 0)

    def test_reconciled_apply_with_new_revision_remains_isolated(self):
        result, backend = self.run_one({"lose_receipt": "apply_trial"})
        self.assertFalse(result["trial_active"])
        self.runtime.set_parameter("frequency_hz", 99., "external-during-outage")
        resumed = self.runtime.resume(result["run_id"], backend)
        self.assertEqual(resumed["state"], "needs_reconciliation")
        self.assertTrue(resumed["trial_active"])
        self.assertEqual(backend.calls["apply_trial"], 1)
        self.assertNotIn("rollback", backend.calls)
        self.assertEqual(self.runtime.db.execute("SELECT count(*) FROM locks").fetchone()[0], 1)

    def test_unknown_after_restart_never_reissues(self):
        result, backend = self.run_one({"lose_receipt": "apply_trial"})
        fresh_backend = ScenarioBackend()
        resumed = self.runtime.resume(result["run_id"], fresh_backend)
        self.assertEqual(resumed["state"], "needs_reconciliation")
        self.assertEqual(fresh_backend.calls, {"reconcile": 1})
        self.assertEqual(self.runtime.db.execute("SELECT count(*) FROM locks").fetchone()[0], 1)

    def test_upstream_change_invalidates_transitive_evidence(self):
        self.runtime.register(StepSpec("quality", depends_on=("rabi",), reads=("pi_amplitude",), criteria={"quality": {"min": .9}}))
        results = self.runtime.run_workflow(ScenarioBackend(), "flow")
        self.assertTrue(all(r["state"] == "completed" for r in results))
        self.runtime.set_parameter("frequency_hz", 8.0, "update")
        for name in ("frequency", "rabi", "quality"):
            self.assertIsNone(self.runtime.evidence(name))
        with self.assertRaises(ContractError):
            self.runtime.run_step("rabi", ScenarioBackend(), "stale")
        self.assertEqual(self.runtime.get_parameters()["pi_amplitude"]["revision"], 1)

    def test_concurrent_revision_change_rejects_commit_and_stale_restore(self):
        runtime = self.runtime
        class ChangingBackend(ScenarioBackend):
            def verify(self, request, candidate):
                result = super().verify(request, candidate)
                runtime.set_parameter("frequency_hz", 99.0, "concurrent")
                return result
        backend = ChangingBackend()
        result = runtime.run_step("frequency", backend, "conflict")
        self.assertEqual(result["state"], "needs_reconciliation")
        self.assertNotIn("rollback", backend.calls)
        self.assertEqual(runtime.get_parameters()["frequency_hz"]["value"], 99.0)
        self.assertIsNone(runtime.evidence("frequency"))

    def test_missing_profile_cannot_commit_readonly_step(self):
        self.runtime.register(StepSpec("readonly"))
        result = self.runtime.run_step("readonly", ScenarioBackend(), "readonly")
        self.assertEqual(result["state"], "rolled_back")
        self.assertIsNone(self.runtime.evidence("readonly"))

    def test_broken_restore_keeps_resource_isolated(self):
        result, backend = self.run_one({"fail_verify": True, "bad_rollback": True})
        self.assertEqual(result["state"], "needs_reconciliation")
        self.assertEqual(backend.calls["verify"], 1)
        with self.assertRaises(ResourceBusy):
            self.runtime.run_step("frequency", ScenarioBackend(), "conflicting")

    def test_scan_budget_prevents_overspend_before_call(self):
        class ScanBackend(ScenarioBackend):
            def capabilities(self, step):
                cap = super().capabilities(step)
                cap["acquisition_counts"] = {"measure": 3, "verify": 3}
                return cap
        backend = ScanBackend()
        spec = StepSpec("scan", writes=("x",), budget=Budget(shots=16,max_total_shots=32,max_acquisitions=2), criteria={"quality":{"min":.9}})
        self.runtime.register(spec)
        result = self.runtime.run_step("scan", backend, "budget")
        self.assertEqual(result["state"], "failed")
        self.assertEqual(backend.calls, {})
        self.assertEqual(result["used_shots"], 0)

    def test_commit_transaction_failure_leaves_no_partial_version_and_restores(self):
        original = self.runtime._event
        def fail_commit(run_id, event, payload=None):
            if event == "parameters_committed":
                raise RuntimeError("injected database commit failure")
            return original(run_id, event, payload)
        self.runtime._event = fail_commit
        result, backend = self.run_one()
        self.assertEqual(result["state"], "rolled_back")
        self.assertEqual(self.runtime.get_parameters(), {})
        self.assertIsNone(self.runtime.evidence("frequency"))
        self.assertEqual(backend.actual_values["frequency_hz"], None)

    def test_same_run_cannot_be_resumed_by_concurrent_live_executor(self):
        result, backend = self.run_one({"lose_receipt": "apply_trial"})
        other = Runtime(self.path)
        try:
            with self.runtime._execution_claim(result["run_id"]):
                with self.assertRaises(ResourceBusy):
                    other.resume(result["run_id"], backend)
        finally:
            other.close()

    def test_process_crash_retains_pending_operation_and_blocks_blind_replay(self):
        path = str(Path(self.temp.name) / "crash.sqlite")
        script = """
import os,sys
from quantum_agents.runtime import Runtime
from quantum_agents.scenario import ScenarioBackend,demo_steps
class Crash(ScenarioBackend):
    def apply_trial(self, request, candidate):
        os._exit(19)
r=Runtime(sys.argv[1], demo_steps()[:1])
r.run_step('frequency',Crash(),'crash')
"""
        process = subprocess.run([sys.executable, "-c", script, path], env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 19, process.stderr)
        runtime = Runtime(path)
        try:
            previous = runtime.list_runs()[0]
            self.assertEqual(previous["state"], "running")
            self.assertEqual(runtime.db.execute("SELECT count(*) FROM operations WHERE state='pending'").fetchone()[0], 1)
            backend = ScenarioBackend()
            current = runtime.resume(previous["run_id"], backend)
            self.assertEqual(current["state"], "needs_reconciliation")
            self.assertEqual(backend.calls, {"reconcile": 1})
            self.assertEqual(runtime.db.execute("SELECT count(*) FROM locks").fetchone()[0], 1)
        finally:
            runtime.close()

    def test_historical_measurement_cannot_be_reused_for_new_attempt(self):
        class ReusingBackend(ScenarioBackend):
            def measure(self, request):
                result = super().measure(request)
                if request.attempt == 1:
                    self.first = result
                return self.first
        backend = ReusingBackend({"fail_verify_once": True})
        result = self.runtime.run_step("frequency", backend, "reused-attempt")
        self.assertEqual(result["state"], "failed")
        self.assertIn("复用", result["error"])
        self.assertEqual(backend.calls["apply_trial"], 1)

    def test_parameter_operation_id_conflict(self):
        first = self.runtime.set_parameter("x", 1., "xop")
        self.assertEqual(first, self.runtime.set_parameter("x", 1., "xop"))
        with self.assertRaises(ContractError):
            self.runtime.set_parameter("x", 2., "xop")
        self.assertEqual(self.runtime.get_parameters()["x"]["revision"], 1)

    def test_demo_exercises_six_scenarios(self):
        result = run_demo(Path(self.temp.name) / "demo")
        self.assertEqual(result["data_kind"], "scenario")
        cases = result["cases"]
        self.assertEqual(cases["success"]["state"], "completed")
        self.assertEqual(cases["remeasure"]["attempt"], 2)
        self.assertEqual(cases["rollback"]["state"], "rolled_back")
        self.assertEqual(cases["restart"]["state"], "completed")
        self.assertTrue(cases["idempotence"]["no_new_calls"])
        self.assertFalse(cases["upstream_invalidation"]["rabi_valid"])


if __name__ == "__main__":
    unittest.main()
