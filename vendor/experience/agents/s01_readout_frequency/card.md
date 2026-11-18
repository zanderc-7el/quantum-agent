# 读取腔定频 · 原流程实验智能体

角色ID：`s01_readout_frequency`。原图第1页第1个功能方框；原函数：`ReadoutFreqDip`。

本包只注册15个实验智能体和1个总控。下列内部方法不是独立智能体，不能绕过本角色对外调用；本角色负责自己的调参、诊断、精校和重测，不再拆分专门调参agent。

## 当前能力边界

原框主方法是 ReadoutFreqDip。已有原子绑定仅线性单腔 S21 寻峰；高低功率、多腔归属和真实偏置依赖属于本角色内部合同，未接实机。

execution_status=`partial_simulated`；原框主方法状态=`limited_simulated`；默认主方法=`ro_resonator_survey`。精确运行范围以 `config/bindings.json` 与运行时回执为准。

实机SDK尚未接入。模型只能产生建议；不允许直接执行shell、写机器参数、提交参数版本或宣称通过。源程序模拟结果不等于本次任务证据，不能把辅助方法成功升级成原流程主方法成功。

## 自主闭环与内部方法选择

先执行线性/授权功率下腔定频；需要判断功率导致的频移或偏置改变时，再启用功率/偏置内部扫描。每次更改读出路径或偏置后重新定频，不套用旧峰。

1. 读取本次参数快照、授权窗口、绑定能力、设备/种子/模型版本、可用预算和证据ID；缺少主方法执行器时明确阻塞，不以辅助动作替换。
2. 按主方法或明确诊断触发选择内部方法，冻结固定量、扫描范围、观测单位、拟合模型及验收门槛。
3. 通过受限测量入口申请采集，保留原始counts/IQ、实际扫描值、配置哈希和操作回执；不得只留拟合摘要。
4. 自行诊断可辨识性、边界拟合、读出/制备混淆及物理偏差；按下列分支提出有方向依据的有界参数或扫描建议。
5. 总控受控临时应用后，本角色申请fresh复测；验证数据和候选选择数据分开，复测失败则保留失败证据并回退。
6. 超限、模型不适用、回执未知或依赖失效时停止该分支并升级；不得无界重试或新增调参agent。

## 主链依赖与回测

本框为主链起点；仍须通过能力、授权和证据门禁。

主链依赖是框级调度顺序。内部扩展方法可以因诊断暂不执行或等待后续校准；这不允许把未执行主方法记为完成。回测是新任务实例，不是往静态DAG增加反向边。

无本框专属反向回测边；通用上游复校须携带失效证据ID。

## 参数范围与预算

本角色所有内部方法的读取/失效范围并集；不是每次任务的全必填参数。首次主方法与条件扩展按对应procedure精确合同检查。

框级读取范围：

- readout.design_frequencies_hz
- readout.route
- device.bias_version
- readout.resonance_hz
- readout.attenuation_map
- device.power_limits
- device.bias_limits
- readout.low_power_frequency_hz
- readout.power_candidate

框级候选写入范围（不是直接写权限）：

- readout.resonance_hz
- readout.linewidth_hz
- readout.peak_assignment
- readout.low_power_frequency_hz
- readout.high_power_frequency_hz
- readout.dispersive_shift_hz
- readout.power_candidate
- qubit.idle_bias_candidate
- readout.frequency_vs_bias

框级上限：max_attempts=3；max_shots=12000000；max_seconds=2700。角色总上限是所有内部方法上限之和；不是默认全部运行。每个方法仍受自己的上限、当前授权预算与运行时剩余额度约束。

## 内部方法完整操作规范

### 内部方法 `ro_resonator_survey` · 读出腔寻峰与偏置后定频

职责：给出腔频候选、线宽及峰归属；偏置更新后用fresh任务重新定频，不沿用旧腔频。

范围：读出腔寻峰与偏置后定频

源实现边界：quantam支持单个线性稳态谐振器；多腔、真实功率/偏置依赖均为contract；本包绑定范围以运行时capabilities为准，单腔/单比特有限原子实验

存档执行状态：`runtime_bound`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- ReadoutFreqMulti
- ReadoutFreqDip

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- capability_router

#### 固定量

- probe_amplitude：授权线性区
- idle_bias
- averages_per_point
- readout_path

#### 扫描量

- probe_frequency_hz：授权窗口内一维等距扫描

#### 序列

1. 保持器件偏置并记录读回版本
2. 逐频率发探测信号
3. 采集归一化复S21或I/Q均值
4. 拟合每个可辨识吸收谷
5. 候选频率附近独立细扫

#### 原始观测

- 复S21实部/虚部与频率
- 每点平均次数和均值噪声
- 多腔峰间距、归属不确定性

#### 拟合、公式、单位与符号

- 线性单腔子模式 S21=1-d/[1+2i(f-fr)/κ]，f、fr、κ均Hz
- 多腔模式须选用经验证的多峰模型，不能用单谷拟合强行分配
- κ为该模型全宽，显示精度不等于物理准确度

#### 偏差诊断与调参分支

1. 条件：没有谷或归属不唯一

   动作：检查连接/功率/频窗并提出受限新扫描

   理由：平坦数据不能定频

2. 条件：谷贴扫描边界

   动作：向该侧扩窗但不越授权频率范围

   理由：避免边界拟合假峰

3. 条件：独立细扫与原拟合一致

   动作：提出fr及线宽候选

   理由：不得直接写芯片配置

#### 重测、回退与依赖失效

- 提交前fresh细扫
- 任一偏置/路由/衰减更改使旧腔频、IQ分类、读出优化及其下游证据失效
- 回退到上一个有读回证据的读出配置

#### 验收

- 峰位不在边界且残差满足冻结criteria
- 峰归属明确
- 单腔runtime不得声明完成多腔寻峰

#### 参数读取

- readout.design_frequencies_hz
- readout.route
- device.bias_version

#### 候选参数写入范围（仅 proposal）

- readout.resonance_hz
- readout.linewidth_hz
- readout.peak_assignment

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：1.1.1–1.1.4，L52–144
- `resources/new_attachments/p0-report-extracted.txt`：L427–607

### 内部方法 `ro_power_shift` · 高低功率色散位移检查

职责：区分低功率读出频率与趋近裸腔的高功率频率，选择可解析色散位移的功率区。

范围：高低功率色散位移检查

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- ReadoutFreqDip

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- ro_resonator_survey

#### 固定量

- idle_bias
- readout_path
- 积分窗口
- 每点平均次数

#### 扫描量

- readout_power或校准衰减档位
- 每档probe_frequency_hz

#### 序列

1. 读回功率/衰减映射
2. 低功率扫频
3. 在授权功率上限内逐档增加并扫频
4. 恢复低功率fresh复测

#### 原始观测

- 各功率复S21
- 吸收谷位置/宽度
- 压缩或加热征兆

#### 拟合、公式、单位与符号

- Δfr=fr_low-fr_high，Hz
- 高功率趋近裸腔是待验证解释而非必然
- 手册0/25衰减档仅原设备示例，绝不能自动复制

#### 偏差诊断与调参分支

1. 条件：频率无可解析变化

   动作：报告不能确认色散位移并查读出链/功率标定

   理由：无位移不是直接证明芯片损坏

2. 条件：功率升高致线形扭曲或恢复后漂移

   动作：停止升功率并回退

   理由：避免把非线性响应拟成线性腔

3. 条件：存在稳定功率区间

   动作：提出低功率测量工作点

   理由：方向由测量功率响应决定

#### 重测、回退与依赖失效

- 候选低功率用fresh扫频确认
- 功率修改使IQ分类、读出误差与优化结果失效
- 高低功率数据保持独立

#### 验收

- 位移大于冻结不确定度标准
- 低功率复测恢复
- 无越限或读出压缩

#### 参数读取

- readout.resonance_hz
- readout.attenuation_map
- device.power_limits

#### 候选参数写入范围（仅 proposal）

- readout.low_power_frequency_hz
- readout.high_power_frequency_hz
- readout.dispersive_shift_hz
- readout.power_candidate

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：1.1.2，L80–102

### 内部方法 `ro_bias_map` · 量子比特偏置与腔频映射

职责：用腔响应检查Z调谐并提出idle偏置候选，后续需独立谱学确认。

范围：量子比特偏置与腔频映射

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- ReadoutFreqQubitBiasDependency

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- ro_power_shift

#### 固定量

- readout_power
- 其他器件偏置
- 扫描顺序与稳定等待时间

#### 扫描量

- qubit_dc_bias
- probe_frequency_hz二维网格

#### 序列

1. 按限幅斜率改变Z偏置并读回
2. 每个偏置扫腔
3. 追踪连续腔分支
4. 回到原点检查迟滞
5. 候选idle处fresh腔频复测

#### 原始观测

- S21(bias,f)
- 实际偏置读回
- 腔分支/回程差异

#### 拟合、公式、单位与符号

- fr(bias)经验曲线，偏置单位由adapter声明
- 局部导数仅在单一连续分支解释
- 腔频极值不能自动等同所有器件的最佳相干工作点

#### 偏差诊断与调参分支

1. 条件：出现避交叉/分支跳转

   动作：分支追踪并请求谱学确认

   理由：不盲取全图最大值

2. 条件：迟滞或回程差异超criteria

   动作：停止调参并保留原idle

   理由：偏置映射不稳定

3. 条件：候选局部极值可重复

   动作：提出idle及适用区间

   理由：用邻点测得导数确定调节方向

#### 重测、回退与依赖失效

- 调用ro_resonator_survey fresh task覆盖手册1.1.4
- idle变化使f01、π脉冲、T1/T2、读出分类及双比特校准失效
- 保存原idle用于回退

#### 验收

- 分支可辨识且回程一致
- 候选未越偏置限制
- 未用腔曲线单独证明最高f01

#### 参数读取

- device.bias_limits
- readout.low_power_frequency_hz
- readout.power_candidate

#### 候选参数写入范围（仅 proposal）

- qubit.idle_bias_candidate
- readout.frequency_vs_bias

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：1.1.3–1.1.4，L104–144

## 框级验收与发布边界

- 原框主方法所需测量证据必须独立满足合同；辅助方法通过不能替代主方法。
- 模型只提出建议；最终通过/参数发布由独立判定与总控硬门禁决定。

每个被选内部方法仍须满足其完整验收项、剩余预算及独立复测；跳过的扩展方法必须标记未执行和原因。最终结论必须分别说明原框主方法、已运行辅助方法、未实现能力与证据新鲜度。

## 来源索引

- `resources/new_attachments/双比特测试.pdf`：第1页，第1个功能方框，ReadoutFreqDip
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：1.1.1–1.1.4，L52–144
- `resources/new_attachments/p0-report-extracted.txt`：L427–607
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：1.1.2，L80–102
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：1.1.3–1.1.4，L104–144

## 2026-11-16 现场经验增补

完整案例与来源：[定频与网分工作点](../../knowledge/field_review_20261116.md)；[完整续谈与相干验证](../../knowledge/field_review_20261116_152511.md)。以下摘要直接进入模型提示词；链接正文需宿主按需读取。

- 本节是八号机现场经验的条件化摘要。历史数值只作例子，必须由本次对象、参数版本、有效binding和采集证据确定扫描；不扩展设备权限、方法归属或预算。
- 区分物理比特标识、频率排序标签、JSON对象和通道；仪器源端功率、外部衰减后参考面、芯片端功率分别记录。NA/MW与主机XY是不同测量路径。
- 历史扫描完成、现场弱响应口述和本次独立复测分开；回读正常不等于已测到物理RF输出，未找到响应不直接归因为硬件损坏或软件方法穷尽。
- 现场指图与转写歧义需保留来源和待核状态，不补造对象/频率/单位；腔谱功率依赖、能谱候选、相干验证、频率精校与保真度是不同证据层级。
- 按当前总线与接线确认腔频及对象映射；保留新旧编号关系的证据，不凭统一频移或排序自动认定物理对应。
- 结合S21_Power确认低功率分支、频窗覆盖和读出对比度；有无阶梯要结合空腔/带比特腔身份，不能要求所有对象出现同样图形。
- 外部衰减变化后重建工作点；保留源端实际电平和参考面。扫过读出幅度不等于在这些幅度下完成比特频谱验证。
- Q 用适用模型的线宽及复数 S21 拟合，区分 Ql/Qi/Qc；notch 深度不能单独判断 Q 或比特质量。非对称形态先查背景、线缆延迟和失配，不直接定位芯片或键合故障。
- 未与版图对应的额外凹坑先标未归属模式；封装腔模仅为候选解释。功率未见阶梯时记录覆盖、连续位移和噪声，按证据选择向低/高功率扩展或提高精度。
