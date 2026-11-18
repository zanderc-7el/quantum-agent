# 控制平面运行契约

公开角色固定为原流程15个 `FlowAgent` 和1个 `WorkflowSupervisor`。44项方法是各角色拥有的内部procedure；当前5类物理方法展开成11个实例。底层每个 `StepSpec` 仍登记为 `StepAgent`，这个兼容类名表示程序节点执行器，不增加公开智能体数量。`Runtime` 管理节点依赖、预算、资源、候选试验和独立复测，仅使用Python标准库。物理计算由后端提供；`ScenarioBackend` 只是故障注入控制流演示。当前始终拒绝 `data_kind='real'`，`allow_hardware=True` 也不会打开实机路径。

## 0. 原流程角色层

固定归属来自 `config/workflow_ownership.json`，内部参数读写来自 `config/procedure_catalog.json`，可执行实例、读写前提与仿真候选范围来自 `config/bindings.json`。公开 `list/show/propose` 只接受16个角色；`plan` 只输出15个实验owner。变更规划先将 `q0.frequency_hz` 等实例键映射到内部程序读写，沿程序依赖传播，再归属owner并沿原流程依赖传播。该保守规划不直接执行设备动作。

| 接口 | 语义 |
| --- | --- |
| `FlowAgent(agent_id)` | 取得一个原流程方框的主方法、内部程序、绑定及候选范围。 |
| `parameter_scope(context=None)` | 返回该owner绑定的仿真范围；有上下文时取调用者范围与绑定范围交集。不是实机幅度/DAC授权。 |
| `propose(context)` | 产生规则诊断和复测建议；默认主方法未绑定时升级处理，不用辅助方法冒充实现。 |
| `run(runtime, backend, operation_id, procedure_id=None, step_ids=None)` | 默认只执行原主方法。显式选择本owner已绑定辅助程序后，可分派其实例；外部owner前置证据缺失则拒绝，不自动跨owner执行。 |
| `WorkflowSupervisor().run_agent(agent_id, runtime, backend, operation_id, ...)` | 只可分派15个owner之一；其余关键字与 `FlowAgent.run` 一致。 |
| `WorkflowSupervisor().resume_run(runtime, backend, run_id)` | 核查角色分派记录，保持原owner绑定和参数范围，再交给Runtime恢复；绑定改变则拒绝。CLI `resume` 使用此入口。 |
| `WorkflowSupervisor().group_report(summary)` | 将原11程序结果映射到15个owner，保留未执行项；不能据此判断原15步完成。 |

`run-agent ROLE --workspace DIR --operation-id ID [--procedure PROCEDURE] [--step-id STEP ...]` 是公开执行入口。新工作区登记原11个内部StepSpec；已有工作区采用其冻结StepSpec。省略 `--procedure` 时选择原流程主方法；不支持的方法直接拒绝。`s03_rabi_amplitude` 的幅度Rabi、`s07_t2` 的CPMG、`d06_cz_xeb` 的XEB均未绑定，不能默认替换成时长Rabi、Ramsey或CZ套件。

```bash
python3 -m quantum_agents run-agent s01_readout_frequency --workspace /tmp/qcal-owner-new --operation-id readout-1
python3 -m quantum_agents run-agent s02_qubit_frequency --workspace /tmp/qcal-owner-new --operation-id spect-q0-1 --step-id sq_spectroscopy:q0
python3 -m quantum_agents run-agent s03_rabi_amplitude --workspace /tmp/qcal-owner-new --operation-id time-rabi-q0-1 --procedure sq_rabi_time --step-id sq_rabi_time:q0:seed
```

上例仅完成仿真内部程序。返回 `state=partial_completed` 表示所选程序完成且其证据当前有效；所有结果保持 `original_step_complete=false`、`workflow_complete=false`。缺证据或失败不会升级为原流程成功。重复同一操作号复用原运行；主动复测必须用新操作号。若历史证据已失效，重放返回 `partial_evidence_stale`，不偷偷重采。

角色层另在同一SQLite保存 `workflow_dispatches(operation_id,request_json)`，冻结owner、程序选择和绑定。修改选择却复用操作号会冲突。每个内部程序操作号为 `operation_id/agent_id/step_id`。实际读写/依赖必须与绑定完全一致，候选试用受该程序仿真范围约束。多程序分派不是跨节点原子事务；已提交节点不会因后续节点失败自动整体撤销。恢复核查仍由底层Runtime承担，`run-step/resume` 是显式低层兼容工具，不构成新的智能体角色或实机授权。

`twin` 保留原5类11实例运行，另写 `agent_execution_report.json`：`registered_agents=16`、`experiment_agents=15`、15项 `agents`、`observed_owner_count=5`、`physical_procedure_runs=11`、`original_steps_completed=0`、`overall_workflow_complete=false`。每项含 `procedure_runs`、`main_procedure_executed` 和 `original_step_complete=false`；主程序曾执行也仅代表局部仿真绑定。CZ仍为 `source_suite_only`，不能通过run-agent调用未绑定XEB或据其17项通过宣称XEB成功。

## 1. 可运行入口

从工程根目录执行：

```bash
python3 -m quantum_agents.scenario --output /tmp/qcal-scenario-new
python3 -m unittest discover -s tests -p test_runtime.py -v
```

演示目录必须不存在或为空。演示覆盖成功、复测失败后的有界重试、最终回退、重开SQLite后核查回执、同操作号幂等、上游参数变更导致下游证据失效六种情形。输出中的 `data_kind=scenario` 与说明不得删去后称为物理测试。

```python
from quantum_agents.runtime import Budget, Runtime, StepSpec
from quantum_agents.scenario import ScenarioBackend

step = StepSpec(
    id="amplitude", reads=("frequency_hz",), writes=("pi_amplitude",),
    resources=("q0",),
    budget=Budget(shots=1024, max_attempts=2, timeout_s=60,
                  max_total_shots=4096, max_acquisitions=4),
    criteria={"quality": {"min": 0.9, "max": 1.0, "max_stderr": 0.1}},
)
runtime = Runtime("state.sqlite", [step])
runtime.set_parameter("frequency_hz", 5e9, operation_id="seed-frequency")
result = runtime.agents["amplitude"].execute(runtime, ScenarioBackend(), "amplitude-run-1")
runtime.close()
```

此例中的 `quality` 是控制流测试指标，不能解释为量子门保真度。数值后端应提供各实验真实定义的指标、标准误和冻结规则。

## 2. 低层 physical_procedure Python API

| 接口 | 语义 |
| --- | --- |
| `Budget(shots=1024, max_attempts=2, timeout_s=60, total_shots=None, max_total_shots=None, max_acquisitions=None)` | `shots` 为每个采集点的重复数；总量上限在调用前检查。`total_shots` 是兼容别名，与 `max_total_shots` 同时提供时必须一致。省略总量时为 `2*shots*max_attempts`。 |
| `StepSpec(id, depends_on=(), reads=(), writes=(), resources=(), budget=Budget(), criteria={})` | 依赖、参数读写和资源均为字符串元组；同ID契约不可原位更换。空 `writes` 支持只读表征；仍需独立复测。 |
| `Runtime(db_path, steps=(), allow_hardware=False)` | 创建或读取SQLite状态；不会自动执行任何步骤、连接设备或请求模型。 |
| `register(step)` | 持久登记一个步骤；恢复时可直接读数据库中的步骤，无须再传入。 |
| `set_parameter(name, value, operation_id)` | 本地参数初始化或受控外部更新，每次新操作产生新修订，并失效受影响证据；不向设备写参。 |
| `get_parameters()` | 返回 `{name:{value,revision}}`。 |
| `run_step(step_id, backend, operation_id)` | 新操作执行一个节点；同操作号返回既有运行，不自动复测、不重发副作用。不同内容复用同号报冲突。 |
| `run_workflow(backend, operation_id)` | 依赖排序执行登记节点，首个非完成节点即停止；每节点操作号为父号加节点ID。 |
| `resume(run_id, backend)` | 显式恢复：先核查pending操作，再使用已保存回执继续；未知时保留资源隔离。 |
| `get_run(run_id)` / `list_runs()` | 读取运行状态、预算、前置修订、候选、证据和错误。也可用顶层操作号查询。 |
| `evidence(step_id)` | 仅返回当前有效的独立验证证据；失效后返回 `None`。 |
| `events(run_id=None)` / `close()` | 读取顺序追加事件或关闭数据库连接。 |

`criteria` 的结构为 `{metric_name:{min?,max?,max_stderr?}}`。每条规则至少含一项有限实数边界，`min<=max`，标准误上限非负。对应指标必须存在且为有限实数；要求标准误时，`verify.stderr[metric_name]` 必须存在且非负。后端的 `passed=True` 只是必要条件；Runtime还独立检查规则。缺规则或缺关键字段不能提交。

## 3. 后端接口与证据

后端实现以下六个方法：

```python
capabilities(step) -> dict
measure(request) -> dict
apply_trial(request, candidate) -> dict
verify(request, candidate) -> dict
rollback(request, snapshot) -> dict
reconcile(operation_id) -> dict
```

`ExecutionRequest` 包含 `run_id, step_id, attempt, operation_id, shots, parameter_values, parameter_versions, candidate_hash, deadline`。`deadline` 为 Unix 秒；后端应在采集点间协作检查它。`parameter_values` 覆盖本步骤 `reads ∪ writes`；未初始化的写参数值为 `None`、修订为0。恢复快照中的 `None` 表示原先未设置；真实设备上的删除/恢复语义不由本原型猜测。

能力声明示例：

```json
{
  "schema_version": 1,
  "data_kind": "simulated",
  "operations": ["measure", "apply_trial", "verify", "rollback", "reconcile"],
  "readback": true,
  "rollback": true,
  "reconcile": true,
  "acquisition_counts": {"measure": 41, "verify": 41}
}
```

来源只接受 `scenario/simulated/real`，未知或缺必要能力直接拒绝；当前 `real` 始终拒绝。`acquisition_counts` 为每阶段预先固定的点数，默认各1；动态点数扫描需要另行扩展预算协议。Runtime在调用前按点数乘每点shots预扣，并检查总shots与采集点数上限。失联后的预算保持保守预留，不把未知副作用当成未消耗。声明与回包不一致时停止流程，不冒充准确成本。

`measure` 与 `verify` 的共同必要字段：

- `evidence_id`、`acquisition_id`：非空字符串，整个数据库内唯一；同一操作的已持久回执重放可复用，新的尝试和新运行不得复用。
- `data_kind`：必须等于运行创建时的来源。
- `parameter_versions`：必须精确等于请求快照。
- `shots`：正整数，必须等于每点请求数。
- `metrics`：有限实数的字典。
- `acquisition_count`、`total_shots`：单点可省略，扫描必须等于预声明点数及其与shots的乘积。
- 可选 `counts`：同长的0/1/2字符串标签，非负整数计数，总和必须等于 `shots`。扫描证据若提供多点counts，应由后端逐点验证后保存原始证据，不能把整场扫描总和伪装成单点counts。

`measure` 另返回 `candidate`，键必须精确等于步骤 `writes`，值为有限实数；只读步骤使用空字典。`verify` 另返回 `passed` 布尔值和 `candidate_hash`，后者必须等于 `runtime.candidate_hash(candidate)`。标准误字典为 `stderr`。验证必须有新的采集与证据编号，选参数据不能重复充当验收数据。

`apply_trial` 返回 `{"status":"applied","actual_values":candidate}`。`rollback` 返回 `{"status":"restored","actual_values":snapshot}`。值不一致或不能确认时进入核查状态，不宣称应用/恢复成功。

`reconcile` 仅返回三种结果：已完成及原回执 `{"status":"completed","result":...}`；独立确认未执行 `{"status":"not_started"}`；无法确认 `{"status":"unknown"}`。只有明确未执行才允许同号继续调用。不得因为客户端超时就把操作说成未执行。Scenario后端只在内存保存回执，换新后端实例后无法证明旧副作用，因此返回unknown；这是一项故意保留的诚实限制。

## 4. 状态与持久化保证

运行终态区分 `completed`、`failed`、`rolled_back`；`running` 为执行中，`needs_reconciliation` 表示不确定且仍隔离资源。`rolled_back` 只表示试验未通过且已按契约恢复，不能显示为校准成功。恢复失败、部分应用、未知回执、试验中的参数冲突不会释放资源。重复顶层操作号只查询原结果；主动复测使用新号。

SQLite采用WAL及同步写盘。参数修订、有效证据、提交终态、提交事件在同一事务中保存；事务失败不留下半个正式版本。调用后端前先保存pending意图。崩溃后先查询该操作，不能仅凭日志末行重发。事件按自增序号排序，并另存墙钟时间。事件表用于本地可恢复证据，不是已实现的网络outbox投递服务。

每个逻辑资源在SQLite中只有一个占用运行。同一run另有本机执行者声明，活动进程存在时拒绝并发resume；确认旧进程已退出后方可接管。PID存在性只用于本机互斥，不是跨机器租约或硬件fencing。PID复用可能保守阻塞，需要人工核查，不能强制覆盖仍不明的执行者。

每个参数有独立修订。参数改变会失效直接读写该参数的既有证据，以及依赖它的全部下游证据。提交前再次核对参数修订及依赖证据ID。检测到试验期间基准变化时进入核查，不盲目恢复过期快照。状态库是本地控制平面的权威记录，不能绕过API直接修改数据库后仍要求其自动识别所有篡改。

## 5. 验收范围与未覆盖项

控制平面测试直接检查数据库参数、修订、证据有效性、资源锁和后端动作次数，不能只看函数返回的success。已覆盖：正常提交、fresh独立复测、有界失败重测、回退无新版本、关键证据缺失、NaN、计数和错误、旧修订、历史采集复用、未知能力、实际阈值检查、缺profile、资源互斥、子进程硬退出、pending核查、回执丢失不重复apply、未知状态保隔离、上游传递失效、事务中断及保守成本控制。

本原型不实现用户身份/鉴权、跨机器租约、硬件fencing、真实停止/排空、跨仪器恢复或生产级数据库迁移。`timeout_s` 是新增采集/应用/提交的检查门槛，不能抢占阻塞的后端Python调用；后端必须使用有限调用时长并协作检查deadline。恢复调用在预算截止后仍可执行，但这里没有实机恢复窗口和租约，因此禁止真实设备模式。能力声明来自受信任的适配器代码，不是从不可信模型文本接受设备权限。

`verify.passed` 与数值metrics仍由后端提供；控制平面独立执行冻结阈值、完整性、独立性及版本检查，物理量计算的正确性需要数值适配器自己的独立oracle。这里不会把控制流演示通过称为物理模型或真实实验通过。当前只提供同步工作流、查询与显式恢复，没有声称已实现完整前端HTTP/SSE协议。
