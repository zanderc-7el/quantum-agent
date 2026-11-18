# 文档：README.md
# 量子测控智能体：原流程15步版

本版保留原PDF的**8个单比特步骤、7个双比特步骤和1个总控**。一个实验智能体负责原图一个功能框，在内部完成采集、拟合、偏差判断、提出调参和独立重测。原有44种细分方法作为这些步骤的内部工具保留，5项额外支持职责归入公共模块，不再注册为额外智能体。

| 原图步骤 / 新ID | 原功能框 | 原函数名 | 当前能力边界 |
| --- | --- | --- | --- |
| 单1 / `s01_readout_frequency` | [读取腔定频](agents/s01_readout_frequency/card.md) | `ReadoutFreqDip` | 单腔S21内部方法已绑定；高低功率/偏置流程未完整绑定 |
| 单2 / `s02_qubit_frequency` | [比特定频](agents/s02_qubit_frequency/card.md) | `F01Spectroscopy` | 粗频谱内部方法已绑定；精频率可借用Ramsey诊断 |
| 单3 / `s03_rabi_amplitude` | [Rabi定幅](agents/s03_rabi_amplitude/card.md) | `XYGateAmpRabiImpl` | 仅时长Rabi辅助已绑定；原幅度Rabi未绑定 |
| 单4 / `s04_readout_fidelity` | [读取保真度测试](agents/s04_readout_fidelity/card.md) | `IqToProbability` | 原始IQ训练与独立混淆矩阵执行器待绑定 |
| 单5 / `s05_readout_optimization` | [读取参数优化](agents/s05_readout_optimization/card.md) | `ReadoutParaSweepByIQProb` | 读出频率×幅度优化执行器待绑定 |
| 单6 / `s06_t1` | [T1测试](agents/s06_t1/card.md) | `T1Idle` | 固定工作点T1内部方法已绑定；频率依赖扩展待绑定 |
| 单7 / `s07_t2` | [T2测试](agents/s07_t2/card.md) | `T2CPMG` | 仅Ramsey辅助已绑定；原T2CPMG未绑定 |
| 单8 / `s08_single_qubit_rb` | [单比特RB测试](agents/s08_single_qubit_rb/card.md) | `RB1QCal` | RB主流程与单比特XEB扩展待绑定 |
| 双1 / `d01_flux_spectroscopy` | [二维比特能谱](agents/d01_flux_spectroscopy/card.md) | `F01ZPulseDependency` | 真实Z幅度—频率映射待绑定 |
| 双2 / `d02_idle_interaction` | [两比特闲置工作点](agents/d02_idle_interaction/card.md) | `ZeroPointFinderByQQSwap` | 交换/ZZ有源套件诊断；真实闲置点搜索待绑定 |
| 双3 / `d03_cz_width` | [CZ宽度粗扫](agents/d03_cz_width/card.md) | `CZSwapSweepZPulse` | 矩形脉冲源套件可诊断；原设备宽度流程待绑定 |
| 双4 / `d04_cz_amplitude` | [CZ幅度精调](agents/d04_cz_amplitude/card.md) | `TTGAczDelicateG` | 重复CZ幅度精调待绑定 |
| 双5 / `d05_cz_detuning` | [CZ失谐与条件相位精调](agents/d05_cz_detuning/card.md) | `TTGAczDelicateDetune` | 源套件有相位/局域Z诊断；原精调流程待绑定 |
| 双6 / `d06_cz_xeb` | [CZ XEB测试](agents/d06_cz_xeb/card.md) | `CZXEBCal` | CZ源套件非XEB；原XEB主流程待绑定 |
| 双7 / `d07_cz_optimizer` | [CZ参数优化](agents/d07_cz_optimizer/card.md) | `CZParaOptimizer` | 受约束硬件参数优化待绑定 |

唯一总控是`supervisor`，负责跨步骤依赖、全局重测、资源与预算、参数版本、提交和回退。表格依据两页PDF的功能方框计数；箭头旁的复测编号不另增步骤。单4是读取保真度测试，单5才是读取参数优化。见[重组说明](docs/reorganization.md)及[唯一方法归属表](knowledge/procedure_ownership.md)。

## 怎样使用

在包根目录运行以下只读命令，先看原步骤及内部方法：

```bash
python3 -m quantum_agents list
python3 -m quantum_agents show s03_rabi_amplitude
python3 -m quantum_agents show s07_t2
python3 -m quantum_agents plan --domain single_qubit
python3 -m quantum_agents plan --changed q0.frequency_hz
```

每个新步骤的`agents/<id>/card.md`是操作合同，`prompt.md`可供模型按该步骤工作。模型为当前步骤选择内部方法并解释偏差；执行和发布由确定性接口检查。`propose`仅输出建议：

```bash
python3 -m quantum_agents propose s03_rabi_amplitude --context examples/advisor_context.json --provider rules
```

数值演示需要`requirements-twin.txt`指定依赖。创建新工作目录运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-twin.txt
.venv/bin/python -m quantum_agents twin --output /tmp/qcal-15-first --qubits 0,1
.venv/bin/python -m quantum_agents status --db /tmp/qcal-15-first/runtime.sqlite
.venv/bin/python -m quantum_agents cz --output /tmp/qcal-15-cz-first
```

`twin`运行五类已绑定内部方法组成的11次操作链，每项另采验证，并保存`agent_execution_report.json`。11次操作不是11个新智能体，也不是原15步全部完成；当前分组报告对原流程完整完成均保持`original_step_complete=false`。`cz`运行源数值套件及完整性门禁，属于诊断桥，不是原XEB步骤。

在同一数值工作区中，显式调用某个步骤已有的辅助方法：

```bash
.venv/bin/python -m quantum_agents run-agent s03_rabi_amplitude   --procedure sq_rabi_time --step-id sq_rabi_time:q0:final   --workspace /tmp/qcal-15-first --operation-id q0-rabi-time-fresh-002
```

此命令只重测时长Rabi辅助，不能写成原幅度Rabi已完成。默认`run-agent s03_rabi_amplitude`选择原幅度主方法；未绑定时应拒绝。同理，s07的Ramsey辅助不能替代CPMG，d06的CZ源套件不能替代XEB。执行器不会自动跨步骤补齐前置实验；总控按依赖安排。详细案例见[使用与重测](docs/usage_walkthrough.md)。

## 能力、证据与资料

- 当前有持久运行状态、参数修订、fresh验证、冻结门槛、有界预算、回退和未知回执核查；物理模拟绑定与公共模块说明见[架构](docs/architecture.md)、[精确接口](docs/runtime_contract.md)、[模型协议](docs/model_interface.md)。
- 原始量子算法、公式、偏差分支与参数依赖保留在[单比特诊断](knowledge/single_qubit_diagnostics.md)、[双比特诊断](knowledge/two_qubit_diagnostics.md)、[依赖矩阵](knowledge/parameter_dependencies.md)及[完整复测案例](knowledge/worked_cases.md)。方法可以跨步骤借用，主归属仍唯一。
- 原图“QQswap交换关断”等同“ZZ关断”的表述已在d02内部纠正；60 ns只作源图示例，不能当任何设备标准，也不因此增加智能体。
- 新版正式证据入口为`output/twin-final/`、`output/cz-current/`和`output/integration_acceptance.json`。测试数、采集数和结论由本轮实际记录确定；[结果说明](output/README.md)列出各证据的用途。
- 旧架构、旧操作卡、旧QA保存在`resources/previous_edition/`；旧运行输出归档到`output/previous_edition/`。这些记录不能作为新版通过证据。[资料边界](resources/SOURCE_NOTES.md)说明原包与新文档的区别。

当前仍拒绝真实设备模式，未提供32所真实SDK，也未宣称完成联网模型质量测试。原twin的T1/Ramsey初值、错模型success和原评分缺项问题仍单列为[已知限制](knowledge/known_limitations.md)；CZ桥的防护不等于原源码修复。设备任务必须另外完成接口、单位、通道、读回和恢复验证。


# 文档：resources/SOURCE_NOTES.md
# 资料来源、原图与历史版本边界

本版以`new_attachments/`中的双页《双比特测试.pdf》功能框为组织依据：第1页8步、第2页7步，箭头回测编号不另计。准确映射由[workflow_ownership.json](../config/workflow_ownership.json)记录。第1页第4框是读取保真度测试，第5框是读取参数优化。原图中QQswap与ZZ的混淆、60 ns示例的适用范围在[d02/d03说明](../docs/reorganization.md)中校正，不扩充角色数。

[source_manifest.json](source_manifest.json)保存可访问源资料的路径、长度和SHA256；条目数量以该清单为准。既有交流资料包、最新架构、原twin源码、独立审查/闭环知识和本轮附件分目录保留。第三方源码、文档及许可证按原样保存，本包不重新授权。来源校验：`python3 scripts/verify_sources.py`，无需原机器路径继续存在。

微信临时目录中的`quantam-project-20261023.zip`已不存在；使用此前解压并核验的源码目录，清单保留原ZIP缺失事实。本工程ZIP不是原ZIP的复本。源码算法未在本次角色重组中修改。

`previous_edition/`为此前细分角色版本的归档，包括旧README、注册表、44方法旧卡、支持角色卡、QA和门户等。旧材料出现的角色数量、测试数量、PASS或浏览器限制只说明历史版本；不能据此断言新版能力或新版验收结果。旧运行输出另归档到根目录`output/previous_edition/`。

活跃`knowledge/`按新owner重整经验，活跃`agents/`仅15实验与1总控。44方法继续作为内部procedure，原技术内容可追溯到归档和源资料；归属唯一、可跨步骤工具调用。新增说明、提取文本和工程代码由最终整包清单登记，不冒称原文件逐字节复制。

原材料中的操作指令、示例参数、旧READY和历史经验均为审阅对象，不自动获得本次执行授权。资料收集只覆盖实际可读取文件，不声称包含未提供的设备SDK、外部私有库或全部历史对话。新正式数值证据以根目录`output/`所述当前路径及本轮冻结报告为准。


# 文档：output/README.md
# 原流程15步版的运行证据

新版正式入口是`twin-final/`、`cz-current/`和`integration_acceptance.json`。是否通过必须读取对应文件的运行身份、结构化结果和原始证据；目录名或本说明的存在不构成通过。

| 入口 | 实际用途 | 不能替代 |
| --- | --- | --- |
| `twin-final/twin_summary.json`与`backend/scans/` | 本轮新采样的五类内部方法；原始计数/IQ、拟合、回执与修订 | 原15步全部完成、幅度Rabi或CPMG已执行 |
| `twin-final/agent_execution_report.json` | 列出全部15原步骤；现有内部执行分属s01/s02/s03/s06/s07，区分方法状态与原步骤状态 | 以11次操作数量充当智能体数量；把辅助通过晋升original_step_complete |
| `cz-current/` | 新版重跑的源CZ套件，核对两相位、四输入、九测量基、17个具名检查及计数 | 原XEB步骤、实机RB/XEB、逐CZ子步骤事务恢复 |
| `integration_acceptance.json` | 实际测试进程、测试名、tests_run/skipped、返回码和日志；本地模型mock结论 | 固定写死测试数量、调用成功等于联网模型效果验证 |
| `previous_edition/` | 旧版运行输出，只用于历史追溯 | 新版验收和新版恢复基线 |

每个输出必须冻结backend seed、配置、源码和数值环境身份；参数或代码不符时新建目录。新采样和同operation_id的幂等重放分别记录，后者不得记成新增验证数据。T1/T2是测量记录，不是可通过写配置改善的物理旋钮。

最终ZIP验收报告位于包旁的`量子测控智能体工程包_原流程15步版_20261024_验收.json`，在冻结后生成。该报告记录两次新解压/新采样及同目录同操作ID重复；检查实际进程、日志、清单哈希与保全证据，不以旧PASS替代。冷运行复用明确的依赖解释器，不代表从空操作系统安装依赖。

上述全部实验输出均为数值模拟或明确标注的scenario，真实设备未接通。源包`resources/twin_source/outputs/`和`resources/previous_edition/`中的结果属于历史资料。


# 文档：docs/architecture.md
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


# 文档：docs/supervisor_policy.md
# 15步总控：跨步骤决策与复测策略

总控`supervisor`管理8单+7双的跨步骤关系。实验Agent先在自身方法与局部预算内完成采集、拟合、偏差判断、调参和重测；只有超出本步骤前提、权限或预算，或者影响其他步骤时，才交总控改全局计划。此文规定总控可选择的动作及条件。默认采取最小、可验证的参数变化；每次动作有证据、成本和后续验证。研究资料提供候选解释，不能覆盖当前观测，也不能使模型建议自动成为写参权限。

## 0. 任务归属与升级路径

- 读出分类问题由s04负责；需要改变读出频率/功率时转s05优化，然后回s04独立重训与复测。s01只给腔频/功率与偏置响应前提，不能代替赋值保真度。
- 频率偏差由s02处理，可经总控调用s07的Ramsey工具；频率改变后s03复测幅度/相位，s08重做规定基准。原s07的CPMG目标仍单列，不因Ramsey通过而关闭。
- T1衰退由s06诊断，必要时请求d01工作点映射、s02频率和s04读出复核；不能把T1数值当可直接写好的旋钮。
- 双比特工作点由d02负责交换与ZZ两类独立证据；d03管宽度/回归、d04管幅度、d05管条件相位/局域Z，三者联动时总控冻结一个联合候选版本。
- d06负责原XEB性能任务，泄漏/布居/层析是它可调用的诊断方法，不可替代XEB主目标。d07优化候选后必须返回d06采新随机线路，并复核受影响d03–d05及单比特步骤。

方法primary owner唯一；跨owner调用是工具借用和证据移交，不注册第二个Agent。引用精确ID见[方法归属](../knowledge/procedure_ownership.md)。总控不得将“内部辅助已通过”提升为“原步骤已完成”，也不得为通过而删除未绑定主目标。

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


# 文档：docs/hardware_adapter.md
# 硬件适配协议与事务边界

下面的操作是本工程的抽象协议建议，**不是32所或任何现有设备的真实 SDK/API**。材料中的 CorrectF01ByPhase、DragAlphaCal、RB1QCal 等只用于定位实验能力。具体模块、签名、授权、单位和自动写参行为均须绑定后验证。

## 0. owner、方法与执行器

注册对象是15个原步骤与1个总控；Backend绑定的是具体procedure，不是所有owner能力的总开关。请求应关联`agent_id`、`procedure_id`、`primary_owner_id`和实际`runtime_step_id`；跨owner借用由宿主保留caller来源。现有低层ExecutionRequest字段以[运行时接口](runtime_contract.md)为准，未加入字段不得直接塞入该dataclass。

`run-agent`默认选择原步骤主方法，能力不足必须拒绝。显式选择s03的`sq_rabi_time`只运行时长辅助，s07的`sq_ramsey`只运行Ramsey辅助。CZ源套件是整体诊断桥，不是d06的XEB适配器。能读到某方法旧数据，不能因此把owner标为HARDWARE_VERIFIED或original_step_complete。

## 0A. 当前本地Backend接口

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


# 文档：docs/validation_strategy.md
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

## 组织、主方法与辅助方法验收

静态结构要求活跃注册表恰为15实验与1总控；PDF两页方框顺序为8/7；44个procedure恰有一个primary owner，公共模块不能混进角色数。每张新步骤卡必须包含内部方法选择、采集、拟合质量、偏差解释、调参依据、fresh复测、局部预算与跨步骤升级条件。保留所有原方法及公式，不能仅改目录标题和数字。

分别记录`procedure_result`与`original_step_result`：具体方法是否实际执行/证据有效、原功能框主目标是否达到、尚缺哪些方法或能力。s03的时长Rabi通过不能满足幅度Rabi主目标；s07的Ramsey不能满足CPMG；d06的CZ源套件不能满足XEB。当前分组报告对所有原流程完整完成保持original_step_complete=false。验收应主动传入这些替代请求，确认拒绝或明确辅助状态，而不是放行。

当前正式证据以[output说明](../output/README.md)的新版`twin-final`、`cz-current`及`integration_acceptance.json`为准。读取真实tests_run、测试名、skipped、returncode和日志，不在文档固定测试数量。旧版QA、旧数据和旧验收只留在归档，不能据其PASS宣布新版完成。

最终冻结ZIP后，在两个新解压目录重新生成数值数据、运行独立oracle、控制流与接口测试；再以同operation_id重复验证幂等。归档验收报告在包旁同名`_验收.json`，不能写回ZIP形成自引用哈希。代码/配置/种子或环境变更后原证据失效，重新生成并重新冻结。科学模型、原源码缺陷和真实设备边界保持原有说明。


# 文档：docs/usage_walkthrough.md
# 按原15步使用、诊断和复测

## 先选原步骤，再选内部方法

`python3 -m quantum_agents list`列出15实验与1总控；`show s03_rabi_amplitude`查看原Rabi定幅功能框的完整操作卡。每个实验Agent内部承担扫描选择、采集、拟合、判断偏差、受限调参和fresh重测。44个procedure是工具层，5项额外支持职责是公共模块；它们不要求额外模型角色。

```bash
python3 -m quantum_agents show s04_readout_fidelity
python3 -m quantum_agents show s05_readout_optimization
python3 -m quantum_agents plan --domain single_qubit
python3 -m quantum_agents plan --domain two_qubit
```

s04负责IQ分类与独立赋值检查，s05负责频率/功率优化；s05修改读出后必须回s04。双比特d07优化后回d06新随机线路XEB，必要时回d03–d05。原箭头复测是新任务版本，不增加智能体，也不把依赖图做成循环。

## 运行现有数值方法链

使用已安装`requirements-twin.txt`依赖的解释器，在新工作目录运行：

```bash
python3 -m quantum_agents twin --output /tmp/qcal-15-first --qubits 0,1
python3 -m quantum_agents status --db /tmp/qcal-15-first/runtime.sqlite
```

流程是公共单腔扫描，以及每个比特的粗频谱、时长Rabi种子、双正交Ramsey、新频率下时长Rabi复测、T1，共11次内部操作。每项独立采集开发与验证数据；Rabi验证实际施加0、π/2、π、2π候选时长。`agent_execution_report.json`把它们归到s01/s02/s03/s06/s07；所有原步骤完整完成状态仍为false。不要将11次执行称为11个Agent或原15步完整实现。

检查`twin_summary.json`的状态与valid_evidence，再读`backend/scans/`的实际采样及`backend/receipts/`回执，最后核对`agent_execution_report.json`的主目标缺口。独立oracle可对已保存数据重算观测、曲线、候选、参数修订与预算：

```bash
QCAL_TWIN_OUTPUT=/tmp/qcal-15-first python3 -m unittest discover -s tests -p test_independent.py -v
```

这是数值模拟证据，固定IQ代理不等于真实读出训练。T1/T2写入为表征记录，不能由写记录改变器件耗散。

## 主方法未绑定必须看得见

`run-agent s03_rabi_amplitude`默认幅度Rabi主方法，`run-agent s07_t2`默认T2CPMG，`run-agent d06_cz_xeb`默认XEB。三者当前主方法未绑定时必须拒绝；不能悄悄回退到时长Rabi、Ramsey或CZ源套件。

当目标就是运行辅助方法时显式指定，并提供已有有效前置的内部step：

```bash
python3 -m quantum_agents run-agent s03_rabi_amplitude   --procedure sq_rabi_time --step-id sq_rabi_time:q0:final   --workspace /tmp/qcal-15-first --operation-id q0-duration-fresh-002
python3 -m quantum_agents run-agent s06_t1   --procedure sq_t1 --step-id sq_t1:q0   --workspace /tmp/qcal-15-first --operation-id q0-t1-after-duration-002
```

第一条只复核候选脉宽；它产生新版本后T1旧证据会失效，所以第二条用新ID重新采集。运行器不自动跨owner补前置；前置过期就交总控安排。`run-step`仍是低层兼容入口，输出必须标为physical_procedure，不能将旧方法ID重新注册为独立Agent。

同目录同operation_id表示取回同一次结果，不新增shots；主动复测用新ID。backend seed、配置、源码或数值环境改变后新建目录，不删除冻结清单来绕过身份检查。

## 一个步骤怎样使用模型

先由可信宿主提供当前参数、版本、证据ID、现象、允许范围和剩余预算：

```bash
python3 -m quantum_agents propose s03_rabi_amplitude --context examples/advisor_context.json --provider rules
```

规则模式用于既定分支和协议检查，不代表模型推理。模型模式必须显式配置provider、模型和凭据；客户端输出仍只是结构化建议。模型可以建议本步骤内部方法及局部参数，不能自动发布、开放新SDK、任意改扫描或取得设备权限。精确字段以[模型协议](model_interface.md)为准。

跨owner借用仍遵循唯一归属：s02需要Ramsey诊断时，原方法属于s07，由总控形成方法调用/证据移交；当前CLI使用声明该方法的owner，而不是把`sq_ramsey`加入另一个活跃角色。方法通过后s02按频率目标作判断，s03承担旋转角复测，原s07的CPMG任务仍未完成。

## 偏差出现后怎么回测

例：s02得到带符号频差，先核符号、别名先验、模型残差和区间，再提出绝对频率。新频率通过fresh Ramsey诊断后，s03复核π/π2及相位，必要时改幅度/DRAG；s04复核依赖这些制备脉冲的分类证据；s08以新随机序列做规定RB。若幅度变化很小且有对照证据，总控可缩小重测集合，但不能直接跳过冻结必需项。详见[完整单比特案例](../knowledge/worked_cases.md)。

双比特中d03先找第一非零回归，d04调幅，d05验证条件相位并校正局域Z；任何改动都可能使另一项失效。d06保留泄漏、布居与层析诊断，并用真正XEB执行器完成原性能目标。d07只产生受约束候选；优化数据不能作为d06的新验收数据。未绑定XEB时应报告该主目标未实现，不以离线Favg填空。

## CZ桥与故障场景

```bash
python3 -m quantum_agents cz --output /tmp/qcal-15-cz-first
python3 -m quantum_agents demo --output /tmp/qcal-15-scenario-first
python3 scripts/verify_sources.py
```

CZ入口重新运行原套件，保存相位、泄漏、四基态表和九基态层析；17项具名检查及计数必须齐全。它是source_suite_only诊断桥，不是d06 XEB主方法，没有逐子步骤事务恢复。`demo`是明确标注scenario的故障注入演示，不能当作物理实验通过。

无效数据、错模型、漂移、设备状态未知和预算耗尽要分别处理：改扫描、追加新shots、回上游或升级均须有依据。候选验证失败要恢复绝对快照；回退读回不明时保留隔离，不能宣称恢复。详细决策见[总控策略](supervisor_policy.md)。

真实设备接口仍未绑定，当前运行时拒绝real。需要按照[硬件适配合同](hardware_adapter.md)验证通道、单位、时钟、读回、停止和恢复；没有只填IP就可启用的开关。


# 文档：docs/build_record.md
# 原流程15步版：构建与验收记录

本版交付对象是15个原图实验步骤与1个总控、44种保留的内部方法、公共支持模块、现有数值执行器和可追溯资料。重组不修改原quantam物理/拟合算法，也不增加已绑定设备能力。

## 本版实际改动

1. 依据两页PDF功能方框固定8单/7双，单4为读取保真度，单5为读出优化；方法归属由config/workflow_ownership.json唯一确定。
2. 把旧细分方法纳入15个步骤内部，要求每个步骤自己完成采集、拟合、偏差分析、候选与复测；总控处理跨步骤依赖、预算、资源、提交/回退和全局复测。
3. 原5项额外支持职责改为公共模块；方法跨owner借用保留caller和唯一primary owner，不增加注册角色。
4. 全部公式、模型适用域、失效矩阵、单/双诊断与完整复测案例继续保留，并补充新owner路由；s03时长辅助、s07 Ramsey辅助、CZ桥非XEB的边界显式写入用法与验收。
5. 旧架构/旧QA在resources/previous_edition归档；原活跃knowledge/qa移到其reorganization_archive/knowledge_qa。旧运行输出由新版构建流程归入output/previous_edition，不作为新版通过记录。

## 本版验收记录的权威位置

- 原图/角色/方法与生成物：新版config、agents及docs/experiment_coverage.md；以本轮机器检查报告为准。
- 文档：新版knowledge/qa内的静态检查、语义审阅与冻结清单；不得引用归档旧检查或旧PASS作为本版验收。
- 数值采样与集成：output/twin-final、output/cz-current、output/integration_acceptance.json；测试数和采集数从实际结果读取，不在本文写死。
- 最终发行：冻结ZIP旁同名_验收.json；包含两次新解压/新采样、真实子进程、独立oracle、场景故障及同操作ID重复，并核对清单与来源哈希。

本说明不提前宣告上述检查通过。最终结论由本轮结构化报告、原始日志和哈希共同支持。新版门户的构建/检查由本版实际记录描述；不把旧版浏览器限制转写为新版已执行或失败的视觉测试。

## 保留的科学与工程边界

QQswap交换关断与ZZ独立验证仍分开，60 ns仍为源图示例。CPMG的静态失谐回聚焦不能直接套Ramsey余弦模型；Rabi启动可明确使用raw IQ代理，不能假称已训练分类器。T1/Ramsey初值与错模型success仍为原源码待修；CZ包装层缺项拒绝不表示原评分函数已修改。

五类模拟原子方法、同目录恢复和模型本地mock不能证明所有原步骤、联网模型效果或实机已验证。冷运行复用明确的依赖解释器，是新目录/新数据验收，不是从空操作系统安装依赖。旧版曾发生的导入路径或GraphLoop格式失败作为历史经验保留；新版若出现新失败，应在本轮报告另记，不能用旧修复结论覆盖。


# 文档：docs/reorganization.md
# 从细分方法目录回到原图15个实验步骤

本版以两页《双比特测试.pdf》的功能方框为组织边界：第一页8个单比特框，第二页7个双比特框。箭头、回测编号、方法变体和诊断工具不再增加角色。唯一额外智能体为总控`supervisor`。权威对应关系保存在[workflow_ownership.json](../config/workflow_ownership.json)，技术方法逐项见[归属索引](../knowledge/procedure_ownership.md)。

## 这次重组改变什么

实验智能体不只是转发某个拟合函数。它接收原步骤目标，在冻结范围内选择本步骤的方法、设计采集、判断数据与模型、解释偏差、产生受限候选并安排新数据复测；最后提交完整证据或明确失败。多个方法可以在一个步骤内部迭代。总控负责跨步骤工作，不替每个实验重复做全部科学判断。

原44种实验方法全部保留为procedure；每种方法有且只有一个primary owner。方法可以被别的智能体作为工具借用，调用证据同时记录caller、procedure及primary owner，不复制成第二个注册角色。方法归属不等于参数所有权，也不代表该方法已绑定设备。比如s02精频可使用s07的Ramsey方法，s03仍负责实际旋转角复核；不能因为借用方法就把s02改名为Ramsey智能体。

旧`mission_supervisor`职责合入新`supervisor`；`capability_router`、`evidence_steward`、`fit_diagnostics`、`parameter_registry`、`independent_acceptance`归入公共服务模块。它们仍保留确定性门禁、日志和版本职责，但不再独立注册、计数或要求启动五个模型进程。

## 原图顺序与两个物理校正

单比特顺序为：读取腔定频→比特定频→Rabi定幅→读取保真度测试→读取参数优化→T1→T2CPMG→RB。第4与第5步分别负责分类及独立赋值验证、读出参数优化；优化后必须返回第4步重新训练/验证，不可以把优化训练分数当最终读出保真度。

双比特顺序为：二维比特能谱→闲置工作点→CZ宽度粗扫→幅度精调→失谐/条件相位精调→XEB→参数优化。第7步优化后返回第6步用新随机线路复测，必要时回第3–5步和受影响单比特步骤；这是新任务版本的回路，不是初始依赖图中的循环。

原图将QQswap关断与ZZ关断混用的表述，在d02内部拆成交换测量与独立条件Ramsey ZZ证据，二者不得互相代替。原图中的60 ns门宽仅作为来源例值，在d03内部重新核对脉冲族、padding、相干、带宽及交换周期。这两项校正不增加智能体。

## 三组不可替代关系

| 原步骤主目标 | 当前已有内部执行能力 | 不能据此宣布 |
| --- | --- | --- |
| s03：固定门宽的幅度Rabi/幅度精校 | 时长Rabi与候选时长布居复测 | 原幅度扫描流程完成 |
| s07：T2CPMG/echo | 双正交Ramsey频率与包络估计 | 已执行回聚焦序列或得到CPMG相干时间 |
| d06：CZ XEB | 原CZ套件的相位、泄漏、基态表、态层析及离线通道评分 | 已执行随机线路XEB、RB或实机平均门验证 |

因此，执行报告分别保存owner的原步骤状态和procedure的实际运行状态。一个辅助方法通过，不能自动把owner的原步骤置为完成。当前分组报告明确保持原流程`original_step_complete=false`。

## 历史迁移与本版验收

旧版本完整资料保存在[previous_edition](../resources/previous_edition/)。其中旧角色数、旧prompt、旧测试数和旧哈希属于历史记录。新版的活跃注册表只允许15实验与1总控；公共模块和44种procedure不加入角色计数。旧knowledge/qa已移出活跃知识目录，不用它证明新版文档通过。

新版验收依次检查：原15步映射→procedure唯一归属→每步内部闭环合同→方法借用的证据与权限→新采样和实际测试→冻结ZIP及两次冷运行/同目录重复。源码算法保持原样，重组不能掩盖此前已知缺陷或扩大真实设备权限。详细验收条件见[验证设计](validation_strategy.md)。


# 文档：knowledge/parameter_dependencies.md
# 参数依赖与复测矩阵

本表将物理依赖分派到原15步，方法归属详见[索引](procedure_ownership.md)。Agent完成本步骤内部诊断，总控根据本表安排跨owner复测。

本表是保守的初始依赖策略，实际设备通过对照实验后可以缩小影响集合。材料中的实验顺序是起点；不能证明所有依赖已穷尽。每次变更先区分“数字驱动设定改变”与“芯片物理工作点改变”，后者通常影响更广。

## 1. 依赖记录

节点记录 `reads={parameter_id:version}`、`writes`、工作点、控制模板/分类器版本。提交上游参数后，所有直接依赖及传递依赖进入 stale；依赖边带原因与复核实验。图必须无环；实际迭代通过新版本/new attempt 表达，不能用环形依赖绕过前提。只要祖先 stale，下游不能作为本轮有效基线。

| 被改变的参数 | 直接需复核 | 进一步需复核/失效 | 说明与可缩小范围的条件 | 负责步骤与回测路线 |
| --- | --- | --- | --- | --- |
| 驱动频率/数字相位参考 fdrive | 双正交Ramsey残余失谐；π/π/2幅度/相位 | DRAG相关相位、单门基准、涉及该比特的CZ局域Z与图态分析 | 纯数字改变不必自动重测器件T1；若表明物理频率漂移，追加工作点/相干复核 | s02提频率；s07 Ramsey工具；s03旋转；s08基准；d05/d06 |
| 物理 fq 或 idle DC flux | 频谱/非谐性、读出腔频/判别、Rabi、Ramsey、T1/T2、flux→fq局部映射 | DRAG、门基准、相邻频碰撞、ZZ、交换、CZ chevron/相位/泄漏/virtual Z | 频率改变可能改变谱、相干和多比特条件，不能只更新一个f01字段 | s01–s07单比特链；d01映射；d02工作点；d03–d06 |
| XY幅度或π门时长/包络 | 旋转角、π/2比例、相位误差、泄漏 | 读出训练态、T1/Ramsey制备分析可靠性、RB、CZ条件相位和层析SPAM | 先检验小幅修正的制备/分析；并非每次微调都从头搜索腔谱 | s03本地复核；s04训练态；s06/s07；s08；d05/d06 |
| DRAG系数/导数分量 | 脉冲面积、相位误差、\|2⟩泄漏 | 频率/幅度交叉检查、单门基准、CZ分析脉冲相关结果 | DRAG与失谐/幅度可能相关；不能只看某点P1最大 | s03局部联合扫描；s02频率；s08；d05/d06 |
| readout频率/功率/窗/积分权重/分类器 | 独立准备集混淆矩阵、赋值错误、泄漏误判、复位相关性 | 所有用旧分类器的概率拟合、条件相位/层析与门基准读出解释 | 原始IQ完整时可按新分类器重处理开发数据；仍需新验证数据，重处理不能称独立采集 | s05优化→s04新分类；所有消费者按版本失效 |
| qubit flux映射/偏置/补偿矩阵 | fq(Φ)局部斜率、相邻比特交叉响应、相干、读出 | 单门参数、idle ZZ、交换、CZ工作点、局域Z | 映射版本变化须记录当前分支与迟滞条件；禁止跨分支外推 | d01映射；s01–s08关联检查；d02–d06 |
| coupler偏置或开关映射 | 相关比特频移、有效g、idle/dressed ZZ、邻居影响 | 双比特工作点、CZ时间/相位/泄漏、并行门测试、受影响单门参数 | 当前twin中的coupling_scale不是实机映射，不能直接等价 | d02映射/交换/ZZ；s02/s03受影响者；d03–d06 |
| Zshape/脉冲上升沿/下降沿/预失真/幅宽 | 实际量化波形、回归时间、条件相位、泄漏、局域相位 | CZ完整基态表/独立门基准、邻居串扰、依赖时序 | 同面积不保证同动力学；矩形模型的最优点不能原样沿用 | d03宽度/波形；d04幅度；d05相位；d06性能 |
| XY–Z、readout–XY或跨通道 timing | 相对时序、触发/量化、重叠、恢复/清空 | 脉冲面积与相位、制备读出、CZ泄漏/相位、并行实验 | 只改变公共起点且确认所有相对关系不变可缩小；需有证据 | s03 XY–Z；d03 qubitZ–couplerZ；s04/s05读出；d04–d06 |
| virtual Z/frame更新 | 固定约定下的已知旋转与下游分析 | 所有用该frame编译的后续混合门/层析 | 局域Z不能修复非局域条件相位、残余交换或泄漏 | d05帧校正；s03分析脉冲；d06验证 |

## 2. 执行依赖与物理依赖

设备资源图决定能否同时执行；参数依赖图决定结果能否使用。两个没有公共仪器通道的节点，若共享同一个读出标定版本，读出变更仍使两者失效。反过来，两个物理参数独立的实验，也可能争用同一AWG或readout总线。

复测计划分为强制项目和观察项目。强制项目未完成时参数不得恢复valid；观察项目用于检测尚未证实的弱耦合。缩小强制集合需记录局部对照数据、范围及审批策略，不能由模型仅凭语义相近删边。

## 3. 最小复测路径示例

数字频率修正：新频率读回 → fresh双正交Ramsey → 旋转角/相位检查 → 需时DRAG联检 → 单门基准 → 受影响CZ局域相位与SPAM复核。物理flux工作点移动：fq/读出/相干 → 单门校准 → idle ZZ/交换 → CZ粗细调 → 条件相位/泄漏/局域Z → 独立门评估。若上游验证失败，后续测量保留为诊断，不能正式提交为该工作点有效参数。

来源：[手册§1.1–2.5、单比特精校、耦合器与两比特门](../resources/source_materials/06_工作脚本与数据/cal_manual_d0.md)、[最新架构第4–5章](../resources/latest_architecture/report.md)、[既有研究经验](../resources/prior_knowledge/deep_study/)。

## 4. 从方法级变化到原步骤状态

内部方法产出新参数时，先使同owner内依赖这些参数的结果失效，再传递到其他owner。s03时长Rabi产生新脉宽，至少复核对应准备/分析及T1/Ramsey消费者；它不产生原幅度Rabi的完成证据。s07 Ramsey辅助修正频率后，s02评估频率目标、s03复测旋转，s07的CPMG主合同仍独立。d06层析或泄漏通过只补充诊断，不能使XEB主状态valid。

状态键至少包含owner、procedure、qubit/pair、working_point、参数版本及证据用途。共享一个owner并不意味着所有方法一起过期或一起通过；影响集合须按实际读写与物理依赖计算。原图回测箭头由总控创建新任务版本，不能把旧证据在不同版本间搬运。


# 文档：knowledge/single_qubit_diagnostics.md
# 单比特偏差诊断知识

这些诊断是原8个单比特Agent的内部知识；方法唯一归属见[索引](procedure_ownership.md)，不是新的独立角色。每条知识给出观测、可检验解释、下一实验与禁止推断。拟合误差、统计误差、物理模型误差和控制执行误差分开记录。下面方程使用Hz、s、rad；DAC code与电压需要另行标定。

## 1. 谐振腔与频谱

归属：s01的ro_resonator_survey/ro_power_shift/ro_bias_map；s02的sq_spectroscopy/sq_anharmonicity。工作点映射由d01的sq_flux_map提供，不能仅由腔曲线猜最佳量子比特频率。

当前twin的归一化notch模型为 `S21=1−d/[1+2i(f−fr)/κ]`，κ是该模型的全线宽，不可把半宽直接填入κ。真实复数背景、线延迟和失配不在该简式中；复数残差转圈、基线倾斜、扫频方向不一致时，先检查归一化/频率轴/功率依赖，再决定扩模型。只用幅度极小值忽略相位可能使参数相关性增大。

光谱峰未覆盖：移动或扩大扫描；多个峰：检查读出态、功率、非谐跃迁/碰撞及控制映射。不得自动把最大峰当目标跃迁。有限脉冲谱可以不是Lorentzian；只有与扫描条件相符的模型才可解释宽度。峰中心恢复成功不等于T2已由线宽精确测得。

## 2. Rabi、幅度和π/2

归属：s03的sq_rabi_amplitude、sq_rabi_time、sq_rabi12、sq_amplitude_fine。原步骤主目标是幅度Rabi，已绑定时长法仅为辅助；制备/分类异常分别借用s04诊断。

在共振、弱驱动且增益线性的理想极限，`P1=sin²(π fR t)`，`tπ=1/(2 fR)`。存在失谐时布居频率与失谐共同决定，不能将全部频率变快都解释为幅度偏大。先用Ramsey约束失谐，再检验幅度与旋转角的局部响应。

固定脉冲形状与时长的线性近似可给 `Anew≈Aold·θtarget/θestimated`，但必须用邻近幅度点或误差放大实测斜率验证。π门与π/2门不应永久假定完全相同比例；包络截断、DRAG、失谐或非线性改变后分别复核。时间Rabi拟合出的衰减参数不是直接T1或T2。

多频/拍频、振幅大幅变化、拟合残差有结构：拒绝单频success作为校准通过，检查频谱、串扰、激发更高能级及采样别名。对比度低可能是读出、制备、退相干、失谐或扫描选择，不能直接提高驱动功率。

## 3. Ramsey及频率方向

方法primary owner：s07 / sq_ramsey；s02可通过总控借用其频差证据进行精频。原s07主目标T2CPMG不由Ramsey代替。

必须记录双正交定义。twin使用 `z=(2Px−1)−i(2Py−1)≈C exp(−t/T2+i2πδt)`，δ=fq−fdrive。按此约定驱动更新为 fdrive+δ；改变Y脉冲极性或复数构造会反号。先用已知正负失谐注入测试验证符号。

均匀网格只确定频率模采样率；小残差和极小局部stderr不能排除别名。约束总失谐，不只约束人为offset；必要时用粗谱先验、不同时间间距、非均匀补点或短时扫描分辨分支。相位unwrap前检查低对比度、π跳变与时间排序；不要对失去相干的数据强行拟合相位直线。

拟合到边界/FFT初值不可行是数值配置问题；不得据此宣布器件没有相干。已确认的原twin初值问题见[已知限制](known_limitations.md)。不同T2窗口给出不同结果可能提示非指数噪声、漂移或SPAM；增shots只能减少统计部分，不能修正这些偏差。

## 4. T1、T2与相干性

归属：s06 / sq_t1、sq_t1_frequency；s07 / sq_cpmg、sq_ramsey。参数记录须包含具体方法及工作点，不得统称同一个T2。

单指数T1模型 `P1=B+A exp(−t/T1)` 需要有效制备、可解释基线和足够时间覆盖。扫描太短会造成B/A/T1强相关；可扩大等待范围和插入长时基线点。短扫描在数学上仍可能可识别，不能用坏初值自动拒绝。振荡型残差、非单调或多时间尺度时先诊断模型失配。

在所选独立Markov模型内 `1/T2=1/(2T1)+γφ`、γφ≥0，因此T2≤2T1。测得两者来自不同时间/工作点或不同模型时，不能先裁剪T2再假装符合；检查区间、漂移和实验定义。Ramsey T2*、echo/CPMG相干时间不能混作同一指标。

CPMG/echo应明确π脉冲位置、相位、N及总等待时间。理想序列回聚焦静态失谐，默认可从`P=B+A exp[−(t/T)^β]`的包络模型开始；只有预声明分析相位ramp或独立证实的残余振荡才增加cos项，并单列频率定义。N增加后更差可能是π门误差、有限脉冲占比或序列失配，不能强制要求T2_CPMG大于T2_Ramsey。

T1/T2性能变差通常不是某个驱动幅度可直接“补好”的校准误差。优先确认制备/读出、工作点和稳定性；保持物理性能测量与控制参数优化职责分离。

## 5. DRAG与相位

归属：s03 / sq_drag；频率精校由s02负责，单门性能复核由s08负责。DRAG属于条件触发的扩展，不是首次粗Rabi启动的先决条件。

记录实际波形约定，例如 `Q(t)=β dI/dt` 或带归一化因子的形式。β的单位随约定变化，材料中的drag_alpha数值不能跨SDK复制。DRAG扫描需观察误差放大相位、旋转角与|2⟩泄漏，不以单一P1最大值替代所有目标。

相位误差与失谐、幅度、脉冲边沿可能耦合。先取得局部斜率/联合响应矩阵，再在信任域内调整。若响应矩阵病态，设计额外诊断实验，不用大步逆矩阵更新。修改DRAG后重验频率、幅度及门基准。

## 6. 读出、时序与门基准

归属：s04 / ro_iq01、ro_iq012；s05 / ro_optimize；s03 / sq_xy_z_timing；s08 / sq_rb、sq_xeb。s05优化后回s04新分类验证，s03改脉冲后s08重做规定基准。

独立准备|0⟩/|1⟩/需要时|2⟩，训练与验证按shot或采集批次拆分，防止同一记录同时训练并验收。保存混淆矩阵、准备误差假设和测量窗；分类器准确率与重复读出一致率不同，不能由一致率推出赋值正确率。平均IQ无法恢复单shot分布。

XY–Z或readout–XY延迟变更会影响重叠、测量窗和复位。扫描两个方向及参考点，检查触发量化、漂移和延迟极性；时延符号由实际通道定义确定。

RB常用 `A p^m+B` 描述对应协议的衰减；把p换算成平均错误率依赖维数与噪声/协议假设。每Clifford错误率、每物理门错误率、交错RB和XEB结果不能无条件互换。拟合可信区间、随机序列间方差和拟合区间敏感性都要报告；不以一条高P0短序列证明高门保真度。

来源：[手册§1、§2及单比特门精细校准](../resources/source_materials/06_工作脚本与数据/cal_manual_d0.md)、[twin公式追溯](../resources/twin_source/docs/model_traceability.md)、[研究知识](../resources/prior_knowledge/deep_study/)。


# 文档：knowledge/two_qubit_diagnostics.md
# 双比特偏差诊断知识

本章服务原7个双比特Agent。交换/ZZ归d02，宽度/chevron归d03，幅度归d04，条件相位/virtual Z归d05，XEB及补充表征归d06，优化归d07；所有方法保持[唯一归属](procedure_ownership.md)。

## 1. 前提和坐标

记录两个比特的单门/读出版本、idle工作点、耦合器映射、相对时序、控制模板和当前frame。参数必须属于当前工作点。twin采用qubit0在张量积左侧、计算索引(0,1,3,4)，Hz进入H/ℏ乘2π。其他后端若采用不同顺序，适配器必须显式置换并用已知态测试。

两态、三态Duffing和显式耦合器模型支持不同问题。三能级能表示|2⟩泄漏，但不含所有更高态。历史截断反例及适用范围见[已知限制](known_limitations.md)，不能用数学归一化通过证明模型适用于任意强驱动。

## 2. 交换与chevron

归属：d02的tq_exchange_off/tq_iswap与d03的tq_cz_chevron/tq_cz_return。两类振荡用不同子空间和矩阵元，不能交换使用g值；原图60 ns只为d03门宽示例，实际宽度由该工作点和脉冲族验证。

共振弱耗散的单激发交换有 `P01=sin²(2πg t)`，布居频率为2|g|，半交换时间为1/(8|g|)。失谐改变频率与对比度；仅从布居振荡不能恢复g的符号，也不能把失谐贡献当成g增加。

孤立|11⟩–|02⟩近似的矩阵元为√2g，`P02≈B+A·8g²/(Δ²+8g²)·sin²[π√(Δ²+8g²)t]`，共振首次返回时间1/(2√2|g|)。P11高也可能是零时长、关门、失谐过大或高阶返回，需结合条件相位、对比度、全部泄漏和单激发错误。短扫描chevron的有效crossing包含额外ZZ、其他能级位移和频率先验误差；不能直接视为精确裸非谐性。

条纹不对称/拟合差：检查扫描范围、Δ定义、上游频率、耦合/flux映射、脉冲边沿和时间量化。先定位是哪一项被数据支持，再改扫描或上游；增加shots不消除错误模型。

## 3. 条件相位与virtual Z

归属：d05的gate-off参考、重复门失谐精校、fresh双target相位与局域Z。d03/d04可借用相位工具，但实际主目标仍分别是宽度/幅度。

定义 `φcond=θ11−θ10−θ01+θ00`，目标π模2π；误差为wrap(φcond−π)，不能直接用相位差跨越±π断点。由相同X+iY约定的两控制分支测 `arg(z1 z0*)`。低相干时角度误差很大，应报告相干幅度和相位区间，拒绝低幅度相位看似达标的候选。

改变幅度/失谐/时长时，条件相位方向取决于局部响应。先采两侧点、unwrap局部连续分支并估斜率；不存在通用“相位不足就增大Z幅度”的规则。幅度-时长联合优化时记录固定量，防止两者同时改变导致无法归因。

twin测得局域φ后以 `exp(−iφn)` 补偿，下游分析相位 `phase_ideal+φ` 与其驱动约定相容。虚Z能消除局域相位，不能修复条件相位、残余交换、泄漏和耗散。新的混合门编译路径必须验证frame传播，不因单次层析符号测试通过而推导通用编译器正确。

## 4. ZZ与工作点

归属：d02的tq_residual_zz/tq_dressed_zz。QQswap测试交换，不能据此宣布ZZ已关断；此原图校正留在同一个d02内部。

`ζ=(E11−E10−E01+E00)/h`，相位随等待按所用观测约定取号。twin的X+iY等待相位为−2πζt，因此两控制分支频差取负得到ζ。必须用符号fixture确认，不仅比较绝对值。

测两个预设耦合强度的ζ属于表征，不等于搜索出ZZ最小工作点。搜索需要当前点及邻域响应、单门频移/相干复核、目标函数和独立验证。twin的residual_zz是在显式交换dressing之外附加的项；未来填入总实测ZZ时必须避免重复计数。

谱学dressed-state重叠分配在避交叉附近可能不唯一；当前0.7重叠门槛是工程拒绝规则。拒绝时应报告无法可靠指认，不能返回某个看似平滑的裸态ZZ。条件Ramsey还需控制分支制备、相干和读出稳定，两个频率区间的差要传播协方差或独立假设。

## 5. 泄漏、态层析与门保真度

归属：d06的tq_leakage、tq_truth_table、tq_state_tomography、tq_process_tomography及p0_bell；tq_xeb是原主方法。表征工具及源CZ套件不能替代XEB，d07优化后必须使用新XEB线路重测。

`Lcounts=含非计算态计数/全部有效shots`；`1−P11`还包含计算子空间内错误，不能称作泄漏。平均输入泄漏、最坏基态泄漏和某一准备态的泄漏不是同一量。存在分类错误时counts给出分类后的泄漏估计；零事件要报告上界。

线性Pauli层析在理想分析测量下重构计算块，全部shots作分母且对|2⟩零延拓；有限分析脉冲和分类错误使结果含SPAM。负特征值可能来自统计或模型/测量失配，既不能直接宣称状态不物理，也不能静默裁剪后报告提升后的保真度。保存原线性结果及任何后处理方法。

投影后非保迹通道的平均门保真度需保留 `Tr Ecomp(I)`；对d=4的CZ，分母d(d+1)=20，不能把生存项固定为4或除以生存率后忽略泄漏。图态保真度、三关联Bell witness、完整平均门保真度、RB/XEB属于不同指标。只测一个输入态不能完整判定通道。

重复CZ放大相位误差时，理想累计相位是`Nπ mod 2π`；可靠解包裹后拟合`θN−Nπ=Nε+offset`或计算每门相位。偶N的理想0不能误判为偏离π。d05的调频方向需局部斜率，d04的调幅也必须重看相位与泄漏，不能仅取平均P11最大。

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


# 文档：knowledge/worked_cases.md
# 两个完整复测案例

以下为原15步组织下的设计演练，保留此前具体观测、公式和回退条件，增加了owner分工。观测和决策均为合成示例，**没有实际设备执行，也不是本工程运行日志**。阈值、计数、轮次和预算为解释协议而设，非设备标准。实现者应将其转换为fixture测试；不能复制数字为某台设备的通过记录。

## A. 单比特频率偏移后的幅度联检

任务归属：s02负责精频目标，借用s07的Ramsey方法；s03负责幅度/相位联检；s04/必要时s05负责读出；s08负责原RB性能任务。supervisor安排跨步骤依赖、总预算和联合提交。当前只绑定部分内部方法，以下完整门基准仍需相应执行器，不能由数值时长Rabi代替。

目标：q0在工作点W0下残余失谐的95%区间完整落入±5 kHz，旋转角/相位与规定单门基准通过。基准revision=17，频率fdrive=5.000000 GHz；readout=ro7、XY模板=pulse4、幅度=0.200。冻结最大3轮、总shots预算、验证和恢复预留；锁住q0、AWG/IQ0、LO0、readout_bus0。

1. supervisor核全局前提，s02接收精频任务。 **PREFLIGHT。**读回revision17及上游版本。确认谱先验将总失谐限制在当前采样无别名区间；验证集与开发集使用不同请求ID。若ro7已过期，先读出复核，不能直接调频。
2. s02请求s07 / sq_ramsey工具观测，s07按方法合同分析。 **采集/诊断。**双正交Ramsey开发数据给δ=+40 kHz、95%区间[28,52] kHz，残差与声明噪声一致；已知符号fixture确认δ=fq−fdrive。方向成立，但不确定性仍大；先按合同允许的额外一批shots缩小估计区间，不能把同一数据重复读取算增加样本。
3. s02形成绝对频率候选，总控传播失效。 **候选。**新开发批合并后δ=+39 kHz、区间[35,43] kHz。候选绝对频率5.000039 GHz，单步在信任域内。标记依赖旧频率的幅度、相位/DRAG联检和门基准stale；记录基准快照及operation_id。
4. s02在候选频率下再次请求s07的新Ramsey证据。 **试用/独立复测。**apply后读回revision18和实际量化值。以新ID重新采Ramsey，得到+0.8 kHz、区间[−1.1,2.7] kHz，完整落入±5 kHz且模型质量通过。不能仅因平均值0.8小就跳过区间检查。
5. s03执行自己的幅度/相位内部闭环；必要时向s04借读出证据。 **幅度联检。**误差放大显示旋转角偏大。邻域三点证明在当前小范围幅度响应单调，且失谐已受控。若测得fR=4.12 MHz而目标4.00 MHz，线性近似给A≈0.194175；这只是候选。按实际斜率和步长上限试用，新独立旋转角数据验证。修改幅度后重检π/2、相位/DRAG敏感项及readout训练态可靠性。
6. s08运行真正RB主任务，s04确认分类，supervisor汇总全项。 **整组验收。**冻结的单门基准用新随机序列和新shots测量，必须报告协议与区间。Ramsey、旋转角、相位、泄漏及门基准required IDs全部齐全后，提交频率/幅度整组候选；重新检查ro7及父版本未变。
7. supervisor完成版本提交及跨步骤失效登记。 **COMMIT/retention。**提交回执和读回一致才发布新有效版本。保留revision17及两个候选、所有开发/验证数据、成本、范围和失效关系。受影响双比特门参数继续stale，直到其规定复测完成。

**失败变体。**如果独立Ramsey区间是[−2,8] kHz，返回INCONCLUSIVE；预算允许且噪声平稳时按预设批次增shots。如果残差出现第二频率，则转MODEL_MISMATCH并改诊断实验，不再追加相同shots。若apply超时但按operation_id查到已执行且revision18读回一致，继续验证；若查不到可信状态，进入UNKNOWN_ISOLATED，不能再加39 kHz。

## B. CZ候选通过相位但需重新验证泄漏

任务归属：d02确认闲置工作点，d03负责宽度/回归，d04负责幅度精调，d05负责失谐、条件相位与局域Z，d06负责原XEB及补充诊断；需要自动候选优化时由d07负责并返回d06新验证。supervisor维持联合版本与回退。

目标：q0–q1当前W2的条件相位误差区间完整落入±0.05 rad，四个输入基态的泄漏单侧上界<0.003，正确率与独立门基准满足冻结准则。两个单比特/readout版本均valid，coupler映射=c3、Zshape=z4、timing=t2。锁住两比特、coupler01、相应flux/AWG、trigger与readout总线。

1. d02核交换与ZZ独立前提，d03确认首回归范围。 **粗扫描。**开发chevron取得首次非零返回邻域。检查至少一周期、时间网格、跨失谐对比度、残差和拟合边界；不把零时长P11≈1当成功。有效g仅用于产生粗候选，保留模型偏差。
2. d04/d05分别给幅度或失谐局部斜率，supervisor冻结本轮实际旋钮。 **局部诊断。**候选的条件相位φ=3.000 rad，误差约−0.142 rad。归一化控制u=0.200；在u±0.005交错取样，局部相位响应斜率约+20 rad/u且区间不跨零，泄漏没有突升。Newton建议Δu≈0.00708，超过本轮步幅0.005，因此只试u=0.205。不能从相位大小直接猜增/减方向。
3. 相应Agent在其内部方法间选点，跨宽度/波形变化交d03。 **选点。**对有限候选分别测相位、泄漏与相干。开发数据的最小泄漏点只用于选择，不能用其乐观最小值验收。若边沿或timing被改，按依赖矩阵将chevron、相位和单激发错误相关节点失效，重新验证。
4. d05做fresh相位，d06的泄漏/基态工具提供独立观测。 **fresh复测。**试用候选后读回revision。两个target各测control=0/1，得到相位误差+0.018/+0.022 rad，其规定区间均在±0.05内；取得四种基态的完整九标签计数。示例|11⟩泄漏12/16384，点估计约0.00073；应计算预声明单侧上界及多项检查alpha分配后再与0.003比较。若缺|10⟩数据，即使其余全部通过也必须拒绝。
5. d05按frame约定处理局域Z并复测。 **局域Z。**由fresh分支测得局域相位，按已验证frame约定补偿；随后用补偿后的新分析序列复测。局域Z不能用来掩盖条件相位或泄漏不达标。
6. d06用真正XEB执行器完成原性能目标；d07若再调参，必须返回此步新采样。 **独立性能。**规定的四基态、相位、泄漏及门基准齐全才决定；图态层析可作额外证据，不能代替完整门指标。模拟器的offline Favg单列为模拟真值评价，不能标成实测RB。
7. supervisor审核全局版本和恢复能力后提交。 **提交。**所有父依赖仍是同一版本才提交(coupler/Z/CZ时间/virtual Z等实际变更集合)，发布具体工作点与适用范围。idle ZZ若作为上游工作点目标，需要在该点独立通过；仅测了两个预设ZZ值不能写“已最小化ZZ”。

**失败与回退变体。**若独立泄漏区间跨过0.003，保留失败点并按冻结规则追加一批或回退；不可因phase已通过而只删泄漏检查。若rollback回执丢失，按transaction查并读回绝对基准；未确认恢复则隔离，不释放给新的CZ任务。预算耗尽且候选未通过时，终态REJECTED_SAFE或UNKNOWN_ISOLATED取决于是否确认恢复，不是“校准完成”。

## C. 测试应核对的事件

每个案例都应检查：读版本→锁资源→冻结合同→采集ID/实际shots→候选hash→apply回执→fresh验证revision→全项判定→commit/rollback→读回→失效传播。另注入父版本变更、验证ID重复、漏基态、负计数、超时未知和恢复预算耗尽；这些变体必须拒绝或升级，不能通过删除检查降级成成功。

## D. 读出优化必须回到原第4步

s04用交织准备0/1（需要时2）采集独立训练/验证数据，保存混淆矩阵及准备误差边界。若分辨率不足，s04不调π幅度来硬补分类，而是把读出问题交总控安排s05。s05在冻结频率/功率范围内比较候选，保留全部搜索成本；找到候选后返回s04重新训练并用fresh验证集判定。若未显著改善或出现加热/漂移，总控恢复原读出频幅和分类器版本；所有依赖旧分类器的结果按矩阵重测。

首次启动可由s03在明确raw IQ模式下取得粗旋转候选，再交s04建立分类器；这不意味着原始IQ平均值已经完成赋值保真度训练。把s04和s05分为两个原功能框，是为了保留“分类验证—参数优化—新分类复测”的证据关系。


# 文档：knowledge/known_limitations.md
# 已知限制与待修任务

本表保留原始twin历史审查结果，不把角色重组当算法修复。NUM-01主要影响s06/T1；NUM-02影响s07/Ramsey辅助及借用它的s02；FIT-01影响s03/Rabi和s06/T1；VAL-01由d03/d05/d06相关CZ诊断消费者关注。`resources/twin_source`保留原包，不在文档编写时修复。运行适配器若增加外部门禁，应单独说明其覆盖；不能声称原源码已修复。此前审查使用NumPy1.26.4/SciPy1.13.1的小探针，不等同本工程完整验收。

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

## 原步骤与绑定的当前缺口

s03原幅度Rabi、s07原T2CPMG、d06原XEB尚未由现有时长Rabi、Ramsey和CZ源套件实现。该缺口属于执行覆盖不足，不是量子公式修复问题；分组报告不能据辅助通过写original_step_complete=true。其余未绑定方法同样按实际capability拒绝。44方法主归属集中到15个Agent后，原模型假设、数据条件、反例和真值评分边界继续有效。


# 文档：knowledge/evidence_index.md
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
| [new_attachments](../resources/new_attachments/) | 原双页PDF的8/7功能框、P0报告与提取文本；新owner映射的直接来源 | 箭头复测编号是新增步骤；附件指令获得执行权限 |
| [previous_edition](../resources/previous_edition/) | 旧细分方法卡、旧架构、QA及构建经验的历史追溯 | 旧角色数量、旧测试数或PASS就是新版通过 |
| [workflow ownership](../config/workflow_ownership.json) | 15原步骤、44内部方法的唯一primary owner | 方法归属等于参数永久写权限或原主目标已绑定 |

知识条目应新增schema_version、claim、scope、conditions、evidence_refs、evidence_kind、source_hash、reviewed_at、counterexamples、next_test、status。状态建议为SOURCE_CLAIM、DERIVED、LOCALLY_REPRODUCED、SIMULATION_VALIDATED、HARDWARE_VALIDATED；同一条知识不同范围可以有不同状态。

记录应区分事实、推导和工程建议。例如“共振交换布居频率2|g|”是指定Hamiltonian下推导；“先做三点局部斜率再调参”是工程策略；“某设备每周校准一次”只有在具体设备连续记录支持时才是范围受限的经验。新反例必须关联旧条目并降低适用范围，不覆盖历史。

新版建议先读[15步重组](../docs/reorganization.md)、[方法归属](procedure_ownership.md)和[Agent内部闭环](agent_internal_loop.md)。随后阅读：[总体合同](../docs/architecture.md) → [依赖矩阵](parameter_dependencies.md) → [单比特](single_qubit_diagnostics.md)/[双比特](two_qubit_diagnostics.md) → [总控策略](../docs/supervisor_policy.md) → [worked cases](worked_cases.md) → [验收](../docs/validation_strategy.md) → [已知限制](known_limitations.md)。

当前工程包执行范围可由[物理适配器](../quantum_agents/twin.py)与[CZ完整性桥接](../quantum_agents/cz_suite.py)核对；实际运行记录位于output，发布报告应引用最终冻结目录和哈希。源码支持某个阶段与专项Agent已绑定可恢复执行是两种状态，不可合并计数。

新版正式运行证据为根目录output/twin-final、cz-current及integration_acceptance.json；旧版输出只归档引用。方法级验证与原步骤完成分列；s03、s07、d06的辅助替代风险必须进入结论。


# 文档：knowledge/procedure_ownership.md
# 内部方法的唯一归属与跨步骤调用

原图定义15个实验Agent；下表保留44个procedure，每个只出现一次primary owner。它们是原步骤内部的方法，不是44个活跃Agent。完整科学内容已经并入各新步骤卡；旧方法卡仅作为技术追溯材料归档。参数读写权限由任务合同规定，不能由primary owner标签自动获得。

跨步骤借用采用`caller_agent_id → primary_owner_id/procedure_id → evidence_id`：记录谁请求、谁维护方法、实际执行何种扫描、结果给哪个原目标使用。一次采集只计一次成本；不同目标可引用同一证据，但独立验证不能与选点复用同一采集。当前CLI通过声明该procedure的owner运行；自动跨owner编排须由宿主显式实现。

| procedure_id | 方法名称 | primary owner / 原步骤 | 技术追溯 |
| --- | --- | --- | --- |
| `ro_resonator_survey` | 读出腔寻峰与偏置后定频 | [s01_readout_frequency：读取腔定频](../agents/s01_readout_frequency/card.md) | [归档方法细节](../resources/previous_edition/agents/ro_resonator_survey/card.md) |
| `ro_power_shift` | 高低功率色散位移检查 | [s01_readout_frequency：读取腔定频](../agents/s01_readout_frequency/card.md) | [归档方法细节](../resources/previous_edition/agents/ro_power_shift/card.md) |
| `ro_bias_map` | 量子比特偏置与腔频映射 | [s01_readout_frequency：读取腔定频](../agents/s01_readout_frequency/card.md) | [归档方法细节](../resources/previous_edition/agents/ro_bias_map/card.md) |
| `sq_spectroscopy` | 单比特f01粗频谱 | [s02_qubit_frequency：比特定频](../agents/s02_qubit_frequency/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_spectroscopy/card.md) |
| `sq_anharmonicity` | f02双光子谱与非谐性 | [s02_qubit_frequency：比特定频](../agents/s02_qubit_frequency/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_anharmonicity/card.md) |
| `sq_frequency_fine` | 相位斜率精校频率 | [s02_qubit_frequency：比特定频](../agents/s02_qubit_frequency/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_frequency_fine/card.md) |
| `sq_rabi_amplitude` | Rabi幅度粗校 | [s03_rabi_amplitude：Rabi定幅](../agents/s03_rabi_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_rabi_amplitude/card.md) |
| `sq_rabi_time` | Rabi时长扫描 | [s03_rabi_amplitude：Rabi定幅](../agents/s03_rabi_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_rabi_time/card.md) |
| `sq_rabi12` | f12驱动幅度与\|2>制备 | [s03_rabi_amplitude：Rabi定幅](../agents/s03_rabi_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_rabi12/card.md) |
| `sq_amplitude_fine` | 重复门幅度精校 | [s03_rabi_amplitude：Rabi定幅](../agents/s03_rabi_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_amplitude_fine/card.md) |
| `sq_drag` | DRAG系数精校 | [s03_rabi_amplitude：Rabi定幅](../agents/s03_rabi_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_drag/card.md) |
| `sq_xy_z_timing` | XY–Z通道相对时延 | [s03_rabi_amplitude：Rabi定幅](../agents/s03_rabi_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_xy_z_timing/card.md) |
| `ro_iq01` | 二态IQ分类训练与独立复测 | [s04_readout_fidelity：读取保真度测试](../agents/s04_readout_fidelity/card.md) | [归档方法细节](../resources/previous_edition/agents/ro_iq01/card.md) |
| `ro_iq012` | 三态IQ分类与泄漏可分辨性 | [s04_readout_fidelity：读取保真度测试](../agents/s04_readout_fidelity/card.md) | [归档方法细节](../resources/previous_edition/agents/ro_iq012/card.md) |
| `ro_optimize` | 读出频率与幅度二维优化 | [s05_readout_optimization：读取参数优化](../agents/s05_readout_optimization/card.md) | [归档方法细节](../resources/previous_edition/agents/ro_optimize/card.md) |
| `sq_t1` | T1能量弛豫 | [s06_t1：T1测试](../agents/s06_t1/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_t1/card.md) |
| `sq_t1_frequency` | T1随工作频率扫描 | [s06_t1：T1测试](../agents/s06_t1/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_t1_frequency/card.md) |
| `sq_cpmg` | CPMG/echo相干时间 | [s07_t2：T2测试](../agents/s07_t2/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_cpmg/card.md) |
| `sq_ramsey` | 双正交Ramsey与T2*估计 | [s07_t2：T2测试](../agents/s07_t2/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_ramsey/card.md) |
| `sq_rb` | 单比特随机基准测试 | [s08_single_qubit_rb：单比特RB测试](../agents/s08_single_qubit_rb/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_rb/card.md) |
| `sq_xeb` | 单比特XEB | [s08_single_qubit_rb：单比特RB测试](../agents/s08_single_qubit_rb/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_xeb/card.md) |
| `sq_flux_map` | Z脉冲幅度—f01映射 | [d01_flux_spectroscopy：二维比特能谱](../agents/d01_flux_spectroscopy/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_flux_map/card.md) |
| `tq_exchange_off` | QQswap交换关断点 | [d02_idle_interaction：两比特闲置工作点](../agents/d02_idle_interaction/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_exchange_off/card.md) |
| `tq_residual_zz` | 残余ZZ独立测量 | [d02_idle_interaction：两比特闲置工作点](../agents/d02_idle_interaction/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_residual_zz/card.md) |
| `tq_dressed_zz` | 工作点有效dressed ZZ | [d02_idle_interaction：两比特闲置工作点](../agents/d02_idle_interaction/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_dressed_zz/card.md) |
| `tq_coupler_resonator_map` | 耦合器偏置与谐振腔响应 | [d02_idle_interaction：两比特闲置工作点](../agents/d02_idle_interaction/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_coupler_resonator_map/card.md) |
| `tq_iswap` | QQ共振交换与iSWAP时长 | [d02_idle_interaction：两比特闲置工作点](../agents/d02_idle_interaction/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_iswap/card.md) |
| `tq_cz_setup` | CZ波形与工作点合同 | [d03_cz_width：CZ宽度粗扫](../agents/d03_cz_width/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_setup/card.md) |
| `tq_cz_chevron` | 11↔02避交叉二维粗扫 | [d03_cz_width：CZ宽度粗扫](../agents/d03_cz_width/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_chevron/card.md) |
| `tq_cz_return` | 第一非零回归时长精修 | [d03_cz_width：CZ宽度粗扫](../agents/d03_cz_width/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_return/card.md) |
| `tq_cz_rough` | CZ回归与条件相位联合粗定点 | [d03_cz_width：CZ宽度粗扫](../agents/d03_cz_width/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_rough/card.md) |
| `tq_qz_cz_timing` | 量子比特Z–耦合器Z时延 | [d03_cz_width：CZ宽度粗扫](../agents/d03_cz_width/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_qz_cz_timing/card.md) |
| `tq_cz_amplitude_fine` | 重复CZ耦合器幅度精校 | [d04_cz_amplitude：CZ幅度精调](../agents/d04_cz_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_amplitude_fine/card.md) |
| `tq_cz_detune_fine` | 重复CZ失谐/条件相位精校 | [d05_cz_detuning：CZ失谐与条件相位精调](../agents/d05_cz_detuning/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_detune_fine/card.md) |
| `tq_gateoff_reference` | 四条件gate-off相位与对比度参考 | [d05_cz_detuning：CZ失谐与条件相位精调](../agents/d05_cz_detuning/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_gateoff_reference/card.md) |
| `tq_cz_phase_verify` | 冻结CZ的双target独立相位复核 | [d05_cz_detuning：CZ失谐与条件相位精调](../agents/d05_cz_detuning/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_phase_verify/card.md) |
| `tq_local_phase` | CZ局域动力学相位与virtual Z | [d05_cz_detuning：CZ失谐与条件相位精调](../agents/d05_cz_detuning/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_local_phase/card.md) |
| `tq_xeb` | 双比特CZ XEB独立基准 | [d06_cz_xeb：CZ XEB测试](../agents/d06_cz_xeb/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_xeb/card.md) |
| `tq_leakage` | CZ泄漏与重复门泄漏诊断 | [d06_cz_xeb：CZ XEB测试](../agents/d06_cz_xeb/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_leakage/card.md) |
| `tq_truth_table` | CZ四输入九输出布居表 | [d06_cz_xeb：CZ XEB测试](../agents/d06_cz_xeb/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_truth_table/card.md) |
| `tq_state_tomography` | 九基双比特态层析 | [d06_cz_xeb：CZ XEB测试](../agents/d06_cz_xeb/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_state_tomography/card.md) |
| `tq_process_tomography` | 16输入×9基过程层析 | [d06_cz_xeb：CZ XEB测试](../agents/d06_cz_xeb/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_process_tomography/card.md) |
| `p0_bell` | P0半交换Bell关联验证 | [d06_cz_xeb：CZ XEB测试](../agents/d06_cz_xeb/card.md) | [归档方法细节](../resources/previous_edition/agents/p0_bell/card.md) |
| `tq_optimizer` | 受约束CZ参数优化与独立复测 | [d07_cz_optimizer：CZ参数优化](../agents/d07_cz_optimizer/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_optimizer/card.md) |

## 典型借用与完成边界

| 发起步骤 | 借用方法及primary owner | 返回的证据 | 原目标如何继续 |
| --- | --- | --- | --- |
| s02比特定频 | s07 / sq_ramsey | 符号已校验的残余频差、包络、别名/模型诊断 | s02判断频率候选；s03复核旋转角；s07的CPMG原目标不变 |
| s03 Rabi定幅 | s04 / ro_iq01或ro_iq012 | 有效分类与准备误差边界 | 分类失效先回s04，不能用幅度补读出偏差；初始可用明确raw IQ bootstrap |
| s04三态分类 | s03 / sq_rabi12 | \|2⟩制备与12旋转候选 | 交织制备/分类独立验证，避免标签与分类循环自证 |
| d03 CZ宽度 | d05 / tq_gateoff_reference、tq_cz_phase_verify | gate-off参考及两方向相位诊断 | 高回归必须同时具有非局域π相位；不因方法跨owner而新增角色 |
| d04或d05精调 | d06 / tq_leakage、tq_truth_table | 完整九标签计数与输入依赖 | 调幅/调频后重新检查泄漏和计算子空间错误；仍需原XEB任务 |
| d07优化 | d06 / tq_xeb及补充诊断 | 未参与选点的新随机线路与计数 | 训练分数不能自验；缺真正XEB执行器则原性能目标保持未完成 |

s02的非谐性/精频、s03的DRAG/时序等是条件触发的内部扩展，不要求第一次粗谱/Rabi启动前完成所有扩展，避免bootstrap死锁。跨步骤迭代通过新task/revision表达；初始依赖图保持无环。


# 文档：knowledge/agent_internal_loop.md
# 一个原步骤Agent怎样独立工作

本协议适用于8个单比特和7个双比特Agent。它要求每个Agent对原图功能框负责，而非将采集、拟合、诊断、调参和重测再拆成多个模型角色。公共模块提供确定性的能力查询、数据处理、证据记录、参数版本和独立验收，不改变原15步边界。

## 输入、输出与局部状态

输入为原步骤目标、主方法、可选内部方法、有效上游版本、原始观测或采集能力、允许参数/范围/单位、冻结准则、局部预算及资源。缺主方法或必要前提时返回BLOCKED_CAPABILITY/UPSTREAM_STALE；若仅能运行辅助，必须由合同明确目标并输出auxiliary_only，不能降级原目标后自行通过。

输出包含：owner_agent_id、main_procedure_id、实际procedure_runs、数据/模型状态、偏差及候选解释、方向依据、参数候选、fresh验证结果、剩余成本、影响的下游owner、恢复状态及original_step_result。`procedure_result=passed`与`original_step_complete`分别计算；不存在任一内部方法通过即关闭原步骤的规则。

| 局部阶段 | Agent要完成的判断 | 公共模块执行/核对 | 不通过后的去向 |
| --- | --- | --- | --- |
| 接收/选法 | 原主目标及现象对应哪个内部方法；是否可借用另一owner的工具 | 唯一归属、能力、上游版本、权限 | 缺能力回总控，不能伪造替代结果 |
| 扫描设计 | 固定量、扫描量、可辨识范围、频率/相位分支、所需参考与shots | 单位、范围、预算、共享资源和实际量化 | 信息不足可有限改设计；新合同版本 |
| 采集 | 复位/制备/参考/随机顺序是否符合方法 | 请求与实际参数、计数、回执及成本 | 数据损坏重新采；状态未知先reconcile |
| 拟合/诊断 | 噪声、残差、相关性、错模型、漂移、SPAM还是控制偏差 | 确定性拟合与完整证据存储 | 统计问题加新shots；模型问题换诊断，不盲重试 |
| 调参建议 | 哪个参数有因果证据；局部斜率符号与信任域 | 候选绝对值、参数范围、哈希、依赖影响 | 方向不明时加邻点；超域/跨步骤改动交总控 |
| 试用/重测 | 冻结候选后用什么新数据判定原目标 | 试用读回、fresh验证、全部必需项和恢复 | 拒绝或不确定则回退；状态未知隔离 |
| 报告 | 哪个方法通过、原步骤还缺什么、是否建议跨步骤复测 | 原始证据引用、成本、版本与最终门禁 | 总控安排下一owner或升级，不删除失败项 |

Agent可在局部预算内做预声明repeat、增加shots或改变扫描；看到验证结果后再调参意味着该数据转为开发集，必须另采验证。不能从模型置信度代替统计区间，也不能用方法名推定设备能力。

## 三个具体分支

**s03幅度偏差。**先固定频率/脉宽/包络和读出版本，扫幅度及规定重复数；区分旋转角偏差、对比度下降和泄漏。局部响应支持θ∝A时才给Anew≈Aold·θtarget/θestimated；验证π与π/2、相位和泄漏。若频率漂移，返回总控请求s02；若分类不稳请求s04；DRAG修改后本步骤内重做幅度再请求s08基准。只运行时长Rabi时，报告时长辅助结果，不关闭幅度主目标。

**s07相干测量。**先确认主合同是CPMG/echo还是辅助Ramsey。CPMG冻结π序列、总idle时间与含脉冲总时长，默认包络不能把静态失谐当作未回聚焦Ramsey项；Ramsey冻结双正交符号、时间采样和频率先验。两者分别报告。T2异常先排制备/读出/漂移，不直接写一个更大的T2配置值。

**d05相位偏差。**固定门宽与波形，以spectator两分支、两个target恢复条件相位；重复N门时检查Nπ模2π与可靠unwrap。取得局部dε/df后才按−ε/(dε/df)提候选；检验邻点、对比度、泄漏与回归。若幅度耦合则请求d04，若需改波形/宽度则回d03。局域virtual Z不能补非局域误差；d06的新性能验证仍单独完成。

上述分支是可实现合同，不宣称全部执行器已绑定。执行字段、拒绝行为与方法级能力以[运行时接口](../docs/runtime_contract.md)为准。
