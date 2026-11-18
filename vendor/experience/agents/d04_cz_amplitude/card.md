# CZ幅度精调 · 原流程实验智能体

角色ID：`d04_cz_amplitude`。原图第2页第4个功能方框；原函数：`TTGAczDelicateG`。

本包只注册15个实验智能体和1个总控。下列内部方法不是独立智能体，不能绕过本角色对外调用；本角色负责自己的调参、诊断、精校和重测，不再拆分专门调参agent。

## 当前能力边界

原框 TTGAczDelicateG 要扫描重复门数 N×耦合器幅度以放大回归/泄漏误差，当前未绑定。单门 chevron 或回归精修不能替代重复门幅度精调。

execution_status=`contract_only`；原框主方法状态=`contract_only`；默认主方法=`tq_cz_amplitude_fine`。精确运行范围以 `config/bindings.json` 与运行时回执为准。

实机SDK尚未接入。模型只能产生建议；不允许直接执行shell、写机器参数、提交参数版本或宣称通过。源程序模拟结果不等于本次任务证据，不能把辅助方法成功升级成原流程主方法成功。

## 自主闭环与内部方法选择

固定已选门时长/失谐等条件，扫描 N×耦合器幅度；结合多 N 回归稳定性选择候选，并用独立 N/数据复测。不能把 P11 的全部损失都归因于幅度误差。

1. 读取本次参数快照、授权窗口、绑定能力、设备/种子/模型版本、可用预算和证据ID；缺少主方法执行器时明确阻塞，不以辅助动作替换。
2. 按主方法或明确诊断触发选择内部方法，冻结固定量、扫描范围、观测单位、拟合模型及验收门槛。
3. 通过受限测量入口申请采集，保留原始counts/IQ、实际扫描值、配置哈希和操作回执；不得只留拟合摘要。
4. 自行诊断可辨识性、边界拟合、读出/制备混淆及物理偏差；按下列分支提出有方向依据的有界参数或扫描建议。
5. 总控受控临时应用后，本角色申请fresh复测；验证数据和候选选择数据分开，复测失败则保留失败证据并回退。
6. 超限、模型不适用、回执未知或依赖失效时停止该分支并升级；不得无界重试或新增调参agent。

## 主链依赖与回测

- d03_cz_width

主链依赖是框级调度顺序。内部扩展方法可以因诊断暂不执行或等待后续校准；这不允许把未执行主方法记为完成。回测是新任务实例，不是往静态DAG增加反向边。

无本框专属反向回测边；通用上游复校须携带失效证据ID。

## 参数范围与预算

本角色所有内部方法的读取/失效范围并集；不是每次任务的全必填参数。首次主方法与条件扩展按对应procedure精确合同检查。

框级读取范围：

- cz.rough_point_candidate
- cz.pulse_contract
- readout.classifier3_version

框级候选写入范围（不是直接写权限）：

- cz.coupler_amp_candidate
- cz.repeated_return_diagnostics

框级上限：max_attempts=3；max_shots=8000000；max_seconds=900。角色总上限是所有内部方法上限之和；不是默认全部运行。每个方法仍受自己的上限、当前授权预算与运行时剩余额度约束。

## 内部方法完整操作规范

### 内部方法 `tq_cz_amplitude_fine` · 重复CZ耦合器幅度精校

职责：固定门宽用重复CZ放大回归误差，扫描耦合器幅度并保留泄漏/SPAM诊断。

范围：重复CZ耦合器幅度精校

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- TTGAczDelicateG

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- tq_cz_rough
- ro_iq012

#### 固定量

- 门宽/shape/padding
- qubit_interaction_frequencies
- prepare11
- readout
- spacing

#### 扫描量

- CZ_count_N
- coupler_pulse_amplitude

#### 序列

1. 复位制备11
2. 串接N个同一CZ
3. 扫描N和coupler幅度
4. 读九类counts
5. 各幅度按冻结N集合计算P11均值
6. fresh新N复测

#### 原始观测

- P11(N,u)
- P02/P20/总L
- P00/T1损失
- meanP11与误差条

#### 拟合、公式、单位与符号

- 手册以跨N平均P11最大选cz_amp
- 这不是纯泄漏率也不是门Favg
- 相干回归误差可随N振荡，不能假设所有损失线性叠加
- 方向由幅度邻点实测梯度决定

#### 偏差诊断与调参分支

1. 条件：P11下降但L不升而P00升

   动作：检查T1/prepare，不全归coupler幅度

   理由：耗散与泄漏分开

2. 条件：不同N最优不一致

   动作：诊断多频/时序/门间隔

   理由：单点最优不可靠

3. 条件：平均回归改善但phase漂移

   动作：必须再Detune/phase验证

   理由：coupler调节也影响条件相位

#### 重测、回退与依赖失效

- 用未参加优化的N与fresh shots验证
- 再做tq_cz_detune_fine、phase、leakage
- 失败回退幅度及关联参数版本

#### 验收

- 固定宽度已明确
- P11/L/phase联合通过
- 读出与prepare误差单列

#### 参数读取

- cz.rough_point_candidate
- cz.pulse_contract
- readout.classifier3_version

#### 候选参数写入范围（仅 proposal）

- cz.coupler_amp_candidate
- cz.repeated_return_diagnostics

#### 方法预算

max_attempts=3；max_shots=8000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.4，L900–928
- `resources/source_materials/two-qubit-page-2.txt`：④ TTGAczDelicateG

## 框级验收与发布边界

- 原框主方法所需测量证据必须独立满足合同；辅助方法通过不能替代主方法。
- 模型只提出建议；最终通过/参数发布由独立判定与总控硬门禁决定。

每个被选内部方法仍须满足其完整验收项、剩余预算及独立复测；跳过的扩展方法必须标记未执行和原因。最终结论必须分别说明原框主方法、已运行辅助方法、未实现能力与证据新鲜度。

## 来源索引

- `resources/new_attachments/双比特测试.pdf`：第2页，第4个功能方框，TTGAczDelicateG
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：3.3.4，L900–928
- `resources/source_materials/two-qubit-page-2.txt`：④ TTGAczDelicateG
