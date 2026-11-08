"""S21 and F01 simulator traces stay attached to the authoritative result."""

import hashlib
import json

from quantamind.calibration.adapters import SimulatedAdapter


def test_single_qubit_s21_and_f01_include_verifiable_traces(make_workflow):
    workflow = make_workflow()
    adapter = SimulatedAdapter()
    readout = adapter.measure(workflow, workflow.nodes[0], 1)["trace"]
    spectrum = adapter.measure(workflow, workflow.nodes[1], 1)["trace"]
    assert (readout["recipeId"], readout["pointCount"]) == ("readout_resonator_scan", 41)
    assert (spectrum["recipeId"], spectrum["pointCount"]) == ("f01_spectroscopy", 101)
    for trace in (readout, spectrum):
        canonical = json.dumps(trace["points"], ensure_ascii=False, separators=(",", ":"))
        assert trace["sha256"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert trace["analysis"]["quality"] == "pass"
        assert trace["publishAllowed"] is False
    assert spectrum["analysis"]["sourceParameterRevision"] is None
