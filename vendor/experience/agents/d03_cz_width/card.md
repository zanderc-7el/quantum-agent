# CZ宽度粗扫 · 原流程实验智能体

角色ID：`d03_cz_width`。原图第2页第3个功能方框；原函数：`CZSwapSweepZPulse`。

本包只注册15个实验智能体和1个总控。下列内部方法不是独立智能体，不能绕过本角色对外调用；本角色负责自己的调参、诊断、精校和重测，不再拆分专门调参agent。

## 当前能力边界

原框 CZSwapSweepZPulse 的粗扫在源 CZ 套件中有三能级矩形 pulse 的 chevron/第一非零回归对应；只有完整套件桥，尚无逐方法原子调用。真实 flux 波形、线路时序和波形整形未接通。

execution_status=`source_suite_only`；原框主方法状态=`source_suite_only`；默认主方法=`tq_cz_chevron`。精确运行范围以 `config/bindings.json` 与运行时回执为准。

实机SDK尚未接入。模型只能产生建议；不允许直接执行shell、写机器参数、提交参数版本或宣称通过。源程序模拟结果不等于本次任务证据，不能把辅助方法成功升级成原流程主方法成功。

## 自主闭环与内部方法选择

核查双比特/耦合器与 QZ–CZ 时序，粗扫 11↔02 避交叉，精修第一非零布居回归后评估粗 CZ。零时长高 P11 不是 CZ；候选仍需相位和泄漏后续验证。

1. 读取本次参数快照、授权窗口、绑定能力、设备/种子/模型版本、可用预算和证据ID；缺少主方法执行器时明确阻塞，不以辅助动作替换。
2. 按主方法或明确诊断触发选择内部方法，冻结固定量、扫描范围、观测单位、拟合模型及验收门槛。
3. 通过受限测量入口申请采集，保留原始counts/IQ、实际扫描值、配置哈希和操作回执；不得只留拟合摘要。
4. 自行诊断可辨识性、边界拟合、读出/制备混淆及物理偏差；按下列分支提出有方向依据的有界参数或扫描建议。
5. 总控受控临时应用后，本角色申请fresh复测；验证数据和候选选择数据分开，复测失败则保留失败证据并回退。
6. 超限、模型不适用、回执未知或依赖失效时停止该分支并升级；不得无界重试或新增调参agent。

## 主链依赖与回测

- d02_idle_interaction

主链依赖是框级调度顺序。内部扩展方法可以因诊断暂不执行或等待后续校准；这不允许把未执行主方法记为完成。回测是新任务实例，不是往静态DAG增加反向边。

无本框专属反向回测边；通用上游复校须携带失效证据ID。

## 参数范围与预算

本角色所有内部方法的读取/失效范围并集；不是每次任务的全必填参数。首次主方法与条件扩展按对应procedure精确合同检查。

框级读取范围：

- qubit.f01_z_inverse
- qubit.anharmonicity_hz
- coupler.amplitude_limits
- timing.version
- cz.pulse_contract
- qubit.pi_amp
- readout.classifier3_version
- cz.first_return_s
- cz.crossing_frequency_candidate_hz
- cz.return_candidates
- phase.gateoff_reference_complex
- timing.xy_z_delay_s
- iswap.frequency_hz
- iswap.duration_s
- coupler.pulse_shape

框级候选写入范围（不是直接写权限）：

- cz.pulse_contract
- cz.initial_workpoint
- cz.crossing_frequency_candidate_hz
- cz.g_effective_hz
- cz.first_return_s
- cz.return_candidates
- cz.rough_point_candidate
- timing.qubit_z_coupler_z_delay_s

框级上限：max_attempts=3；max_shots=30000000；max_seconds=4500。角色总上限是所有内部方法上限之和；不是默认全部运行。每个方法仍受自己的上限、当前授权预算与运行时剩余额度约束。

## 内部方法完整操作规范

### 内部方法 `tq_cz_setup` · CZ波形与工作点合同

职责：冻结CZ脉冲族、两比特交互频率、coupler幅度、有效时间与padding定义。

范围：CZ波形与工作点合同

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- 本方法未声明该项；不得据此省略运行时合同检查。

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- sq_flux_map
- sq_anharmonicity
- tq_exchange_off
- tq_qz_cz_timing

#### 固定量

- pulse_family
- total_width_s
- padding_definition
- f01_0/f01_1
- coupler_on_amp

#### 扫描量

- 仅生成受限初始候选，不执行科学扫描

#### 序列

1. 核对两比特频率/α与可调域
2. 计算可达11↔02或20分支
3. 将频率映射到已验证Z幅度
4. 编译预览并核对边沿/padding
5. 提交候选实验计划

#### 原始观测

- 编译波形摘要
- 实际总时长/有效时长
- 映射版本
- 可达性报告

#### 拟合、公式、单位与符号

- 11↔02近似交叉条件f0≈f1+α1
- 频差约200MHz只是手册实例，依实测α决定
- cz_width含padding规则必须显式，不默认60ns
- slepian/erf_flattop与矩形twin不等价

#### 偏差诊断与调参分支

1. 条件：需外推flux map或超幅度

   动作：blocked并扩展映射证据

   理由：不能直接发未知电压

2. 条件：PDF固定60ns与设备预算不兼容

   动作：保留为示例，不自动采用

   理由：门宽需由相干/带宽/交换窗口决定

3. 条件：合同完整

   动作：安排chevron

   理由：参数不是校准结果

#### 重测、回退与依赖失效

- 任何shape/padding/时序修改使全部CZ校准失效
- 保留旧完整pulse contract用于回退

#### 验收

- 编译检查可达/时间合法
- 单位与padding无歧义
- 无硬件写操作

#### 参数读取

- qubit.f01_z_inverse
- qubit.anharmonicity_hz
- coupler.amplitude_limits
- timing.version

#### 候选参数写入范围（仅 proposal）

- cz.pulse_contract
- cz.initial_workpoint

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.1，L818–830
- `resources/source_materials/two-qubit-page-2.txt`：④前CZ_width=60ns为流程示例

### 内部方法 `tq_cz_chevron` · 11↔02避交叉二维粗扫

职责：从真实三态计数定位避交叉及第一非零回归窗口，不能只找P11高点。

范围：11↔02避交叉二维粗扫

源实现边界：quantam三能级矩形pulse的chevron阶段；真实flux/slepian未实现

存档执行状态：`source_suite_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- CZSwapSweepZPulse

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_cz_setup
- ro_iq012

#### 固定量

- coupler_amp
- 另一qubit频率
- prepare11
- pulse_shape/padding
- shots

#### 扫描量

- one_qubit_interaction_frequency_hz
- effective_duration_s

#### 序列

1. 有限π依序制备11
2. 执行待测CZ段
3. 九类读出
4. 频率×时长扫描
5. 拟合有效二维交换面
6. fresh局部验证

#### 原始观测

- P11,P02,P20及总泄漏
- 九类counts
- scan axes
- fit surface

#### 拟合、公式、单位与符号

- 简化两态P02=B+C·8g²/(δ²+8g²)·sin²(π sqrt(δ²+8g²)t)
- g,δ Hz
- 共振首回归1/(2√2|g|)
- 高能级/ZZ/20通道使crossing反推α存在系统误差

#### 偏差诊断与调参分支

1. 条件：最优为t=0

   动作：拒绝并限定非零第一回归分支

   理由：零时长恒等不是CZ

2. 条件：20通道/多频明显

   动作：拒绝简化模型确定性结论并扩展分析

   理由：局部标准误不含错模型

3. 条件：coarse回归可辨

   动作：提出frequency/time窗口给refine

   理由：不依据隐藏Favg选点

#### 重测、回退与依赖失效

- 新计数局部扫描
- 改变coupler/shape/padding使候选失效
- 不能将另一配置P0 g直接继承

#### 验收

- 非零首回归可观测
- counts保留泄漏
- 拟合适用域明确

#### 参数读取

- cz.pulse_contract
- qubit.pi_amp
- qubit.anharmonicity_hz
- readout.classifier3_version

#### 候选参数写入范围（仅 proposal）

- cz.crossing_frequency_candidate_hz
- cz.g_effective_hz
- cz.first_return_s

#### 方法预算

max_attempts=3；max_shots=6000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.2，L832–864
- `resources/new_attachments/p0-report-extracted.txt`：L2677–2809

### 内部方法 `tq_cz_return` · 第一非零回归时长精修

职责：对每条失谐线精修低泄漏回归时间，给条件相位验证候选。

范围：第一非零回归时长精修

源实现边界：quantam内有21×31回归精修，不是运行时独立原子动作

存档执行状态：`source_suite_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- quantum_twin.two_qubit_experiments.run_two_qubit_experiments

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_cz_chevron

#### 固定量

- coupler_amp
- prepared11
- readout3
- 固定搜索边界

#### 扫描量

- 小失谐窗口
- 首回归附近duration_s

#### 序列

1. 围绕首回归的小窗口扫t和detuning
2. 采P11及总泄漏
3. 局部低阶曲率拟合
4. 截断在已测区间内
5. 各detuning给候选

#### 原始观测

- L(t,δ)
- P11(t,δ)
- 曲率
- 时间边界
- counts

#### 拟合、公式、单位与符号

- L≈a(t-t0)^2+b(t-t0)+c
- a>0时topt=t0-b/(2a)，只能在局部有效
- a≤0或极值越窗则不外推
- 量化时钟后重新测

#### 偏差诊断与调参分支

1. 条件：最佳在窗口端点

   动作：扩/移受限窗口或回粗扫

   理由：边界最小不证明局部回归

2. 条件：L低但条件相位未知

   动作：交tq_cz_phase_verify/rough

   理由：高回归可为0或2π相位

3. 条件：曲率过小

   动作：报告时间不确定，增加辨识度而非假精度

   理由：shots噪声可决定极小点

#### 重测、回退与依赖失效

- fresh重复最优与邻点
- 任何shape或g变化失效
- 保留全部未选候选
- 失败回退coarse窗口

#### 验收

- t>0且第一回归分支
- 不确定度/量化裕量达criteria
- 无凭真值选点

#### 参数读取

- cz.first_return_s
- cz.crossing_frequency_candidate_hz
- cz.pulse_contract

#### 候选参数写入范围（仅 proposal）

- cz.return_candidates

#### 方法预算

max_attempts=3；max_shots=8000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程细化：3.3.2 L864的完整交换周期
- `resources/new_attachments/p0-report-extracted.txt`：L2811–2903

### 内部方法 `tq_cz_rough` · CZ回归与条件相位联合粗定点

职责：固定门宽二维扫描qubit/coupler幅度，同时满足回归与π条件相位。

范围：CZ回归与条件相位联合粗定点

源实现边界：quantam仅detuning×time候选搜索+相位/泄漏筛选；未实现手册coupler×qubit幅度二维粗点

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- TTGAczRoughPoint

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_cz_return
- tq_gateoff_reference

#### 固定量

- 门总宽/shape/padding
- 另一qubit频率
- shots
- 参考分支

#### 扫描量

- coupler_amp
- one_qubit_interaction_frequency_hz

#### 序列

1. 候选点先制备11测回归/泄漏
2. 再spectator0/1下做target X/Y四线路
3. 求条件相位
4. 按冻结联合目标选候选
5. 冻结后fresh复测

#### 原始观测

- P11及L
- 两条件复相干
- θconditional
- contrast
- 候选预算

#### 拟合、公式、单位与符号

- θcond=wrap(arg z1-arg z0)，目标π
- 误差用wrap(θcond-π)
- 回归/泄漏/相位需分列，不能只优化P11
- frequency→Z幅度走实际映射

#### 偏差诊断与调参分支

1. 条件：回归好但相位接近0

   动作：拒绝CZ候选

   理由：恒等也可高P11

2. 条件：phase好但泄漏/contrast坏

   动作：拒绝或改回归窗口

   理由：相位无法替代生存率

3. 条件：多个候选相当

   动作：选择有裕量且成本合理者并独立验证

   理由：不得按隐藏门通道保真度选择

#### 重测、回退与依赖失效

- fresh验证冻结候选
- coupler/qubit幅度耦合需交替重测
- 失败回退整组幅度/频率，不只回退一个值

#### 验收

- 联合criteria全达标且独立数据验证
- 候选数据不能充当fresh

#### 参数读取

- cz.pulse_contract
- cz.return_candidates
- phase.gateoff_reference_complex

#### 候选参数写入范围（仅 proposal）

- cz.rough_point_candidate

#### 方法预算

max_attempts=3；max_shots=8000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.3，L866–898

### 内部方法 `tq_qz_cz_timing` · 量子比特Z–耦合器Z时延

职责：用交换窗口定位两Z通道相对到达时间。

范围：量子比特Z–耦合器Z时延

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- QubitXYCouplerZTimingCal

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_iswap
- sq_xy_z_timing

#### 固定量

- QQ共振幅度
- 相同Z片段宽度
- initial01/10
- readout

#### 扫描量

- coupler_delay_relative_qubit_z_s

#### 序列

1. 制备01/10
2. 两Z脉冲开启交换
3. 扫coupler相对延迟
4. 测交换人口
5. 拟合重叠窗口
6. 补偿后fresh扫描

#### 原始观测

- P10/P01随delay
- 脉冲读回时序
- 平台/边沿对称性

#### 拟合、公式、单位与符号

- delay=coupler到达−qubitZ到达，秒
- 实际响应与边沿/滤波卷积有关
- 负补偿用另一通道延迟实现，保持统一触发坐标

#### 偏差诊断与调参分支

1. 条件：平台或边缘多重结构

   动作：检查线响应而非强拟单delay

   理由：脉冲畸变可能非平移

2. 条件：方向未知

   动作：施加受限正delay探针

   理由：从响应移动验证符号

3. 条件：补偿后中心正确

   动作：提出同步表版本

   理由：不能只更改显示时间轴

#### 重测、回退与依赖失效

- fresh两种初态
- 改变shape/filter/cable使时延失效
- 同步表改动后需QQswap、CZphase重测

#### 验收

- 补偿量可由硬件时钟表示
- 窗口/相对时序在criteria内

#### 参数读取

- timing.xy_z_delay_s
- iswap.frequency_hz
- iswap.duration_s
- coupler.pulse_shape

#### 候选参数写入范围（仅 proposal）

- timing.qubit_z_coupler_z_delay_s

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.2.2，L784–812

## 框级验收与发布边界

- 原框主方法所需测量证据必须独立满足合同；辅助方法通过不能替代主方法。
- 模型只提出建议；最终通过/参数发布由独立判定与总控硬门禁决定。

每个被选内部方法仍须满足其完整验收项、剩余预算及独立复测；跳过的扩展方法必须标记未执行和原因。最终结论必须分别说明原框主方法、已运行辅助方法、未实现能力与证据新鲜度。

## 来源索引

- `resources/new_attachments/双比特测试.pdf`：第2页，第3个功能方框，CZSwapSweepZPulse
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.1，L818–830
- `resources/source_materials/two-qubit-page-2.txt`：④前CZ_width=60ns为流程示例
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.2，L832–864
- `resources/new_attachments/p0-report-extracted.txt`：L2677–2809
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程细化：3.3.2 L864的完整交换周期
- `resources/new_attachments/p0-report-extracted.txt`：L2811–2903
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.3，L866–898
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.2.2，L784–812
