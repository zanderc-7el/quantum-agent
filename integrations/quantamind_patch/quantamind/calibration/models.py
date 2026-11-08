"""Pydantic models for the calibration workflow contract.

Field names are snake_case in Python and camelCase on the wire, matching the
TypeScript ``Workflow`` interface in ``quantum-agent/frontend/src/types.ts``.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

SINGLE_TARGET_RE = re.compile(r"^Q\d+$", re.IGNORECASE)
TWO_TARGET_RE = re.compile(r"^(Q\d+)\s*[-–,，/]\s*(Q\d+)$", re.IGNORECASE)


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    ATTENTION = "attention"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class NodeStatus(str, Enum):
    SUCCESS = "success"
    RUNNING = "running"
    PENDING = "pending"
    ATTENTION = "attention"
    ABANDONED = "abandoned"


class Policy(str, Enum):
    STRICT = "strict"
    THRESHOLD = "threshold"
    AUTOPILOT = "autopilot"


class Scenario(str, Enum):
    NORMAL = "normal"
    ANOMALY = "anomaly"


class Experiment(str, Enum):
    SINGLE = "single"
    TWO = "two"


class WorkflowType(str, Enum):
    ADAPTIVE = "adaptive"
    FIXED = "fixed"


class EventKind(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"


class WorkflowAction(str, Enum):
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    APPROVE = "approve"
    RETRY = "retry"
    ABORT = "abort"


class Event(CamelModel):
    time: str
    message: str
    kind: EventKind = EventKind.INFO
    # Internal ordering key. Excluded from the API payload; the frontend Event
    # shape never contained it and ``extra="forbid"`` would reject it.
    seq: int = Field(default=0, ge=0, exclude=True)


class ExperimentNode(CamelModel):
    id: str
    name: str
    label: str
    status: NodeStatus = NodeStatus.PENDING
    # Fractional while running: a node gains 25 points per simulated second.
    progress: float = Field(default=0.0, ge=0, le=100)
    attempt: int = Field(default=0, ge=0)
    qubits: str
    detail: str = ""


class Workflow(CamelModel):
    import_source_id: str | None = None
    experiment: Experiment
    id: str
    name: str
    machine: str
    qubits: int
    target: str
    type: WorkflowType
    status: WorkflowStatus
    progress: int = Field(default=0, ge=0, le=100)
    owner: str
    owner_id: str | None = None
    created_at: str
    policy: Policy
    scenario: Scenario
    elapsed: float = Field(default=0.0, ge=0)
    iterations: int = Field(default=0, ge=0)
    nodes: list[ExperimentNode]
    events: list[Event]
    approved: bool = False
    revision: int = Field(default=1, ge=1)
    run_id: str | None = None


class CreateWorkflowInput(CamelModel):
    experiment: Experiment | None = None
    # Explicit blueprint scope for new clients; legacy callers may omit it.
    # Version 1 maps one-to-one to the persisted experiment kind.
    blueprint_id: Experiment | None = None
    blueprint_version: int | None = Field(default=None, ge=1)
    name: str
    machine: str
    qubits: int
    target: str = ""
    type: WorkflowType
    policy: Policy
    scenario: Scenario


def canonical_qubit(qubit: str) -> str:
    """Normalize ``Q01`` to ``Q1`` and keep ``Q0`` valid."""
    digits = qubit[1:].lstrip("0") or "0"
    return f"Q{digits}"


def normalize_target(value: str, experiment: Experiment | str) -> str:
    """Normalize the target qubit string, mirroring the frontend rules.

    Single-qubit targets are ``Q12``. Two-qubit targets accept ``-``, ``–``,
    ``,``, ``，`` or ``/`` as separators and are stored with an en dash.
    """
    experiment_value = experiment.value if isinstance(experiment, Experiment) else experiment
    default = "Q12–Q13" if experiment_value == "two" else "Q12"
    text = (value or "").strip() or default

    if experiment_value == "single":
        if not SINGLE_TARGET_RE.match(text):
            raise ValueError("单比特目标须为一个量子比特编号，例如 Q12。")
        return canonical_qubit(text)

    match = TWO_TARGET_RE.match(text)
    if not match:
        raise ValueError(
            "双比特目标须为两个不同编号，例如 Q12–Q13，可用 -、–、逗号或 / 分隔。"
        )
    first = canonical_qubit(match.group(1))
    second = canonical_qubit(match.group(2))
    if first == second:
        raise ValueError("双比特实验需要两个不同的量子比特，不能重复选择同一个编号。")
    return f"{first}–{second}"
