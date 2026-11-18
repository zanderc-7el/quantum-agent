# 两比特闲置工作点 · 原流程实验智能体

角色ID：`d02_idle_interaction`。原图第2页第2个功能方框；原函数：`ZeroPointFinderByQQSwap`。

本包只注册15个实验智能体和1个总控。下列内部方法不是独立智能体，不能绕过本角色对外调用；本角色负责自己的调参、诊断、精校和重测，不再拆分专门调参agent。

## 当前能力边界

原框 ZeroPointFinderByQQSwap 的耦合器扫描未原子绑定。源程序仅有固定 coupling_scale 的独立 ZZ 测量和 P0 共振交换子流程；交换关断不等于 ZZ 为零，也不等于真实闲置工作点已找到。

execution_status=`source_suite_only`；原框主方法状态=`contract_only`；默认主方法=`tq_exchange_off`。精确运行范围以 `config/bindings.json` 与运行时回执为准。

实机SDK尚未接入。模型只能产生建议；不允许直接执行shell、写机器参数、提交参数版本或宣称通过。源程序模拟结果不等于本次任务证据，不能把辅助方法成功升级成原流程主方法成功。

## 自主闭环与内部方法选择

主任务以 QQswap 找交换抑制候选，然后独立 conditional Ramsey 测 ZZ；耦合器腔偏置图、dressed ZZ、共振交换/iSWAP 为诊断内部方法。禁止将一项通过写成另一项已通过。

1. 读取本次参数快照、授权窗口、绑定能力、设备/种子/模型版本、可用预算和证据ID；缺少主方法执行器时明确阻塞，不以辅助动作替换。
2. 按主方法或明确诊断触发选择内部方法，冻结固定量、扫描范围、观测单位、拟合模型及验收门槛。
3. 通过受限测量入口申请采集，保留原始counts/IQ、实际扫描值、配置哈希和操作回执；不得只留拟合摘要。
4. 自行诊断可辨识性、边界拟合、读出/制备混淆及物理偏差；按下列分支提出有方向依据的有界参数或扫描建议。
5. 总控受控临时应用后，本角色申请fresh复测；验证数据和候选选择数据分开，复测失败则保留失败证据并回退。
6. 超限、模型不适用、回执未知或依赖失效时停止该分支并升级；不得无界重试或新增调参agent。

## 主链依赖与回测

- d01_flux_spectroscopy

主链依赖是框级调度顺序。内部扩展方法可以因诊断暂不执行或等待后续校准；这不允许把未执行主方法记为完成。回测是新任务实例，不是往静态DAG增加反向边。

无本框专属反向回测边；通用上游复校须携带失效证据ID。

## 参数范围与预算

本角色所有内部方法的读取/失效范围并集；不是每次任务的全必填参数。首次主方法与条件扩展按对应procedure精确合同检查。

框级读取范围：

- coupler.idle_bias
- qubit.f01_z_inverse
- qubit.pi_amp
- readout.assignment_matrix
- phase.gateoff_reference_complex
- qubit.f01_hz
- coupler.bias_version
- readout.classifier3_version
- coupler.bias_limits
- readout.bare_frequency_hz
- coupler.topology
- coupler.on_amplitude
- qubit.pi_duration_s

框级候选写入范围（不是直接写权限）：

- coupler.exchange_off_bias_candidate
- coupler.exchange_bound_hz
- coupler.residual_zz_hz
- coupler.residual_zz_diagnostics
- coupler.dressed_zz_hz
- coupler.dressed_zz_diagnostics
- coupler.idle_bias_candidate
- coupler.branch_evidence
- coupler.g_exchange_hz
- iswap.frequency_hz
- iswap.duration_s

框级上限：max_attempts=3；max_shots=24000000；max_seconds=4500。角色总上限是所有内部方法上限之和；不是默认全部运行。每个方法仍受自己的上限、当前授权预算与运行时剩余额度约束。

## 内部方法完整操作规范

### 内部方法 `tq_exchange_off` · QQswap交换关断点

职责：测量单激发交换随coupler偏置的变化，找小交换区域并与ZZ关断分开。

范围：QQswap交换关断点

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- ZeroPointFinderByQQSwap

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_coupler_resonator_map
- sq_flux_map
- sq_rabi_amplitude
- ro_iq01

#### 固定量

- QQ共振调谐
- initial01/10
- qubit_pulse_shape
- readout
- shots

#### 扫描量

- coupler_pulse_amplitude或已校dc_bias
- interaction_duration_s

#### 序列

1. 分别制备01和10
2. 用Z将两比特调近共振
3. 同时开coupler片段
4. 二维扫coupler幅度与时长
5. 测全四类counts
6. 候选偏置回零脉冲验证

#### 原始观测

- P01/P10互补交换
- P00/T1损失
- g上界及分辨率
- 偏置读回

#### 拟合、公式、单位与符号

- 近共振Pswap=sin²(2πgt)，g Hz，人口周期1/(2|g|)
- 失谐Δ使振幅4g²/(Δ²+4g²)，弱振荡可能是失谐而非g=0
- 无可解析周期时给上界而非零值

#### 偏差诊断与调参分支

1. 条件：看不到交换且未证共振

   动作：先校准QQ detuning再判断关断

   理由：防失谐假关断

2. 条件：候选g最小

   动作：提出bias并安排独立ZZ任务

   理由：交换不等于ZZ

3. 条件：dc与pulse标度未知

   动作：blocked映射更新

   理由：不能直接把pulse幅度加到dc偏置

#### 重测、回退与依赖失效

- 更新bias后fresh IQ和QQswap
- 同时发tq_residual_zz但不由本agent宣称其通过
- 源PDF把两者等同的文字不沿用

#### 验收

- 两种初态一致
- 分辨率/置信上界满足criteria
- ζ独立报告

#### 参数读取

- coupler.idle_bias
- qubit.f01_z_inverse
- qubit.pi_amp
- readout.assignment_matrix

#### 候选参数写入范围（仅 proposal）

- coupler.exchange_off_bias_candidate
- coupler.exchange_bound_hz

#### 方法预算

max_attempts=3；max_shots=8000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.1.2，L696–736
- `resources/source_materials/two-qubit-page-2.txt`：② ZeroPointFinderByQQSwap；交换与ZZ等同文字需纠正

### 内部方法 `tq_residual_zz` · 残余ZZ独立测量

职责：在固定工作点测条件频率差ζ，独立于QQ交换关断判断。

范围：残余ZZ独立测量

源实现边界：quantam可测固定coupling_scale的residual/dressed ZZ；无真实耦合器ζ零点搜索

存档执行状态：`source_suite_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- quantum_twin.two_qubit_experiments.run_two_qubit_experiments

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_gateoff_reference

#### 固定量

- coupler_scale=0（仅twin）或明确hardware_bias
- target_offset_hz
- π2
- readout
- shots

#### 扫描量

- wait_time_s
- spectator0/1
- X/Y analysis
- 可选coupler_bias复核邻域

#### 序列

1. 准备spectator0/1
2. target制备+
3. 等待并保持指定coupler工作点
4. X/Y分析
5. 除以对应参考
6. 分别拟合两频率
7. fresh复测

#### 原始观测

- 两个复Ramsey轨迹
- conditional frequencies
- contrast/T1相关偏差
- counts

#### 拟合、公式、单位与符号

- ζ=(E11-E10-E01+E00)/h，Hz
- 若拟合exp(+i2πνt)而物理X+iY为exp(-iEt/ħ)，quantam取ζ=-(ν1-ν0)
- 需通过约定验证符号
- spectator弛豫使单指数模型有偏差

#### 偏差诊断与调参分支

1. 条件：spectator1包络显著畸变

   动作：报告单频模型偏差并查T1/缩短扫描或用扩展模型

   理由：不把拟合时间常数当target固有T2

2. 条件：ζ小于分辨率

   动作：给区间/上界而非宣称0

   理由：有限shots无法证明严格零

3. 条件：目标是扫ζ零点

   动作：用相邻bias的ζ斜率确定方向并受约束迭代

   理由：不得据QQswap直接宣布ZZ关断

#### 重测、回退与依赖失效

- fresh复测且与QQswap结果并列
- bias/频率/时序/prepare更新失效
- 两条件全shots保留
- 失败回退偏置

#### 验收

- 两分支可辨识且alias先验有效
- 点估计及不确定度符合任务criteria
- 无事后真值参与选bias

#### 参数读取

- phase.gateoff_reference_complex
- qubit.f01_hz
- coupler.bias_version
- readout.classifier3_version

#### 候选参数写入范围（仅 proposal）

- coupler.residual_zz_hz
- coupler.residual_zz_diagnostics

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程补充：手册3.1.2只测交换，L696–736不构成ZZ证据
- `resources/new_attachments/p0-report-extracted.txt`：L2420–2675
- `resources/source_materials/two-qubit-page-2.txt`：②中的交换/ZZ混淆需分开

### 内部方法 `tq_dressed_zz` · 工作点有效dressed ZZ

职责：在固定工作点测条件频率差ζ，独立于QQ交换关断判断。

范围：工作点有效dressed ZZ

源实现边界：quantam可测固定coupling_scale的residual/dressed ZZ；无真实耦合器ζ零点搜索

存档执行状态：`source_suite_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- quantum_twin.two_qubit_experiments.run_two_qubit_experiments

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_gateoff_reference

#### 固定量

- coupler_scale=0.1（仅twin）或明确hardware_bias
- target_offset_hz
- π2
- readout
- shots

#### 扫描量

- wait_time_s
- spectator0/1
- X/Y analysis
- 可选coupler_bias复核邻域

#### 序列

1. 准备spectator0/1
2. target制备+
3. 等待并保持指定coupler工作点
4. X/Y分析
5. 除以对应参考
6. 分别拟合两频率
7. fresh复测

#### 原始观测

- 两个复Ramsey轨迹
- conditional frequencies
- contrast/T1相关偏差
- counts

#### 拟合、公式、单位与符号

- ζ=(E11-E10-E01+E00)/h，Hz
- 若拟合exp(+i2πνt)而物理X+iY为exp(-iEt/ħ)，quantam取ζ=-(ν1-ν0)
- 需通过约定验证符号
- spectator弛豫使单指数模型有偏差

#### 偏差诊断与调参分支

1. 条件：spectator1包络显著畸变

   动作：报告单频模型偏差并查T1/缩短扫描或用扩展模型

   理由：不把拟合时间常数当target固有T2

2. 条件：ζ小于分辨率

   动作：给区间/上界而非宣称0

   理由：有限shots无法证明严格零

3. 条件：目标是扫ζ零点

   动作：用相邻bias的ζ斜率确定方向并受约束迭代

   理由：不得据QQswap直接宣布ZZ关断

#### 重测、回退与依赖失效

- fresh复测且与QQswap结果并列
- bias/频率/时序/prepare更新失效
- 两条件全shots保留
- 失败回退偏置

#### 验收

- 两分支可辨识且alias先验有效
- 点估计及不确定度符合任务criteria
- 无事后真值参与选bias

#### 参数读取

- phase.gateoff_reference_complex
- qubit.f01_hz
- coupler.bias_version
- readout.classifier3_version

#### 候选参数写入范围（仅 proposal）

- coupler.dressed_zz_hz
- coupler.dressed_zz_diagnostics

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程补充：条件Ramsey；手册L874条件相位思想
- `resources/new_attachments/p0-report-extracted.txt`：L2420–2675
- `resources/source_materials/two-qubit-page-2.txt`：②中的交换/ZZ混淆需分开

### 内部方法 `tq_coupler_resonator_map` · 耦合器偏置与谐振腔响应

职责：找到可用耦合器idle候选及分支，随后分别检验交换与ZZ。

范围：耦合器偏置与谐振腔响应

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- ReadoutFreqQubitBiasDependency

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- ro_resonator_survey

#### 固定量

- 两比特idle
- 探测功率
- readout_path
- 稳定等待时间

#### 扫描量

- coupler_dc_bias
- probe_frequency_hz

#### 序列

1. 设置并读回coupler偏置
2. 二维腔扫
3. 追踪避交叉两分支
4. 回程验证
5. 提出远离歧义区的idle候选

#### 原始观测

- S21(bias,f)
- 避交叉
- 峰归属
- actual_bias

#### 拟合、公式、单位与符号

- 腔频相对裸腔的位移可辅助推断coupler频率所在侧，但需已知耦合拓扑
- 不直接用该图证明g=0或ζ=0

#### 偏差诊断与调参分支

1. 条件：避交叉分支不清

   动作：停止单峰拟合并请求分支模型

   理由：不能取最大点盲更新

2. 条件：idle候选可重复

   动作：安排QQswap及条件Ramsey分别测试

   理由：耦合器最高频不等于关断点

3. 条件：读出变化大

   动作：先fresh IQ复测

   理由：偏置会改变读出条件

#### 重测、回退与依赖失效

- 候选提交后ro_resonator_survey和ro_iq01 fresh
- coupler idle变化使全部双比特工作点/相位失效
- 回退旧bias

#### 验收

- 分支与读回明确
- 安全范围内
- 不宣称真实关断已完成

#### 参数读取

- coupler.bias_limits
- readout.bare_frequency_hz
- coupler.topology

#### 候选参数写入范围（仅 proposal）

- coupler.idle_bias_candidate
- coupler.branch_evidence

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.1.1，L666–694

### 内部方法 `tq_iswap` · QQ共振交换与iSWAP时长

职责：调共振与交换时间；兼容P0交换g测量，但门指标需独立验证。

范围：QQ共振交换与iSWAP时长

源实现边界：quantam仅P0两能级exchange测g与后续Bell；手册真实频率×电压iSWAP未绑定

存档执行状态：`source_suite_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- QQSwapSweepZPulse
- quantum_twin.experiments.run_experiments

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- sq_flux_map
- sq_rabi_time
- ro_iq01

#### 固定量

- coupler_amplitude
- initial01/10
- Z形状
- readout
- shots

#### 扫描量

- qubit_interaction_frequency_hz
- interaction_duration_s

#### 序列

1. π制备单激发
2. 共参考系下调一个比特
3. coupler固定开启
4. 扫描频率×时长
5. 读四类counts
6. 选共振分支并fresh交换复测

#### 原始观测

- P01,P10,P00,P11
- 振荡频率和幅度
- P01-P10

#### 拟合、公式、单位与符号

- fosc=sqrt(Δ²+4g²)，Hz
- 共振fosc=2|g|、t_iSWAP=1/(4|g|)
- 取人口周期一半实现转移，Bell半交换再半
- P0差分拟合弱耗散/近共振有效，不能据此定g符号

#### 偏差诊断与调参分支

1. 条件：周期长但转移幅度不足

   动作：检查失谐/T1，联合拟合幅度与频率

   理由：最长周期不是唯一标准

2. 条件：频率调节方向不明

   动作：查flux分支df/dZ并邻点验证

   理由：不可直接按电压正负猜频率

3. 条件：共振与半周期可复测

   动作：提出frequency和duration

   理由：高转移不等于完整iSWAP门保真度

#### 重测、回退与依赖失效

- fresh双初态及相位验证
- flux/时序/coupler变化失效
- P0子模式无需真实flux映射但必须标模拟

#### 验收

- 振荡覆盖与对比度合格
- 时间量化后转移/相位符合目标
- 未把P0 g与CZ另一配置混用

#### 参数读取

- qubit.f01_hz
- qubit.f01_z_inverse
- coupler.on_amplitude
- qubit.pi_duration_s

#### 候选参数写入范围（仅 proposal）

- coupler.g_exchange_hz
- iswap.frequency_hz
- iswap.duration_s

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.2.1，L742–782
- `resources/new_attachments/p0-report-extracted.txt`：L1991–2103

## 框级验收与发布边界

- 原框主方法所需测量证据必须独立满足合同；辅助方法通过不能替代主方法。
- 模型只提出建议；最终通过/参数发布由独立判定与总控硬门禁决定。

每个被选内部方法仍须满足其完整验收项、剩余预算及独立复测；跳过的扩展方法必须标记未执行和原因。最终结论必须分别说明原框主方法、已运行辅助方法、未实现能力与证据新鲜度。

## 来源索引

- `resources/new_attachments/双比特测试.pdf`：第2页，第2个功能方框，ZeroPointFinderByQQSwap
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.1.2，L696–736
- `resources/source_materials/two-qubit-page-2.txt`：② ZeroPointFinderByQQSwap；交换与ZZ等同文字需纠正
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程补充：手册3.1.2只测交换，L696–736不构成ZZ证据
- `resources/new_attachments/p0-report-extracted.txt`：L2420–2675
- `resources/source_materials/two-qubit-page-2.txt`：②中的交换/ZZ混淆需分开
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：工程补充：条件Ramsey；手册L874条件相位思想
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.1.1，L666–694
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.2.1，L742–782
- `resources/new_attachments/p0-report-extracted.txt`：L1991–2103
