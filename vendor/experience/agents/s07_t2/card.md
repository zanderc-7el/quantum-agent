# T2测试 · 原流程实验智能体

角色ID：`s07_t2`。原图第1页第7个功能方框；原函数：`T2CPMG`。

本包只注册15个实验智能体和1个总控。下列内部方法不是独立智能体，不能绕过本角色对外调用；本角色负责自己的调参、诊断、精校和重测，不再拆分专门调参agent。

## 当前能力边界

原框主方法是 T2CPMG。已有 sq_ramsey 原子绑定只测自由演化 Ramsey 的 T2* 与带符号频偏，是诊断辅助；不能替代回聚焦的 CPMG，也不能据此宣称原框 T2CPMG 通过。

execution_status=`partial_simulated`；原框主方法状态=`contract_only`；默认主方法=`sq_cpmg`。精确运行范围以 `config/bindings.json` 与运行时回执为准。

实机SDK尚未接入。模型只能产生建议；不允许直接执行shell、写机器参数、提交参数版本或宣称通过。源程序模拟结果不等于本次任务证据，不能把辅助方法成功升级成原流程主方法成功。

## 自主闭环与内部方法选择

主任务按 T2CPMG 定义扫描自由演化/脉冲数，明确总时长与间隔口径。Ramsey 用于频偏、T2* 与去相干诊断；它可以帮助决定是否回到 s02，但不能作为 CPMG 的完成证据。

1. 读取本次参数快照、授权窗口、绑定能力、设备/种子/模型版本、可用预算和证据ID；缺少主方法执行器时明确阻塞，不以辅助动作替换。
2. 按主方法或明确诊断触发选择内部方法，冻结固定量、扫描范围、观测单位、拟合模型及验收门槛。
3. 通过受限测量入口申请采集，保留原始counts/IQ、实际扫描值、配置哈希和操作回执；不得只留拟合摘要。
4. 自行诊断可辨识性、边界拟合、读出/制备混淆及物理偏差；按下列分支提出有方向依据的有界参数或扫描建议。
5. 总控受控临时应用后，本角色申请fresh复测；验证数据和候选选择数据分开，复测失败则保留失败证据并回退。
6. 超限、模型不适用、回执未知或依赖失效时停止该分支并升级；不得无界重试或新增调参agent。

## 主链依赖与回测

- s05_readout_optimization

主链依赖是框级调度顺序。内部扩展方法可以因诊断暂不执行或等待后续校准；这不允许把未执行主方法记为完成。回测是新任务实例，不是往静态DAG增加反向边。

无本框专属反向回测边；通用上游复校须携带失效证据ID。

## 参数范围与预算

本角色所有内部方法的读取/失效范围并集；不是每次任务的全必填参数。首次主方法与条件扩展按对应procedure精确合同检查。

框级读取范围：

- qubit.pi_amp
- qubit.half_pi_amp
- qubit.f01_hz
- drive.axis_convention
- qubit.pi_duration_s
- qubit.f01_coarse_hz
- drive.phase_convention
- qubit.frequency_prior_hz

框级候选写入范围（不是直接写权限）：

- qubit.t2_cpmg_s_by_n
- qubit.echo_diagnostics
- qubit.ramsey_detuning_hz
- qubit.t2_ramsey_s
- qubit.f01_candidate_hz

框级上限：max_attempts=3；max_shots=8000000；max_seconds=1800。角色总上限是所有内部方法上限之和；不是默认全部运行。每个方法仍受自己的上限、当前授权预算与运行时剩余额度约束。

## 内部方法完整操作规范

### 内部方法 `sq_cpmg` · CPMG/echo相干时间

职责：通过回聚焦脉冲区分低频失谐敏感性，测T2_CPMG(N)，不冒充Ramsey同一参数。

范围：CPMG/echo相干时间

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- T2CPMG

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- sq_rabi_amplitude
- ro_iq01

#### 固定量

- π及π2校准
- pulse shape
- analysis offset
- readout

#### 扫描量

- total_idle_time_s
- refocus_count_N
- 可选X/Y refocus轴

#### 序列

1. π2制备
2. 总等待t分N等份，每份中心插π
3. 末π2分析
4. N=1作为echo
5. 对各N重复时间扫描

#### 原始观测

- P1(t,N)
- 脉冲累计时长
- 对比度
- 衰减与振荡残差

#### 拟合、公式、单位与符号

- 默认拟合P=B+A exp[-(t/T)^β]，A符号依末分析脉冲
- 理想echo/CPMG回聚焦静态失谐，不能直接沿用Ramsey物理fq-fdrive的cos振荡
- 仅当末分析脉冲明确施加人为phase ramp或协议独立证实残余振荡时加cos(2πδt+φ)，δ定义必须另存
- β固定还是拟合须预声明
- 分开idle总时间和含脉冲总时长
- T2_CPMG>T2_Ramsey不是强制验收条件

#### 偏差诊断与调参分支

1. 条件：增加N反而对比度下降

   动作：检查π幅度/轴误差与有限脉冲占比

   理由：门误差可盖过回聚焦收益

2. 条件：包络非指数

   动作：报告β模型比较并保留初始门槛

   理由：不事后选模型逼通过

3. 条件：曲线可重复

   动作：记录T2_CPMG(N)供工作点评估

   理由：不直接修改物理退相干率

#### 重测、回退与依赖失效

- fresh抽取N与t复测
- π/DRAG/时序改变触发重测
- 失效只标记测量条件，不写硬件T2

#### 验收

- 序列时间核算正确
- 各N足够动态范围
- 指标与Ramsey分开

#### 参数读取

- qubit.pi_amp
- qubit.half_pi_amp
- qubit.f01_hz
- drive.axis_convention

#### 候选参数写入范围（仅 proposal）

- qubit.t2_cpmg_s_by_n
- qubit.echo_diagnostics

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.4.3，L414–434
- `resources/source_materials/two-qubit-page-1.txt`：step3 T2CPMG

### 内部方法 `sq_ramsey` · 双正交Ramsey与T2*估计

职责：恢复带符号失谐与相干包络，给频率修正候选但保留别名先验。

范围：双正交Ramsey与T2*估计

源实现边界：quantam P0有双正交及Markov包络；此T2非完整实机T2*/1f噪声证明；本包绑定范围以运行时capabilities为准，单腔/单比特有限原子实验

存档执行状态：`runtime_bound`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- T2Idle

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- sq_rabi_time

#### 固定量

- π2参数
- 参考频率
- 人为offset
- readout
- shots

#### 扫描量

- idle_duration_s
- analysis_basis∈{X,Y}

#### 序列

1. 复位
2. π2制备
3. 等待t
4. 分别X/Y分析π2
5. 取复信号
6. 拟合带符号振荡
7. 新频率下fresh残差验证

#### 原始观测

- Px(t),Py(t)
- 复相干
- counts
- 频率分支
- 包络及残差

#### 拟合、公式、单位与符号

- quantam约定z=(2Px-1)-i(2Py-1)≈C exp(-t/T2+i2πΔf t)+B
- Δf=fq-fdrive，候选fdrive_new=fdrive+Δf
- 改变分析相位约定需重新验证符号
- 均匀dt的别名间距1/dt，XY不消除该别名

#### 偏差诊断与调参分支

1. 条件：后验/频谱多峰或先验跨alias

   动作：加短时间或非公度间隔复测，报告歧义

   理由：不得选幅度最高峰即真值

2. 条件：无振荡但有衰减

   动作：允许零失谐模型，不强造频差

   理由：零频与低对比度需区分

3. 条件：offset修正后残差反增

   动作：回退并核对符号/频率读回

   理由：禁止继续同向叠加修正

#### 重测、回退与依赖失效

- 使用fresh记录验证更新后频率
- T2*与CPMG/echo分字段
- idle/π2/读出变化失效
- 保留原频率绝对值回退

#### 验收

- 频率分支有先验支持
- 局部误差和模型诊断合格
- fresh残差达criteria

#### 参数读取

- qubit.pi_duration_s
- qubit.f01_coarse_hz
- drive.phase_convention
- qubit.frequency_prior_hz

#### 候选参数写入范围（仅 proposal）

- qubit.ramsey_detuning_hz
- qubit.t2_ramsey_s
- qubit.f01_candidate_hz

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.4.2，L386–412

## 框级验收与发布边界

- 原框主方法所需测量证据必须独立满足合同；辅助方法通过不能替代主方法。
- 模型只提出建议；最终通过/参数发布由独立判定与总控硬门禁决定。

每个被选内部方法仍须满足其完整验收项、剩余预算及独立复测；跳过的扩展方法必须标记未执行和原因。最终结论必须分别说明原框主方法、已运行辅助方法、未实现能力与证据新鲜度。

## 来源索引

- `resources/new_attachments/双比特测试.pdf`：第1页，第7个功能方框，T2CPMG
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.4.3，L414–434
- `resources/source_materials/two-qubit-page-1.txt`：step3 T2CPMG
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.4.2，L386–412

## 2026-11-16 现场经验增补

完整案例与来源：[定频与网分工作点](../../knowledge/field_review_20261116.md)；[完整续谈与相干验证](../../knowledge/field_review_20261116_152511.md)。以下摘要直接进入模型提示词；链接正文需宿主按需读取。

- 本节是八号机现场经验的条件化摘要。历史数值只作例子，必须由本次对象、参数版本、有效binding和采集证据确定扫描；不扩展设备权限、方法归属或预算。
- 区分物理比特标识、频率排序标签、JSON对象和通道；仪器源端功率、外部衰减后参考面、芯片端功率分别记录。NA/MW与主机XY是不同测量路径。
- 历史扫描完成、现场弱响应口述和本次独立复测分开；回读正常不等于已测到物理RF输出，未找到响应不直接归因为硬件损坏或软件方法穷尽。
- 现场指图与转写歧义需保留来源和待核状态，不补造对象/频率/单位；腔谱功率依赖、能谱候选、相干验证、频率精校与保真度是不同证据层级。
- 有可用 π/2 脉冲后，已支持的 Ramsey 可辅助 s02 估计频偏；确认人工失谐、符号、单位与混叠范围，修改频率需独立数据验证。
- 现场转写中的 Rabi/Ramsey 用语待核，按实际脉冲序列判别。Ramsey 频偏/T2* 辅助不等于原主任务 CPMG 完成，不扩展本角色参数写入权限。
