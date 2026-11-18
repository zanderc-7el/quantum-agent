# 旧版文档：README.md
# 量子测控智能体工程包

本包把单双比特实验拆成专责角色，提供逐步操作卡、提示词、机器可读注册表与总控运行器。总控管理参数修订、步骤依赖、独立复测、发布、回退和恢复。打开根目录 `index.html` 可以浏览角色、流程、资料和实现边界。

## 三种能力层次

- **完整角色资料**：注册表与 `agents/` 覆盖原流程和手册的小步骤。每个角色都有固定量/扫描量、测量序列、分析、诊断、调参、复测、预算与来源。
- **已实现可运行控制平面**：SQLite持久状态、确定性调度、资源互斥、幂等、参数版本、失效传播、临时应用后fresh验证、失败回退、未知回执核查、有界成本；模型建议另受结构约束。
- **已绑定的物理执行器**：quantam虚拟后端中的单腔S21、比特能谱、Rabi时长、T1、双正交Ramsey五类原子步骤；另有完整CZ套件桥。其它角色有完整操作合同与提示词，执行器尚待绑定。真正设备模式当前拒绝执行。不能把角色数量当成已接通设备的实验数量。

## 快速开始

Python 3.10–3.12 可运行标准库控制平面；物理验证采用 Python 3.12.14、NumPy 1.26.4、SciPy 1.13.1。无后台服务、无默认远程模型请求。

```bash
# 在本目录运行；Windows使用python，macOS/Linux可用python3。
python3 -m quantum_agents list
python3 -m quantum_agents show sq_rabi_time
python3 -m quantum_agents plan
python3 -m quantum_agents demo --output /tmp/qcal-scenario-new
python3 -m unittest discover -s tests -v

# 物理后端依赖；建议在新虚拟环境安装。
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-twin.txt
.venv/bin/python -m quantum_agents twin --output /tmp/qcal-twin-new --qubits 0,1
.venv/bin/python -m quantum_agents cz --output /tmp/qcal-cz-new
```

`demo`是故障注入控制流演示，不是物理仿真。`twin`新运行真实数值演化、采样与拟合；同目录同operation_id重复执行只返回旧回执，不重复测量。主动复测须指定新operation_id，参数变动会失效下游证据。后端seed、配置、源码或数值环境改变时，原目录拒绝继续复用，须新建实验目录。`cz`是原套件整体运行，要求空目录，尚未支持逐CZ子步骤事务恢复。

## 人工和模型如何使用

每个 `agents/<id>/` 保存角色操作卡和提示词；`config/agent_catalog.json` 是分派索引。调用者为所选agent提供当前参数、版本、测量证据、预算与失败标签。模型输出结构化建议，总控按冻结合同检查后分派已绑定执行器。`propose`命令本身只生成建议，不改参数。详见 [模型接口](docs/model_interface.md)。

```bash
python3 -m quantum_agents propose sq_rabi_time --context examples/advisor_context.json
python3 -m quantum_agents status --db /tmp/qcal-twin-new/runtime.sqlite
python3 -m quantum_agents run-step sq_rabi_time:q0:final --workspace /tmp/qcal-twin-new --operation-id fresh-rabi-002
```

独立物理oracle默认读取包中本次验收数据，也可指定新运行：

```bash
QCAL_TWIN_OUTPUT=/tmp/qcal-twin-new python3 -m unittest discover -s tests -p test_independent.py -v
python3 scripts/verify_sources.py
```

## 资料入口

- [上手与人工重测](docs/usage_walkthrough.md)、[已实现绑定的精确范围](config/bindings.json)、[来源与授权说明](resources/SOURCE_NOTES.md)。
- [总体架构](docs/architecture.md)、[总控与重测](docs/supervisor_policy.md)、[运行器精确接口](docs/runtime_contract.md)。
- [实验覆盖映射](docs/experiment_coverage.md)、[硬件适配合同](docs/hardware_adapter.md)、[验收设计](docs/validation_strategy.md)。
- `knowledge/`：偏差诊断、参数依赖、具体重测案例、已知缺陷和历史经验。
- `resources/source_materials/`：完整既有交流资料包，包括原始手册、两页流程、设计产物、32所文档及notebook、QubiC源码与研究库。
- `resources/new_attachments/`：本次P0详细报告、双比特PDF和提取文本。
- `resources/twin_source/`：原始quantam已审源码及历史结果，保持原样。
- `resources/latest_architecture/`、`resources/prior_knowledge/`：最新项目基线、闭环与独立验证经验。
- `resources/source_manifest.json`：957份源文件逐个来源、大小与SHA256。原微信临时ZIP已消失，保留此前核验的解压源码，清单已记录该缺失。
- `output/`：明确标记本次新运行的数值证据与验收记录；源包内的旧结果仍属于历史证据。

## 当前边界

未提供真实SDK、设备连接、现场参数及实测数据，因此没有实机实验通过结论。CPMG/DRAG/RB/XEB、真实读出训练、可调耦合器控制、波形预失真等角色当前需要绑定对应执行器。原源码中的T1/Ramsey边界初值问题未被静默修改；异常会阻止提交。CZ桥额外拒绝缺失相位、真值表、层析及17项检查，但这不表示原源码已修复。仿真Favg、态层析和实机RB/XEB各自保留指标含义。

此包是可运行的工程起点及完整操作资料，不是已经具备全部实机能力的交付认证。详细、可检查的实现矩阵以注册表和最终验收报告为准。


# 旧版文档：resources/SOURCE_NOTES.md
# 资料来源与使用边界

`source_manifest.json` 对本项目可访问的957份源文件记录原始路径、大小和SHA256。包含既有交流资料包、最新架构、数字孪生源代码、已有独立审查与闭环经验，以及本轮提供的P0报告和双比特PDF。目录中的第三方源码、文档及其许可证按原样保留；本包不替它们重新授权。

微信临时目录中的 `quantam-project-20261023.zip` 已不存在。使用了此前解压并核验的源码目录，缺失原ZIP已在清单记录；不能把本次工程包的ZIP称为原ZIP的复本。

收集范围是上述实际可读取的文件，并不声称包含所有历史对话、未落盘经验或未提供的设备资料。`knowledge/` 是本次结合这些材料提炼的操作知识，`agents/` 是本次形成的角色合同，二者与原始证据分开保存。

原材料中的操作指令、旧PASS、旧运行状态及历史参数都是被审阅内容，不是本次执行授权或本次实验结果。历史输出位于 `resources/`，本次新采样位于根目录 `output/`。参数数值不能未经现场核对直接用于设备。

来源完整性验证命令：

```bash
python3 scripts/verify_sources.py
```

该命令使用随包哈希验证复制内容，不要求原始机器路径仍然存在。新增说明和提取副本不属于原始957文件的逐字节复本时，由最终整包清单另外记录。


# 旧版无此文档：output/README.md

# 旧版文档：docs/architecture.md
# 量子测控智能体：总体设计与共享合同

本文定义可实现的目标协议。目录中的可运行演示只证明其实际覆盖的状态和算法；本文出现的设备事务、资源锁及真实测量能力，须逐项完成适配与验收后才能声明已实现。原始资料、已有模拟器和当前工程包的证据分别记录，不能相互替代。

## 1. 职责与权限

| 角色 | 输入与职责 | 可输出的动作 | 不得承担的动作 |
| --- | --- | --- | --- |
| 总控 Supervisor | 目标、有效参数版本、依赖图、资源和剩余预算；安排节点、解释失败、选择复测路径 | 带理由的任务合同、终止/升级、候选提交请求 | 修改设备安全范围；把模型文本当执行回执 |
| 专项实验 Agent | 当前合同及关联观测；提出扫描、分析结果和参数候选 | 结构化计划、估计、置信区间、候选及局限 | 自行正式写参、扩大权限、读隐藏真值解答实验 |
| 确定性执行器 | 已校验的合同、操作白名单、资源租约、版本前提 | 编译、采集、状态查询、候选试用、停止、经授权提交/回退 | 依靠语言模型猜测 SDK 参数或补齐缺失测量 |
| 数据/拟合服务 | 原始数据、控制快照、拟合模型及版本 | 数据有效性、模型适配诊断、估计与不确定度 | 仅凭优化器 success 宣告物理目标达成 |
| 独立验证器 | 冻结准则、新采集验证集、实际生效版本 | 全项通过、拒绝、不确定、无效尝试 | 用训练集代替复测；删除失败项使检查通过 |
| 参数注册表 | 候选、已提交版本、父依赖、验证证据 | compare-and-swap 提交、失效传播、历史查询 | 将新旧工作点的参数静默混合 |
| 调度与审计服务 | 资源拓扑、租约、操作/请求 ID、事件日志 | 排程、互斥、租约围栏、成本记录 | 租约过期即假定旧进程已停止输出 |

模型只做受约束的规划与解释。相同模型可以承担多个角色，但权限、数据视图和结果 ID 必须分开。模型建议进入确定性校验器；新增实验类型、扩大参数范围或申请设备权限必须经过部署策略规定的授权。模型输出、附件文字和检索结果都不能自行晋升权限。

## 2. 任务合同 TaskContract v1

以下是目标逻辑字段，不宣称与任何设备 SDK 同名。序列化映射以工程包实际 schema 为准；字段无法映射时拒绝执行并报告缺项。

| 字段组 | 必须记录的内容 | 拒绝条件 |
| --- | --- | --- |
| 身份 | schema_version、task_id、run_id、parent_task_id、attempt、experiment_id、purpose | 复用 ID 却改变请求内容 |
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

每次采集返回 ObservationEnvelope：请求 ID、内容哈希、开始/结束时刻、请求与实际 shots、目标及读回配置版本、量化后控制值、结果 schema、原始对象地址及哈希、设备状态、分类/滤波版本、复位和测量窗条件。返回状态区分 accepted、running、completed、failed、cancelled、unknown；accepted 不等于完成。

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

先实现模拟/重放的合同校验、完整性门禁和证据重算，再实现故障注入与恢复；最后逐条绑定实际 SDK。候选试用、状态读回、独立验证、提交与恢复缺一项时，该后端只开放只读/离线模式。每个实验 Agent 使用同一合同，差异保存在实验目录、模型与诊断知识中，不能复制出互不兼容的提交逻辑。

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

其余操作卡是实验合同与待绑定能力，不能因目录中存在卡片就声明可运行。默认StepAgent是确定性调用基线；模型顾问输出经schema和范围审查的proposal，发布权仍在Runtime。

依据：[最新架构资料](../resources/latest_architecture/)、[实验手册](../resources/source_materials/06_工作脚本与数据/cal_manual_d0.md)、[既有闭环经验](../resources/prior_knowledge/)、[模拟器边界](../resources/twin_source/docs/model_traceability.md)。资料中的接口名称仅用于核对能力，具体绑定见[适配协议](hardware_adapter.md)。


# 旧版文档：docs/supervisor_policy.md
# 总控决策与复测策略

此文规定总控可选择的动作及条件。默认采取最小、可验证的参数变化；每次动作有证据、成本和后续验证。研究资料提供候选解释，不能覆盖当前观测，也不能使模型建议自动成为写参权限。

## 1. 选择下一步的确定性顺序

1. 查实际设备状态与依赖版本；存在 unknown 时先核对状态。
2. 验证数据完整性、计数分母、单位、时间轴和请求关联。
3. 检查模型适用域、可识别性、残差结构和参数相关性。
4. 区分统计不确定性、持续偏差、参数漂移和硬件故障。
5. 根据下面决策表选择 repeat、more_shots、redesign_scan、retest_upstream、propose、stop 或 escalate。
6. 检查剩余预算；为新验证、回退和读回保留额度，再执行。

| 观测 | 分类与确认方法 | 允许动作 | 不合理的动作 |
| --- | --- | --- | --- |
| 提交超时、回执丢失 | DEVICE/UNKNOWN；按 operation_id 查询、读回 | reconcile；确认未执行后才重试 | 直接再次加同一修正 |
| 总计数不符、缺 basis、NaN、轴重复 | DATA_INVALID；核原始文件及实际 shots | 修数据接口后重新采集；保留失败数据 | 插零补齐必需观测、用 success 放行 |
| 少量随机误差使区间跨门槛 | STATISTICAL；控制稳定、残差无结构、增加样本有信息收益 | more_shots，按预设批次追加新数据 | 无限加 shots；事后移动门槛 |
| 拟合撞边、峰在扫描外、不到一周期、多峰别名 | FIT/IDENTIFIABILITY | 改范围/网格/两正交/先验，冻结新合同 | 不改变扫描而盲目重复 |
| 拟合收敛但残差周期性、两种频率、系统偏差 | PHYSICAL_MODEL | 诊断、扩充候选模型或回上游复核 | 用更多 shots 强化错误模型 |
| 同一冻结配置重复测量相互矛盾 | DRIFT_OR_NONSTATIONARY | 插入参考点、交错顺序、分时拟合、查环境 | 把不同时间窗口直接合并成更窄区间 |
| 相位/幅度误差相关，读出或工作点已变 | UPSTREAM_STALE | 按依赖矩阵回上游，随后重测下游 | 把旧校准当新工作点前提 |
| 无法定位或同一原因连续失败 | UNRESOLVED | 到达预算上限后停机隔离或升级 | 无上限“再试一次” |

repeat 是在同一冻结控制下取得新的重复观测，用于检验稳定性；重放同一 request_id 是重复读取旧结果。more_shots 是增加统计信息；其收益取决于噪声模型。redesign_scan 改变信息结构，需要新合同版本，原结果不得从报告中删除。

## 2. 单次任务预算

预算包含采集、拟合、选择、验证、监控、失败尝试和恢复。机时按设备实际占用与等待分别记账；并行步骤不能简单相加成总墙钟。shot_cost 的 executed、discarded、replayed、unknown 四类分开；unknown 不得视为零，按可执行上界预留。

离线教学策略示例（不是设备标准）：最大候选轮数 3，同一失败原因最多 2 次，最多 2 次扩扫，每点 shots 只能按预先给出的 256→1024→4096 批次递增，恢复/状态核对预留不少于事先估算的恢复成本。真实设备需按单次采集成本、复位时间和安全范围重新配置；不要套用这些数字作为验收规格。

停止/升级条件：达到任一成本上限；连续两轮改进不超过预定最小效果且区间重叠；局部斜率无法辨别符号；候选贴近范围边界；依赖版本变化；验证集多次失效；设备状态不明；停止命令未确认；目标无法在可用模型/仪器范围内实现。预算耗尽输出 INCONCLUSIVE_BUDGET 或 REJECTED_SAFE，不能输出 passed。

## 3. 参数调整与方向

对观测偏差 y、可调参数 u，先在允许信任域做 u−h、u、u+h 的交错测量。局部斜率 s≈[y(u+h)−y(u−h)]/(2h)。只有斜率区间不跨零、两侧数据有效且没有相位分支跳变，才可用 Δu≈−y/s。随后限制步幅并重新测量。控制映射、极性和编译器相位约定不同，不能凭“相位偏大就增幅”或经验图像决定方向。

频率修正也要固定符号：若 fitted_detuning 定义为 fq−fdrive，则候选 fdrive_new=fdrive_old+fitted_detuning；若分析量采用相反约定，修正号随之改变。保存观测量、模型方程和一次已知符号注入测试。幅度、DRAG、CZ失谐、Zshape、时序各有不同误差响应，见[单比特诊断](../knowledge/single_qubit_diagnostics.md)和[双比特诊断](../knowledge/two_qubit_diagnostics.md)。

## 4. fresh independent 验证与提交

冻结验证准则后，以独立 acquisition_id 在已读回的候选 revision 下采集；验证集不参与选点或优化。若看过验证结果后调整候选，该验证集退为开发数据，下一候选必须重新采集。有限 shots 的不确定结论不得等同失败设备，也不得等同通过。

验证通过后再次检查父依赖和资源围栏，用 expected_revision 提交绝对参数快照；写入发生但回执丢失时，查询操作记录并读回，不重复创建提交。设备已生效、注册表未完成时进入 RECONCILING，根据同一事务意图收敛。回退采用记录的绝对基准值；回退请求成功但读回不符时，状态是 UNKNOWN_ISOLATED，不能写“已恢复”。

验证拒绝时保留候选、成本和所有失败项。恢复基准并确认后，决定是否下一轮；参数注册表只发布验证通过的整组版本或明确的局部提交集合。影响的下游参数全部标 stale。提交后短时参考测量失败，撤销有效状态并启动新事务；历史通过记录仍保留。

## 5. 漂移维护与共享经验

监控基准必须来自最近一次独立验证及其配置；没有 accepted baseline 时返回 NO_REFERENCE，不能宣告稳定。预先规定监控时间、统计量、告警规则及多次检测的错误预算。触发只说明“与参考不一致”，先排查读出/环境/上游变更，再选择校准路径。不能从一次未触发推导一周有效期。

经验条目保存适用器件/工作点、观测、候选解释、排除证据、动作、变化方向依据、复测结果、失效条件、来源及反例。失败案例与成功案例同等保留；新设备上只能作为待检验假设。总控报告每次状态应包含当前时间、状态、最近速度、预计剩余时间、是否正常；无法测量写 N/A。

完整示例见[worked cases](../knowledge/worked_cases.md)。


# 旧版文档：docs/hardware_adapter.md
# 硬件适配协议与事务边界

下面的操作是本工程的抽象协议建议，**不是32所或任何现有设备的真实 SDK/API**。材料中的 CorrectF01ByPhase、DragAlphaCal、RB1QCal 等只用于定位实验能力。具体模块、签名、授权、单位和自动写参行为均须绑定后验证。

## 0. 当前本地Backend接口

当前Python Protocol包含`capabilities(step)`、`measure(request)`、`apply_trial(request,candidate)`、`verify(request,candidate)`、`rollback(request,snapshot)`、`reconcile(operation_id)`。这六项是本包真实代码接口；后文细分的编译、查询、设备commit等属于待扩展协议。

capabilities返回schema_version=1、data_kind、operations、readback/rollback/reconcile布尔能力以及`acquisition_counts={measure:N,verify:M}`。两阶段采集成本分别是N×request.shots和M×request.shots。扫描证据要包含acquisition_count、total_shots与预声明一致；多点原始计数保存在后端扫描文件，顶层counts如提供则表示其声明的单个shots分母，不能塞入全扫描聚合计数后误用分母。

measure/verify公共证据字段为evidence_id、acquisition_id、data_kind、parameter_versions、shots、metrics。measure返回精确匹配StepSpec.writes的candidate；verify返回布尔passed及匹配请求的candidate_hash，并可返回stderr。apply_trial回执是`status=applied,actual_values=candidate`；rollback是`status=restored,actual_values=snapshot`。reconcile返回completed及原result、not_started或unknown。当前控制器可验证回执内容，但硬件真实性仍需设备适配验收。

ExecutionRequest.deadline是Unix秒截止时刻。Runtime在调用前后检查，物理模拟器逐扫描点协作检查；这属于软截止，无法抢占阻塞的原子SDK调用。真实适配需要超时取消、查询与输出排空能力，不能用Python函数超时代表设备已停止。

parameter_versions表示任务启动时冻结的注册表版本；候选试用通过candidate_hash及apply回执关联，提交成功才递增注册表revision。它不是独立的实机actuation revision，接入真实设备前必须增加实际生效版本和读回证明。当前运行时拒绝所有real后端，不提供解除该限制的使用步骤。

## 1. 能力登记

后端登记 backend_id、transport、device/chip/firmware/compiler_version、支持的实验与结果 schema、通道拓扑、范围/分辨率、时基/触发、复位条件、共享资源、查询和停止能力。另行声明：候选试用是否持久、读取当前配置是否可信、幂等键保留窗口、操作历史保留期、提交是否原子、回退是否可验证。

每项能力状态为 UNBOUND、DOCUMENTED、CODE_INSPECTED、OFFLINE_TESTED、HARDWARE_VERIFIED；不得由接口名或文档自动提升。设备范围与权限来自部署策略，模型不能补齐。含有自动写配置副作用的旧实验，要么包装成可恢复事务，要么先关闭写入并验证；不可当作纯采集函数调用。

## 2. 建议逻辑操作

| 操作 | 关键请求 | 成功证据及失败语义 |
| --- | --- | --- |
| describe_capabilities | backend_id、版本要求 | 机器可读能力及资源拓扑，不代表设备已就绪 |
| read_state | 对象、freshness_limit | 当前 revision、读回值、设备状态、时刻；缓存应标明 |
| validate_compile | 合同、模板哈希、控制/范围 | 实际量化序列、单位、资源、总时长/shot成本估计；不能执行 |
| submit_acquisition | request_id、request_hash、expected_revision、fencing_token | 作业 ID 与 accepted；完成需另查 |
| query/fetch | job_id/request_id、内容签名 | 原结果或当前状态；重复 fetch 不产生新 shots |
| cancel_and_drain | job_id、资源、停止原因 | 取消接收与输出排空分别确认；未知时隔离 |
| apply_candidate | operation_id、绝对目标、expected_revision、租约 | actual_values、new_revision、设备持久化回执 |
| commit_candidate | transaction_id、验证ID、候选revision、父依赖 | 一次提交的版本回执；不能将新结果套给旧版本 |
| rollback | transaction_id、原绝对快照、expected_revision | 读回一致及恢复 revision；冲突时不能覆盖他人新值 |
| query_operation | operation_id、原内容哈希 | 未执行/已执行/失败/未知，支持超时恢复 |

提交参数时区分 requested_value 与 quantized_value。拟合和复测必须使用 actual/quantized 值；若量化使候选超出科学容差，拒绝或重设计。Hz、s、rad 是逻辑公共单位，DAC code、电压和采样点只在经验证的映射处转换。

## 3. 回执丢失处理

先把 intent 和原快照持久化，再执行。超时后禁止再次发送累加修正。执行器先查询同一 operation_id；确认已执行且读回一致则继续；确认未执行才允许同 ID 同内容重试；结果矛盾或查询能力不足则隔离。事务发生中租约丢失时，旧执行者不能继续变更，新的执行者先做状态核对。

不能用文件系统原子 rename 推导真实设备“恰好执行一次”。真实测量触发、设备持久化、客户端日志通常不是同一事务。没有设备侧去重/查询证据时，重复 acquisition 可能产生额外 shots，必须记录 unknown_physical_cost 或上界；恢复后重新采集应使用新 ID 并保留原因。

COMMITTING 崩溃后可能是“设备已提交、控制器未记完成”；恢复应读取操作回执并完成登记，不能重复增加 calibration_version。ROLLING_BACK 崩溃同理。若实际状态与候选、基准都不一致，禁止强行覆盖，报告 CONFIG_CONFLICT/UNKNOWN_ISOLATED。

## 4. 适配验收清单

1. 在测试后端验证所有单位、符号、量化边界、非有限值、越界、错误对象与版本拒绝。
2. 验证 submit→query→fetch、重复读回不重采、重复ID异内容拒绝、部分shots、丢包和取消语义。
3. 在写参前后、提交前后、回退中断处注入故障；重启后只接受读回和操作历史支撑的状态。
4. 两个任务竞争同一 readout/AWG/coupler，确保不可并发；租约过期旧请求被围栏拒绝，旧波形停止后才能转交。
5. 检查 raw IQ/counts、分类器/积分权重版本、实际控制、时间戳和成本均可关联；不能只有截图或拟合图。
6. 仪器操作验收须由具备权限的设备责任团队绑定授权范围；本包离线/虚拟验收不替代此步骤。

未来可先实现只读扩展：读取历史数据、拟合与提出候选，并用例如 `execution_mode=offline` 标记，禁用 apply/commit/rollback。该字段与模式尚未接入当前Runtime；当前Backend仍须声明apply_trial、rollback、readback等完整能力，不能直接据此构造只读后端。“未调用硬件”不能被包装成硬件运行成功。

## 5. 待绑定记录模板

每项保存：材料名称/节号、目标软件版本、真实入口（待确认时为 null）、输入单位、输出schema、自动写参副作用、资源、查询/取消、幂等语义、验证命令、证据位置、责任人、绑定状态。真实入口未确认时明确写 UNBOUND；不要编造 import 路径或 SDK 方法以补齐表格。

来源：[手册](../resources/source_materials/06_工作脚本与数据/cal_manual_d0.md)、[最新架构](../resources/latest_architecture/)、[既有持久闭环与恢复经验](../resources/prior_knowledge/)。


# 旧版文档：docs/validation_strategy.md
# 验证策略：数据、物理与事务分别判定

每个实验开始前冻结目标、必须检查的集合、阈值、采样和预算。所有数值例子仅用于离线开发与解释，均非设备标准。优化器成功、脚本退出零、图像好看或文件存在，都不能替代以下门禁。

## 1. 三种结果必须分开

| 结果层 | 必须回答的问题 | 可接受的证据 |
| --- | --- | --- |
| 执行/恢复正确 | 计划是否执行在指定对象/版本；重试是否重复写入；回退是否确认 | 回执、读回、状态事件、故障注入和成本 |
| 科学判定有效 | 观测是否足以支持参数与目标；模型残差能否解释 | 原始数据、诊断、区间、独立验证、缺项门禁 |
| 器件性能达标 | 指定工作点下独立性能实验是否通过 | 设备实测及明确指标定义；模拟真值只评模拟 |

流程正确但候选拒绝仍是有效实验结果；不得为了“完成”删除拒绝轮次。测试开始前基础设施无效和实际测试不达标分别统计。报告所有随机种子、失败、耗费和未知成本，禁止事后选种子或调整阈值制造通过。

## 2. 数据与拟合门禁

先校验对象、版本、必需基、实际 shots、标签、单位、轴顺序、有限值、缺失和重复请求。拟合需检查可行初值、边界命中、Jacobian条件/参数相关性、可识别性、残差量级与结构、别名分支、对不同初值/拟合窗口的敏感性。区间方法必须注明是局部协方差、bootstrap、频率网格后验还是其他方法；局部标准误不覆盖模型失配。

二项数据可使用已声明的似然或 deviance；低计数、边界概率、参数从同一数据估计、多次自适应选择时，简单卡方近似的误报率不能当作精确保证。必要时按冻结模型做参数 bootstrap 校准。残差出现周期结构或多频时判 MODEL_MISMATCH，不用增加 shots 掩盖。

## 3. 区间判据和选点偏差

对 k/n，优先保存 k、n 及区间方法。Wilson 双侧区间可用中心 `(p+z²/(2n))/(1+z²/n)`、半宽 `z*sqrt(p(1-p)/n+z²/(4n²))/(1+z²/n)`；精确 Clopper–Pearson 或预声明贝叶斯区间也可，但不得混称同一覆盖概率。零事件也不等于零错误：二项独立假设下 k=0 的单侧95%上界为 `1−0.05^(1/n)`。

最小保真度准则要求预定下置信界超过阈值，最大泄漏准则要求上置信界低于阈值；点估计通过而区间跨界返回 INCONCLUSIVE。比率、相关量、层析保真度和拟合参数不能直接套一个二项区间；按其联合观测传播不确定度，并保存每个测量设置的样本数。

从很多候选中选最小泄漏或最高保真度会产生选择偏差。开发集用于拟合和选点，新独立验证集用于接受。若同时声明多个门/多个条件全部达标，应预先规定 family-wise 或其他错误控制；简单保守方案可按检查数量分配 alpha。若重复查看并追加 shots，使用预设有限批次及 alpha spending 或适用的序贯方法；不得每批都用同一95%门槛反复尝试直到通过。

## 4. 完整性与独立性

预先列 required_check_ids，然后验证 `set(observed)==set(required)`，拒绝缺失、重复及未知检查。双比特 twin 的现有17项仅适用于那套明确协议，不是所有CZ任务的普遍常数；其17项含4项参数误差、2项条件相位、8项四基态泄漏/正确率、门保真度、图态保真度、平均泄漏。两个相位或四个基态缺失必须失败。详细旧源码缺陷见[已知限制](../knowledge/known_limitations.md)。

验证集的 request_id、时间、candidate revision 必须与选点集分离。独立 evaluator 从最终序列化记录重新计算指标和决定；不能只调用待测 `score()` 来证明 `score()` 正确。虚拟设备可另存隐藏真值供 oracle，实验 Agent 和控制器看不到该数据。真实设备没有已知真值时，使用已校准参考、误差放大、交叉实验及适合的基准；不捏造 truth 字段。

## 5. 漂移、保留与重放

随机化或交错采集顺序、插入参考点，并保存时间。若窗口内非平稳，应分段评估并重新定义有效窗口；把漂移数据合并会造成虚假的精度。fresh 指在相关控制与有效窗口下的新观测，单纯新文件名不够。监控时间间隔和有效期基于设备数据及用途；历史“约一周”经验不能设成硬保证。

保留内容包括原始IQ/计数、有效/丢弃标签、控制请求与实际量化波形、分类器/积分权重、拟合模型与依赖版本、候选及全部拒绝、验证集、事务回执、故障和成本。根据项目容量/隐私/设备策略设定 retention_class、expiry、归档位置和哈希；原始数据到期后仍应保留允许保留的摘要与删除事件，禁止伪称可精确重放。示例保留天数不得当成统一要求。

## 6. 工程验收矩阵

| 测试 | 独立 oracle | 必须观察的失败 |
| --- | --- | --- |
| 合同/schema | 固定合法与错误fixture，读取序列化结果 | 缺字段、错单位、版本冲突、空必需检查、NaN |
| 物理/拟合 | 解析极限、已知算符/Kraus、独立数据生成 | 多频、短扫描、边界、别名、错模型仍success |
| 成本/重放 | 原始事件求和与设备回执 | 失败shots漏计、重放重复收费、unknown记零 |
| 提交/恢复 | 独立进程在持久边界中断后读回 | 重复叠加、重复commit、未确认rollback写成功 |
| 调度 | 两执行者竞争共享资源 | 仅按qubit并发、失效租约继续发波形 |
| 完整流程 | 新目录、新实例、冻结合同与数据重算 | 依赖旧READY、缓存、旧日志、污染输出 |

验收至少两次干净目录运行和一次同目录重复；同目录行为应明确为安全拒绝、幂等返回或显式新运行，不能静默覆盖。静态文档检查覆盖相对资源链接、字段一致、状态映射和“已实现/待实现”边界；服务存活等不适用项需写理由。冻结交付后保存哈希；最后检查没有后台写入、遗留锁或持续变更。

验收报告分别列 PASS、FAIL、INCONCLUSIVE、INVALID、NOT_IMPLEMENTED，不用一个总的 success 隐藏未实现项。当前工程包能运行的范围以实际测试报告为准。来源：[既有验证与恢复经验](../resources/prior_knowledge/)、[模型追溯](../resources/twin_source/docs/model_traceability.md)。

## 7. 当前包的验收范围

Runtime已执行的门禁是schema、来源、版本、ID、candidate hash、有限指标、计数一致性、冻结min/max/max_stderr与后端passed联合判定。它尚未自动实现Wilson/Clopper–Pearson、alpha spending、模型残差自相关或设备漂移统计。应用这些规则时应由特定分析器输出定义明确的区间端点/诊断指标，并加入required criteria和独立oracle。不能把存在本设计文档写成统计方法已经集成。

scenario测试证明控制流分支；simulated测试证明指定物理模型和算法；real路径当前禁止。两次冷运行与重复运行分别对实际交付的演示/适配器执行，不能用scenario通过替代物理拟合验收。硬件SDK、租约围栏、通用DRAG/CZ实机闭环仍作为待绑定事项。

## 本包当前证据的解释

单比特当前流程运行11步并各自另采验证，共22份扫描；Rabi每次验证增加4个候选脉宽布居点。次数与shots以本次输出及独立oracle为准，不能把历史文件存在当作新运行成功。候选布居与扫描一致性只是本模拟profile的验收，不能推出完整单门保真度。

CZ桥接对原套件结果执行具名全项完整性校验，包括原评分函数遗漏风险的空列表/空表情形；原源码未修改。新增门禁仍需分别报告计算所得通道指标与采样观测指标。2786次采集、16,869,376 shots和17项通过对应本次套件结果，不代表144线路过程层析或实机XEB已执行。后续改变配置、源码、随机种子或数值环境应使用新冻结运行目录。


# 旧版文档：docs/usage_walkthrough.md
# 从操作卡到一次可追溯实验

## 先选择你要用的层次

打开 `index.html` 查找实验，例如 Rabi、Ramsey、ZZ 或 CZ。角色卡写的是实验合同，`execution_status` 写的是当前代码能力。50个角色并不需要50个模型进程；同一个模型服务可按角色加载不同提示词和当前实验上下文。

只有 `config/bindings.json` 中的五类原子步骤能直接使用本包的事务式物理执行器。两比特 CZ 另有整套仿真入口。其余角色可用于人工实验指导和模型建议，但还需要专门编写并验证执行器，不能仅改一个状态标签就视为已经接通。

## 运行一次单双比特仿真

在包根目录创建虚拟环境，安装 `requirements-twin.txt`。运行：

```bash
python3 -m quantum_agents twin --output /tmp/qcal-first --qubits 0,1
python3 -m quantum_agents status --db /tmp/qcal-first/runtime.sqlite
```

这一条链包含单腔扫描，以及每个比特的能谱、初始Rabi、双正交Ramsey、更新频率后Rabi复测、T1，共11个步骤。每一步都分别采集选择候选和验证候选的数据；Rabi验证额外施加0、半π、π、2π四个候选时长。原始采样、拟合、回执、参数修订与事件保存在同一工作目录。

先看 `twin_summary.json` 中11个状态是否全部为 `completed`，再看 `valid_evidence` 是否全部为真。`backend/scans/` 保存原始IQ或计数和请求；`backend/receipts/` 保存操作回执；`runtime.sqlite` 保存步骤合同和参数版本。不要只看命令退出码或一张曲线。

```bash
QCAL_TWIN_OUTPUT=/tmp/qcal-first python3 -m unittest discover -s tests -p test_independent.py -v
```

该独立校验器从已保存的原始计数重新恢复观测、重算曲线和残差，核对候选参数、Rabi实际施加时长、参数修订及锁释放。它没有使用适配器的验收函数。

## 同一实验与主动重测

完全相同的工作目录、参数身份和 `operation_id` 表示重取同一次操作结果，不会重复采样。真正重测必须使用新ID：

```bash
python3 -m quantum_agents run-step sq_rabi_time:q0:final --workspace /tmp/qcal-first --operation-id q0-rabi-retest-002
python3 -m quantum_agents status --db /tmp/qcal-first/runtime.sqlite
python3 -m quantum_agents run-step sq_t1:q0 --workspace /tmp/qcal-first --operation-id q0-t1-after-rabi-002
```

Rabi产生新参数修订后，下游T1旧证据会失效。总控保留旧证据用于追溯，但不把它作为当前通过依据。工作目录的seed、配置、源码或数值环境发生变化时，冻结身份检查会拒绝复用；新开目录，不能为了绕过检查而删除清单。

查看较完整的角色级影响分析：

```bash
python3 -m quantum_agents plan --changed q0.frequency_hz
python3 -m quantum_agents plan --domain two_qubit
```

这是规划输出。角色级抽象参数会保守展开受影响实验；实例级实际失效和运行顺序由工作区的 `StepSpec` 决定。角色列表中存在某项，不意味着运行器会自动执行尚未绑定的步骤。

## 让某个模型角色分析一次偏差

从 `examples/advisor_context.json` 复制上下文模板，填入本轮真实证据ID、失败现象、剩余尝试和可建议参数范围，再执行：

```bash
python3 -m quantum_agents propose sq_ramsey --context my-context.json --provider rules
```

规则模式用于检查接口和既定决策，不会产生模型推理。使用 `--provider openai` 时，需自行配置明确的模型和API凭据，客户端才会发起请求。输出仍只是建议；人工或宿主程序需要把它与已绑定执行合同对应。当前没有把模型建议自动转换为任意扫描、任意代码或仪器动作的模块。

上下文中的允许参数与证据清单是可信宿主提供的约束，不是鉴权机制。不要直接采用模型自己写出的“允许范围”。要接入其他模型服务，保持同一结构化建议协议，并补做拒绝、截断、幻觉证据、单位、越界和预算测试。

## 失败时怎么走

无新数据、计数不完整、参数修订不一致、拟合不合法、验收指标缺失或超限，均不能发布。回执不明时进入核查，不盲目重发。选择点过少、扫描区间不合适或原源码初值异常，应形成新的扫描合同后重测；当前冻结合同不会由模型自动改写。候选应用后验证失败，按快照回退；回退无法确认则保留隔离状态。

`python3 -m quantum_agents demo --output /tmp/qcal-failure-demo` 展示重测、回退、重启恢复、幂等和上游变更。它是控制流故障注入场景，不能充当物理实验通过证据。具体诊断见 `knowledge/` 和每个角色的 `retest_rules`。

## 两比特套件与设备接入

```bash
python3 -m quantum_agents cz --output /tmp/qcal-cz-first
```

该命令对原两比特数值套件做完整性保护并重新采样，保存相位、泄漏、真值表、层析等证据；17项检查必须完整。它没有逐CZ子步骤的事务恢复，也没有验证实机RB/XEB。

真实设备需要在 `docs/hardware_adapter.md` 所列接口下补充通道映射、单位、时钟/触发、仪器回读、波形提交、采集和可核查的回退。当前真机路径明确拒绝执行；没有仅靠提供IP即可启用的开关。


# 旧版文档：docs/build_record.md
# 构建与验收记录

本轮交付对象为可运行控制平面、5类原子数值绑定、CZ整套数值桥、44个实验角色和6个总控/支持角色，以及能够实际读取的源资料。真实设备接入、所有实验执行器、线上模型推理效果不属于已验证能力。

## 已完成的独立检查

1. 操作卡和注册表：两次新目录生成及同目录重复生成，102份核心产物逐字节一致；依赖图无环，29个手册历史函数名有对应角色。具体记录见 `agents/catalog_acceptance.json`。
2. 文档：原理、偏差诊断、参数联动、总控策略与接口核对；117项静态检查通过，记录见 `knowledge/qa/README.md`。该记录只证明文档范围内的检查，不代替运行测试。
3. 控制平面与集成：45项测试涵盖参数版本、fresh验证、资源锁、预算、计数、拒绝越权建议、回退、真实子进程退出后的恢复、依赖传播及后端冻结身份。机器记录见 `output/integration_acceptance.json`。
4. 数值证据：重新运行11步链与CZ套件；独立校验器从序列化原始数据重算观测和公式，并核对候选参数及来源。具体路径见 `output/README.md`。
5. 最终发行：通过 `scripts/validate_archive.py` 对冻结ZIP执行两次独立解压、数值链、CZ套件、单元测试、场景演示及重复操作验收。最终结论只以ZIP旁的验收JSON为准。

## 本轮发现与处理

- 原微信临时ZIP缺失：明确保留缺失记录，使用之前核验过的解压源码，不伪造原ZIP。
- CPMG卡片的拟合模型：去掉直接套用Ramsey静态失谐振荡项的默认假设。
- Rabi读出启动依赖：允许校验后的原始IQ代理入口，解除分类器训练与Rabi准备互相等待的问题；当前数值绑定仍是固定读出代理。
- 候选验证容差：在验证前由候选冻结，不能根据验证结果放宽；Rabi额外实际施加四个候选时长。
- 原CZ评分函数对缺项的接受风险：桥层强制检查两相位、四输入、九层析基、17项指标和原始计数；原源码保持原样。
- 集成测试首次从父目录启动：崩溃恢复子进程因导入路径失败，属于该子测试开始前的基础设施无效尝试；已修入口工作目录，重新运行45项全部通过，未将首次失败计为通过。
- GraphLoop初始规格字段和任务时限校验未通过：在执行节点前修正为该引擎支持的字段与时限，不属于实验结果。

已知原源码T1/Ramsey边界初值与模型适用性问题保留在 `knowledge/known_limitations.md`。未因常规配置下通过而将这些缺陷宣称为已修复。

## 可复跑性边界

最终数值验收使用已安装的Python 3.12.14、NumPy 1.26.4和SciPy 1.13.1。两次冷运行是新的解压工作目录和新采样目录，复用同一依赖解释器；不是从空操作系统重新下载、构建依赖的验收。没有常驻服务、端口或后台模型调用需要保活，运行结束检查本轮子进程均退出。


# 旧版无此文档：docs/reorganization.md

# 旧版文档：knowledge/parameter_dependencies.md
# 参数依赖与复测矩阵

本表是保守的初始依赖策略，实际设备通过对照实验后可以缩小影响集合。材料中的实验顺序是起点；不能证明所有依赖已穷尽。每次变更先区分“数字驱动设定改变”与“芯片物理工作点改变”，后者通常影响更广。

## 1. 依赖记录

节点记录 `reads={parameter_id:version}`、`writes`、工作点、控制模板/分类器版本。提交上游参数后，所有直接依赖及传递依赖进入 stale；依赖边带原因与复核实验。图必须无环；实际迭代通过新版本/new attempt 表达，不能用环形依赖绕过前提。只要祖先 stale，下游不能作为本轮有效基线。

| 被改变的参数 | 直接需复核 | 进一步需复核/失效 | 说明与可缩小范围的条件 |
| --- | --- | --- | --- |
| 驱动频率/数字相位参考 fdrive | 双正交Ramsey残余失谐；π/π/2幅度/相位 | DRAG相关相位、单门基准、涉及该比特的CZ局域Z与图态分析 | 纯数字改变不必自动重测器件T1；若表明物理频率漂移，追加工作点/相干复核 |
| 物理 fq 或 idle DC flux | 频谱/非谐性、读出腔频/判别、Rabi、Ramsey、T1/T2、flux→fq局部映射 | DRAG、门基准、相邻频碰撞、ZZ、交换、CZ chevron/相位/泄漏/virtual Z | 频率改变可能改变谱、相干和多比特条件，不能只更新一个f01字段 |
| XY幅度或π门时长/包络 | 旋转角、π/2比例、相位误差、泄漏 | 读出训练态、T1/Ramsey制备分析可靠性、RB、CZ条件相位和层析SPAM | 先检验小幅修正的制备/分析；并非每次微调都从头搜索腔谱 |
| DRAG系数/导数分量 | 脉冲面积、相位误差、|2⟩泄漏 | 频率/幅度交叉检查、单门基准、CZ分析脉冲相关结果 | DRAG与失谐/幅度可能相关；不能只看某点P1最大 |
| readout频率/功率/窗/积分权重/分类器 | 独立准备集混淆矩阵、赋值错误、泄漏误判、复位相关性 | 所有用旧分类器的概率拟合、条件相位/层析与门基准读出解释 | 原始IQ完整时可按新分类器重处理开发数据；仍需新验证数据，重处理不能称独立采集 |
| qubit flux映射/偏置/补偿矩阵 | fq(Φ)局部斜率、相邻比特交叉响应、相干、读出 | 单门参数、idle ZZ、交换、CZ工作点、局域Z | 映射版本变化须记录当前分支与迟滞条件；禁止跨分支外推 |
| coupler偏置或开关映射 | 相关比特频移、有效g、idle/dressed ZZ、邻居影响 | 双比特工作点、CZ时间/相位/泄漏、并行门测试、受影响单门参数 | 当前twin中的coupling_scale不是实机映射，不能直接等价 |
| Zshape/脉冲上升沿/下降沿/预失真/幅宽 | 实际量化波形、回归时间、条件相位、泄漏、局域相位 | CZ完整基态表/独立门基准、邻居串扰、依赖时序 | 同面积不保证同动力学；矩形模型的最优点不能原样沿用 |
| XY–Z、readout–XY或跨通道 timing | 相对时序、触发/量化、重叠、恢复/清空 | 脉冲面积与相位、制备读出、CZ泄漏/相位、并行实验 | 只改变公共起点且确认所有相对关系不变可缩小；需有证据 |
| virtual Z/frame更新 | 固定约定下的已知旋转与下游分析 | 所有用该frame编译的后续混合门/层析 | 局域Z不能修复非局域条件相位、残余交换或泄漏 |

## 2. 执行依赖与物理依赖

设备资源图决定能否同时执行；参数依赖图决定结果能否使用。两个没有公共仪器通道的节点，若共享同一个读出标定版本，读出变更仍使两者失效。反过来，两个物理参数独立的实验，也可能争用同一AWG或readout总线。

复测计划分为强制项目和观察项目。强制项目未完成时参数不得恢复valid；观察项目用于检测尚未证实的弱耦合。缩小强制集合需记录局部对照数据、范围及审批策略，不能由模型仅凭语义相近删边。

## 3. 最小复测路径示例

数字频率修正：新频率读回 → fresh双正交Ramsey → 旋转角/相位检查 → 需时DRAG联检 → 单门基准 → 受影响CZ局域相位与SPAM复核。物理flux工作点移动：fq/读出/相干 → 单门校准 → idle ZZ/交换 → CZ粗细调 → 条件相位/泄漏/局域Z → 独立门评估。若上游验证失败，后续测量保留为诊断，不能正式提交为该工作点有效参数。

来源：[手册§1.1–2.5、单比特精校、耦合器与两比特门](../resources/source_materials/06_工作脚本与数据/cal_manual_d0.md)、[最新架构第4–5章](../resources/latest_architecture/report.md)、[既有研究经验](../resources/prior_knowledge/deep_study/)。


# 旧版文档：knowledge/single_qubit_diagnostics.md
# 单比特偏差诊断知识

每条知识给出观测、可检验解释、下一实验与禁止推断。拟合误差、统计误差、物理模型误差和控制执行误差分开记录。下面方程使用Hz、s、rad；DAC code与电压需要另行标定。

## 1. 谐振腔与频谱

当前twin的归一化notch模型为 `S21=1−d/[1+2i(f−fr)/κ]`，κ是该模型的全线宽，不可把半宽直接填入κ。真实复数背景、线延迟和失配不在该简式中；复数残差转圈、基线倾斜、扫频方向不一致时，先检查归一化/频率轴/功率依赖，再决定扩模型。只用幅度极小值忽略相位可能使参数相关性增大。

光谱峰未覆盖：移动或扩大扫描；多个峰：检查读出态、功率、非谐跃迁/碰撞及控制映射。不得自动把最大峰当目标跃迁。有限脉冲谱可以不是Lorentzian；只有与扫描条件相符的模型才可解释宽度。峰中心恢复成功不等于T2已由线宽精确测得。

## 2. Rabi、幅度和π/2

在共振、弱驱动且增益线性的理想极限，`P1=sin²(π fR t)`，`tπ=1/(2 fR)`。存在失谐时布居频率与失谐共同决定，不能将全部频率变快都解释为幅度偏大。先用Ramsey约束失谐，再检验幅度与旋转角的局部响应。

固定脉冲形状与时长的线性近似可给 `Anew≈Aold·θtarget/θestimated`，但必须用邻近幅度点或误差放大实测斜率验证。π门与π/2门不应永久假定完全相同比例；包络截断、DRAG、失谐或非线性改变后分别复核。时间Rabi拟合出的衰减参数不是直接T1或T2。

多频/拍频、振幅大幅变化、拟合残差有结构：拒绝单频success作为校准通过，检查频谱、串扰、激发更高能级及采样别名。对比度低可能是读出、制备、退相干、失谐或扫描选择，不能直接提高驱动功率。

## 3. Ramsey及频率方向

必须记录双正交定义。twin使用 `z=(2Px−1)−i(2Py−1)≈C exp(−t/T2+i2πδt)`，δ=fq−fdrive。按此约定驱动更新为 fdrive+δ；改变Y脉冲极性或复数构造会反号。先用已知正负失谐注入测试验证符号。

均匀网格只确定频率模采样率；小残差和极小局部stderr不能排除别名。约束总失谐，不只约束人为offset；必要时用粗谱先验、不同时间间距、非均匀补点或短时扫描分辨分支。相位unwrap前检查低对比度、π跳变与时间排序；不要对失去相干的数据强行拟合相位直线。

拟合到边界/FFT初值不可行是数值配置问题；不得据此宣布器件没有相干。已确认的原twin初值问题见[已知限制](known_limitations.md)。不同T2窗口给出不同结果可能提示非指数噪声、漂移或SPAM；增shots只能减少统计部分，不能修正这些偏差。

## 4. T1、T2与相干性

单指数T1模型 `P1=B+A exp(−t/T1)` 需要有效制备、可解释基线和足够时间覆盖。扫描太短会造成B/A/T1强相关；可扩大等待范围和插入长时基线点。短扫描在数学上仍可能可识别，不能用坏初值自动拒绝。振荡型残差、非单调或多时间尺度时先诊断模型失配。

在所选独立Markov模型内 `1/T2=1/(2T1)+γφ`、γφ≥0，因此T2≤2T1。测得两者来自不同时间/工作点或不同模型时，不能先裁剪T2再假装符合；检查区间、漂移和实验定义。Ramsey T2*、echo/CPMG相干时间不能混作同一指标。

T1/T2性能变差通常不是某个驱动幅度可直接“补好”的校准误差。优先确认制备/读出、工作点和稳定性；保持物理性能测量与控制参数优化职责分离。

## 5. DRAG与相位

记录实际波形约定，例如 `Q(t)=β dI/dt` 或带归一化因子的形式。β的单位随约定变化，材料中的drag_alpha数值不能跨SDK复制。DRAG扫描需观察误差放大相位、旋转角与|2⟩泄漏，不以单一P1最大值替代所有目标。

相位误差与失谐、幅度、脉冲边沿可能耦合。先取得局部斜率/联合响应矩阵，再在信任域内调整。若响应矩阵病态，设计额外诊断实验，不用大步逆矩阵更新。修改DRAG后重验频率、幅度及门基准。

## 6. 读出、时序与门基准

独立准备|0⟩/|1⟩/需要时|2⟩，训练与验证按shot或采集批次拆分，防止同一记录同时训练并验收。保存混淆矩阵、准备误差假设和测量窗；分类器准确率与重复读出一致率不同，不能由一致率推出赋值正确率。平均IQ无法恢复单shot分布。

XY–Z或readout–XY延迟变更会影响重叠、测量窗和复位。扫描两个方向及参考点，检查触发量化、漂移和延迟极性；时延符号由实际通道定义确定。

RB常用 `A p^m+B` 描述对应协议的衰减；把p换算成平均错误率依赖维数与噪声/协议假设。每Clifford错误率、每物理门错误率、交错RB和XEB结果不能无条件互换。拟合可信区间、随机序列间方差和拟合区间敏感性都要报告；不以一条高P0短序列证明高门保真度。

来源：[手册§1、§2及单比特门精细校准](../resources/source_materials/06_工作脚本与数据/cal_manual_d0.md)、[twin公式追溯](../resources/twin_source/docs/model_traceability.md)、[研究知识](../resources/prior_knowledge/deep_study/)。


# 旧版文档：knowledge/two_qubit_diagnostics.md
# 双比特偏差诊断知识

## 1. 前提和坐标

记录两个比特的单门/读出版本、idle工作点、耦合器映射、相对时序、控制模板和当前frame。参数必须属于当前工作点。twin采用qubit0在张量积左侧、计算索引(0,1,3,4)，Hz进入H/ℏ乘2π。其他后端若采用不同顺序，适配器必须显式置换并用已知态测试。

两态、三态Duffing和显式耦合器模型支持不同问题。三能级能表示|2⟩泄漏，但不含所有更高态。历史截断反例及适用范围见[已知限制](known_limitations.md)，不能用数学归一化通过证明模型适用于任意强驱动。

## 2. 交换与chevron

共振弱耗散的单激发交换有 `P01=sin²(2πg t)`，布居频率为2|g|，半交换时间为1/(8|g|)。失谐改变频率与对比度；仅从布居振荡不能恢复g的符号，也不能把失谐贡献当成g增加。

孤立|11⟩–|02⟩近似的矩阵元为√2g，`P02≈B+A·8g²/(Δ²+8g²)·sin²[π√(Δ²+8g²)t]`，共振首次返回时间1/(2√2|g|)。P11高也可能是零时长、关门、失谐过大或高阶返回，需结合条件相位、对比度、全部泄漏和单激发错误。短扫描chevron的有效crossing包含额外ZZ、其他能级位移和频率先验误差；不能直接视为精确裸非谐性。

条纹不对称/拟合差：检查扫描范围、Δ定义、上游频率、耦合/flux映射、脉冲边沿和时间量化。先定位是哪一项被数据支持，再改扫描或上游；增加shots不消除错误模型。

## 3. 条件相位与virtual Z

定义 `φcond=θ11−θ10−θ01+θ00`，目标π模2π；误差为wrap(φcond−π)，不能直接用相位差跨越±π断点。由相同X+iY约定的两控制分支测 `arg(z1 z0*)`。低相干时角度误差很大，应报告相干幅度和相位区间，拒绝低幅度相位看似达标的候选。

改变幅度/失谐/时长时，条件相位方向取决于局部响应。先采两侧点、unwrap局部连续分支并估斜率；不存在通用“相位不足就增大Z幅度”的规则。幅度-时长联合优化时记录固定量，防止两者同时改变导致无法归因。

twin测得局域φ后以 `exp(−iφn)` 补偿，下游分析相位 `phase_ideal+φ` 与其驱动约定相容。虚Z能消除局域相位，不能修复条件相位、残余交换、泄漏和耗散。新的混合门编译路径必须验证frame传播，不因单次层析符号测试通过而推导通用编译器正确。

## 4. ZZ与工作点

`ζ=(E11−E10−E01+E00)/h`，相位随等待按所用观测约定取号。twin的X+iY等待相位为−2πζt，因此两控制分支频差取负得到ζ。必须用符号fixture确认，不仅比较绝对值。

测两个预设耦合强度的ζ属于表征，不等于搜索出ZZ最小工作点。搜索需要当前点及邻域响应、单门频移/相干复核、目标函数和独立验证。twin的residual_zz是在显式交换dressing之外附加的项；未来填入总实测ZZ时必须避免重复计数。

谱学dressed-state重叠分配在避交叉附近可能不唯一；当前0.7重叠门槛是工程拒绝规则。拒绝时应报告无法可靠指认，不能返回某个看似平滑的裸态ZZ。条件Ramsey还需控制分支制备、相干和读出稳定，两个频率区间的差要传播协方差或独立假设。

## 5. 泄漏、态层析与门保真度

`Lcounts=含非计算态计数/全部有效shots`；`1−P11`还包含计算子空间内错误，不能称作泄漏。平均输入泄漏、最坏基态泄漏和某一准备态的泄漏不是同一量。存在分类错误时counts给出分类后的泄漏估计；零事件要报告上界。

线性Pauli层析在理想分析测量下重构计算块，全部shots作分母且对|2⟩零延拓；有限分析脉冲和分类错误使结果含SPAM。负特征值可能来自统计或模型/测量失配，既不能直接宣称状态不物理，也不能静默裁剪后报告提升后的保真度。保存原线性结果及任何后处理方法。

投影后非保迹通道的平均门保真度需保留 `Tr Ecomp(I)`；对d=4的CZ，分母d(d+1)=20，不能把生存项固定为4或除以生存率后忽略泄漏。图态保真度、三关联Bell witness、完整平均门保真度、RB/XEB属于不同指标。只测一个输入态不能完整判定通道。

## 6. 诊断分支

| 表现 | 优先确认 | 可做的下一实验 |
| --- | --- | --- |
| 返回概率高而相位错误 | 零/高阶返回、关门、失谐、符号 | 首次非零返回窗口+条件Ramsey |
| 相位正确而泄漏高 | 波形边沿/时序/工作点/高态 | 局部幅宽二维扫描+新泄漏验证 |
| 单基态正确但门分数低 | 残余交换、局域相位、相干损失 | 四基态表、双分支相位、独立门基准 |
| 图态比模型门分数差 | SPAM与制备/分析、读出、模型偏差 | gate-off参考+独立单门/读出检查 |
| ZZ或相位随顺序变化 | 漂移、加热、复位/读出相关 | 交错参考、倒序/随机顺序、分时分析 |
| 上游变更后CZ最优点迁移 | 依赖版本和工作点 | 依矩阵重测，不直接沿用旧最优点 |

来源：[手册耦合器与两比特门章节](../resources/source_materials/06_工作脚本与数据/cal_manual_d0.md)、[two_qubit_physics](../resources/twin_source/docs/two_qubit_physics.md)、[逐项物理审计](../resources/twin_source/docs/audit/two_qubit_physics.json)。


# 旧版文档：knowledge/worked_cases.md
# 两个完整复测案例

以下为设计演练用的合成观测和决策示例，**没有实际设备执行，也不是本工程运行日志**。阈值、计数、轮次和预算为解释协议而设，非设备标准。实现者应将其转换为fixture测试；不能复制数字为某台设备的通过记录。

## A. 单比特频率偏移后的幅度联检

目标：q0在工作点W0下残余失谐的95%区间完整落入±5 kHz，旋转角/相位与规定单门基准通过。基准revision=17，频率fdrive=5.000000 GHz；readout=ro7、XY模板=pulse4、幅度=0.200。冻结最大3轮、总shots预算、验证和恢复预留；锁住q0、AWG/IQ0、LO0、readout_bus0。

1. **PREFLIGHT。**读回revision17及上游版本。确认谱先验将总失谐限制在当前采样无别名区间；验证集与开发集使用不同请求ID。若ro7已过期，先读出复核，不能直接调频。
2. **采集/诊断。**双正交Ramsey开发数据给δ=+40 kHz、95%区间[28,52] kHz，残差与声明噪声一致；已知符号fixture确认δ=fq−fdrive。方向成立，但不确定性仍大；先按合同允许的额外一批shots缩小估计区间，不能把同一数据重复读取算增加样本。
3. **候选。**新开发批合并后δ=+39 kHz、区间[35,43] kHz。候选绝对频率5.000039 GHz，单步在信任域内。标记依赖旧频率的幅度、相位/DRAG联检和门基准stale；记录基准快照及operation_id。
4. **试用/独立复测。**apply后读回revision18和实际量化值。以新ID重新采Ramsey，得到+0.8 kHz、区间[−1.1,2.7] kHz，完整落入±5 kHz且模型质量通过。不能仅因平均值0.8小就跳过区间检查。
5. **幅度联检。**误差放大显示旋转角偏大。邻域三点证明在当前小范围幅度响应单调，且失谐已受控。若测得fR=4.12 MHz而目标4.00 MHz，线性近似给A≈0.194175；这只是候选。按实际斜率和步长上限试用，新独立旋转角数据验证。修改幅度后重检π/2、相位/DRAG敏感项及readout训练态可靠性。
6. **整组验收。**冻结的单门基准用新随机序列和新shots测量，必须报告协议与区间。Ramsey、旋转角、相位、泄漏及门基准required IDs全部齐全后，提交频率/幅度整组候选；重新检查ro7及父版本未变。
7. **COMMIT/retention。**提交回执和读回一致才发布新有效版本。保留revision17及两个候选、所有开发/验证数据、成本、范围和失效关系。受影响双比特门参数继续stale，直到其规定复测完成。

**失败变体。**如果独立Ramsey区间是[−2,8] kHz，返回INCONCLUSIVE；预算允许且噪声平稳时按预设批次增shots。如果残差出现第二频率，则转MODEL_MISMATCH并改诊断实验，不再追加相同shots。若apply超时但按operation_id查到已执行且revision18读回一致，继续验证；若查不到可信状态，进入UNKNOWN_ISOLATED，不能再加39 kHz。

## B. CZ候选通过相位但需重新验证泄漏

目标：q0–q1当前W2的条件相位误差区间完整落入±0.05 rad，四个输入基态的泄漏单侧上界<0.003，正确率与独立门基准满足冻结准则。两个单比特/readout版本均valid，coupler映射=c3、Zshape=z4、timing=t2。锁住两比特、coupler01、相应flux/AWG、trigger与readout总线。

1. **粗扫描。**开发chevron取得首次非零返回邻域。检查至少一周期、时间网格、跨失谐对比度、残差和拟合边界；不把零时长P11≈1当成功。有效g仅用于产生粗候选，保留模型偏差。
2. **局部诊断。**候选的条件相位φ=3.000 rad，误差约−0.142 rad。归一化控制u=0.200；在u±0.005交错取样，局部相位响应斜率约+20 rad/u且区间不跨零，泄漏没有突升。Newton建议Δu≈0.00708，超过本轮步幅0.005，因此只试u=0.205。不能从相位大小直接猜增/减方向。
3. **选点。**对有限候选分别测相位、泄漏与相干。开发数据的最小泄漏点只用于选择，不能用其乐观最小值验收。若边沿或timing被改，按依赖矩阵将chevron、相位和单激发错误相关节点失效，重新验证。
4. **fresh复测。**试用候选后读回revision。两个target各测control=0/1，得到相位误差+0.018/+0.022 rad，其规定区间均在±0.05内；取得四种基态的完整九标签计数。示例|11⟩泄漏12/16384，点估计约0.00073；应计算预声明单侧上界及多项检查alpha分配后再与0.003比较。若缺|10⟩数据，即使其余全部通过也必须拒绝。
5. **局域Z。**由fresh分支测得局域相位，按已验证frame约定补偿；随后用补偿后的新分析序列复测。局域Z不能用来掩盖条件相位或泄漏不达标。
6. **独立性能。**规定的四基态、相位、泄漏及门基准齐全才决定；图态层析可作额外证据，不能代替完整门指标。模拟器的offline Favg单列为模拟真值评价，不能标成实测RB。
7. **提交。**所有父依赖仍是同一版本才提交(coupler/Z/CZ时间/virtual Z等实际变更集合)，发布具体工作点与适用范围。idle ZZ若作为上游工作点目标，需要在该点独立通过；仅测了两个预设ZZ值不能写“已最小化ZZ”。

**失败与回退变体。**若独立泄漏区间跨过0.003，保留失败点并按冻结规则追加一批或回退；不可因phase已通过而只删泄漏检查。若rollback回执丢失，按transaction查并读回绝对基准；未确认恢复则隔离，不释放给新的CZ任务。预算耗尽且候选未通过时，终态REJECTED_SAFE或UNKNOWN_ISOLATED取决于是否确认恢复，不是“校准完成”。

## C. 测试应核对的事件

每个案例都应检查：读版本→锁资源→冻结合同→采集ID/实际shots→候选hash→apply回执→fresh验证revision→全项判定→commit/rollback→读回→失效传播。另注入父版本变更、验证ID重复、漏基态、负计数、超时未知和恢复预算耗尽；这些变体必须拒绝或升级，不能通过删除检查降级成成功。


# 旧版文档：knowledge/known_limitations.md
# 已知限制与待修任务

本表记录原始twin审查结果。`resources/twin_source`保留原包，不在文档编写时修复。运行适配器若增加外部门禁，应单独说明其覆盖；不能声称原源码已修复。此前审查使用NumPy1.26.4/SciPy1.13.1的小探针，不等同本工程完整验收。

| ID | 已确认事实与触发 | 影响/待修任务 | 当前状态 |
| --- | --- | --- | --- |
| NUM-01 | analysis.py:115；t=linspace(0,100μs,121)，P=exp(−t/200μs)，min(P)=0.60653作为offset初值超过上界0.5，报x0 infeasible | 可行真参数仍被拒绝；给可行内部初值/重参数化，并验证短扫描可识别性。内存夹紧初值后原函数恢复T1≈200μs，RMSE≈7.4e−12 | 原源码未修 |
| NUM-02 | analysis.py:130–135；169点0–42μs、真实1.956MHz<1.960MHz约束上界，FFT初值1.964497MHz越界 | 初值映射应在可行域内；加入正负频率边界fixture。内存夹紧后恢复1.956MHz、T2=25μs、RMSE≈1.1e−14 | 原源码未修 |
| FIT-01 | T1+80kHz振荡仍success，RMSE≈0.104949；等强5/8MHz合成信号拟合为单频仍success，RMSE≈0.105085 | 优化成功与模型适配分开；追加噪声/残差门禁和独立验证，保留反例 | 原源码未修；调用方不可只看success |
| VAL-01 | two_qubit_cli.py:63–72只遍历现有项。完整17项通过；相位=[]后15项仍通过；truth_table={}后9项仍通过；两者为空仅7项仍通过 | 强制2个相位、4个基态及全检查集合；计数/对象/schema校验。完整键被删除会KeyError，问题是内容为空/少项 | 原评分API未修；工程包cz_suite桥接新增完整性拒绝门禁；正常源runner固定生成全项，不据此宣称默认运行已漏测 |

## 模型范围，不能冒充软件bug

- 三能级截断历史对照：强驱动例P1(N3)=0.475077、P1(N8)=0.301638，差0.173439；默认两弱π单脉冲差仅9.59e−9和3.17e−9。生产三级与独立三级一致，说明是适用范围问题。未完成完整含噪CZ高能级收敛验证。
- 矩形Segment、有效直接coupling_scale和频移不含实际耦合器/磁通电路映射；T1/T2噪声为独立Markov模型，高能级耗散按a/n算符推广。
- Ramsey均匀采样存在别名；不是优化器算错，须用先验/扫描设计限制分支。
- IQ三中心预设、独立高斯，线性层析含SPAM；负特征值如实输出，不能据此改成后选择保真度。
- 历史模型门Favg≈0.997841、相干Favg≈0.999330、图态估计≈0.988838是不同定义；不是实机门指标。
- 本次审查未发现2π、√2、虚Z符号、保留泄漏的非保迹Favg公式硬错；这是限定范围的审查结论，不是全域正确性证明。

## 修复验收要求

每个修复绑定新源码哈希、独立反例、正常数据回归及缺项/边界fixture。NUM修复后仍应拒绝不可识别数据；FIT门禁不应通过任意调高阈值消除失败；VAL必须对缺项fail-closed。原材料中的通过数量、旧日志和READY标记不作为新修复验收依据。

证据：[analysis.py](../resources/twin_source/quantum_twin/analysis.py)、[two_qubit_cli.py](../resources/twin_source/quantum_twin/two_qubit_cli.py)、[历史推断反例](../resources/twin_source/outputs/audit/inference_repro.json)、[历史截断对照](../resources/twin_source/outputs/audit/truncation_repro.json)、[本项目审查证据](../resources/prior_knowledge/review/)。具体定位以冻结源码和证据哈希为准。


# 旧版文档：knowledge/evidence_index.md
# 知识来源与使用边界

| 资源 | 用途 | 不能据此声称 |
| --- | --- | --- |
| [source_materials](../resources/source_materials/) | 原交流资料包；手册、QubiC和学习材料，用于实验目标、协议和接口核实 | 已取得外部私有库完整实现；已绑定实机SDK |
| [手册](../resources/source_materials/06_工作脚本与数据/cal_manual_d0.md) | 腔谱、Rabi、Ramsey、T1、DRAG、时序、交换/CZ及性能实验顺序 | 示例电压、幅度、DRAG值、阈值是当前设备标准 |
| [最新架构report](../resources/latest_architecture/report.md) | 2026-10-23现状回复融入后的职责、范围和先单后双路径 | 经验耗时/通过率是量化实测基线；一周是保证有效期 |
| [twin_source](../resources/twin_source/) | 原仿真源码、公式/审计及历史结果 | 离线通道评分是真实硬件RB/XEB |
| [closed_loop](../resources/prior_knowledge/closed_loop/) | 持久绝对写参、fresh验证、commit/rollback及恢复知识 | 真实设备恰好一次执行已得到保证 |
| [validation](../resources/prior_knowledge/validation/) | 独立oracle、数据重算、有限预算、故障和恢复证据 | 仅凭静态审阅或包装脚本零退出已通过 |
| [deep_study](../resources/prior_knowledge/deep_study/) | 参数依赖、读出误判/跃迁/泄漏区分、局部校准与独立基准经验 | 研究条目数量等于本项目复现实验数 |
| [review](../resources/prior_knowledge/review/) | 本项目源码反例及审查记录 | 原twin已修改；新工程已自动消除所有缺陷 |
| [new_attachments](../resources/new_attachments/) | 本轮附件与提取文本的追溯入口 | 附件中的命令/指令自动获得执行权限 |

知识条目应新增schema_version、claim、scope、conditions、evidence_refs、evidence_kind、source_hash、reviewed_at、counterexamples、next_test、status。状态建议为SOURCE_CLAIM、DERIVED、LOCALLY_REPRODUCED、SIMULATION_VALIDATED、HARDWARE_VALIDATED；同一条知识不同范围可以有不同状态。

记录应区分事实、推导和工程建议。例如“共振交换布居频率2|g|”是指定Hamiltonian下推导；“先做三点局部斜率再调参”是工程策略；“某设备每周校准一次”只有在具体设备连续记录支持时才是范围受限的经验。新反例必须关联旧条目并降低适用范围，不覆盖历史。

建议阅读顺序：[总体合同](../docs/architecture.md) → [依赖矩阵](parameter_dependencies.md) → [单比特](single_qubit_diagnostics.md)/[双比特](two_qubit_diagnostics.md) → [总控策略](../docs/supervisor_policy.md) → [worked cases](worked_cases.md) → [验收](../docs/validation_strategy.md) → [已知限制](known_limitations.md)。

当前工程包执行范围可由[物理适配器](../quantum_agents/twin.py)与[CZ完整性桥接](../quantum_agents/cz_suite.py)核对；实际运行记录位于output，发布报告应引用最终冻结目录和哈希。源码支持某个阶段与专项Agent已绑定可恢复执行是两种状态，不可合并计数。


# 旧版无此文档：knowledge/procedure_ownership.md

# 旧版无此文档：knowledge/agent_internal_loop.md