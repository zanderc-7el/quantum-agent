"""Time-based execution engine for calibration workflows.

M1 deliberately simulates time: no hardware is contacted. The executor is
separate from HTTP so it can be tested directly and swapped for a device
adapter later (M3). Only one process may run the loop in a multi-instance
deployment; enable it with ``QUANTAMIND_CALIBRATION_WORKER=true``.

Every tick is persisted through ``save_with_action`` so it bumps ``revision``
and receives an idempotency key. A conflicting revision means another writer
(or another worker) advanced the workflow first; the tick is skipped and the
next pass picks up the fresh snapshot.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from quantamind.calibration.adapters import (
    ANOMALY_FIDELITY,
    DeviceAdapter,
    SimulatedAdapter,
    UnknownAdapterError,
    adapter_for_workflow,
    fidelity_for,
    simulated_metrics,
)
from quantamind.calibration.errors import (
    CalibrationConflictError,
    CalibrationNotFoundError,
)
from quantamind.calibration.lease import ExecutionLease
from quantamind.calibration.models import (
    ExperimentNode,
    NodeStatus,
    Workflow,
)
from quantamind.calibration.repository import CalibrationRepository, action_request_hash
from quantamind.calibration.skills import evaluation_payload
from quantamind.calibration.state_machine import tick_workflow

SYSTEM_ACTOR = "system"

CONTROL_DRY_RUN = "dry_run"
CONTROL_LIVE = "live"
CONTROL_MODES = frozenset({CONTROL_DRY_RUN, CONTROL_LIVE})

logger = logging.getLogger("quantamind.calibration.executor")

# Backwards-compatible alias for code that imported the old private helper.
_fidelity = fidelity_for

__all__ = [
    "ANOMALY_FIDELITY",
    "CONTROL_DRY_RUN",
    "CONTROL_LIVE",
    "CalibrationRunner",
    "DeviceControlDisabledError",
    "TimeSimulationExecutor",
    "simulated_metrics",
]


class DeviceControlDisabledError(RuntimeError):
    """A real device adapter was asked to execute while control is dry-run.

    Failing closed is the point: a deployment that never opted into live
    control must not send pulses just because a workflow was started.
    """


class TimeSimulationExecutor:
    def __init__(
        self,
        repository: CalibrationRepository,
        *,
        seconds_per_tick: float = 1.0,
        adapter: DeviceAdapter | None = None,
        control_mode: str = CONTROL_DRY_RUN,
    ) -> None:
        if control_mode not in CONTROL_MODES:
            raise ValueError(
                f"control_mode must be one of {sorted(CONTROL_MODES)}, got {control_mode!r}"
            )
        self._repository = repository
        self._seconds_per_tick = seconds_per_tick
        self._control_mode = control_mode
        # An explicit adapter overrides device-profile lookup; used by tests and
        # by a deployment that pins one adapter for every workflow. Without one,
        # each workflow's device profile selects an adapter — and an unknown
        # adapter name raises instead of silently simulating.
        self._adapter_override = adapter

    @property
    def control_mode(self) -> str:
        return self._control_mode

    def step(self, *, now: str | None = None) -> int:
        """Advance every running workflow by one simulation tick.

        Returns the number of workflows that were persisted.
        """
        advanced = 0
        for workflow in self._repository.list(status="running", limit=500):
            measured: dict[tuple[str, int], dict] = {}

            def measure_once(current: Workflow, node: ExperimentNode, attempt: int) -> dict:
                metrics = self._measure(current, node, attempt)
                measured[(node.id, attempt)] = metrics
                return metrics

            try:
                updated = tick_workflow(
                    workflow,
                    self._seconds_per_tick,
                    now=now,
                    measure=measure_once,
                )
            except (UnknownAdapterError, DeviceControlDisabledError) as exc:
                # Leave the workflow untouched and keep serving other devices;
                # the operator sees the error instead of a simulated "success".
                logger.error(
                    "calibration adapter unavailable: %s", exc, extra={"workflowId": workflow.id}
                )
                continue
            if updated is workflow:
                continue
            try:
                self._repository.save_with_action(
                    updated,
                    expected_revision=workflow.revision,
                    request_id=f"auto-{uuid4().hex}",
                    request_hash=action_request_hash(
                        workflow.id, "tick", workflow.revision
                    ),
                    actor_id=SYSTEM_ACTOR,
                    action="tick",
                )
            except (CalibrationConflictError, CalibrationNotFoundError):
                # Another writer advanced or removed the workflow first; the
                # next pass re-reads the authoritative snapshot.
                continue
            self._record_results(workflow, updated, measured)
            advanced += 1
        return advanced

    def _adapter_for(self, workflow: Workflow) -> DeviceAdapter:
        return self._adapter_override or adapter_for_workflow(workflow)

    def _measure(
        self, workflow: Workflow, node: ExperimentNode, attempt: int
    ) -> dict:
        """The measurement handed to the state machine's acceptance check.

        A real adapter is only invoked when the deployment explicitly opted
        into live control. The default is dry-run, so a mis-started workflow
        cannot send hardware commands just because an adapter is registered.
        """
        adapter = self._adapter_for(workflow)
        if not adapter.simulated and self._control_mode != CONTROL_LIVE:
            raise DeviceControlDisabledError(
                f"设备适配器 {adapter.name!r} 为真实设备，但当前处于 dry-run "
                "模式；需显式设置 QUANTAMIND_DEVICE_CONTROL=live 才允许下发。"
            )
        return adapter.measure(workflow, node, attempt)

    def _record_results(
        self, before: Workflow, after: Workflow,
        measured: dict[tuple[str, int], dict],
    ) -> None:
        """Persist one result row for every node attempt this tick finalised.

        Only committed transitions are recorded: ``step`` calls this after
        ``save_with_action`` succeeded, so a conflicting tick leaves no trace.
        """
        for original, updated in zip(before.nodes, after.nodes):
            stage = None
            attempt = updated.attempt
            if (
                updated.status is NodeStatus.SUCCESS
                and original.status is not NodeStatus.SUCCESS
                and updated.attempt == original.attempt
            ):
                stage = "completed"
            elif (
                updated.status is NodeStatus.ATTENTION
                and updated.attempt == original.attempt
                and original.status is not NodeStatus.ATTENTION
            ):
                stage = "attention"
            elif (
                updated.status is NodeStatus.RUNNING
                and original.status in (NodeStatus.RUNNING, NodeStatus.ATTENTION)
                and original.attempt >= 1
                and updated.attempt == original.attempt + 1
            ):
                # Autopilot auto-retry: the failed first attempt is still worth
                # keeping, keyed to the attempt number that actually ran. A node
                # that was merely *launched* this tick is PENDING attempt 0 and
                # has produced no result yet.
                stage = "auto_retry"
                attempt = original.attempt
            if stage is None:
                continue
            # tick_workflow already measured this attempt for acceptance.
            # A second adapter call could send a second hardware experiment.
            metrics = dict(measured[(updated.id, attempt)])
            metrics["evaluation"] = evaluation_payload(
                after, updated.id, metrics, decision=stage
            )
            self._repository.put_node_result(
                workflow_id=after.id,
                node_id=updated.id,
                attempt=attempt,
                stage=stage,
                metrics=metrics,
            )


class CalibrationRunner:
    """Runs ``TimeSimulationExecutor.step`` on an asyncio loop.

    With a lease, every replica may run the loop; only the one that holds the
    database lease advances workflows. Without a lease the loop is
    single-instance by deployment convention (development and tests).
    """

    def __init__(
        self,
        executor: TimeSimulationExecutor,
        *,
        interval_seconds: float = 1.0,
        lease: ExecutionLease | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if lease is not None and lease.ttl_seconds <= interval_seconds:
            # The lease must outlive one full interval; otherwise every replica
            # would take turns on alternating ticks.
            raise ValueError("lease ttl must be greater than the tick interval")
        self._executor = executor
        self._interval_seconds = interval_seconds
        self._lease = lease

    async def run(self) -> None:
        try:
            while True:
                if self._lease is None or self._lease.acquire():
                    await asyncio.to_thread(self._executor.step)
                else:
                    logger.debug("calibration lease held by another replica")
                await asyncio.sleep(self._interval_seconds)
        finally:
            if self._lease is not None:
                self._lease.release()

    def start(self) -> asyncio.Task:
        return asyncio.create_task(self.run(), name="calibration-runner")
