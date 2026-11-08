"""The device boundary for calibration execution.

The executor never talks to an instrument directly: it asks the adapter bound
to the workflow's device profile for a measurement. Today only the simulator
is registered. A real adapter (for example Arclight or Vendor32) must be added
here under an explicit name; an unregistered adapter name fails closed rather
than silently falling back to simulation, because a run that looks real but
never touched hardware is worse than a run that refuses to start.
"""

from __future__ import annotations

import hashlib

from quantamind.calibration.definitions import anomaly_node_id
from quantamind.calibration.devices import DeviceProfile, profile_for_workflow
from quantamind.calibration.models import ExperimentNode, Scenario, Workflow
from quantamind.calibration.simulated_traces import trace_for_node

ANOMALY_FIDELITY = 0.942  # the value the frontend shows for the demo anomaly


class UnknownAdapterError(RuntimeError):
    """Raised when a device profile names an adapter this build lacks."""


class DeviceAdapter:
    """Protocol-like base: a named, auditable measurement source."""

    name: str = "abstract"
    simulated: bool = True

    def measure(
        self, workflow: Workflow, node: ExperimentNode, attempt: int
    ) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


def _seed(*parts: object) -> float:
    """Deterministic 0..1 value so repeated runs produce comparable results."""
    digest = hashlib.sha256(":".join(str(part) for part in parts).encode()).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


def _qubit_labels(target: str) -> list[str]:
    separators = "–—-/,，"
    normalized = target
    for separator in separators:
        normalized = normalized.replace(separator, "-")
    return [part.strip() for part in normalized.split("-") if part.strip()]


def simulated_metrics(
    workflow: Workflow, node: ExperimentNode, attempt: int, *, fidelity: float
) -> dict:
    """A stand-in for a device adapter's measurement result."""
    parameters = {}
    for qubit in _qubit_labels(workflow.target):
        value = _seed(workflow.id, node.id, attempt, qubit)
        parameters[qubit] = {
            "f01Ghz": round(4.6 + 0.5 * value, 6),
            "readoutGhz": round(6.8 + 0.2 * value, 6),
            "t1Us": round(20 + 40 * value, 3),
            "t2Us": round(10 + 30 * value, 3),
        }
    return {
        "fidelity": round(fidelity, 4),
        "parameters": parameters,
        "simulated": True,
        "scenario": workflow.scenario.value,
    }


def fidelity_for(workflow: Workflow, node: ExperimentNode, attempt: int) -> float:
    if (
        workflow.scenario is Scenario.ANOMALY
        and node.id == anomaly_node_id(workflow.experiment.value)
        and attempt == 1
    ):
        return ANOMALY_FIDELITY
    return round(0.985 + 0.014 * _seed(workflow.id, node.id, attempt, "fidelity"), 4)


class SimulatedAdapter(DeviceAdapter):
    name = "simulator"
    simulated = True

    def measure(
        self, workflow: Workflow, node: ExperimentNode, attempt: int
    ) -> dict:
        metrics = simulated_metrics(
            workflow, node, attempt, fidelity=fidelity_for(workflow, node, attempt)
        )
        metrics["adapter"] = self.name
        if workflow.experiment.value == "single":
            trace = trace_for_node(node.id)
            if trace is not None:
                metrics["trace"] = trace
        return metrics


ADAPTERS: dict[str, DeviceAdapter] = {"simulator": SimulatedAdapter()}


def adapter_for_profile(profile: DeviceProfile) -> DeviceAdapter:
    adapter = ADAPTERS.get(profile.adapter)
    if adapter is None:
        raise UnknownAdapterError(
            f"设备 {profile.device_id} 的适配器 {profile.adapter!r} 未注册；"
            "拒绝回退到仿真。"
        )
    return adapter


def adapter_for_workflow(workflow: Workflow) -> DeviceAdapter:
    return adapter_for_profile(profile_for_workflow(workflow))
