# 读取保真度测试 · 原流程实验智能体

角色ID：`s04_readout_fidelity`。原图第1页第4个功能方框；原函数：`IqToProbability`。

本包只注册15个实验智能体和1个总控。下列内部方法不是独立智能体，不能绕过本角色对外调用；本角色负责自己的调参、诊断、精校和重测，不再拆分专门调参agent。

## 当前能力边界

原图第4框是 IqToProbability。真实 IQ 分类训练、留出复测与 0/1/2 混淆矩阵均未绑定；仿真中的已设读出误差不是训练分类器的实测通过证据。

execution_status=`contract_only`；原框主方法状态=`contract_only`；默认主方法=`ro_iq01`。精确运行范围以 `config/bindings.json` 与运行时回执为准。

实机SDK尚未接入。模型只能产生建议；不允许直接执行shell、写机器参数、提交参数版本或宣称通过。源程序模拟结果不等于本次任务证据，不能把辅助方法成功升级成原流程主方法成功。

## 自主闭环与内部方法选择

先训练和留出验证 0/1 IQ 判别；需要泄漏分辨且具备 1→2 制备后再训练 0/1/2。把制备误差、读出误差与矩阵反演偏差分别记录；接收 s05 优化回测时必须建立新任务和新数据。

1. 读取本次参数快照、授权窗口、绑定能力、设备/种子/模型版本、可用预算和证据ID；缺少主方法执行器时明确阻塞，不以辅助动作替换。
2. 按主方法或明确诊断触发选择内部方法，冻结固定量、扫描范围、观测单位、拟合模型及验收门槛。
3. 通过受限测量入口申请采集，保留原始counts/IQ、实际扫描值、配置哈希和操作回执；不得只留拟合摘要。
4. 自行诊断可辨识性、边界拟合、读出/制备混淆及物理偏差；按下列分支提出有方向依据的有界参数或扫描建议。
5. 总控受控临时应用后，本角色申请fresh复测；验证数据和候选选择数据分开，复测失败则保留失败证据并回退。
6. 超限、模型不适用、回执未知或依赖失效时停止该分支并升级；不得无界重试或新增调参agent。

## 主链依赖与回测

- s03_rabi_amplitude

主链依赖是框级调度顺序。内部扩展方法可以因诊断暂不执行或等待后续校准；这不允许把未执行主方法记为完成。回测是新任务实例，不是往静态DAG增加反向边。

无本框专属反向回测边；通用上游复校须携带失效证据ID。

## 参数范围与预算

本角色所有内部方法的读取/失效范围并集；不是每次任务的全必填参数。首次主方法与条件扩展按对应procedure精确合同检查。

框级读取范围：

- qubit.pi_amp
- readout.frequency
- readout.amplitude
- readout.integration_window
- qubit.prep2_evidence

框级候选写入范围（不是直接写权限）：

- readout.iq01_centers
- readout.classifier_version
- readout.assignment_matrix
- readout.iq012_centers
- readout.assignment_matrix_3
- readout.classifier3_version

框级上限：max_attempts=3；max_shots=8000000；max_seconds=1800。角色总上限是所有内部方法上限之和；不是默认全部运行。每个方法仍受自己的上限、当前授权预算与运行时剩余额度约束。

## 内部方法完整操作规范

### 内部方法 `ro_iq01` · 二态IQ分类训练与独立复测

职责：建立0/1判别器与混淆矩阵，报告制备+读出联合误差边界。

范围：二态IQ分类训练与独立复测

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- IqToProbability

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- sq_rabi_amplitude

#### 固定量

- idle_bias
- π制备
- 读出频幅
- 积分时间
- shots_per_label

#### 扫描量

- prepared_label∈{0,1}
- 独立train/validation重复采集

#### 序列

1. 随机交织0/1制备顺序
2. 逐shot保存IQ
3. 仅训练集拟合中心/边界
4. 冻结分类器
5. fresh验证集估计混淆矩阵

#### 原始观测

- 逐shot IQ和准备标签
- F00/F11
- 2×2 assignment矩阵及不确定度

#### 拟合、公式、单位与符号

- 手册质心连线垂直平分判别是候选方法
- F=(F00+F11)/2
- 验证标签来自制备，未独立去除SPAM时不是纯本征读出保真度
- 校正counts不得生成负概率再偷偷裁剪

#### 偏差诊断与调参分支

1. 条件：云团重叠或漂移

   动作：先交ro_optimize调读出，不用π幅度硬补

   理由：读出偏差与制备错误需区分

2. 条件：训练好而留出差

   动作：拒绝分类器并查过拟合/漂移

   理由：训练分离度不是验收

3. 条件：混淆矩阵病态

   动作：停止概率逆校正并报告未校正计数

   理由：避免放大统计噪声

#### 重测、回退与依赖失效

- 任一读出频幅/积分窗/idle变更触发同agent fresh task
- 训练和复测不得复用样本
- 失败回退上一个有效分类器并标记依赖结果失效

#### 验收

- 独立验证F00/F11及CI达到冻结标准
- 原始IQ/未校正counts保留
- 无只选好shots

#### 参数读取

- qubit.pi_amp
- readout.frequency
- readout.amplitude
- readout.integration_window

#### 候选参数写入范围（仅 proposal）

- readout.iq01_centers
- readout.classifier_version
- readout.assignment_matrix

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.3.1，L276–300
- `resources/source_materials/two-qubit-page-1.txt`：step2 IqToProbability

### 内部方法 `ro_iq012` · 三态IQ分类与泄漏可分辨性

职责：为0/1/2训练分类器，验证|2>检出能力；没有三态分辨不能给可靠泄漏指标。

范围：三态IQ分类与泄漏可分辨性

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- IqToProbability

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- sq_rabi12

#### 固定量

- 01π及12π
- idle
- 读出链
- 各态shots

#### 扫描量

- prepared_label∈{0,1,2}
- train/validation批次

#### 序列

1. 交织三态制备
2. 保存逐shotIQ与准备版本
3. 拟合三中心或经验证分类器
4. 冻结后fresh三态混淆复测
5. 与2制备可信度联合报告

#### 原始观测

- 3×3 confusion及每态检出率
- IQ漂移
- 联合九类counts schema

#### 拟合、公式、单位与符号

- nearest-centroid只是基线
- P(leak)=Σ含2的counts/全部shots
- 制备2误差与分类2误差需分别标注不可完全分离项

#### 偏差诊断与调参分支

1. 条件：2云团与1不可辨

   动作：禁用可靠泄漏声明，调读出或扩大测量接口

   理由：不能将2强制归1

2. 条件：2制备证据不足

   动作：返回sq_rabi12 fresh任务

   理由：训练标签不等于真实态

3. 条件：三态均达阈值

   动作：发布分类器候选

   理由：仅参数注册器提交

#### 重测、回退与依赖失效

- 读出/频率/12准备变化需重新训练和fresh验证
- 原始九类结果及未校正counts必须保留
- 失败回退旧classifier且阻塞泄漏验收

#### 验收

- 每态独立验证及CI合格
- counts总和等于shots
- 训练/复测样本独立

#### 参数读取

- qubit.prep2_evidence
- readout.frequency
- readout.amplitude
- readout.integration_window

#### 候选参数写入范围（仅 proposal）

- readout.iq012_centers
- readout.assignment_matrix_3
- readout.classifier3_version

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.3.1、2.3.3，L290–300、L332–360

## 框级验收与发布边界

- 原框主方法所需测量证据必须独立满足合同；辅助方法通过不能替代主方法。
- 模型只提出建议；最终通过/参数发布由独立判定与总控硬门禁决定。

每个被选内部方法仍须满足其完整验收项、剩余预算及独立复测；跳过的扩展方法必须标记未执行和原因。最终结论必须分别说明原框主方法、已运行辅助方法、未实现能力与证据新鲜度。

## 来源索引

- `resources/new_attachments/双比特测试.pdf`：第1页，第4个功能方框，IqToProbability
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.3.1，L276–300
- `resources/source_materials/two-qubit-page-1.txt`：step2 IqToProbability
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.3.1、2.3.3，L290–300、L332–360
