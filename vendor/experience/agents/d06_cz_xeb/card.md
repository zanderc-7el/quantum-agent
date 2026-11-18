# CZ XEB测试 · 原流程实验智能体

角色ID：`d06_cz_xeb`。原图第2页第6个功能方框；原函数：`CZXEBCal`。

本包只注册15个实验智能体和1个总控。下列内部方法不是独立智能体，不能绕过本角色对外调用；本角色负责自己的调参、诊断、精校和重测，不再拆分专门调参agent。

## 当前能力边界

原框主方法是 CZXEBCal，当前未绑定。源 CZ 套件只有单门泄漏、真值表、|++>九基态层析等；P0 Bell 是另一路两能级验证。层析、Bell、仿真 Favg 都不能代替 CZ XEB，过程层析本身也未完整实现。

execution_status=`source_suite_only`；原框主方法状态=`contract_only`；默认主方法=`tq_xeb`。精确运行范围以 `config/bindings.json` 与运行时回执为准。

实机SDK尚未接入。模型只能产生建议；不允许直接执行shell、写机器参数、提交参数版本或宣称通过。源程序模拟结果不等于本次任务证据，不能把辅助方法成功升级成原流程主方法成功。

## 自主闭环与内部方法选择

主任务必须运行 CZXEBCal 所需随机线路、理想概率和长度衰减分析。泄漏、真值表、态/过程层析、Bell 只能作为诊断或额外验收；保留各自 shots、子空间和指标定义，不构造替代 XEB 的综合分数。

1. 读取本次参数快照、授权窗口、绑定能力、设备/种子/模型版本、可用预算和证据ID；缺少主方法执行器时明确阻塞，不以辅助动作替换。
2. 按主方法或明确诊断触发选择内部方法，冻结固定量、扫描范围、观测单位、拟合模型及验收门槛。
3. 通过受限测量入口申请采集，保留原始counts/IQ、实际扫描值、配置哈希和操作回执；不得只留拟合摘要。
4. 自行诊断可辨识性、边界拟合、读出/制备混淆及物理偏差；按下列分支提出有方向依据的有界参数或扫描建议。
5. 总控受控临时应用后，本角色申请fresh复测；验证数据和候选选择数据分开，复测失败则保留失败证据并回退。
6. 超限、模型不适用、回执未知或依赖失效时停止该分支并升级；不得无界重试或新增调参agent。

## 主链依赖与回测

- d05_cz_detuning

主链依赖是框级调度顺序。内部扩展方法可以因诊断暂不执行或等待后续校准；这不允许把未执行主方法记为完成。回测是新任务实例，不是往静态DAG增加反向边。

无本框专属反向回测边；通用上游复校须携带失效证据ID。

## 参数范围与预算

本角色所有内部方法的读取/失效范围并集；不是每次任务的全必填参数。首次主方法与条件扩展按对应procedure精确合同检查。

框级读取范围：

- gates.pi2_versions
- cz.frozen_candidate
- benchmark.seed_set
- readout.version
- readout.classifier3_version
- qubit.prep_versions
- cz.virtual_z_angles_rad
- qubit.pi2_versions
- prep.informationally_complete_16
- analysis.pauli9
- coupler.g_exchange_hz
- qubit.f01_hz
- qubit.pi_duration_s
- drive.axis_convention

框级候选写入范围（不是直接写权限）：

- benchmark.cz_xeb_report
- cz.measured_leakage
- cz.leakage_by_input_and_n
- cz.population_truth_table
- state.rho_comp
- state.graph_fidelity
- state.tomography_diagnostics
- process.choi_or_chi
- process.fidelity
- process.local_phases
- state.p0_bell_fidelity
- state.p0_correlations

框级上限：max_attempts=3；max_shots=48000000；max_seconds=7200。角色总上限是所有内部方法上限之和；不是默认全部运行。每个方法仍受自己的上限、当前授权预算与运行时剩余额度约束。

## 内部方法完整操作规范

### 内部方法 `tq_xeb` · 双比特CZ XEB独立基准

职责：在随机单比特层与固定CZ交替线路中评估冻结门组，区分每层与CZ单门归因。

范围：双比特CZ XEB独立基准

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- CZXEBCal

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_local_phase
- sq_rb
- ro_iq012

#### 固定量

- 两比特八轴独立π2随机规则
- CZ版本
- shots
- XEB定义
- phase拟合开关

#### 扫描量

- depth_m
- random_sequence_id

#### 序列

1. 制备00
2. 每层两比特各随机π2后CZ
3. 准确理想模拟
4. 最终计算基测量并额外保留泄漏
5. 不同深度交织
6. fresh随机线路验收

#### 原始观测

- counts及ideal probabilities
- per-circuit scores
- L
- phase训练/验证标识

#### 拟合、公式、单位与符号

- D=4有限维normalized linear XEB可用Σ(pobs·pideal-1/D)/Σ(pideal·pideal-1/D)
- 实际评分定义须与adapter一致并冻结
- depth衰减包含单比特门、CZ、SPAM与相关噪声，分离CZ需参考及模型

#### 偏差诊断与调参分支

1. 条件：重新拟合phase与同批评分

   动作：视为训练结果，另采heldout

   理由：优化数据不能当独立保真度

2. 条件：深度衰减非指数

   动作：报告范围和模型不足

   理由：不能强给0.99x单数

3. 条件：有泄漏

   动作：保留总shots口径并单报L

   理由：不能丢弃2后归一使XEB变好

#### 重测、回退与依赖失效

- 优化器结束后本agent fresh task，新随机seed且预算相同
- 门/读出/π2更新失效
- 失败回退上一有效参数集

#### 验收

- 独立理想线路核算
- shot/时间成本完整
- 序列聚类CI
- 每层与CZ归因边界清楚

#### 参数读取

- gates.pi2_versions
- cz.frozen_candidate
- benchmark.seed_set
- readout.version

#### 候选参数写入范围（仅 proposal）

- benchmark.cz_xeb_report

#### 方法预算

max_attempts=3；max_shots=16000000；max_seconds=1800。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.8，L1004–1030
- `resources/source_materials/two-qubit-page-2.txt`：⑥ CZXEBCal

### 内部方法 `tq_leakage` · CZ泄漏与重复门泄漏诊断

职责：分别报告门后含2人口、重复门演化和观测SPAM，不做后选择。

范围：CZ泄漏与重复门泄漏诊断

源实现边界：quantam有单CZ候选、真值表/层析三态L；重复门泄漏尚contract

存档执行状态：`source_suite_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- 本方法未声明该项；不得据此省略运行时合同检查。

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- ro_iq012
- tq_cz_phase_verify

#### 固定量

- CZ参数
- 读出三态
- shots
- gate_spacing

#### 扫描量

- prepared_input00/01/10/11
- 可选CZ_count_N

#### 序列

1. 交织gate-off准备参考
2. 各计算输入执行CZ或重复N
3. 九类counts
4. 保留含2全部结果
5. fresh候选复测

#### 原始观测

- P02/P20/P12/P21/P22
- 计算生存率
- 未校正counts
- prep/reference

#### 拟合、公式、单位与符号

- L=Σlabel含2 counts/shots
- 22只计一次
- 不能除以计算子空间shots
- 测得L含prepare/readout，不能等同离线理想计算输入的通道平均泄漏

#### 偏差诊断与调参分支

1. 条件：分类2不稳定

   动作：阻塞可靠泄漏结论并回ro_iq012

   理由：不能用二态分类证明无泄漏

2. 条件：L随N振荡

   动作：考虑leakage/seepage相干回流

   理由：不能用L/N当单门泄漏

3. 条件：泄漏超阈值

   动作：返回幅度/回归/shape候选

   理由：VZ不能修泄漏

#### 重测、回退与依赖失效

- fresh重复并交独立验收
- 任何gate/readout3变更失效
- 搜索候选与最终验证分开

#### 验收

- 分母总shots
- 各输入泄漏含CI
- 无删2数据
- 模型口径独立

#### 参数读取

- cz.frozen_candidate
- readout.classifier3_version
- qubit.prep_versions

#### 候选参数写入范围（仅 proposal）

- cz.measured_leakage
- cz.leakage_by_input_and_n

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程补充：3.3.2、3.3.4回归不能代替三态泄漏
- `resources/new_attachments/p0-report-extracted.txt`：L3410–3491

### 内部方法 `tq_truth_table` · CZ四输入九输出布居表

职责：验证计算基布居保持和泄漏，同时明确它无法区分CZ与恒等。

范围：CZ四输入九输出布居表

源实现边界：quantam有4输入9输出；不能冒称16输入过程层析

存档执行状态：`source_suite_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- quantum_twin.two_qubit_experiments.run_two_qubit_experiments

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- ro_iq012
- tq_cz_phase_verify

#### 固定量

- 冻结CZ
- 准备π
- readout
- shots_per_input

#### 扫描量

- prepared_input∈{00,01,10,11}

#### 序列

1. 有限脉冲制备输入
2. 执行CZ
3. 三态联合读出
4. 每输入独立counts
5. gate-off参考并fresh验证

#### 原始观测

- 4×9条件频数
- P(a|a)
- 每输入L
- 制备参考

#### 拟合、公式、单位与符号

- P(b|a)=counts_b/shots_a
- correct=P(a|a)
- 局域Z不改计算基布居
- 该表不是完整过程矩阵，也不是去SPAM门本体

#### 偏差诊断与调参分支

1. 条件：对角高但phase未验

   动作：阻止完整CZ结论

   理由：恒等可有同样完美表

2. 条件：01与10转移

   动作：查残余交换/时序

   理由：不只调读出矩阵

3. 条件：各输入差异大

   动作：拆prepare/readout/gate诊断

   理由：不得只报四输入均值

#### 重测、回退与依赖失效

- fresh每输入
- gate/prepare/readout变更失效
- 失败回退gate并保留全表

#### 验收

- 每行计数和=shots
- correct与L分别达criteria
- phase证据另存

#### 参数读取

- cz.frozen_candidate
- qubit.prep_versions
- readout.classifier3_version

#### 候选参数写入范围（仅 proposal）

- cz.population_truth_table

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程细化：3.3.3 L874回归与3.3.6过程表征
- `resources/new_attachments/p0-report-extracted.txt`：L3410–3491

### 内部方法 `tq_state_tomography` · 九基双比特态层析

职责：对固定制备输入的门后态做九基层析，输出态保真度而非平均门保真度。

范围：九基双比特态层析

源实现边界：quantam仅CZ|++>九基态层析；非16输入过程层析

存档执行状态：`source_suite_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- quantum_twin.two_qubit_metrics.state_tomography

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_local_phase
- ro_iq012

#### 固定量

- 固定输入如++
- CZ
- π2和VZ
- shots_per_basis

#### 扫描量

- analysis_basis∈{X,Y,Z}²

#### 序列

1. 制备++
2. 执行CZ
3. 按九基设置分析旋转
4. 保留九类结果
5. 线性重构计算子块
6. 独立计算期望及迹检查

#### 原始观测

- 九基counts
- 16 Pauli矩
- rho实虚部
- 迹/本征值
- 每基survival

#### 拟合、公式、单位与符号

- rho_comp=1/4 Σab T_ab σa⊗σb，包含I指标且TII为计算生存率，不强设为1
- Fgraph=<G|rho|G>
- 统计负本征值报告不暗裁剪，若MLE需作为另一方法留原结果

#### 偏差诊断与调参分支

1. 条件：不同基survival严重不一致

   动作：查有限分析pulse/SPAM，报告模型不一致

   理由：不能静默归一

2. 条件：rho轻微负本征值

   动作：报告统计区间及线性估计边界

   理由：负值不是自动物理算法崩溃

3. 条件：态F高

   动作：只报告这一输入的态结果

   理由：不能称过程/平均CZ门F

#### 重测、回退与依赖失效

- fresh九基或预声明留出观测
- 分析pulse、VZ、gate/readout更新失效
- 保持无后选择

#### 验收

- Pauli符号/分母检验通过
- 有限统计/残差说明
- 只宣称态指标

#### 参数读取

- cz.frozen_candidate
- cz.virtual_z_angles_rad
- qubit.pi2_versions
- readout.classifier3_version

#### 候选参数写入范围（仅 proposal）

- state.rho_comp
- state.graph_fidelity
- state.tomography_diagnostics

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.6 L972–980的每输入九基层析
- `resources/new_attachments/p0-report-extracted.txt`：L3493–3714

### 内部方法 `tq_process_tomography` · 16输入×9基过程层析

职责：收集信息完备输入输出并重建门过程，分开SPAM与局域相位校正口径。

范围：16输入×9基过程层析

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- CZProcessTomography

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_state_tomography
- tq_truth_table

#### 固定量

- 门版本
- 制备/分析校准
- 是否拟合localphase
- shots

#### 扫描量

- 16个信息完备输入
- 9组分析基

#### 序列

1. 独立验证16输入的理想张成性
2. 每输入执行CZ后九基层析
3. 保存144条线路
4. 重建过程及可选localphase
5. fresh保留线路验证

#### 原始观测

- 144组counts
- 输入设计矩阵
- Choi/χ
- trace preservation/leakage
- CI

#### 拟合、公式、单位与符号

- 过程表示、basis规范与保真度定义先冻结
- 存在泄漏时输出投影通道可不保迹，不强TP后美化
- SPAM未独立校准则过程估计含SPAM
- state F不等于process F或Favg

#### 偏差诊断与调参分支

1. 条件：输入矩阵病态或prepare不可信

   动作：停止重建并返回准备校准

   理由：144数量不能替代信息完备

2. 条件：拟合localphase改善

   动作：同时保存未校正与校正结果及角度

   理由：不得改名混淆物理门性能

3. 条件：物理化约束改变结论

   动作：报告线性与约束两版和假设

   理由：避免投影掩盖错误

#### 重测、回退与依赖失效

- fresh留出线路和独立重复
- 若用过程数据调参则另做新验收
- gate/SPAM改动全部过程证据失效

#### 验收

- 信息完备
- 模型残差/物理性诊断
- 指标定义及泄漏处理明确

#### 参数读取

- prep.informationally_complete_16
- analysis.pauli9
- cz.frozen_candidate
- readout.classifier3_version

#### 候选参数写入范围（仅 proposal）

- process.choi_or_chi
- process.fidelity
- process.local_phases

#### 方法预算

max_attempts=3；max_shots=16000000；max_seconds=1800。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.6，L964–988

### 内部方法 `p0_bell` · P0半交换Bell关联验证

职责：在两能级P0配置内用三Pauli关联验证特定Bell目标态，不能借此证明CZ。

范围：P0半交换Bell关联验证

源实现边界：quantam两能级P0套件已实现Bell witness

存档执行状态：`source_suite_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- quantum_twin.experiments.run_experiments

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_iswap
- sq_rabi_time

#### 固定量

- P0两能级配置
- 半交换时间
- 共参考频率
- shots

#### 扫描量

- analysis_basis∈{XY,YX,ZZ}

#### 序列

1. π制备10
2. 共振交换t=1/(8|g|)
3. 按XY/YX/ZZ分析旋转
4. 测联合counts
5. fresh计算三关联

#### 原始观测

- XY,YX,ZZ correlations
- counts
- π/2和交换参数来源

#### 拟合、公式、单位与符号

- 目标(|10>-i|01>)/√2，F=(1-<XY>+<YX>-<ZZ>)/4
- 符号依实际Hamiltonian/分析约定必须核对
- 这是态投影估计，非完整态/过程层析或CZ Favg

#### 偏差诊断与调参分支

1. 条件：Z反关联好而XY/YX错

   动作：检查相干相位/分析轴/交换时长

   理由：人口正确不代表纠缠相位正确

2. 条件：F偏低

   动作：区分readout、prepare、T1/T2与交换失谐

   理由：不得仅拉长交换

3. 条件：与CZ配置不同

   动作：保持两个结果独立

   理由：0.85MHz与12MHz不可互换

#### 重测、回退与依赖失效

- fresh三关联
- 交换/准备/读出参数更新失效
- 失败回退半交换控制并重新估g

#### 验收

- 目标和符号明确
- 完整shots
- 不超范围宣称门保真度

#### 参数读取

- coupler.g_exchange_hz
- qubit.f01_hz
- qubit.pi_duration_s
- drive.axis_convention

#### 候选参数写入范围（仅 proposal）

- state.p0_bell_fidelity
- state.p0_correlations

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程扩展：3.2.1交换思路
- `resources/new_attachments/p0-report-extracted.txt`：L2103–2171

## 框级验收与发布边界

- 原框主方法所需测量证据必须独立满足合同；辅助方法通过不能替代主方法。
- 模型只提出建议；最终通过/参数发布由独立判定与总控硬门禁决定。

每个被选内部方法仍须满足其完整验收项、剩余预算及独立复测；跳过的扩展方法必须标记未执行和原因。最终结论必须分别说明原框主方法、已运行辅助方法、未实现能力与证据新鲜度。

## 来源索引

- `resources/new_attachments/双比特测试.pdf`：第2页，第6个功能方框，CZXEBCal
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.8，L1004–1030
- `resources/source_materials/two-qubit-page-2.txt`：⑥ CZXEBCal
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程补充：3.3.2、3.3.4回归不能代替三态泄漏
- `resources/new_attachments/p0-report-extracted.txt`：L3410–3491
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程细化：3.3.3 L874回归与3.3.6过程表征
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.6 L972–980的每输入九基层析
- `resources/new_attachments/p0-report-extracted.txt`：L3493–3714
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.6，L964–988
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程扩展：3.2.1交换思路
- `resources/new_attachments/p0-report-extracted.txt`：L2103–2171
