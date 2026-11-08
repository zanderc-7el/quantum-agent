"""Application service for the calibration workflow API.

Responsibilities kept here (and out of the pure domain and the repository):

* authorization: role permissions and per-workflow ownership,
* the rules the frontend cannot be trusted to enforce (operators cannot
  approve, operators always create ``threshold`` workflows),
* binding ``approve``/``retry`` to the exact node and attempt awaiting a
  decision, so a retried HTTP request cannot approve a later node,
* idempotent replay of a repeated ``requestId``.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from quantamind.calibration.definitions import MAX_WORKFLOWS
from quantamind.calibration.errors import (
    CalibrationConflictError,
    CalibrationForbiddenError,
    CalibrationIdempotencyConflictError,
    CalibrationNotFoundError,
    CalibrationStateConflictError,
    CalibrationValidationError,
)
from quantamind.calibration.models import (
    CreateWorkflowInput,
    NodeStatus,
    Policy,
    Workflow,
    WorkflowAction,
    WorkflowStatus,
)
from quantamind.calibration.repository import (
    CalibrationRepository,
    NodeResult,
    SaveResult,
    action_request_hash,
)
from quantamind.calibration import devices as device_registry
from quantamind.calibration import skills as skill_registry
from quantamind.calibration.adapters import UnknownAdapterError, adapter_for_workflow
from quantamind.calibration.executor import CONTROL_DRY_RUN, CONTROL_LIVE
from quantamind.calibration.state_machine import (
    apply_action,
    create_workflow,
    set_policy,
    to_wire,
)
from quantamind.calibration.workspace import (
    ImportResult,
    export_workspace,
    parse_workspace,
    plan_import,
)

CSV_COLUMNS = (
    "id",
    "name",
    "experiment",
    "machine",
    "qubits",
    "target",
    "type",
    "status",
    "progress",
    "policy",
    "scenario",
    "owner",
    "createdAt",
    "elapsed",
    "iterations",
    "importSourceId",
)

# Roles come from the shared auth service. Imported lazily inside
# ``Actor.from_user`` to keep this module importable without FastAPI wiring.


@dataclass(frozen=True)
class Actor:
    user_id: str
    display_name: str
    role_id: str
    permissions: frozenset[str] = frozenset()

    @classmethod
    def from_user(cls, user: dict) -> "Actor":
        from backend.quantamind_api.services.auth_service import ROLE_POLICIES

        policy = ROLE_POLICIES.get(user.get("role_id", ""), {})
        return cls(
            user_id=str(user.get("user_id", "")),
            display_name=str(user.get("display_name", "")),
            role_id=str(user.get("role_id", "")),
            permissions=frozenset(policy.get("permissions", [])),
        )

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def is_leader(self) -> bool:
        return self.can("calibration:approve")


class CalibrationService:
    def __init__(
        self,
        repository: CalibrationRepository,
        *,
        control_mode: str = CONTROL_DRY_RUN,
    ) -> None:
        if control_mode not in (CONTROL_DRY_RUN, CONTROL_LIVE):
            raise ValueError(
                f"control_mode must be {CONTROL_DRY_RUN!r} or {CONTROL_LIVE!r}"
            )
        self._repository = repository
        self._control_mode = control_mode

    @property
    def control_mode(self) -> str:
        return self._control_mode

    # ----------------------------------------------------------------- reads
    def list_workflows(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        machine: str | None = None,
        owner: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Workflow], int]:
        self._require(actor, "calibration:read")
        owner_id = (owner or None) if actor.is_leader else actor.user_id
        items = self._repository.list(
            owner_id=owner_id,
            status=status,
            machine=machine,
            limit=limit,
            offset=offset,
        )
        total = self._repository.count(
            owner_id=owner_id, status=status, machine=machine
        )
        return items, total

    def get_workflow(self, actor: Actor, workflow_id: str) -> Workflow:
        self._require(actor, "calibration:read")
        return self._load(actor, workflow_id)

    def list_events(
        self, actor: Actor, workflow_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[dict]:
        workflow = self.get_workflow(actor, workflow_id)
        return [
            {
                "seq": event.seq,
                "time": event.time,
                "message": event.message,
                "kind": event.kind.value,
            }
            for event in self._repository.list_events(
                workflow.id, after_seq=after_seq, limit=limit
            )
        ]

    def list_results(
        self,
        actor: Actor,
        workflow_id: str,
        *,
        node_id: str | None = None,
        attempt: int | None = None,
    ) -> list[dict]:
        workflow = self.get_workflow(actor, workflow_id)
        results = self._repository.list_node_results(
            workflow.id, node_id=node_id, attempt=attempt
        )
        return [_result_payload(result) for result in results]

    def list_parameters(
        self, actor: Actor, workflow_id: str, *, node_id: str | None = None
    ) -> list[dict]:
        """Latest parameter snapshot per node, newest attempt wins."""
        workflow = self.get_workflow(actor, workflow_id)
        latest: dict[str, NodeResult] = {}
        for result in self._repository.list_node_results(workflow.id, node_id=node_id):
            current = latest.get(result.node_id)
            if current is None or result.attempt >= current.attempt:
                latest[result.node_id] = result
        return [
            {
                "nodeId": result.node_id,
                "attempt": result.attempt,
                "stage": result.stage,
                "capturedAt": result.created_at,
                "fidelity": result.metrics.get("fidelity"),
                "parameters": result.metrics.get("parameters", {}),
            }
            for result in sorted(latest.values(), key=lambda item: item.node_id)
        ]

    # ------------------------------------------------------------ skills/audit
    def list_skills(self, actor: Actor) -> list[dict]:
        self._require(actor, "calibration:read")
        return [skill.to_wire() for skill in skill_registry.list_skills()]

    def get_skill(
        self, actor: Actor, skill_id: str, *, version: str | None = None
    ) -> dict:
        self._require(actor, "calibration:read")
        skill = skill_registry.get_skill(skill_id, version)
        if skill is None:
            raise CalibrationNotFoundError(f"{skill_id}@{version or 'latest'}")
        return skill.to_wire()

    def list_devices(self, actor: Actor) -> list[dict]:
        self._require(actor, "calibration:read")
        return [device.to_wire() for device in device_registry.list_devices()]

    def get_device(self, actor: Actor, device_id: str) -> dict:
        self._require(actor, "calibration:read")
        device = device_registry.get_device(device_id)
        if device is None:
            raise CalibrationNotFoundError(device_id)
        return device.to_wire()

    def workflow_skill(self, actor: Actor, workflow_id: str) -> dict:
        """The versioned Skill, device profile and autonomy a workflow uses."""
        workflow = self._load(actor, workflow_id)
        skill = skill_registry.skill_for_workflow(workflow)
        device = device_registry.profile_for_workflow(workflow)
        effective_steps = _effective_steps(device, skill)
        return {
            "workflowId": workflow.id,
            "revision": workflow.revision,
            "binding": {
                "skillId": skill.skill_id,
                "version": skill.version,
                "source": "builtin",
            },
            "device": device.to_wire(),
            "autonomy": skill_registry.autonomy_for_policy(workflow.policy).to_wire(),
            "skill": skill.to_wire(),
            "effectiveSteps": effective_steps,
            # Flat index so a client does not have to walk every step to find
            # the ideal value for the node it is showing.
            "targets": [
                {
                    "nodeId": step["nodeId"],
                    "target": step["effectiveTarget"],
                    "source": step["targetSource"],
                }
                for step in effective_steps
                if step["effectiveTarget"] is not None
            ],
        }

    def get_audit(self, actor: Actor, workflow_id: str, *, limit: int = 1000) -> dict:
        """Append-only view of measurements, commands and events.

        Results recorded before the acceptance pipeline existed have no stored
        ``evaluation``; it is derived from the current Skill and marked
        ``evaluationSource: "derived"`` so a reader can tell the difference.
        """
        workflow = self._load(actor, workflow_id)
        skill = skill_registry.skill_for_workflow(workflow)
        device = device_registry.profile_for_workflow(workflow)
        results = self._repository.list_node_results(workflow.id)
        actions = self._repository.list_actions(workflow.id, limit=limit)
        events = self._repository.list_events(workflow.id, limit=limit)

        timeline: list[dict] = []
        for result in results:
            recorded = result.metrics.get("evaluation")
            recorded = recorded if isinstance(recorded, dict) else None
            evaluation = recorded or skill_registry.evaluation_payload(
                workflow,
                result.node_id,
                result.metrics,
                decision=result.stage,
            )
            timeline.append(
                {
                    "type": "result",
                    "time": result.created_at,
                    "nodeId": result.node_id,
                    "attempt": result.attempt,
                    "stage": result.stage,
                    "evaluation": evaluation,
                    "evaluationSource": "recorded" if recorded else "derived",
                    "escalation": skill_registry.escalation_for(
                        result.stage, evaluation
                    ),
                    "metrics": result.metrics,
                    "rawRef": result.raw_ref,
                }
            )
        for log in actions:
            timeline.append(
                {
                    "type": "action",
                    "time": log.created_at,
                    "requestId": log.request_id,
                    "actorId": log.actor_id,
                    "action": log.action,
                    "nodeId": log.node_id,
                    "attempt": log.attempt,
                    "expectedRevision": log.expected_revision,
                    "resultRevision": log.result_revision,
                    "resultStatus": log.result_status,
                    "response": log.response_json,
                }
            )
        for event in events:
            timeline.append(
                {
                    "type": "event",
                    "time": event.time,
                    "seq": event.seq,
                    "kind": event.kind.value,
                    "message": event.message,
                }
            )

        type_order = {"result": 0, "action": 1, "event": 2}
        timeline.sort(key=lambda item: (item["time"], type_order[item["type"]]))
        escalations = sum(
            1
            for entry in timeline
            if entry["type"] == "result" and entry["escalation"] is not None
        )
        return {
            "workflowId": workflow.id,
            "revision": workflow.revision,
            "status": workflow.status.value,
            "skill": {
                "skillId": skill.skill_id,
                "version": skill.version,
                "name": skill.name,
            },
            "device": device.to_wire(),
            "autonomy": skill_registry.autonomy_for_policy(
                workflow.policy
            ).to_wire(),
            "counts": {
                "results": len(results),
                "actions": len(actions),
                "events": len(events),
                "escalations": escalations,
            },
            "timeline": timeline,
        }

    def replay_audit(self, actor: Actor, workflow_id: str) -> dict:
        """Re-evaluate recorded measurements against today's Skill.

        Read-only: nothing is written. ``changed`` is true only when a stored
        verdict disagrees with the current criteria; ``targetChanged`` is the
        separate signal that the design goal itself moved. A result recorded
        before targets existed has no baseline and is never reported as drift.
        """
        workflow = self._load(actor, workflow_id)
        skill = skill_registry.skill_for_workflow(workflow)
        device = device_registry.profile_for_workflow(workflow)
        items: list[dict] = []
        changed = 0
        target_changed = 0
        for result in self._repository.list_node_results(workflow.id):
            recorded = result.metrics.get("evaluation")
            recorded = recorded if isinstance(recorded, dict) else None
            recorded_passed = (
                recorded.get("passed") if recorded is not None else None
            )
            outcome = skill_registry.evaluate_step(
                workflow, result.node_id, result.metrics
            )
            differs = (
                isinstance(recorded_passed, bool)
                and recorded_passed != outcome.passed
            )
            changed += 1 if differs else 0
            target, target_source = skill_registry.resolve_target(
                workflow.experiment, result.node_id, machine=workflow.machine
            )
            replayed_target = target.to_wire() if target is not None else None
            recorded_target = (
                recorded.get("target") if recorded is not None else None
            )
            target_differs = _target_differs(recorded_target, replayed_target)
            target_changed += 1 if target_differs else 0
            items.append(
                {
                    "nodeId": result.node_id,
                    "attempt": result.attempt,
                    "stage": result.stage,
                    "recorded": recorded,
                    "replayed": outcome.to_wire(),
                    "recordedTarget": recorded_target,
                    "replayedTarget": replayed_target,
                    "replayedTargetSource": target_source,
                    "changed": differs,
                    "targetChanged": target_differs,
                }
            )
        return {
            "workflowId": workflow.id,
            "skill": {
                "skillId": skill.skill_id,
                "version": skill.version,
                "name": skill.name,
            },
            "device": device.to_wire(),
            "summary": {
                "total": len(items),
                "changed": changed,
                "targetChanged": target_changed,
            },
            "items": items,
        }

    # --------------------------------------------------- import and export
    def import_workspace(self, actor: Actor, text: str) -> ImportResult:
        self._require(actor, "calibration:import")
        workflows = parse_workspace(text)
        prepared, result = plan_import(
            workflows,
            existing=self._own_workflows(actor),
            owner_id=actor.user_id,
            owner_display=actor.display_name,
        )
        if prepared:
            self._repository.create_many(prepared)
        return result

    def export_workspace_json(self, actor: Actor) -> str:
        self._require(actor, "calibration:export")
        return export_workspace(self._own_workflows(actor))

    def export_workflows_csv(self, actor: Actor) -> str:
        self._require(actor, "calibration:export")
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        for workflow in self._own_workflows(actor):
            writer.writerow(
                [
                    workflow.id,
                    workflow.name,
                    workflow.experiment.value,
                    workflow.machine,
                    workflow.qubits,
                    workflow.target,
                    workflow.type.value,
                    workflow.status.value,
                    workflow.progress,
                    workflow.policy.value,
                    workflow.scenario.value,
                    workflow.owner,
                    workflow.created_at,
                    f"{workflow.elapsed:.3f}",
                    workflow.iterations,
                    workflow.import_source_id or "",
                ]
            )
        # Excel opens UTF-8 CSVs correctly only with a BOM.
        return "﻿" + buffer.getvalue()

    # ---------------------------------------------------------------- writes
    def create_workflow(
        self, actor: Actor, payload: CreateWorkflowInput
    ) -> Workflow:
        self._require(actor, "calibration:create")
        experiment = payload.experiment or (
            "two" if payload.qubits == 2 else "single"
        )
        experiment_value = getattr(experiment, "value", experiment)
        if payload.blueprint_id is not None and payload.blueprint_id.value != experiment_value:
            raise CalibrationValidationError("蓝图与实验类型不一致；拒绝跨蓝图创建工作流。")
        if payload.blueprint_version is not None and payload.blueprint_version != 1:
            raise CalibrationValidationError("不支持该蓝图版本；请刷新蓝图定义。")
        self._assert_device_supports(
            payload.machine, payload.experiment, payload.qubits
        )
        if not actor.is_leader:
            # Operators may not create strict workflows: only leaders approve.
            payload = payload.model_copy(update={"policy": Policy.THRESHOLD})
        workflow = create_workflow(
            payload,
            owner=actor.display_name,
            owner_id=actor.user_id,
        )
        self._repository.create(workflow)
        return workflow

    def apply_action(
        self,
        actor: Actor,
        workflow_id: str,
        *,
        action: WorkflowAction,
        expected_revision: int,
        request_id: str,
        node_id: str | None = None,
        attempt: int | None = None,
        confirm: bool = False,
    ) -> SaveResult:
        self._require(actor, "calibration:operate")
        workflow = self._load(actor, workflow_id)

        request_hash = action_request_hash(
            workflow_id,
            action.value,
            expected_revision,
            {"nodeId": node_id, "attempt": attempt},
        )
        replay = self._replay(actor, request_id, request_hash)
        if replay is not None:
            return replay

        control = self._control_semantics(workflow, action, confirm)
        if control is not None:
            self._record_rejection(
                request_id=request_id,
                request_hash=request_hash,
                workflow_id=workflow_id,
                actor_id=actor.user_id,
                action=action.value,
                expected_revision=expected_revision,
                status=workflow.status.value,
                message=control,
            )
            raise CalibrationValidationError(control)

        if action is WorkflowAction.APPROVE:
            self._require(actor, "calibration:approve")
        if action is WorkflowAction.RETRY and workflow.status is WorkflowStatus.ATTENTION:
            # Operators may retry their own failed runs, but releasing an
            # approval gate is a leader decision.
            self._require(actor, "calibration:approve")

        if action in (WorkflowAction.APPROVE, WorkflowAction.RETRY):
            self._assert_target(workflow, action, node_id, attempt)

        updated = apply_action(workflow, action)
        if updated is workflow:
            message = (
                f"动作 {action.value} 在当前状态 {workflow.status.value} 下不可执行。"
            )
            self._record_rejection(
                request_id=request_id,
                request_hash=request_hash,
                workflow_id=workflow_id,
                actor_id=actor.user_id,
                action=action.value,
                expected_revision=expected_revision,
                status=workflow.status.value,
                message=message,
            )
            raise CalibrationStateConflictError(
                message, status=workflow.status.value, action=action.value
            )

        return self._save(
            updated,
            expected_revision=expected_revision,
            request_id=request_id,
            request_hash=request_hash,
            actor_id=actor.user_id,
            action=action.value,
            node_id=node_id,
            attempt=attempt,
            response_extra={"confirmed": True} if confirm else None,
        )

    def change_policy(
        self,
        actor: Actor,
        workflow_id: str,
        *,
        policy: Policy,
        expected_revision: int,
        request_id: str,
    ) -> SaveResult:
        self._require(actor, "calibration:policy")
        workflow = self._load(actor, workflow_id)

        request_hash = action_request_hash(
            workflow_id, "policy", expected_revision, {"policy": policy.value}
        )
        replay = self._replay(actor, request_id, request_hash)
        if replay is not None:
            return replay

        updated = set_policy(workflow, policy)
        if updated is workflow:
            if workflow.policy is policy:
                return SaveResult(workflow=workflow, replayed=False)
            message = f"终态工作流 {workflow.status.value} 不能修改审批策略。"
            self._record_rejection(
                request_id=request_id,
                request_hash=request_hash,
                workflow_id=workflow_id,
                actor_id=actor.user_id,
                action="policy",
                expected_revision=expected_revision,
                status=workflow.status.value,
                message=message,
            )
            raise CalibrationStateConflictError(
                message, status=workflow.status.value, action="policy"
            )

        return self._save(
            updated,
            expected_revision=expected_revision,
            request_id=request_id,
            request_hash=request_hash,
            actor_id=actor.user_id,
            action="policy",
            response_extra={"policy": policy.value},
        )

    # ------------------------------------------------------------- internals
    def _assert_device_supports(
        self, machine: str, experiment, qubits: int
    ) -> None:
        """Reject a workflow the resolved device profile cannot run.

        ``experiments`` and ``qubits`` used to be display metadata; a two-qubit
        route could be created on a single-qubit machine and only fail much
        later inside the executor. Checking at creation keeps an impossible
        job out of the queue entirely.
        """
        profile = device_registry.profile_for_machine(machine)
        label = getattr(experiment, "value", str(experiment))
        if not profile.supports(experiment):
            supported = ", ".join(profile.experiments) or "无"
            raise CalibrationValidationError(
                f"设备 {profile.device_id} 不支持 {label} 实验（支持：{supported}）。"
            )
        if profile.qubits > 0 and qubits > profile.qubits:
            raise CalibrationValidationError(
                f"设备 {profile.device_id} 只有 {profile.qubits} 个比特，"
                f"无法创建 {qubits} 比特任务。"
            )

    def _control_semantics(
        self, workflow: Workflow, action: WorkflowAction, confirm: bool
    ) -> str | None:
        """Hardware safety gate for start/abort; returns a rejection message.

        * A real adapter never runs while the deployment is in the default
          dry-run mode, and needs an explicit per-request ``confirm`` to run
          live (two independent switches).
        * Aborting a workflow bound to a real adapter requires confirming the
          hardware actually stopped. A device acknowledging a cancel command
          is not proof that it stopped, so the operator's confirmation is
          recorded in the action log.
        """
        if action not in (WorkflowAction.START, WorkflowAction.ABORT):
            return None
        profile = device_registry.profile_for_workflow(workflow)
        try:
            adapter = adapter_for_workflow(workflow)
        except UnknownAdapterError as exc:
            return str(exc)
        label = getattr(workflow.experiment, "value", str(workflow.experiment))

        if action is WorkflowAction.START:
            if not profile.supports(workflow.experiment):
                supported = ", ".join(profile.experiments) or "无"
                return (
                    f"设备 {profile.device_id} 不支持 {label} 实验"
                    f"（支持：{supported}），拒绝启动。"
                )
            if profile.qubits > 0 and workflow.qubits > profile.qubits:
                return (
                    f"设备 {profile.device_id} 只有 {profile.qubits} 个比特，"
                    f"无法运行 {workflow.qubits} 比特任务。"
                )
            if adapter.simulated:
                return None
            if self._control_mode != CONTROL_LIVE:
                if not profile.supports_dry_run:
                    return (
                        f"设备 {profile.device_id} 不支持 dry-run，且当前"
                        " QUANTAMIND_DEVICE_CONTROL=dry_run；拒绝启动。"
                    )
                return (
                    f"真实设备适配器 {adapter.name!r} 处于 dry-run 模式，"
                    "不会向硬件下发命令；需由负责人在部署中设置 "
                    "QUANTAMIND_DEVICE_CONTROL=live 后再启动。"
                )
            if not confirm:
                return (
                    f"设备 {profile.device_id} 使用真实适配器 {adapter.name!r}，"
                    "启动属于硬件下发，需要二次确认：请在请求中携带 "
                    "confirm=true。"
                )
            return None

        # ABORT
        if adapter.simulated:
            return None
        if not confirm:
            return (
                f"终止设备 {profile.device_id} 上的任务前，必须确认硬件已停止"
                "（不能仅凭设备返回 cancelCalibration=false）：请在请求中携带 "
                "confirm=true。"
            )
        return None

    def _save(self, workflow: Workflow, **kwargs) -> SaveResult:
        """Persist, attaching the authoritative snapshot to a stale-revision 409.

        The plan's contract returns ``409 + 最新快照`` so the client can refresh
        without a second round trip.
        """
        try:
            return self._repository.save_with_action(workflow, **kwargs)
        except CalibrationConflictError as exc:
            current = self._repository.get(workflow.id)
            if current is not None:
                exc.details["actualRevision"] = current.revision
                exc.details["workflow"] = to_wire(current)
            raise

    def _own_workflows(self, actor: Actor) -> list[Workflow]:
        """The caller's personal workspace.

        Import/export mirror the frontend's single-workspace model: a leader
        may read every owner's workflows, but a backup file they export (and
        later re-import) belongs to them alone.
        """
        return self._repository.list(owner_id=actor.user_id, limit=MAX_WORKFLOWS)

    def _require(self, actor: Actor, permission: str) -> None:
        if not actor.can(permission):
            raise CalibrationForbiddenError(f"缺少权限：{permission}")

    def _load(self, actor: Actor, workflow_id: str) -> Workflow:
        workflow = self._repository.get(workflow_id)
        if workflow is None:
            raise CalibrationNotFoundError(workflow_id)
        if not actor.is_leader and workflow.owner_id != actor.user_id:
            raise CalibrationForbiddenError("无权访问其他用户的工作流。")
        return workflow

    def _replay(
        self, actor: Actor, request_id: str, request_hash: str
    ) -> SaveResult | None:
        log = self._repository.get_action(request_id)
        if log is None:
            return None
        if log.request_hash != request_hash or log.actor_id != actor.user_id:
            # Reusing another client's key is a client bug, not a retry.
            raise CalibrationIdempotencyConflictError(request_id)
        if log.result_status != "success":
            response = log.response_json or {}
            raise CalibrationStateConflictError(
                response.get("message", "该请求此前已被拒绝。"),
                status=response.get("status", "unknown"),
                action=response.get("action", log.action),
            )
        current = self._repository.get(log.workflow_id)
        if current is None:
            raise CalibrationNotFoundError(log.workflow_id)
        return SaveResult(workflow=current, replayed=True)

    def _record_rejection(
        self,
        *,
        request_id: str,
        request_hash: str,
        workflow_id: str,
        actor_id: str,
        action: str,
        expected_revision: int,
        status: str,
        message: str,
    ) -> None:
        """Consume the idempotency key for a command rejected by the state
        machine, so a retry of the same request returns the same 409."""
        self._repository.record_rejection(
            request_id=request_id,
            request_hash=request_hash,
            workflow_id=workflow_id,
            actor_id=actor_id,
            action=action,
            result_status="rejected",
            expected_revision=expected_revision,
            response={"message": message, "status": status, "action": action},
        )

    @staticmethod
    def _assert_target(
        workflow: Workflow,
        action: WorkflowAction,
        node_id: str | None,
        attempt: int | None,
    ) -> None:
        if not node_id or attempt is None:
            raise CalibrationValidationError(
                f"{action.value} 必须携带 nodeId 与 attempt，用于绑定当前等待决策的节点。"
            )
        target = next(
            (
                node
                for node in workflow.nodes
                if node.status in (NodeStatus.RUNNING, NodeStatus.ATTENTION)
            ),
            None,
        )
        if target is None or target.id != node_id or target.attempt != attempt:
            raise CalibrationStateConflictError(
                "目标节点或尝试编号与当前等待决策的节点不一致。",
                status=workflow.status.value,
                action=action.value,
                details={
                    "nodeId": node_id,
                    "attempt": attempt,
                    "activeNode": target.id if target else None,
                    "activeAttempt": target.attempt if target else None,
                },
            )


_TARGET_KEYS = ("metric", "ideal", "unit", "direction", "tolerance")


def _target_differs(recorded, replayed) -> bool | None:
    """Whether the design goal moved since the result was recorded.

    ``None`` means there is no baseline (an old result, or a control step
    without a target); such an item must not be reported as drift.
    """
    if not isinstance(recorded, dict) or not isinstance(replayed, dict):
        return None
    return any(recorded.get(key) != replayed.get(key) for key in _TARGET_KEYS)


def _effective_steps(device, skill) -> list[dict]:
    """Skill steps with the criterion and target the device actually applies."""
    steps: list[dict] = []
    for step in skill.steps:
        effective = device.criterion_for(step.acceptance, step.node_id)
        target = device.target_for(step.target, step.node_id)
        steps.append(
            {
                **step.to_wire(),
                "effectiveAcceptance": effective.to_wire(),
                "deviceOverride": effective is not step.acceptance,
                "effectiveTarget": (
                    target.to_wire() if target is not None else None
                ),
                "targetSource": (
                    None
                    if target is None
                    else "device"
                    if target is not step.target
                    else "skill"
                ),
            }
        )
    return steps


def _result_payload(result: NodeResult) -> dict:
    return {
        "id": result.id,
        "workflowId": result.workflow_id,
        "nodeId": result.node_id,
        "attempt": result.attempt,
        "stage": result.stage,
        "metrics": result.metrics,
        "rawRef": result.raw_ref,
        "createdAt": result.created_at,
    }
