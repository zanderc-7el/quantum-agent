"""故障注入控制流演示；不是量子物理孪生，不产生实测结论。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .runtime import Budget, ExecutionRequest, Runtime, StepSpec, candidate_hash


class ScenarioBackend:
    """仅在内存中模拟参数和已完成回执；新实例无法证明旧副作用。"""
    data_kind = "scenario"

    def __init__(self, faults: dict[str, Any] | None = None):
        self.faults = dict(faults or {})
        self.receipts = {}
        self.calls = {}
        self.actual_values = {}
        self._steps = {}
        self._lost = set()

    def capabilities(self, step):
        self._steps[step.id] = step
        result = {"schema_version": 1, "data_kind": self.data_kind,
                  "operations": ["measure", "apply_trial", "verify", "rollback", "reconcile"],
                  "readback": True, "rollback": True, "reconcile": True,
                  "acquisition_counts": {"measure": 1, "verify": 1}}
        if self.faults.get("unknown_capability"):
            result.pop("readback")
        return result

    def _receipt(self, request, kind, build):
        key = request.operation_id
        if key in self.receipts:
            return json.loads(json.dumps(self.receipts[key]))
        self.calls[kind] = self.calls.get(kind, 0) + 1
        result = build()
        self.receipts[key] = result
        if self.faults.get("lose_receipt") == kind and key not in self._lost:
            self._lost.add(key)
            raise ConnectionError("故障注入：副作用完成后的回执丢失")
        return json.loads(json.dumps(result))

    def _evidence(self, request, kind):
        return {"evidence_id": request.operation_id + "/evidence", "acquisition_id": request.operation_id + "/acquisition", "data_kind": "scenario", "parameter_versions": request.parameter_versions, "shots": request.shots, "acquisition_count": 1, "total_shots": request.shots, "counts": {"0": request.shots}, "metrics": {"quality": 1.0, "residual": 0.0}, "stderr": {"quality": 0.0}, "note": "故障注入控制流演示；非物理仿真、非实测"}

    def measure(self, request):
        def build():
            result = self._evidence(request, "measure")
            step = self._steps[request.step_id]
            result["candidate"] = {name: float(request.parameter_values.get(name) or 0) + 1 for name in step.writes}
            if self.faults.get("missing_evidence"):
                result.pop("acquisition_id")
            if self.faults.get("nan"):
                result["metrics"]["quality"] = float("nan")
            if self.faults.get("bad_counts"):
                result["counts"] = {"0": request.shots + 1}
            if self.faults.get("stale_evidence"):
                result["parameter_versions"] = {**request.parameter_versions, "unrequested": 99}
            return result
        return self._receipt(request, "measure", build)

    def apply_trial(self, request, candidate):
        def build():
            self.actual_values.update(candidate)
            return {"status": "applied", "actual_values": dict(candidate)}
        return self._receipt(request, "apply_trial", build)

    def verify(self, request, candidate):
        def build():
            result = self._evidence(request, "verify")
            failed = self.faults.get("fail_verify") or (self.faults.get("fail_verify_once") and request.attempt == 1)
            result.update(passed=not bool(failed), candidate_hash=candidate_hash(candidate))
            if self.faults.get("low_metric"):
                result["metrics"]["quality"] = 0.1
            if self.faults.get("missing_metric"):
                result["metrics"].pop("quality")
            if self.faults.get("reused_evidence"):
                base = request.operation_id.rsplit("/", 1)[0] + "/measure"
                result["evidence_id"] = base + "/evidence"
                result["acquisition_id"] = base + "/acquisition"
            return result
        return self._receipt(request, "verify", build)

    def rollback(self, request, snapshot):
        def build():
            self.actual_values.update(snapshot)
            if self.faults.get("bad_rollback"):
                return {"status": "unknown", "actual_values": {}}
            return {"status": "restored", "actual_values": dict(snapshot)}
        return self._receipt(request, "rollback", build)

    def reconcile(self, operation_id):
        self.calls["reconcile"] = self.calls.get("reconcile", 0) + 1
        if self.faults.get("unknown_reconcile") or operation_id not in self.receipts:
            return {"status": "unknown"}
        return {"status": "completed", "result": json.loads(json.dumps(self.receipts[operation_id]))}


def demo_steps():
    budget = Budget(shots=16, max_attempts=2)
    criteria = {"quality": {"min": 0.9, "max": 1.0, "max_stderr": 0.1}}
    return [StepSpec("frequency", writes=("frequency_hz",), resources=("q0",), budget=budget, criteria=criteria),
            StepSpec("rabi", depends_on=("frequency",), reads=("frequency_hz",), writes=("pi_amplitude",), resources=("q0",), budget=budget, criteria=criteria)]


def run_demo(directory: str | Path) -> dict:
    """运行六种控制流情形；目录必须为空，避免将旧演示结果当成新结果。"""
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("演示输出目录必须为空")
    directory.mkdir(parents=True, exist_ok=True)
    summary = {"data_kind": "scenario", "note": "仅演示控制流，非量子物理孪生、非真机验收", "cases": {}}
    for name, faults in (("success", {}), ("remeasure", {"fail_verify_once": True}), ("rollback", {"fail_verify": True}), ("restart", {"lose_receipt": "apply_trial"})):
        runtime = Runtime(directory / (name + ".sqlite"), demo_steps()[:1])
        backend = ScenarioBackend(faults)
        result = runtime.run_step("frequency", backend, name)
        if name == "restart":
            first_state = result["state"]
            runtime.close()
            runtime = Runtime(directory / (name + ".sqlite"))
            result = runtime.resume(result["run_id"], backend)
            result["before_restart"] = first_state
        summary["cases"][name] = {"state": result["state"], "attempt": result["attempt"], "calls": backend.calls, "used_shots": result["used_shots"]}
        runtime.close()
    runtime = Runtime(directory / "idempotence.sqlite", demo_steps())
    backend = ScenarioBackend()
    first = runtime.run_workflow(backend, "workflow")
    before = dict(backend.calls)
    replay = runtime.run_workflow(backend, "workflow")
    summary["cases"]["idempotence"] = {"same_run_ids": [r["run_id"] for r in first] == [r["run_id"] for r in replay], "no_new_calls": before == backend.calls}
    runtime.set_parameter("frequency_hz", 2.0, "external-update")
    summary["cases"]["upstream_invalidation"] = {"frequency_valid": runtime.evidence("frequency") is not None, "rabi_valid": runtime.evidence("rabi") is not None}
    runtime.close()
    (directory / "scenario_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="控制流故障注入演示（非物理孪生）")
    parser.add_argument("--output", required=True)
    print(json.dumps(run_demo(parser.parse_args().output), ensure_ascii=False, indent=2))
