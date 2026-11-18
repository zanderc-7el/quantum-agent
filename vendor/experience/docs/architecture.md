# 原流程15步：总体设计与共享合同

本版只注册15个原图实验智能体及1个总控。每个实验智能体内部保留采集、拟合、偏差分析、候选调整和新数据重测；44种细分方法是procedure，公共支持模块是服务。角色边界见[原图重组](reorganization.md)，每种方法的唯一primary owner见[归属索引](../knowledge/procedure_ownership.md)。本文包含目标协议和当前实现的明确对照，设备事务、租约和真实测量能力须逐项验收，不能由文档或角色名称推定已实现。

## 1. 职责与权限

| 实体 | 输入与职责 | 可输出的动作 | 权限边界 |
| --- | --- | --- | --- |
| `supervisor`，唯一总控 | 原15步目标、全局依赖、资源、参数版本和总预算 | 分配步骤合同、跨步骤回测、提交/回退请求、停止/升级 | 不替专项Agent猜物理原因；不扩大设备范围 |
| 15个实验智能体 | 原图一个功能框及其内部方法，当前观测与局部预算 | 扫描计划、采集请求、拟合/偏差诊断、参数候选、fresh重测及步骤报告 | 对自己步骤负责；不能绕过全局版本、共享资源和发布门禁 |
| 能力路由公共模块 | 已验证能力清单、owner/procedure映射、资源拓扑 | 判断能否执行及选择适配器 | 未绑定主方法不得用已绑定辅助冒充 |
| 数据/拟合与证据公共模块 | 原始数据、模型/分类版本、残差与来源 | 数据有效性、拟合诊断、证据存取、成本 | 不以success代替物理适配，不补造缺测项 |
| 参数注册公共模块 | 候选、父依赖、验证、当前revision | CAS登记、失效传播和历史查询 | 新旧工作点不得静默混用 |
| 独立验收公共模块 | 冻结准则、完整观测、新验证集 | 通过、拒绝、不确定、无效尝试 | 不参与选点；不拥有任意硬件权限 |
| 确定性执行与调度 | 已批准的具体procedure合同、资源锁、操作ID | 编译、采集、试用、查询、恢复及受门禁约束的发布 | 不靠模型猜SDK、不把租约过期等同设备停止 |

公共模块不是五个新增智能体。模型进程数量也不由16个角色数决定；同一受限模型服务可逐任务加载不同角色提示词。科学判断由实验Agent提出，发布判定由公共门禁独立计算。

模型只做受约束的规划与解释。相同模型可以承担多个角色，但权限、数据视图和结果 ID 必须分开。模型建议进入确定性校验器；新增实验类型、扩大参数范围或申请设备权限必须经过部署策略规定的授权。模型输出、附件文字和检索结果都不能自行晋升权限。

## 1A. 每个实验智能体内部的闭环

```text
接收原步骤目标 → 选内部procedure/核前提 → 冻结扫描和准则
→ 采集 → 数据与拟合诊断 → 判断偏差 → 受限调参候选
→ 试用读回 → 新数据复测 → 局部报告或有界再试
→ 总控处理跨步骤影响、提交/回退与下一原步骤
```

步骤内可使用多个方法，例如s03结合幅度扫描、重复门放大、DRAG和时序诊断；它们共同服务于Rabi定幅步骤，不再各自变成独立角色。原主方法未绑定、上游参数过期或局部预算不足时，步骤报告必须标缺口并回总控，不能随意选择一个易通过的辅助方法结束。

跨步骤借用方法保留`caller_agent_id`、`procedure_id`和`primary_owner_id`。例如s02请求带符号Ramsey诊断：方法归属s07，频率候选仍需按当前任务的参数范围审核，s03随后复核旋转角；同一采集只计一次成本。primary owner是方法维护/解释归属，不是所有输出参数的永久写入权。当前CLI通过声明该方法的owner执行，跨owner自动编排仍须由宿主明确映射。

## 2. 任务合同 TaskContract v1

以下是目标逻辑字段，不宣称与任何设备 SDK 同名。序列化映射以工程包实际 schema 为准；字段无法映射时拒绝执行并报告缺项。

| 字段组 | 必须记录的内容 | 拒绝条件 |
| --- | --- | --- |
| 身份 | schema_version、task_id、run_id、parent_task_id、attempt、agent_id、procedure_id、primary_owner_id、caller_agent_id、purpose | 复用 ID 却改变请求内容 |
| 对象 | backend_id、device/chip_id、qubit_ids、coupler_ids、working_point_id | 对象不在授权范围，或硬件能力未知 |
| 前提 | expected_config_revision、上游 parameter_ids/versions、固件/编译器版本、readout_version | 依赖已过期、版本不匹配、设备读回不同 |
| 扫描 | 自变量名/单位/实际点列、固定参数、序列模板哈希、shots、顺序/随机化、复位条件 | 非有限值、量纲错误、越界、混叠约束不满足 |
| 模型 | 模型方程/实现版本、适用域、初值/边界、符号约定、拟合质量规则 | 初值不可行、模型不支持观测或扫描 |
| 准则 | required_checks、目标容差、区间规则、缺失处理、独立验证设计、失效传播表 | 缺必需项；阈值在看过结果后被静默改写 |
| 预算 | 最大 shots/请求数/机时/轮次、恢复预留、截止时刻、单步最大变化 | 验证和恢复预留不足；成本未知而无法保守约束 |
| 执行 | resource_ids、锁模式、operation_id、幂等域、停止策略、允许状态转换 | 缺资源围栏或动作不在允许集合 |
| 输出 | evidence_root、保留策略、数据 schema、决策枚举、报告位置 | 输出不可追溯或与其他运行目录混用 |

`purpose` 至少区分 discover、fit、select、verify、monitor、rollback_check。验证数据须有新的 acquisition_id；对同一请求的重放返回原回执，不计作新独立验证。

专项 Agent 的 `CandidateProposal` 包含：基准 revision、绝对目标值及单位、变化量、依据记录 ID、局部斜率及区间（若据此选方向）、信任域、预测影响、需失效节点、验证计划与预算。目标必须是绝对值，避免重试时重复叠加修正。

## 3. 观测与证据协议

每次采集返回 ObservationEnvelope：owner/procedure/runtime_step_id关联、请求 ID、内容哈希、开始/结束时刻、请求与实际 shots、目标及读回配置版本、量化后控制值、结果 schema、原始对象地址及哈希、设备状态、分类/滤波版本、复位和测量窗条件。返回状态区分 accepted、running、completed、failed、cancelled、unknown；accepted 不等于完成。

计数数据必须满足整数、非负、标签集合正确、总数与实际有效 shots 一致。缺失标签的含义由 schema 规定为零或错误，不能每个分析器自行猜测。部分 shots 必须显式标记；不能把 requested_shots 当作已完成分母。IQ 若只有平均值，不能据此声称已具备单次判别训练集。

每轮证据链为：冻结合同 → 参数/资源快照 → 编译及量化回执 → 采集 → 数据有效性 → 拟合与残差 → 候选 → 试用回执 → 新验证集 → 独立判定 → 提交或回退回执。日志追加写；报告引用原始记录，不覆盖旧轮次。`decision.json` 保存 required/observed/missing 检查 ID 及每项理由，缺项一律不通过。

ParameterRecord 应保存 value、unit、uncertainty_method、interval、measured_at、working_point、parents、source_observations、validation_id、config_revision、status。参数状态为 candidate、valid、stale、invalid、unknown；只更改驱动频率也会使依赖旧频率的幅度/相位结果 stale。有效期不是统一固定一周，需依据漂移数据和用途设定。

## 4. 总控状态机

设计状态名用于说明语义，运行时若使用更少状态，必须有明确映射。

```text
NEW → PREFLIGHT → READY → ACQUIRING → ANALYZING → PROPOSING
PROPOSING → APPLYING → VERIFYING → DECIDING
DECIDING → COMMITTING → COMMITTED
DECIDING → ROLLING_BACK → REJECTED_SAFE → READY（预算允许）
任一设备变更阶段 → RECONCILING → 恢复原阶段或 UNKNOWN_ISOLATED
只读/数据阶段 → FAILED_VALID / INVALID_INFRA / ESCALATED
```

| 转换 | 必须满足的条件 | 持久化顺序 |
| --- | --- | --- |
| PREFLIGHT→READY | 能力、依赖、范围、预算、准则齐全；拿到资源租约 | 冻结合同和快照后才下发 |
| READY→ACQUIRING | 请求版本与设备读回相符 | 先写 request intent，再提交；按 ID 查询 |
| ANALYZING→PROPOSING | 数据/模型质量通过；方向有依据 | 保存完整分析及失败候选 |
| PROPOSING→APPLYING | 候选范围及受影响节点已校验；恢复预算充足 | 持久化基准、目标、operation_id，再试用 |
| APPLYING→VERIFYING | 实际生效版本和量化值确认 | 保存 applied receipt；采集全新验证数据 |
| DECIDING→COMMITTING | 全部 required_checks 通过、依赖未变、证据仍新鲜 | 写 commit intent；设备/注册表 CAS；读回 |
| DECIDING→ROLLING_BACK | 验证拒绝、不确定或试用取消 | 写 rollback intent；恢复绝对基准值；读回 |
| RECONCILING→UNKNOWN_ISOLATED | 无法确认操作是否执行或当前值 | 隔离目标与资源，停止新调参，升级责任人 |

COMMITTED 仅表示本次规定目标已通过并提交；不能意味着永久有效。FAILED_VALID 是实际取得有效数据后的不达标；INVALID_INFRA 是测试开始前环境不成立。发生过物理 shots 后数据损坏属于 DATA_INVALID，成本仍计入，不能归零为“未运行”。

## 5. 共享资源与调度

锁的粒度由能力清单给出：qubit、coupler、readout_bus、ADC、AWG/IQ_pair、LO_group、flux_line、trigger_domain。共享 readout 总线默认互斥；只有验证了并发模式、频分分配及串扰预算后，才允许兼容租约。不同比特也可能共享 AWG、触发或耦合器，不得仅按比特编号并行。

调度器先展开物理资源闭包，再按固定资源 ID 顺序原子取得全部锁，避免等待中持有部分资源。租约包含 owner、expiry、fencing_token；所有变更请求携带 token，旧 token 被执行服务拒绝。租约过期后先 cancel/query/drain/readback，确认设备静止才转交；取消失败进入隔离。配置快照读锁与写锁分开，分析已冻结数据不必占设备锁。

参数依赖失效和资源互斥是两张图：没有资源冲突的任务也可能依赖过期参数。提交时必须再次检查两张图，不沿用启动时检查。完整矩阵见[参数依赖](../knowledge/parameter_dependencies.md)。

## 6. 从设计进入实现

先实现模拟/重放的合同校验、完整性门禁和证据重算，再实现故障注入与恢复；最后逐条绑定实际 SDK。候选试用、状态读回、独立验证、提交与恢复缺一项时，该后端只开放只读/离线模式。15个实验Agent使用同一合同；内部方法、模型和诊断分支不同，但不能复制出互不兼容的提交逻辑。步骤是否完成按原主目标判定，不能按内部方法数量或任意一次成功计数。

## 7. 当前运行时与目标协议的对应

已实现接口以[运行时源码](../quantum_agents/runtime.py)为准。StepSpec使用`id/depends_on/reads/writes/resources/budget/criteria`；ExecutionRequest使用`run_id/step_id/attempt/operation_id/shots/parameter_values/parameter_versions/candidate_hash/deadline`。初始化reads通过Runtime.set_parameter，执行通过StepAgent.execute或Runtime.run_step；依赖流程使用run_workflow。返回证据和事件可通过evidence/events/get_run查询。

| 目标要求 | 当前实现 | 保留边界 |
| --- | --- | --- |
| 持久合同与版本 | SQLite保存StepSpec、参数revision、运行、操作回执、证据与事件；父版本改变使证据失效 | 尚无完整工作点/固件/原始对象retention字段体系 |
| 事务状态机 | `state=running/completed/failed/rolled_back/needs_reconciliation`，`phase=measure/apply_trial/verify/rollback/committed/restored`细化阶段 | 上文大写状态为设计语义，不能当当前API枚举传入 |
| 有限预算 | Budget的shots/max_attempts/timeout_s/total_shots或max_total_shots/max_acquisitions；按声明采集数预扣 | 未实现所有设备机时/恢复成本类型；阻塞SDK需协作截止或隔离执行 |
| 冻结指标 | criteria支持min/max/max_stderr，必需指标缺失拒绝；空criteria不能发布 | min/max是指标界，不自动生成统计CI或多重比较控制，需分析器提供已定义指标 |
| fresh验证 | 选参与验证ID不同、candidate_hash一致、参数版本一致 | 新ID不能单独证明统计独立；后端必须实际生成新观测 |
| 资源互斥 | 同一SQLite内按resource字符串互斥，活动PID阻止同run并发恢复 | 不是跨设备租约/围栏协议；拓扑闭包和过期输出排空待实现 |
| 参数发布 | 验证通过后SQLite原子更新参数revision及有效证据；试用/回退由后端回执确认 | 未绑定实机commit，文件式模拟回执不保证物理恰好一次 |
| 来源隔离 | scenario为控制流fixture；simulated为物理模拟；real被运行时拒绝 | 即使allow_hardware=True，当前版本仍拒绝real |

目前[物理适配器](../quantum_agents/twin.py)绑定单腔谱、单比特光谱、时间Rabi、T1及Ramsey。流程为公共腔谱后，对q0/q1各执行粗频谱→Rabi种子脉宽→Ramsey精频率与T2记录→新频率下重新Rabi→T1记录，共11步。每步另采fresh验证；Rabi还实际下发候选脉宽的0、π/2、π、2π四个点复测布居。该检查覆盖旋转人口，并未完成完整门过程、DRAG或RB/XEB验收。T1/T2写入的是表征记录，不能通过写记录改变物理退相干率。

[CZ套件桥接](../quantum_agents/cz_suite.py)可运行原三能级套件，并补充2方向条件相位、4输入九输出布居表、9测量基和17个具名检查的完整性门禁。它仍是`source_suite_only`，未实现每个双比特Agent的原子恢复；其依赖隐藏通道的评分只作模拟诊断。原twin源码保持原样，原评分API的缺项漏洞与桥接防护必须分别报告。

五类绑定归到s01/s02/s03/s06/s07的内部能力，分组报告保留各方法证据；当前文件名为`agent_execution_report.json`，列出全部15个原步骤（包括未执行者）、procedure_runs、main_procedure_executed和original_step_complete；overall_workflow_complete保持false。所有原步骤original_step_complete仍为false。s03时长Rabi不等于幅度主任务，s07Ramsey不等于CPMG，CZ套件不等于d06 XEB。其余方法仍是合同与待绑定能力。WorkflowSupervisor按owner分派，低层StepSpec只代表具体执行实例；模型顾问输出经schema和范围审查的proposal，发布权仍在Runtime。

依据：[最新架构资料](../resources/latest_architecture/)、[实验手册](../resources/source_materials/06_工作脚本与数据/cal_manual_d0.md)、[既有闭环经验](../resources/prior_knowledge/)、[模拟器边界](../resources/twin_source/docs/model_traceability.md)。资料中的接口名称仅用于核对能力，具体绑定见[适配协议](hardware_adapter.md)。
