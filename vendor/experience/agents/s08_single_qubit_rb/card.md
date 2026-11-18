# 单比特RB测试 · 原流程实验智能体

角色ID：`s08_single_qubit_rb`。原图第1页第8个功能方框；原函数：`RB1QCal`。

本包只注册15个实验智能体和1个总控。下列内部方法不是独立智能体，不能绕过本角色对外调用；本角色负责自己的调参、诊断、精校和重测，不再拆分专门调参agent。

## 当前能力边界

原框主方法是 RB1QCal。RB 与单比特 XEB 都保留为各自有预算/序列/指标的内部方法，当前未绑定；T1/T2 或单脉冲布居不能代替 RB 门误差。

execution_status=`contract_only`；原框主方法状态=`contract_only`；默认主方法=`sq_rb`。精确运行范围以 `config/bindings.json` 与运行时回执为准。

实机SDK尚未接入。模型只能产生建议；不允许直接执行shell、写机器参数、提交参数版本或宣称通过。源程序模拟结果不等于本次任务证据，不能把辅助方法成功升级成原流程主方法成功。

## 自主闭环与内部方法选择

主任务 RB 使用冻结随机序列、多个长度和独立种子；XEB 为可选的独立验证方法，不与 RB 误差混算。若制备/读出或相干性失效，保留原始失败并请求对应上游复校。

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

- gates.clifford_compilation
- qubit.f01_hz
- readout.classifier_version
- benchmark.seed_set
- gates.pi2_axis_map

框级候选写入范围（不是直接写权限）：

- benchmark.rb1q_decay
- benchmark.rb1q_epc
- benchmark.rb1q_report
- benchmark.xeb1q_report

框级上限：max_attempts=3；max_shots=16000000；max_seconds=1800。角色总上限是所有内部方法上限之和；不是默认全部运行。每个方法仍受自己的上限、当前授权预算与运行时剩余额度约束。

## 内部方法完整操作规范

### 内部方法 `sq_rb` · 单比特随机基准测试

职责：冻结门集后评估Clifford平均衰减，保留序列级统计与SPAM拟合项。

范围：单比特随机基准测试

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- RB1QCal

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- sq_amplitude_fine
- sq_drag
- ro_iq01

#### 固定量

- 门实现版本
- gate_spacing_s
- 每序列shots
- 随机种子
- 拟合模型

#### 扫描量

- Clifford_length_m
- random_sequence_id

#### 序列

1. 生成m个随机Clifford及准确逆门
2. 理想验证全序列为恒等
3. 执行并测P0
4. 跨长度交织
5. 留出新随机序列复测

#### 原始观测

- 每序列P0
- counts
- 编译出的物理门数
- 时间与漂移参考

#### 拟合、公式、单位与符号

- P0(m)=A p^m+B
- 单比特在标准近似下EPC=(1-p)/2
- Clifford长度不等于物理脉冲数
- 未经门组成和误差假设验证不能直接换算π门保真度

#### 偏差诊断与调参分支

1. 条件：理想逆门不等于I

   动作：blocked并修正编译器

   理由：不能把编译错误当硬件误差

2. 条件：非指数或门依赖明显

   动作：报告模型不适合，不硬给单数保真度

   理由：RB近似有条件

3. 条件：门集更新

   动作：判旧RB过期并fresh复测

   理由：基准只对应冻结版本

#### 重测、回退与依赖失效

- 新随机序列/新计数验证
- 分析训练与验收分开
- 所有失败序列保留
- 性能差只出诊断proposal，不自动写参数

#### 验收

- 预声明长度/序列数/CI
- 理想逆门正确
- 残差与bootstrap按序列聚类

#### 参数读取

- gates.clifford_compilation
- qubit.f01_hz
- readout.classifier_version
- benchmark.seed_set

#### 候选参数写入范围（仅 proposal）

- benchmark.rb1q_decay
- benchmark.rb1q_epc
- benchmark.rb1q_report

#### 方法预算

max_attempts=3；max_shots=8000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：L616–634
- `resources/source_materials/two-qubit-page-1.txt`：⑦ RB1QCal

### 内部方法 `sq_xeb` · 单比特XEB

职责：用随机赤道π2线路比较观测与理想输出，报告序列/层级指标。

范围：单比特XEB

源实现边界：手册定义实验；未绑定执行后端

存档执行状态：`contract_only`；该状态仅说明源版该方法，当前能否执行以 owner + procedure 的精确绑定为准，不能作为独立活跃智能体调用。

#### 原函数与内部依赖

- XEB1QCal

历史依赖引用（当前由本 owner 与总控解析为内部方法或公共模块，不注册为新 agent）：

- sq_amplitude_fine
- sq_drag
- ro_iq01

#### 固定量

- 八个轴相位集合
- gate_spacing
- 门版本
- shots
- XEB归一化定义

#### 扫描量

- depth_m
- random_sequence_id

#### 序列

1. 从0出发
2. 每层随机八轴π2
3. 计算准确理想输出
4. 执行后直接Z测量无逆门
5. 不同深度交织

#### 原始观测

- counts及ideal probabilities
- 各层随机轴
- per-circuit XEB

#### 拟合、公式、单位与符号

- 可用有限维归一化linear XEB：Σc(Σx p_obs p_ideal-1/D)/Σc(Σx p_ideal²-1/D)，D=2
- 具体统计定义须冻结且分母非退化
- 对depth拟合衰减并报告模型条件

#### 偏差诊断与调参分支

1. 条件：理想输出接近均匀使分母退化

   动作：重新声明可辨识设计，保留原结果

   理由：小D不能盲用大维Porter–Thomas近似

2. 条件：相位优化与评分同数据

   动作：禁止宣称独立验证，另采heldout

   理由：防基准过拟合

3. 条件：XEB下降

   动作：与RB/频率/幅度诊断交叉定位

   理由：不能仅凭XEB判一种误差

#### 重测、回退与依赖失效

- 用fresh随机线路复测
- 门版本变更旧XEB失效
- 不以优化器训练分数作最终值

#### 验收

- 理想概率归一
- 序列级CI
- 归一化和单门/每层口径明确

#### 参数读取

- gates.pi2_axis_map
- benchmark.seed_set
- readout.classifier_version

#### 候选参数写入范围（仅 proposal）

- benchmark.xeb1q_report

#### 方法预算

max_attempts=3；max_shots=8000000；max_seconds=900。预算耗尽立即保留证据并升级，不能无界重复。

#### 来源

- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：L636–656

## 框级验收与发布边界

- 原框主方法所需测量证据必须独立满足合同；辅助方法通过不能替代主方法。
- 模型只提出建议；最终通过/参数发布由独立判定与总控硬门禁决定。

每个被选内部方法仍须满足其完整验收项、剩余预算及独立复测；跳过的扩展方法必须标记未执行和原因。最终结论必须分别说明原框主方法、已运行辅助方法、未实现能力与证据新鲜度。

## 来源索引

- `resources/new_attachments/双比特测试.pdf`：第1页，第8个功能方框，RB1QCal
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：L616–634
- `resources/source_materials/two-qubit-page-1.txt`：⑦ RB1QCal
- `resources/source_materials/06_工作脚本与数据/cal_manual_d0.md`：L636–656
