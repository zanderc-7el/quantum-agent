# CZ失谐与条件相位精调 · 原流程实验智能体

角色ID：`d05_cz_detuning`。原图第2页第5个功能方框；原函数：`TTGAczDelicateDetune`。

本包只注册15个实验智能体和1个总控。下列内部方法不是独立智能体，不能绕过本角色对外调用；本角色负责自己的调参、诊断、精校和重测，不再拆分专门调参agent。

## 当前能力边界

原框 TTGAczDelicateDetune 的 N×量子比特幅度精调未绑定。源 CZ 套件包含 gate-off、双 target 相位复核和分析相位 VZ 子阶段；这些诊断不能冒充重复门失谐优化，也不能将所有 N 的累计相位直接对 π。

execution_status=`source_suite_only`；原框主方法状态=`contract_only`；默认主方法=`tq_cz_detune_fine`。精确运行范围以 `config/bindings.json` 与运行时回执为准。

实机SDK尚未接入。模型只能产生建议；不允许直接执行shell、写机器参数、提交参数版本或宣称通过。源程序模拟结果不等于本次任务证据，不能把辅助方法成功升级成原流程主方法成功。

## 自主闭环与内部方法选择

先采 gate-off 参考以固定相位零点，再做 N×量子比特幅度精调；明确定义每门或累计相位，处理 unwrap 与符号，冻结候选后做双 target fresh 相位复核和局域 VZ 验证。

1. 读取本次参数快照、授权窗口、绑定能力、设备/种子/模型版本、可用预算和证据ID；缺少主方法执行器时明确阻塞，不以辅助动作替换。
2. 按主方法或明确诊断触发选择内部方法，冻结固定量、扫描范围、观测单位、拟合模型及验收门槛。
3. 通过受限测量入口申请采集，保留原始counts/IQ、实际扫描值、配置哈希和操作回执；不得只留拟合摘要。
4. 自行诊断可辨识性、边界拟合、读出/制备混淆及物理偏差；按下列分支提出有方向依据的有界参数或扫描建议。
5. 总控受控临时应用后，本角色申请fresh复测；验证数据和候选选择数据分开，复测失败则保留失败证据并回退。
6. 超限、模型不适用、回执未知或依赖失效时停止该分支并升级；不得无界重试或新增调参agent。

## 主链依赖与回测

- d04_cz_amplitude

主链依赖是框级调度顺序。内部扩展方法可以因诊断暂不执行或等待后续校准；这不允许把未执行主方法记为完成。回测是新任务实例，不是往静态DAG增加反向边。

无本框专属反向回测边；通用上游复校须携带失效证据ID。

## 参数范围与预算

本角色所有内部方法的读取/失效范围并集；不是每次任务的全必填参数。首次主方法与条件扩展按对应procedure精确合同检查。

框级读取范围：

- cz.pulse_contract
- cz.coupler_amp_candidate
- qubit.f01_z_inverse
- phase.gateoff_reference_complex
- qubit.pi_duration_s
- qubit.f01_hz
- readout.classifier3_version
- timing.version
- cz.frozen_candidate
- cz.local_phases_rad
- drive.frame_convention

框级候选写入范围（不是直接写权限）：

- cz.interaction_frequency_candidate_hz
- cz.conditional_error_per_gate_rad
- phase.gateoff_reference_complex
- phase.reference_version
- cz.phase_verification_evidence
- cz.local_phases_rad
- cz.virtual_z_angles_rad
- drive.phase_frame_update

框级上限：max_attempts=3；max_shots=20000000；max_seconds=3600。角色总上限是所有内部方法上限之和；不是默认全部运行。每个方法仍受自己的上限、当前授权预算与运行时剩余额度约束。

## 内部方法完整操作规范

### 内部方法 `tq_cz_detune_fine` · 重复CZ失谐/条件相位精校

职责：用门数量放大每门条件相位误差，调整一个比特交互频率的实际Z幅度。

范围：重复CZ失谐/条件相位精校

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- TTGAczDelicateDetune

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_cz_amplitude_fine
- tq_gateoff_reference

#### 固定量

- 另一比特频率
- coupler幅度
- 门宽/shape
- target/spectator定义
- shots

#### 扫描量

- CZ_count_N
- one_qubit_interaction_frequency_hz

#### 序列

1. 每点spectator0/1各两条X/Y线路
2. 执行N个CZ
3. 恢复累计条件相位
4. 基于小N和连续性解包裹
5. 拟合每门误差
6. 调频后fresh新N验证

#### 原始观测

- 累计θ_N rad
- 分支/unwrap证据
- contrast
- 相位对N斜率
- L

#### 拟合、公式、单位与符号

- 理想累计θ_N=Nπ mod2π
- 若报告每门相位须可靠unwrap后除N，或拟合θ_N-Nπ=Nε+offset
- 手册L962“平均相位对π”未说明归一化，不能所有N原始相位直接对π
- Δf≈-ε/(dε/df)仅局部导数有证据时适用

#### 偏差诊断与调参分支

1. 条件：N奇偶/相位分支不明确

   动作：blocked并增加小N参考，不硬unwrap

   理由：避免把理想偶N=0判错

2. 条件：dε/df近零或换号

   动作：缩小域或回粗扫

   理由：不能固定向上/下调频

3. 条件：phase改善但泄漏增加

   动作：交回G/return联合迭代

   理由：失谐旋钮也影响回归

#### 重测、回退与依赖失效

- fresh新N及N=1验证
- 调频使localphase、leakage、truth/tomography失效
- 回退绝对频率及映射幅度配对值

#### 验收

- 累计/每门定义一致
- branch有证据
- 相位/泄漏/contrast达criteria

#### 参数读取

- cz.pulse_contract
- cz.coupler_amp_candidate
- qubit.f01_z_inverse
- phase.gateoff_reference_complex

#### 候选参数写入范围（仅 proposal）

- cz.interaction_frequency_candidate_hz
- cz.conditional_error_per_gate_rad

#### 方法预算

max_attempts=3；max_shots=8000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.5，L930–962
- `resources/source_materials/two-qubit-page-2.txt`：⑤ TTGAczDelicateDetune

### 内部方法 `tq_gateoff_reference` · 四条件gate-off相位与对比度参考

职责：分别测target0/1与spectator0/1的参考，避免一条参考补偿所有SPAM条件。

范围：四条件gate-off相位与对比度参考

源实现边界：quantam三能级CZ套件内reference阶段；非独立runtime agent

存档执行状态：`source_suite_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- quantum_twin.two_qubit_experiments.run_two_qubit_experiments

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- sq_rabi_time
- ro_iq012

#### 固定量

- π2参数
- idle
- 参考线路不插门
- shots_per_condition

#### 扫描量

- target∈{0,1}
- spectator∈{0,1}
- X/Y分析基

#### 序列

1. 按spectator准备0或1
2. target制备+
3. 无门无等待
4. X/Y分析
5. 四条件共8采集
6. fresh重复参考检查漂移

#### 原始观测

- z_ref=X+iY
- contrast
- counts
- 准备与分析版本

#### 拟合、公式、单位与符号

- z_ref为复数参考，目标z_norm=z_gate/z_ref
- 小模长会放大噪声必须设预声明contrast下界
- 参考仅校正该线路相位/幅度，非完整SPAM反演

#### 偏差诊断与调参分支

1. 条件：某分支contrast低

   动作：阻塞除法与相位估计，查准备/读出

   理由：避免数值放大

2. 条件：ref随时间漂移

   动作：重新交织参考并缩短候选批次

   理由：旧参考不能永久复用

3. 条件：四条件稳定

   动作：提供reference证据ID

   理由：不可写物理真值

#### 重测、回退与依赖失效

- 门参数冻结后的独立phase复测引用同批或fresh有效参考
- π2/idle/readout更新全部参考失效
- 不得将参考数据当验证门数据

#### 验收

- 四分支齐全且contrast及重复性达criteria

#### 参数读取

- qubit.pi_duration_s
- qubit.f01_hz
- readout.classifier3_version
- timing.version

#### 候选参数写入范围（仅 proposal）

- phase.gateoff_reference_complex
- phase.reference_version

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程扩展：手册L874条件相位；P0报告L2283–2419
- `resources/new_attachments/p0-report-extracted.txt`：L2283–2419

### 内部方法 `tq_cz_phase_verify` · 冻结CZ的双target独立相位复核

职责：门参数冻结后用新采样确认两方向条件相位，不将验证数据再用于挑点。

范围：冻结CZ的双target独立相位复核

源实现边界：quantam双target8条fresh采集，源suite内实现

存档执行状态：`source_suite_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- quantum_twin.two_qubit_experiments.run_two_qubit_experiments

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_cz_return
- tq_gateoff_reference

#### 固定量

- 冻结gate_hash
- π2
- reference版本
- shots
- phase sign

#### 扫描量

- target0/1
- spectator0/1
- X/Y分析基

#### 序列

1. 锁定candidate hash
2. 8条fresh线路
3. 归一复参考
4. 提取每target局域/条件相位
5. 与冻结criteria比较
6. 提交验证建议

#### 原始观测

- fresh counts
- 两方向θcond
- localφ
- contrast
- batch/seed IDs

#### 拟合、公式、单位与符号

- θcond,q=wrap(arg zq,1-arg zq,0)，error=wrap(θcond,q-π)
- 局域φq=arg zq,0
- 公共参考诱发相关误差需统计传播
- 两方向不一致是模型/SPAM信号

#### 偏差诊断与调参分支

1. 条件：验证失败

   动作：返回recalibrate而非从复测中重选候选

   理由：保证独立性

2. 条件：两target结论矛盾

   动作：检查spectator T1/π2/SPAM/符号

   理由：不可平均掉矛盾

3. 条件：通过所有观测门槛

   动作：只输出verify proposal给独立验收

   理由：agent无commit/passed权限

#### 重测、回退与依赖失效

- 候选改变后所有fresh验证作废并新task
- 若使用验证调参必须另采新验证
- 失败回退上次有效gate

#### 验收

- 新数据/冻结hash/参考完整
- 两方向相位和contrast合格

#### 参数读取

- cz.frozen_candidate
- phase.gateoff_reference_complex
- readout.classifier3_version

#### 候选参数写入范围（仅 proposal）

- cz.phase_verification_evidence
- cz.local_phases_rad

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程细化：3.3.3 L874条件相位
- `resources/new_attachments/p0-report-extracted.txt`：L3193–3374

### 内部方法 `tq_local_phase` · CZ局域动力学相位与virtual Z

职责：扣除可分离的单比特相位，保留非局域条件相位误差，不优化隐藏unitary。

范围：CZ局域动力学相位与virtual Z

源实现边界：quantam仅更新后续分析pulse相位并离线同相位评分；非硬件通用帧服务

存档执行状态：`source_suite_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- CZProcessTomography
- quantum_twin.two_qubit_experiments.run_two_qubit_experiments

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_cz_phase_verify

#### 固定量

- CZ参数
- local phase测量证据
- pulse conventions
- readout

#### 扫描量

- 必要时analysis_phase_rad验证扫描

#### 序列

1. 读取fresh localφ0/φ1
2. 按实现约定产生相位帧更新
3. 编译预览
4. 用独立Ramsey/图态线路验证符号与作用

#### 原始观测

- 相位更新前后复相干
- phase frame回执
- θcond保持性

#### 拟合、公式、单位与符号

- 目标局域校正Ulocal=exp(-iφ0 n0)exp(-iφ1 n1)
- quantam分析pulse相位更新为phase_ideal+φq实现测量等价补偿
- 不能不看约定直接将驱动相位减φ

#### 偏差诊断与调参分支

1. 条件：localphase扣除后条件相位仍错

   动作：回Detune，不继续VZ补偿

   理由：局域Z不能修非局域θ

2. 条件：更新后相位偏差翻倍

   动作：立即回退并核对符号/顺序

   理由：帧约定可能反向

3. 条件：相位对齐且θ保持

   动作：提出帧更新候选

   理由：不把Favg设为1

#### 重测、回退与依赖失效

- fresh图态/相位验证
- CZ/π2/频率/同步更新使VZ失效
- 帧采用版本化绝对值或幂等更新ID

#### 验收

- 观测符号正确
- 无重复加相位
- 校正只作用局域项

#### 参数读取

- cz.local_phases_rad
- drive.frame_convention
- cz.frozen_candidate

#### 候选参数写入范围（仅 proposal）

- cz.virtual_z_angles_rad
- drive.phase_frame_update

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.6，L964–988
- `resources/new_attachments/p0-report-extracted.txt`：L3375–3409

## 框级验收与发布边界

- 原框主方法所需测量证据必须独立满足合同；辅助方法通过不能替代主方法。
- 模型只提出建议；最终通过/参数发布由独立判定与总控硬门禁决定。

每个被选内部方法仍须满足其完整验收项、剩余预算及独立复测；跳过的扩展方法必须标记未执行和原因。最终结论必须分别说明原框主方法、已运行辅助方法、未实现能力与证据新鲜度。

## 来源索引

- `resources/new_attachments/双比特测试.pdf`：第2页，第5个功能方框，TTGAczDelicateDetune
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.5，L930–962
- `resources/source_materials/two-qubit-page-2.txt`：⑤ TTGAczDelicateDetune
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程扩展：手册L874条件相位；P0报告L2283–2419
- `resources/new_attachments/p0-report-extracted.txt`：L2283–2419
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程细化：3.3.3 L874条件相位
- `resources/new_attachments/p0-report-extracted.txt`：L3193–3374
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.6，L964–988
- `resources/new_attachments/p0-report-extracted.txt`：L3375–3409
