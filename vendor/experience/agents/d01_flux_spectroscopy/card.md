# 二维比特能谱 · 原流程实验智能体

角色ID：`d01_flux_spectroscopy`。原图第2页第1个功能方框；原函数：`F01ZPulseDependency`。

本包只注册15个实验智能体和1个总控。下列内部方法不是独立智能体，不能绕过本角色对外调用；本角色负责自己的调参、诊断、精校和重测，不再拆分专门调参agent。

## 当前能力边界

原框 F01ZPulseDependency 扫偏置×频率并建立 f01(Z)；当前单偏置能谱原子绑定不构成二维 flux mapping。真实偏置安全窗、迟滞与工作支路仍需设备适配。

execution_status=`contract_only`；原框主方法状态=`contract_only`；默认主方法=`sq_flux_map`。精确运行范围以 `config/bindings.json` 与运行时回执为准。

实机SDK尚未接入。模型只能产生建议；不允许直接执行shell、写机器参数、提交参数版本或宣称通过。源程序模拟结果不等于本次任务证据，不能把辅助方法成功升级成原流程主方法成功。

## 自主闭环与内部方法选择

先在授权电压/磁通支路二维扫描，检查 f01(Z) 单调段、迟滞与跳模；把逆映射适用域和单位交给后续角色，不能跨支路外推。

1. 读取本次参数快照、授权窗口、绑定能力、设备/种子/模型版本、可用预算和证据ID；缺少主方法执行器时明确阻塞，不以辅助动作替换。
2. 按主方法或明确诊断触发选择内部方法，冻结固定量、扫描范围、观测单位、拟合模型及验收门槛。
3. 通过受限测量入口申请采集，保留原始counts/IQ、实际扫描值、配置哈希和操作回执；不得只留拟合摘要。
4. 自行诊断可辨识性、边界拟合、读出/制备混淆及物理偏差；按下列分支提出有方向依据的有界参数或扫描建议。
5. 总控受控临时应用后，本角色申请fresh复测；验证数据和候选选择数据分开，复测失败则保留失败证据并回退。
6. 超限、模型不适用、回执未知或依赖失效时停止该分支并升级；不得无界重试或新增调参agent。

## 主链依赖与回测

- s06_t1
- s07_t2
- s08_single_qubit_rb

主链依赖是框级调度顺序。内部扩展方法可以因诊断暂不执行或等待后续校准；这不允许把未执行主方法记为完成。回测是新任务实例，不是往静态DAG增加反向边。

无本框专属反向回测边；通用上游复校须携带失效证据ID。

## 参数范围与预算

本角色所有内部方法的读取/失效范围并集；不是每次任务的全必填参数。首次主方法与条件扩展按对应procedure精确合同检查。

框级读取范围：

- qubit.idle_bias
- qubit.f01_coarse_hz
- z.amplitude_limits
- z.transfer_calibration

框级候选写入范围（不是直接写权限）：

- qubit.f01_z_map
- qubit.f01_z_inverse
- qubit.max_f01_hz
- qubit.flux_branch

框级上限：max_attempts=3；max_shots=4000000；max_seconds=900。角色总上限是所有内部方法上限之和；不是默认全部运行。每个方法仍受自己的上限、当前授权预算与运行时剩余额度约束。

## 内部方法完整操作规范

### 内部方法 `sq_flux_map` · Z脉冲幅度—f01映射

职责：建立局部分支内frequency↔Z幅度转换，为双比特相互作用频率提供真实控制映射。

范围：Z脉冲幅度—f01映射

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- F01ZPulseDependency

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- sq_spectroscopy

#### 固定量

- Z脉冲宽度/shape
- 谱学幅度
- readout
- bias
- 参考电压

#### 扫描量

- z_pulse_amplitude
- drive_frequency_hz

#### 序列

1. 初始宽频窗采数个Z点
2. 拟合f(Z)
3. 下一Z点在预测附近窄扫并更新
4. 保留盲检点和回程扫描

#### 原始观测

- f01随Z
- 峰归属
- 实际DAC/电压读回
- hysteresis
- 拟合外推距离

#### 拟合、公式、单位与符号

- 局部可用f(Z)=a(Z-Z0)^2+fmax等经验式，但跨分支需物理或分段模型
- inverse须指定分支
- df/dZ可为正负，调节ΔZ≈Δf/(df/dZ)只在局部有效

#### 偏差诊断与调参分支

1. 条件：df/dZ近零或inverse双解

   动作：冻结方向并请求邻点/分支选择

   理由：sweet spot处线性逆不稳定

2. 条件：峰出预测窗口

   动作：回宽扫并标记模型失配

   理由：不靠插值猜峰

3. 条件：盲检点残差合格

   动作：提出映射版本

   理由：禁止超已验证幅度区间外推

#### 重测、回退与依赖失效

- 不同pulse shape/width/line改变映射失效
- 映射更新使iSWAP/CZ/ZZ工作点及时序相位需重测
- 回退旧映射与控制设置

#### 验收

- 盲检/回程一致
- 单位/branch/有效区间完整
- 误差达criteria

#### 参数读取

- qubit.idle_bias
- qubit.f01_coarse_hz
- z.amplitude_limits
- z.transfer_calibration

#### 候选参数写入范围（仅 proposal）

- qubit.f01_z_map
- qubit.f01_z_inverse
- qubit.max_f01_hz
- qubit.flux_branch

#### 方法预算

max_attempts=3；max_shots=4000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.5.1，L440–484
- `resources/source_materials/two-qubit-page-1.txt`：⑧ F01ZPulseDependency

## 框级验收与发布边界

- 原框主方法所需测量证据必须独立满足合同；辅助方法通过不能替代主方法。
- 模型只提出建议；最终通过/参数发布由独立判定与总控硬门禁决定。

每个被选内部方法仍须满足其完整验收项、剩余预算及独立复测；跳过的扩展方法必须标记未执行和原因。最终结论必须分别说明原框主方法、已运行辅助方法、未实现能力与证据新鲜度。

## 来源索引

- `resources/new_attachments/双比特测试.pdf`：第2页，第1个功能方框，F01ZPulseDependency
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：2.5.1，L440–484
- `resources/source_materials/two-qubit-page-1.txt`：⑧ F01ZPulseDependency
