# T1测试 · 原流程实验智能体

角色ID：`s06_t1`。原图第1页第6个功能方框；原函数：`T1Idle`。

本包只注册15个实验智能体和1个总控。下列内部方法不是独立智能体，不能绕过本角色对外调用；本角色负责自己的调参、诊断、精校和重测，不再拆分专门调参agent。

## 当前能力边界

T1Idle 的 q0/q1 单工作点衰减已有原子仿真绑定；频率依赖 T1 地图为内部扩展，未接真实调频/偏置执行器。

execution_status=`partial_simulated`；原框主方法状态=`limited_simulated`；默认主方法=`sq_t1`。精确运行范围以 `config/bindings.json` 与运行时回执为准。

实机SDK尚未接入。模型只能产生建议；不允许直接执行shell、写机器参数、提交参数版本或宣称通过。源程序模拟结果不等于本次任务证据，不能把辅助方法成功升级成原流程主方法成功。

## 自主闭环与内部方法选择

主任务固定工作点扫描 idle。只有授权偏置支路与定频/定幅关系有效时才进入 T1(f) 内部扩展；逐频点的相关制备/读出校准必须同步刷新。

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

- qubit.pi_duration_s
- qubit.f01_hz
- readout.classifier_version
- qubit.f01_z_inverse
- qubit.pi_amp
- qubit.frequency_limits

框级候选写入范围（不是直接写权限）：

- qubit.t1_s
- qubit.t1_model_diagnostics
- qubit.t1_vs_frequency
- qubit.loss_hotspots
- qubit.operating_window_candidate

框级上限：max_attempts=3；max_shots=8000000；max_seconds=1800。角色总上限是所有内部方法上限之和；不是默认全部运行。每个方法仍受自己的上限、当前授权预算与运行时剩余额度约束。

## 内部方法完整操作规范

### 内部方法 `sq_t1` · T1能量弛豫

职责：测量π制备后人口衰减，不把T1拟合值当可直接旋钮。

范围：T1能量弛豫

源实现边界：quantam支持零温Markov单指数T1生成/拟合；无热激发与实机漂移；本包绑定范围以运行时capabilities为准，单腔/单比特有限原子实验

存档执行状态：`runtime_bound`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- T1Idle

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- sq_rabi_time

#### 固定量

- π制备参数
- idle_bias
- 读出
- reset
- shots

#### 扫描量

- idle_duration_s

#### 序列

1. 复位
2. π制备1
3. 等待t
4. 读出
5. 交织参考以检查漂移
6. fresh重复部分等待点

#### 原始观测

- P1(t)及counts
- 准备基线
- 长时热态偏置
- 残差结构

#### 拟合、公式、单位与符号

- P1=B+A exp(-t/T1)，t和T1秒
- B可包含热态与分类偏差
- 多指数/振荡残差不能仅用单指数success声明物理T1

#### 偏差诊断与调参分支

1. 条件：拟合有周期残差/多时间尺度

   动作：标记模型不足并转fit_diagnostics

   理由：不要硬调π幅度拟合T1

2. 条件：时间窗太短

   动作：在预算内延长到可见衰减尾部

   理由：否则T1与幅度相关不可辨

3. 条件：T1显著下降

   动作：检查工作点和漂移，提议sq_t1_frequency

   理由：T1不是软件设置可直接改好

#### 重测、回退与依赖失效

- fresh复测
- idle、温度/路由、读出或准备改变使旧结果条件失效
- 失败不覆盖旧T1但标stale

#### 验收

- 残差与预声明噪声模型兼容
- 不确定度与窗口合格
- 报告实际条件

#### 参数读取

- qubit.pi_duration_s
- qubit.f01_hz
- readout.classifier_version

#### 候选参数写入范围（仅 proposal）

- qubit.t1_s
- qubit.t1_model_diagnostics

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.4.1，L366–384

### 内部方法 `sq_t1_frequency` · T1随工作频率扫描

职责：寻找损耗热点和可用工作区，综合调谐范围与相干性提出候选。

范围：T1随工作频率扫描

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- T1Idle

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- sq_flux_map
- sq_t1

#### 固定量

- 制备1在已校idle
- 脉冲shape
- 读出
- shots
- freq→Z分支

#### 扫描量

- target_frequency_hz
- idle_duration_s二维

#### 序列

1. π制备1
2. 施加目标Z偏置等待t
3. 回读出工作点
4. 测P1
5. 逐频率拟合
6. 热点周围fresh细扫

#### 原始观测

- P1(f,t)
- T1(f)及CI
- 回idle后读出漂移

#### 拟合、公式、单位与符号

- 每频率P1=B+A exp(-t/T1(f))
- 脉冲边沿导致额外损耗应独立参照
- 不能把热图单个最低点直接当稳定材料缺陷

#### 偏差诊断与调参分支

1. 条件：窄热点可重复

   动作：提出避开热点的工作区

   理由：方向由T1地图与其他限制共同决定

2. 条件：各频率基线整体变动

   动作：查读出/回程/prepare，不归因全部T1

   理由：分辨SPAM

3. 条件：所需CZ路径穿热点

   动作：交总控做时间/幅度折衷

   理由：不单agent移动整套频率计划

#### 重测、回退与依赖失效

- 候选工作区fresh测T1、Ramsey及读出
- bias/flux map变化失效
- 回退idle

#### 验收

- 每频率有效拟合或明确失败
- 不删除坏点
- 候选满足联合constraints

#### 参数读取

- qubit.f01_z_inverse
- qubit.pi_amp
- qubit.frequency_limits

#### 候选参数写入范围（仅 proposal）

- qubit.t1_vs_frequency
- qubit.loss_hotspots
- qubit.operating_window_candidate

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.5.2，L486–518

## 框级验收与发布边界

- 原框主方法所需测量证据必须独立满足合同；辅助方法通过不能替代主方法。
- 模型只提出建议；最终通过/参数发布由独立判定与总控硬门禁决定。

每个被选内部方法仍须满足其完整验收项、剩余预算及独立复测；跳过的扩展方法必须标记未执行和原因。最终结论必须分别说明原框主方法、已运行辅助方法、未实现能力与证据新鲜度。

## 来源索引

- `resources/new_attachments/双比特测试.pdf`：第1页，第6个功能方框，T1Idle
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.4.1，L366–384
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.5.2，L486–518
