"""可持久化的校准控制流；数值物理与真实设备行为由受限后端提供。"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol


class ContractError(ValueError):
    """输入、证据或能力不符合已冻结契约。"""


class ResourceBusy(ContractError):
    pass


class NeedsReconciliation(RuntimeError):
    pass


class VerificationFailed(ContractError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def candidate_hash(candidate: Mapping[str, float]) -> str:
    return hashlib.sha256(_json(dict(candidate)).encode()).hexdigest()


def _real(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


@dataclass(frozen=True)
class Budget:
    shots: int = 1024
    max_attempts: int = 2
    timeout_s: float = 60.0
    total_shots: int | None = None
    max_total_shots: int | None = None
    max_acquisitions: int | None = None

    def __post_init__(self):
        for name in ("shots", "max_attempts"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ContractError(f"{name}必须为正整数")
        if not _real(self.timeout_s) or self.timeout_s <= 0:
            raise ContractError("timeout_s必须为有限正数")
        if self.max_total_shots is not None:
            if isinstance(self.max_total_shots, bool) or not isinstance(self.max_total_shots, int) or self.max_total_shots < 1:
                raise ContractError("max_total_shots必须是正整数")
            if self.total_shots is not None and self.total_shots != self.max_total_shots:
                raise ContractError("total_shots和max_total_shots冲突")
            object.__setattr__(self, "total_shots", self.max_total_shots)
        if self.max_acquisitions is not None and (isinstance(self.max_acquisitions, bool) or not isinstance(self.max_acquisitions, int) or self.max_acquisitions < 2):
            raise ContractError("max_acquisitions至少为2")
        if self.total_shots is None:
            object.__setattr__(self, "total_shots", 2 * self.shots * self.max_attempts)
        if isinstance(self.total_shots, bool) or not isinstance(self.total_shots, int) or self.total_shots < 2 * self.shots:
            raise ContractError("total_shots至少覆盖一次选参与独立复测")


@dataclass(frozen=True)
class StepSpec:
    id: str
    depends_on: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    budget: Budget = field(default_factory=Budget)
    criteria: dict[str, dict[str, float]] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id:
            raise ContractError("步骤必须有非空id")
        for name in ("depends_on", "reads", "writes", "resources"):
            values = tuple(getattr(self, name))
            if any(not isinstance(v, str) or not v for v in values) or len(values) != len(set(values)):
                raise ContractError(f"{name}必须是无重复的非空字符串列表")
            object.__setattr__(self, name, values)
        if self.id in self.depends_on:
            raise ContractError("步骤不能依赖自身")
        if isinstance(self.budget, Mapping):
            object.__setattr__(self, "budget", Budget(**self.budget))
        if not isinstance(self.budget, Budget):
            raise ContractError("budget必须是Budget")
        if not isinstance(self.criteria, dict):
            raise ContractError("criteria必须是指标规则字典")
        for metric, rule in self.criteria.items():
            if not isinstance(metric, str) or not metric or not isinstance(rule, dict) or not rule:
                raise ContractError("指标名称或规则无效")
            if set(rule) - {"min", "max", "max_stderr"} or any(not _real(v) for v in rule.values()):
                raise ContractError("指标规则仅支持有限min/max/max_stderr")
            if rule.get("min", -math.inf) > rule.get("max", math.inf) or rule.get("max_stderr", 0) < 0:
                raise ContractError("指标上下界或误差上限无效")


@dataclass(frozen=True)
class ExecutionRequest:
    run_id: str
    step_id: str
    attempt: int
    operation_id: str
    shots: int
    parameter_values: dict[str, float | None]
    parameter_versions: dict[str, int]
    candidate_hash: str | None = None
    deadline: float | None = None


class Backend(Protocol):
    def capabilities(self, step: StepSpec) -> Mapping[str, Any]: ...
    def measure(self, request: ExecutionRequest) -> Mapping[str, Any]: ...
    def apply_trial(self, request: ExecutionRequest, candidate: Mapping[str, float]) -> Mapping[str, Any]: ...
    def verify(self, request: ExecutionRequest, candidate: Mapping[str, float]) -> Mapping[str, Any]: ...
    def rollback(self, request: ExecutionRequest, snapshot: Mapping[str, float | None]) -> Mapping[str, Any]: ...
    def reconcile(self, operation_id: str) -> Mapping[str, Any]: ...


class StepAgent:
    """一个步骤一个实例；只提出受限后端调用，发布权保留在Runtime。"""
    def __init__(self, spec: StepSpec):
        self.spec = spec

    def execute(self, runtime: "Runtime", backend: Backend, operation_id: str) -> dict:
        return runtime.run_step(self.spec.id, backend, operation_id)


class Runtime:
    def __init__(self, db_path: str | Path, steps=(), *, allow_hardware: bool = False):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS steps(id TEXT PRIMARY KEY,spec TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS parameters(name TEXT PRIMARY KEY,value TEXT NOT NULL,revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,operation_id TEXT UNIQUE NOT NULL,step_id TEXT NOT NULL,request_hash TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,run_id TEXT,kind TEXT NOT NULL,request_hash TEXT NOT NULL,state TEXT NOT NULL,result TEXT);
        CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,acquisition_id TEXT UNIQUE NOT NULL,run_id TEXT NOT NULL,step_id TEXT NOT NULL,kind TEXT NOT NULL,operation_id TEXT NOT NULL,payload TEXT NOT NULL,valid INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS locks(resource TEXT PRIMARY KEY,run_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS execution_claims(run_id TEXT PRIMARY KEY,owner_pid INTEGER NOT NULL,owner_token TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,event TEXT NOT NULL,at REAL NOT NULL,payload TEXT NOT NULL);
        """)
        self.allow_hardware = allow_hardware
        self._owner_token = uuid.uuid4().hex
        self.steps = {r["id"]: self._spec(json.loads(r["spec"])) for r in self.db.execute("SELECT * FROM steps")}
        for step in steps:
            self.register(step)
        self.agents = {key: StepAgent(spec) for key, spec in self.steps.items()}

    @staticmethod
    def _spec(value):
        return StepSpec(**{**value, "budget": Budget(**value["budget"])})

    def close(self):
        self.db.close()

    @contextmanager
    def _transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def _event(self, run_id, event, payload=None):
        self.db.execute("INSERT INTO events(run_id,event,at,payload) VALUES(?,?,?,?)", (run_id, event, time.time(), _json(payload or {})))

    def register(self, step: StepSpec):
        if not isinstance(step, StepSpec):
            raise ContractError("仅接受StepSpec")
        step = self._spec(json.loads(_json(asdict(step))))
        existing = self.steps.get(step.id)
        if existing is not None and existing != step:
            raise ContractError("已登记步骤契约不可原位修改；请使用新的步骤id")
        self.db.execute("INSERT OR IGNORE INTO steps VALUES(?,?)", (step.id, _json(asdict(step))))
        self.steps[step.id] = step
        if hasattr(self, "agents"):
            self.agents[step.id] = StepAgent(step)

    def get_parameters(self) -> dict:
        return {r["name"]: {"value": json.loads(r["value"]), "revision": r["revision"]} for r in self.db.execute("SELECT * FROM parameters ORDER BY name")}

    def _invalidate(self, names, exclude=None):
        affected = {s.id for s in self.steps.values() if set(names) & (set(s.reads) | set(s.writes))}
        while True:
            expanded = affected | {s.id for s in self.steps.values() if set(s.depends_on) & affected}
            if expanded == affected:
                break
            affected = expanded
        affected.discard(exclude)
        for step_id in sorted(affected):
            self.db.execute("UPDATE evidence SET valid=0 WHERE step_id=?", (step_id,))
        if affected:
            self._event(None, "evidence_invalidated", {"parameters": sorted(names), "steps": sorted(affected)})

    def set_parameter(self, name: str, value: float, operation_id: str) -> dict:
        if not isinstance(name, str) or not name or not _real(value) or not operation_id:
            raise ContractError("参数名、有限实数值和operation_id不可缺失")
        digest = candidate_hash({"name": name, "value": value})
        with self._transaction():
            if self.db.execute("SELECT 1 FROM runs WHERE operation_id=?", (operation_id,)).fetchone():
                raise ContractError("IDEMPOTENCY_CONFLICT")
            old = self.db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            if old:
                if old["request_hash"] != digest or old["kind"] != "set_parameter":
                    raise ContractError("IDEMPOTENCY_CONFLICT")
                return json.loads(old["result"])
            current = self.db.execute("SELECT revision FROM parameters WHERE name=?", (name,)).fetchone()
            revision = 1 if current is None else current["revision"] + 1
            result = {"name": name, "value": value, "revision": revision}
            self.db.execute("INSERT OR REPLACE INTO parameters VALUES(?,?,?)", (name, _json(value), revision))
            self._invalidate([name])
            self.db.execute("INSERT INTO operations VALUES(?,NULL,'set_parameter',?,'completed',?)", (operation_id, digest, _json(result)))
            self._event(None, "parameter_updated", result)
            return result

    def evidence(self, step_id: str) -> dict | None:
        row = self.db.execute("SELECT payload FROM evidence WHERE step_id=? AND valid=1 AND kind='verify' ORDER BY rowid DESC LIMIT 1", (step_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def events(self, run_id=None) -> list[dict]:
        rows = self.db.execute("SELECT * FROM events ORDER BY seq" if run_id is None else "SELECT * FROM events WHERE run_id=? ORDER BY seq", () if run_id is None else (run_id,))
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def get_run(self, run_id: str) -> dict:
        row = self.db.execute("SELECT * FROM runs WHERE id=? OR operation_id=?", (run_id, run_id)).fetchone()
        if not row:
            raise ContractError(f"未找到运行：{run_id}")
        return {"run_id": row["id"], "operation_id": row["operation_id"], "step_id": row["step_id"], "state": row["state"], **json.loads(row["payload"])}

    def list_runs(self) -> list[dict]:
        return [self.get_run(row[0]) for row in self.db.execute("SELECT id FROM runs ORDER BY rowid")]

    def _save(self, run, state=None):
        if state:
            run["state"] = state
        payload = {k: v for k, v in run.items() if k not in ("run_id", "operation_id", "step_id", "state")}
        self.db.execute("UPDATE runs SET state=?,payload=? WHERE id=?", (run["state"], _json(payload), run["run_id"]))

    def _capabilities(self, backend, step):
        cap = dict(backend.capabilities(step))
        required = {"measure", "apply_trial", "verify", "rollback", "reconcile"}
        if cap.get("schema_version") != 1 or cap.get("data_kind") not in ("scenario", "simulated", "real"):
            raise ContractError("未知能力版本或数据来源")
        if not required.issubset(set(cap.get("operations", []))) or any(cap.get(k) is not True for k in ("readback", "rollback", "reconcile")):
            raise ContractError("缺少安全执行、读回、恢复或核查能力")
        if cap["data_kind"] == "real" and not self.allow_hardware:
            raise ContractError("默认禁止真实硬件操作")
        if cap["data_kind"] == "real":
            raise ContractError("此原型尚无硬件授权/租约适配实现，禁止真实硬件操作")
        counts = cap.get("acquisition_counts", {"measure": 1, "verify": 1})
        if not isinstance(counts, dict) or set(counts) != {"measure", "verify"} or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in counts.values()):
            raise ContractError("acquisition_counts必须明确measure/verify正整数采集次数")
        cap["acquisition_counts"] = counts
        return cap

    def run_step(self, step_id: str, backend: Backend, operation_id: str) -> dict:
        if step_id not in self.steps or not isinstance(operation_id, str) or not operation_id:
            raise ContractError("步骤或operation_id无效")
        step = self.steps[step_id]
        digest = candidate_hash(asdict(step))
        old = self.db.execute("SELECT id,request_hash FROM runs WHERE operation_id=?", (operation_id,)).fetchone()
        if old:
            if old["request_hash"] != digest:
                raise ContractError("IDEMPOTENCY_CONFLICT")
            return self.get_run(old["id"])
        cap = self._capabilities(backend, step)
        with self._transaction():
            if self.db.execute("SELECT 1 FROM operations WHERE id=?", (operation_id,)).fetchone():
                raise ContractError("IDEMPOTENCY_CONFLICT")
            parameters = self.get_parameters()
            missing = set(step.reads) - set(parameters)
            if missing:
                raise ContractError(f"缺少前置参数：{sorted(missing)}")
            deps = {}
            for dependency in step.depends_on:
                ev = self.evidence(dependency)
                if ev is None:
                    raise ContractError(f"前置证据无效或缺失：{dependency}")
                deps[dependency] = ev["evidence_id"]
            run_id = "run-" + uuid.uuid4().hex
            for resource in step.resources:
                if self.db.execute("SELECT 1 FROM locks WHERE resource=?", (resource,)).fetchone():
                    raise ResourceBusy(f"资源已占用：{resource}")
                self.db.execute("INSERT INTO locks VALUES(?,?)", (resource, run_id))
            names = sorted(set(step.reads) | set(step.writes))
            payload = {"attempt": 1, "used_shots": 0, "used_acquisitions": 0, "acquisition_counts": cap["acquisition_counts"], "started_at": time.time(), "data_kind": cap["data_kind"], "parameter_values": {n: parameters.get(n, {}).get("value") for n in names}, "parameter_versions": {n: parameters.get(n, {}).get("revision", 0) for n in names}, "dependency_evidence": deps, "phase": "measure", "trial_active": False, "error": None}
            self.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", (run_id, operation_id, step.id, digest, "running", _json(payload)))
            self._event(run_id, "run_started", {"step": step.id, "data_kind": cap["data_kind"]})
        return self._execute(self.get_run(run_id), backend)

    def _request(self, run, kind):
        return ExecutionRequest(run["run_id"], run["step_id"], run["attempt"], f"{run['operation_id']}/{run['attempt']}/{kind}", self.steps[run["step_id"]].budget.shots, dict(run["parameter_values"]), dict(run["parameter_versions"]), None if kind == "measure" else run.get("candidate_hash"), run["started_at"] + self.steps[run["step_id"]].budget.timeout_s)

    def _call(self, run, backend, kind, extra=None):
        request = self._request(run, kind)
        digest = candidate_hash({"request": asdict(request), "extra": extra})
        old = self.db.execute("SELECT * FROM operations WHERE id=?", (request.operation_id,)).fetchone()
        if old:
            if old["request_hash"] != digest:
                raise ContractError("IDEMPOTENCY_CONFLICT")
            if old["state"] == "completed":
                return json.loads(old["result"])
            if old["state"] != "not_started":
                raise NeedsReconciliation(f"操作{request.operation_id}结果未知，必须核查")
        if kind in ("measure", "verify"):
            budget = self.steps[run["step_id"]].budget
            if time.time() - run["started_at"] > budget.timeout_s:
                raise ContractError("执行预算截止，停止新增采集")
            count = run["acquisition_counts"][kind]
            extra_shots = request.shots * count if old is None else 0
            extra_acquisitions = count if old is None else 0
            if run["used_shots"] + extra_shots > budget.total_shots:
                raise ContractError("采集预算不足")
            if budget.max_acquisitions is not None and run["used_acquisitions"] + extra_acquisitions > budget.max_acquisitions:
                raise ContractError("采集次数预算不足")
        with self._transaction():
            self.db.execute("INSERT OR REPLACE INTO operations VALUES(?,?,?,?,?,NULL)", (request.operation_id, run["run_id"], kind, digest, "pending"))
            if kind in ("measure", "verify") and old is None:
                run["used_shots"] += request.shots * run["acquisition_counts"][kind]
                run["used_acquisitions"] += run["acquisition_counts"][kind]
            run["phase"] = kind
            self._save(run)
            self._event(run["run_id"], "operation_started", {"operation_id": request.operation_id, "kind": kind})
        # 崩溃或超时后，pending保留；不假定后端未执行。
        method = getattr(backend, kind)
        result = method(request) if extra is None else method(request, extra)
        try:
            encoded = _json(dict(result))
        except (TypeError, ValueError) as exc:
            raise NeedsReconciliation(f"后端回执无法持久化：{exc}") from exc
        with self._transaction():
            self.db.execute("UPDATE operations SET state='completed',result=? WHERE id=?", (encoded, request.operation_id))
            self._event(run["run_id"], "operation_completed", {"operation_id": request.operation_id})
        return json.loads(encoded)

    def _validate_evidence(self, run, value, kind):
        required = ("evidence_id", "acquisition_id", "data_kind", "parameter_versions", "shots", "metrics")
        if any(key not in value for key in required):
            raise ContractError("测量缺少关键证据字段")
        if any(not isinstance(value[key], str) or not value[key] for key in ("evidence_id", "acquisition_id")):
            raise ContractError("证据/采集编号为空")
        if value["data_kind"] != run["data_kind"] or value["parameter_versions"] != run["parameter_versions"]:
            raise ContractError("证据来源或参数修订不匹配")
        shots = value["shots"]
        if isinstance(shots, bool) or not isinstance(shots, int) or shots != self.steps[run["step_id"]].budget.shots:
            raise ContractError("实际shots不完整或无效")
        if not isinstance(value["metrics"], dict) or any(not _real(v) for v in value["metrics"].values()):
            raise ContractError("指标必须为有限实数")
        count = value.get("acquisition_count", 1)
        total = value.get("total_shots", shots)
        if isinstance(count, bool) or not isinstance(count, int) or count != run["acquisition_counts"][kind] or isinstance(total, bool) or not isinstance(total, int) or total != count * shots:
            raise ContractError("扫描采集次数或总shots与预声明不一致")
        if "counts" in value:
            counts = value["counts"]
            if not isinstance(counts, dict) or not counts or any(not isinstance(k, str) or not k for k in counts):
                raise ContractError("counts结构无效")
            labels = list(counts)
            if len({len(k) for k in labels}) != 1 or any(set(k) - set("012") for k in labels):
                raise ContractError("counts标签不一致")
            if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in counts.values()) or sum(counts.values()) != shots:
                raise ContractError("counts必须非负且总和等于shots")
        if kind == "verify":
            if not isinstance(value.get("passed"), bool) or value.get("candidate_hash") != run.get("candidate_hash"):
                raise ContractError("独立复测缺少布尔判定或候选哈希不匹配")
            if value["acquisition_id"] == run["measurement"]["acquisition_id"] or value["evidence_id"] == run["measurement"]["evidence_id"]:
                raise ContractError("独立复测复用了选参证据")
        old = self.db.execute("SELECT * FROM evidence WHERE id=? OR acquisition_id=?", (value["evidence_id"], value["acquisition_id"])).fetchone()
        if old:
            if old["run_id"] != run["run_id"] or old["kind"] != kind or old["operation_id"] != self._request(run, kind).operation_id or old["payload"] != _json(value):
                raise ContractError("证据或采集编号被复用")
        else:
            self.db.execute("INSERT INTO evidence(id,acquisition_id,run_id,step_id,kind,operation_id,payload) VALUES(?,?,?,?,?,?,?)", (value["evidence_id"], value["acquisition_id"], run["run_id"], run["step_id"], kind, self._request(run, kind).operation_id, _json(value)))

    def _check_current(self, run):
        parameters = self.get_parameters()
        if any(parameters.get(name, {}).get("revision", 0) != revision for name, revision in run["parameter_versions"].items()):
            raise ContractError("STALE_REVISION：参数已被更新，禁止提交")
        for name, evidence_id in run["dependency_evidence"].items():
            ev = self.evidence(name)
            if ev is None or ev["evidence_id"] != evidence_id:
                raise ContractError("STALE_EVIDENCE：前置验证证据已失效")

    def _rollback(self, run, backend):
        snapshot = {name: run["parameter_values"][name] for name in self.steps[run["step_id"]].writes}
        result = self._call(run, backend, "rollback", snapshot)
        if result.get("status") != "restored" or result.get("actual_values") != snapshot:
            raise NeedsReconciliation("恢复回执未证明实际值与快照一致")
        run["trial_active"] = False
        run["phase"] = "restored"
        self._save(run)
        self._event(run["run_id"], "trial_restored")

    def _finish(self, run, state):
        with self._transaction():
            self._save(run, state)
            self.db.execute("DELETE FROM locks WHERE run_id=?", (run["run_id"],))
            self._event(run["run_id"], "run_" + state, {"error": run.get("error")})
        return self.get_run(run["run_id"])

    @contextmanager
    def _execution_claim(self, run_id):
        with self._transaction():
            row = self.db.execute("SELECT * FROM execution_claims WHERE run_id=?", (run_id,)).fetchone()
            if row:
                try:
                    os.kill(row["owner_pid"], 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    alive = True
                if alive:
                    raise ResourceBusy("同一运行仍有活动执行者，不能并发恢复")
                self.db.execute("DELETE FROM execution_claims WHERE run_id=?", (run_id,))
            self.db.execute("INSERT INTO execution_claims VALUES(?,?,?)", (run_id, os.getpid(), self._owner_token))
        try:
            yield
        finally:
            self.db.execute("DELETE FROM execution_claims WHERE run_id=? AND owner_token=?", (run_id, self._owner_token))

    def _execute(self, run, backend):
        with self._execution_claim(run["run_id"]):
            return self._execute_owned(run, backend)

    def _check_deadline(self, run):
        if time.time() - run["started_at"] > self.steps[run["step_id"]].budget.timeout_s:
            raise ContractError("执行预算截止，停止新增试验或提交")

    def _execute_owned(self, run, backend):
        step = self.steps[run["step_id"]]
        while run["attempt"] <= step.budget.max_attempts:
            try:
                self._check_current(run)
                measure = self._call(run, backend, "measure")
                self._validate_evidence(run, measure, "measure")
                candidate = measure.get("candidate")
                if not isinstance(candidate, dict) or set(candidate) != set(step.writes) or any(not _real(v) for v in candidate.values()):
                    raise ContractError("候选必须精确匹配写参数并为有限实数")
                run.update(measurement=measure, candidate=candidate, candidate_hash=candidate_hash(candidate))
                self._save(run)
                self._check_current(run)
                self._check_deadline(run)
                applied = self._call(run, backend, "apply_trial", candidate)
                if applied.get("status") != "applied" or applied.get("actual_values") != candidate:
                    raise NeedsReconciliation("临时下发回执无法确认实际应用值")
                run["trial_active"] = True
                self._save(run)
                verified = self._call(run, backend, "verify", candidate)
                self._validate_evidence(run, verified, "verify")
                run["verification"] = verified
                self._save(run)
                criteria = step.criteria
                if not criteria:
                    raise ContractError("缺少冻结验收规则，禁止只凭后端passed提交")
                for name, rule in criteria.items():
                    value = verified["metrics"].get(name)
                    if not _real(value):
                        raise ContractError(f"缺少必需验收指标：{name}")
                    if value < rule.get("min", -math.inf) or value > rule.get("max", math.inf):
                        raise VerificationFailed(f"验收指标越界：{name}")
                    if "max_stderr" in rule:
                        stderr = verified.get("stderr", {}).get(name)
                        if not _real(stderr) or stderr < 0:
                            raise ContractError(f"缺少有效标准误：{name}")
                        if stderr > rule["max_stderr"]:
                            raise VerificationFailed(f"指标不确定性过高：{name}")
                if not verified["passed"]:
                    raise VerificationFailed("独立复测未通过")
                self._check_deadline(run)
                with self._transaction():
                    self._check_current(run)
                    # 参数版本、有效证据、终态和事件同事务固化。
                    for name, value in candidate.items():
                        revision = run["parameter_versions"][name] + 1
                        self.db.execute("INSERT OR REPLACE INTO parameters VALUES(?,?,?)", (name, _json(value), revision))
                    self._invalidate(candidate, exclude=step.id)
                    self.db.execute("UPDATE evidence SET valid=0 WHERE step_id=?", (step.id,))
                    self.db.execute("UPDATE evidence SET valid=1 WHERE id=?", (verified["evidence_id"],))
                    committed_run = {**run, "phase": "committed", "trial_active": False, "error": None,
                                     "committed_versions": {n: run["parameter_versions"][n] + 1 for n in candidate}}
                    self._save(committed_run, "completed")
                    self.db.execute("DELETE FROM locks WHERE run_id=?", (run["run_id"],))
                    self._event(run["run_id"], "parameters_committed", {"versions": committed_run["committed_versions"], "evidence_id": verified["evidence_id"]})
                return self.get_run(run["run_id"])
            except BaseException as exc:
                # KeyboardInterrupt/SystemExit也只能记录不确定状态，不能宣称取消成功。
                run["error"] = f"{type(exc).__name__}: {exc}"
                pending = self.db.execute("SELECT 1 FROM operations WHERE run_id=? AND state='pending'", (run["run_id"],)).fetchone()
                if pending or isinstance(exc, NeedsReconciliation):
                    self._save(run, "needs_reconciliation")
                    self._event(run["run_id"], "reconciliation_required", {"error": run["error"]})
                    return self.get_run(run["run_id"])
                if run["trial_active"] and str(exc).startswith(("STALE_REVISION", "STALE_EVIDENCE")):
                    self._save(run, "needs_reconciliation")
                    self._event(run["run_id"], "reconciliation_required", {"error": run["error"], "note": "基准冲突，禁止盲目恢复旧值"})
                    return self.get_run(run["run_id"])
                if run["trial_active"]:
                    try:
                        self._rollback(run, backend)
                    except BaseException as restore_error:
                        run["error"] += f"; 恢复未确认：{restore_error}"
                        self._save(run, "needs_reconciliation")
                        self._event(run["run_id"], "reconciliation_required", {"error": run["error"]})
                        return self.get_run(run["run_id"])
                if isinstance(exc, VerificationFailed) and run["attempt"] < step.budget.max_attempts:
                    run["attempt"] += 1
                    run["phase"] = "measure"
                    for key in ("candidate", "candidate_hash", "measurement", "verification"):
                        run.pop(key, None)
                    self._save(run)
                    self._event(run["run_id"], "bounded_retry", {"attempt": run["attempt"]})
                    continue
                return self._finish(run, "rolled_back" if run["phase"] == "restored" else "failed")
        return self._finish(run, "failed")

    def resume(self, run_id: str, backend: Backend) -> dict:
        run = self.get_run(run_id)
        with self._execution_claim(run["run_id"]):
            return self._resume_owned(run, backend)

    def _resume_owned(self, run, backend):
        if run["state"] in ("completed", "failed", "rolled_back"):
            return run
        cap = self._capabilities(backend, self.steps[run["step_id"]])
        if cap["data_kind"] != run["data_kind"] or cap["acquisition_counts"] != run["acquisition_counts"]:
            raise ContractError("重启后后端能力不匹配")
        for operation in list(self.db.execute("SELECT * FROM operations WHERE run_id=? AND state='pending'", (run["run_id"],))):
            result = dict(backend.reconcile(operation["id"]))
            status = result.get("status")
            if status == "completed" and isinstance(result.get("result"), Mapping):
                self.db.execute("UPDATE operations SET state='completed',result=? WHERE id=?", (_json(dict(result["result"])), operation["id"]))
            elif status == "not_started":
                self.db.execute("UPDATE operations SET state='not_started' WHERE id=?", (operation["id"],))
            else:
                run["error"] = "操作结果仍未知，保持资源隔离，禁止重复下发"
                self._save(run, "needs_reconciliation")
                return self.get_run(run["run_id"])
            self._event(run["run_id"], "operation_reconciled", {"operation_id": operation["id"], "status": status})
        # 崩溃可能发生在apply回执落盘之后、trial_active保存之前。
        # 根据当前attempt的权威回执恢复副作用状态，再做版本检查。
        apply_id = self._request(run, "apply_trial").operation_id
        restore_id = self._request(run, "rollback").operation_id
        applied = self.db.execute("SELECT state,result FROM operations WHERE id=?", (apply_id,)).fetchone()
        restored = self.db.execute("SELECT state,result FROM operations WHERE id=?", (restore_id,)).fetchone()
        if applied and applied["state"] == "completed":
            receipt = json.loads(applied["result"])
            if receipt.get("status") != "applied" or receipt.get("actual_values") != run.get("candidate"):
                run["error"] = "已记录的下发仍无法证明实际状态，保持隔离"
                self._save(run, "needs_reconciliation")
                return self.get_run(run["run_id"])
            run["trial_active"] = True
            if restored and restored["state"] == "completed":
                receipt = json.loads(restored["result"])
                snapshot = {name: run["parameter_values"][name] for name in self.steps[run["step_id"]].writes}
                if receipt.get("status") == "restored" and receipt.get("actual_values") == snapshot:
                    run["trial_active"] = False
                    run["phase"] = "restored"
        self._save(run, "running")
        return self._execute_owned(run, backend)

    def run_workflow(self, backend: Backend, operation_id: str) -> list[dict]:
        if not operation_id:
            raise ContractError("workflow operation_id不能为空")
        pending = set(self.steps)
        results = []
        while pending:
            ready = sorted(k for k in pending if all(d not in pending for d in self.steps[k].depends_on))
            if not ready:
                raise ContractError("步骤依赖存在环或不可排序")
            for key in ready:
                if any(d not in self.steps for d in self.steps[key].depends_on):
                    raise ContractError("步骤依赖未登记")
                result = self.run_step(key, backend, f"{operation_id}/{key}")
                results.append(result)
                pending.remove(key)
                if result["state"] != "completed":
                    return results
        return results
