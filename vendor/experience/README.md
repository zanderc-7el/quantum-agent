# 量子测控智能体：原流程15步版

## 2026-11-16 完整续谈更新

新增[从腔谱形态到相干验证](knowledge/field_review_20261116_152511.md)：Q/失配拟合、未归属封装模式、弱谱候选的 Rabi 验证及频率精校。已将规则写入 s01、s02、s03、s05、s07 与总控的模型提示词，并归档此前完成的 Q19 原始数据与复测日志。最新静态校验见 `knowledge/qa/experience_update_20261116_152511.json`；以下同日较早更新及 10 月历史验收分别保留。

## 2026-11-16 较早经验更新

已加入[八号机定频与网分工作点复盘](knowledge/field_review_20261116.md)，并接入 s01、s02、s03、s05 与总控的卡片和模型提示词。内容包含对象映射、两个功率/频率轴、弱响应验证、历史已做扫描及未完成的参数组合。来源转写、扫描记录摘要与代码快照保存在 `resources/field_updates/20261116/`。

本次更新知识和文档入口；历史仿真/实机状态依原记录解释，未把现场建议作为新测量或硬件接入完成证明。原10月24日验收作为历史保留，本次增补校验单列于 `knowledge/qa/experience_update_20261116.json`。

本版保留原PDF的**8个单比特步骤、7个双比特步骤和1个总控**。一个实验智能体负责原图一个功能框，在内部完成采集、拟合、偏差判断、提出调参和独立重测。原有44种细分方法作为这些步骤的内部工具保留，5项额外支持职责归入公共模块，不再注册为额外智能体。

| 原图步骤 / 新ID | 原功能框 | 原函数名 | 当前能力边界 |
| --- | --- | --- | --- |
| 单1 / `s01_readout_frequency` | [读取腔定频](agents/s01_readout_frequency/card.md) | `ReadoutFreqDip` | 单腔S21内部方法已绑定；高低功率/偏置流程未完整绑定 |
| 单2 / `s02_qubit_frequency` | [比特定频](agents/s02_qubit_frequency/card.md) | `F01Spectroscopy` | 粗频谱内部方法已绑定；精频率可借用Ramsey诊断 |
| 单3 / `s03_rabi_amplitude` | [Rabi定幅](agents/s03_rabi_amplitude/card.md) | `XYGateAmpRabiImpl` | 仅时长Rabi辅助已绑定；原幅度Rabi未绑定 |
| 单4 / `s04_readout_fidelity` | [读取保真度测试](agents/s04_readout_fidelity/card.md) | `IqToProbability` | 原始IQ训练与独立混淆矩阵执行器待绑定 |
| 单5 / `s05_readout_optimization` | [读取参数优化](agents/s05_readout_optimization/card.md) | `ReadoutParaSweepByIQProb` | 读出频率×幅度优化执行器待绑定 |
| 单6 / `s06_t1` | [T1测试](agents/s06_t1/card.md) | `T1Idle` | 固定工作点T1内部方法已绑定；频率依赖扩展待绑定 |
| 单7 / `s07_t2` | [T2测试](agents/s07_t2/card.md) | `T2CPMG` | 仅Ramsey辅助已绑定；原T2CPMG未绑定 |
| 单8 / `s08_single_qubit_rb` | [单比特RB测试](agents/s08_single_qubit_rb/card.md) | `RB1QCal` | RB主流程与单比特XEB扩展待绑定 |
| 双1 / `d01_flux_spectroscopy` | [二维比特能谱](agents/d01_flux_spectroscopy/card.md) | `F01ZPulseDependency` | 真实Z幅度—频率映射待绑定 |
| 双2 / `d02_idle_interaction` | [两比特闲置工作点](agents/d02_idle_interaction/card.md) | `ZeroPointFinderByQQSwap` | 交换/ZZ有源套件诊断；真实闲置点搜索待绑定 |
| 双3 / `d03_cz_width` | [CZ宽度粗扫](agents/d03_cz_width/card.md) | `CZSwapSweepZPulse` | 矩形脉冲源套件可诊断；原设备宽度流程待绑定 |
| 双4 / `d04_cz_amplitude` | [CZ幅度精调](agents/d04_cz_amplitude/card.md) | `TTGAczDelicateG` | 重复CZ幅度精调待绑定 |
| 双5 / `d05_cz_detuning` | [CZ失谐与条件相位精调](agents/d05_cz_detuning/card.md) | `TTGAczDelicateDetune` | 源套件有相位/局域Z诊断；原精调流程待绑定 |
| 双6 / `d06_cz_xeb` | [CZ XEB测试](agents/d06_cz_xeb/card.md) | `CZXEBCal` | CZ源套件非XEB；原XEB主流程待绑定 |
| 双7 / `d07_cz_optimizer` | [CZ参数优化](agents/d07_cz_optimizer/card.md) | `CZParaOptimizer` | 受约束硬件参数优化待绑定 |

唯一总控是`supervisor`，负责跨步骤依赖、全局重测、资源与预算、参数版本、提交和回退。表格依据两页PDF的功能方框计数；箭头旁的复测编号不另增步骤。单4是读取保真度测试，单5才是读取参数优化。见[重组说明](docs/reorganization.md)及[唯一方法归属表](knowledge/procedure_ownership.md)。

## 怎样使用

在包根目录运行以下只读命令，先看原步骤及内部方法：

```bash
python3 -m quantum_agents list
python3 -m quantum_agents show s03_rabi_amplitude
python3 -m quantum_agents show s07_t2
python3 -m quantum_agents plan --domain single_qubit
python3 -m quantum_agents plan --changed q0.frequency_hz
```

每个新步骤的`agents/<id>/card.md`是操作合同，`prompt.md`可供模型按该步骤工作。模型为当前步骤选择内部方法并解释偏差；执行和发布由确定性接口检查。`propose`仅输出建议：

```bash
python3 -m quantum_agents propose s03_rabi_amplitude --context examples/advisor_context.json --provider rules
```

数值演示需要`requirements-twin.txt`指定依赖。创建新工作目录运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-twin.txt
.venv/bin/python -m quantum_agents twin --output /tmp/qcal-15-first --qubits 0,1
.venv/bin/python -m quantum_agents status --db /tmp/qcal-15-first/runtime.sqlite
.venv/bin/python -m quantum_agents cz --output /tmp/qcal-15-cz-first
```

`twin`运行五类已绑定内部方法组成的11次操作链，每项另采验证，并保存`agent_execution_report.json`。11次操作不是11个新智能体，也不是原15步全部完成；当前分组报告对原流程完整完成均保持`original_step_complete=false`。`cz`运行源数值套件及完整性门禁，属于诊断桥，不是原XEB步骤。

在同一数值工作区中，显式调用某个步骤已有的辅助方法：

```bash
.venv/bin/python -m quantum_agents run-agent s03_rabi_amplitude   --procedure sq_rabi_time --step-id sq_rabi_time:q0:final   --workspace /tmp/qcal-15-first --operation-id q0-rabi-time-fresh-002
```

此命令只重测时长Rabi辅助，不能写成原幅度Rabi已完成。默认`run-agent s03_rabi_amplitude`选择原幅度主方法；未绑定时应拒绝。同理，s07的Ramsey辅助不能替代CPMG，d06的CZ源套件不能替代XEB。执行器不会自动跨步骤补齐前置实验；总控按依赖安排。详细案例见[使用与重测](docs/usage_walkthrough.md)。

## 能力、证据与资料

- 当前有持久运行状态、参数修订、fresh验证、冻结门槛、有界预算、回退和未知回执核查；物理模拟绑定与公共模块说明见[架构](docs/architecture.md)、[精确接口](docs/runtime_contract.md)、[模型协议](docs/model_interface.md)。
- 原始量子算法、公式、偏差分支与参数依赖保留在[单比特诊断](knowledge/single_qubit_diagnostics.md)、[双比特诊断](knowledge/two_qubit_diagnostics.md)、[依赖矩阵](knowledge/parameter_dependencies.md)及[完整复测案例](knowledge/worked_cases.md)。方法可以跨步骤借用，主归属仍唯一。
- 原图“QQswap交换关断”等同“ZZ关断”的表述已在d02内部纠正；60 ns只作源图示例，不能当任何设备标准，也不因此增加智能体。
- 新版正式证据入口为`output/twin-final/`、`output/cz-current/`和`output/integration_acceptance.json`。测试数、采集数和结论由本轮实际记录确定；[结果说明](output/README.md)列出各证据的用途。
- 旧架构、旧操作卡、旧QA保存在`resources/previous_edition/`；旧运行输出归档到`output/previous_edition/`。这些记录不能作为新版通过证据。[资料边界](resources/SOURCE_NOTES.md)说明原包与新文档的区别。

当前仍拒绝真实设备模式，未提供32所真实SDK，也未宣称完成联网模型质量测试。原twin的T1/Ramsey初值、错模型success和原评分缺项问题仍单列为[已知限制](knowledge/known_limitations.md)；CZ桥的防护不等于原源码修复。设备任务必须另外完成接口、单位、通道、读回和恢复验证。


## 2026-11-17 Q11 实测经验增补

新增[Q11 采集边界、慢 Rabi、频率精修与条件 RB 案例](knowledge/field_case_20261117_q11.md)，以及[单比特诊断补充](knowledge/single_qubit_diagnostics.md)。包含硬 IQ 2047 段上限的同 capture 对照、≤2000 shots × 独立 CaptureRepeat、随机时间/交错幅度 Rabi、相干时间、实际驱动频偏符号验证、弱单 shot 下的 mean-IQ RB 和配对序列区间。当前接线已支持 Q11 的 Rabi/相干/真实 RB，不能继续全局断言主机 XY 无法作用到芯片。该初始截点的 Q18/Q19 仍为弱候选，后续结果见下文。

native X/2 IRB 模型条件估计为 90.6597% [87.7012%, 93.5358%]；平均 Clifford 88.3667% [86.0142%, 90.1191%]，两者不可混用。本次经验包编辑未修改原 F8、执行代码、角色注册或 15 步结构；此前文档与验收保留为历史。原 F8 在实验期间的既有哈希差异另见后续来源审查，不能据此句声称其全程未变。初始证据来源见 `resources/field_updates/20261117/intake_manifest.json`，初始静态检查见 `knowledge/qa/experience_update_20261117.json`，不构成工程包实机绑定或全流程验收。

### Q11 最终会话成果

短版完整 RB 已完成：物理半 π **160 ns / 0.98**、π **320 ns / 0.98**、XYDelay **100 ns**（名义读出前间隔 **200 ns**）。X/2 IRB 条件估计 **94.8609% [93.4061%, 96.2202%]**，平均 Clifford **92.1615% [91.0208%, 93.1730%]**；相对 native 的 X/2 条件差值 **+4.2012 个百分点 [+1.6019, +6.8493]**。幅度/宽度和时序同时改变且两轮顺序测量，不作脉宽单因果结论。短版前后读出对比度下降约 19.5%；单次制备标签仅约 55–57%，不可称高保真读出。区间只计模型条件下的统计不确定度。

最终同参数复测：T1 **2.959 µs**，正/负 Ramsey T2* **1.479 / 1.378 µs**，Echo **2.737 µs**；最终 Rabi 半周期 **318.797 ns [315.082, 322.532]** 支持保留 320 ns。两条 Ramsey 的频率和不支持同一固定失谐，取消本轮追加 f01 修正，维持 **4.862690 GHz**。详见[完整案例与区间/限制](knowledge/field_case_20261117_q11.md)及[本会话验证参数](resources/field_updates/20261117/final_q11/calibration/Q11_validated_session_20261117.json)。原 TRIAL 快照不覆盖；适配器的 0.49 半幅种子不等于短策略的实际 0.98 输出。

最终证据及内容寻址源码见 `resources/field_updates/20261117/final_q11/intake_manifest.json`；旧 native 报告和初始经验包快照保留。该阶段静态文档/门户/哈希校验见 `knowledge/qa/experience_update_20261117_q11_final.json`。当时 Q18/Q19 新补测另行记录，后续已定稿结果见下文。

### Q18/Q19 后续实测与原件来源

新增 [Q18/Q19 案例](knowledge/field_case_20261117_q18_q19.md)：五个旧长窗候选未复现后，Q18 在新频带独立确认 **4.678040 GHz**，共振双幅度频率比 **0.493116**。选定完整 π **484 ns/0.98**、短半 π **242 ns/0.98**、XYDelay **300 ns**；AllXY RMS 0.137 是诊断量。读出约 **7.358766667 GHz/0.8**、T0 **80 µs**，单次制备标签仍弱。最终相干 T1 **4.1147 µs**、Ramsey T2* **2.8532/3.1413 µs**、Echo **4.0932 µs**；最终完整 X 时长 Rabi 半周期 **483.7385 ns [478.3245, 488.4827]** 支持保留 484 ns。Q18 物理改频符号尚未验证，代数 79.85 kHz 不自动写回。Q19 新旧候选均未得到有效 Rabi 验证。

原 F8 两文件与 18:07 复制基线不同，观测修改时间 20:10/20:12 早于首次成功隔离 ADC 连接；作者未知、只读留档、不覆盖恢复。隔离副本采集的证据与工程包 1751 个保护文件检查是不同范围，不能称原 F8 全程哈希不变。完整来源和阶段审查见 `resources/field_updates/20261117/q18_q19/intake_manifest.json`。

Q18 完整 **30 对 seed、8 长度** RB 已完成并通过带漂移限制的独立审查：X/2 IRB 条件估计 **92.8724% [90.0616%, 95.8034%]**，平均 Clifford **91.0142% [88.5371%, 93.3833%]**。读出前后对比度下降 **18.42%**（约 2.16 capture 标准误），reference 公共偏移趋势 p≈0.005994；分块未检出差异不证明平稳。旧读出报告的“相对训练 I 均值漂移”字段已更正，RB 数值和前后对比度不变，旧报告保留。最终参数见 [Q18 会话验证](resources/field_updates/20261117/q18_q19/rb_final/calibration/Q18_validated_session_20261117.json)，科学审查与更正增补随证据归档。

本轮 Q11、Q18 各自具备 Rabi/相干/完整条件 IRB 证据；Q19 仍未验证，不等于原 15 步全部完成或高保真单次读出。原闲置设备连接已恢复、IDN 成功；未重新下发全部硬件设置。恢复动作没有改原件，不抵消早期原件哈希差异。最新经验包检查为 `knowledge/qa/experience_update_20261117_q18_q19_final.json`；此前所有验收按其历史截点保留。
