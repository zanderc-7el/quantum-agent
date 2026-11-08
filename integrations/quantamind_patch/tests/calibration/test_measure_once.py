"""One completed node attempt must invoke its device adapter once."""

from quantamind.calibration.adapters import DeviceAdapter, simulated_metrics
from quantamind.calibration.executor import TimeSimulationExecutor
from quantamind.calibration.repository import action_request_hash
from quantamind.calibration.state_machine import apply_action


class CountingAdapter(DeviceAdapter):
    name = "counting-simulator"
    simulated = True

    def __init__(self):
        self.calls = []

    def measure(self, workflow, node, attempt):
        self.calls.append((workflow.id, node.id, attempt))
        return simulated_metrics(workflow, node, attempt, fidelity=0.99)


def test_completed_attempt_is_measured_once(repo, make_workflow):
    workflow = make_workflow()
    repo.create(workflow)
    started = apply_action(workflow, "start")
    repo.save_with_action(
        started, expected_revision=workflow.revision,
        request_id="start-measure-once", request_hash=action_request_hash(
            workflow.id, "start", workflow.revision),
        actor_id="leader", action="start",
    )
    adapter = CountingAdapter()
    executor = TimeSimulationExecutor(repo, adapter=adapter)
    for _ in range(4):
        executor.step()
    assert adapter.calls == [(workflow.id, "ReadoutFreqDip", 1)]
    results = repo.list_node_results(workflow.id, "ReadoutFreqDip")
    assert len(results) == 1
