"""Server-side blueprint scope checks for calibration creation."""

import pytest

from backend.quantamind_api.services.calibration_service import Actor, CalibrationService
from quantamind.calibration.errors import CalibrationValidationError
from quantamind.calibration.models import CreateWorkflowInput


def _payload(**changes):
    data = dict(name="Q12 calibration", machine="M7", qubits=1,
                experiment="single", target="Q12", type="adaptive",
                policy="threshold", scenario="normal", blueprintId="single",
                blueprintVersion=1)
    data.update(changes)
    return CreateWorkflowInput(**data)


def test_matching_blueprint_creates_workflow(repo):
    actor = Actor("leader", "Leader", "leader", frozenset({"calibration:create"}))
    workflow = CalibrationService(repo).create_workflow(actor, _payload())
    assert workflow.experiment.value == "single"


@pytest.mark.parametrize("changes", [
    {"blueprintId": "two"},
    {"blueprintVersion": 2},
])
def test_cross_scope_or_stale_blueprint_is_rejected(repo, changes):
    actor = Actor("leader", "Leader", "leader", frozenset({"calibration:create"}))
    with pytest.raises(CalibrationValidationError):
        CalibrationService(repo).create_workflow(actor, _payload(**changes))
    assert repo.count() == 0
