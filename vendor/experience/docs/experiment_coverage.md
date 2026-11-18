# 原流程15步覆盖与内部方法归属

唯一活跃结构：8个单比特框 + 7个双比特框 + 1个总控。旧44实验条目无损保存为procedure，每项只归属一个owner；旧5支持角色不再注册，原6项公共职责合并在总控。

## 原图功能方框

| 页/框 | 原函数 | 当前智能体 | 主方法 | 内部方法数 | 当前范围 |
|---|---|---|---|---:|---|
| 1/1 | `ReadoutFreqDip` | [s01_readout_frequency · 读取腔定频](../agents/s01_readout_frequency/card.md) | `ro_resonator_survey` | 3 | partial_simulated；主方法 limited_simulated |
| 1/2 | `F01Spectroscopy` | [s02_qubit_frequency · 比特定频](../agents/s02_qubit_frequency/card.md) | `sq_spectroscopy` | 3 | partial_simulated；主方法 limited_simulated |
| 1/3 | `XYGateAmpRabiImpl` | [s03_rabi_amplitude · Rabi定幅](../agents/s03_rabi_amplitude/card.md) | `sq_rabi_amplitude` | 6 | partial_simulated；主方法 contract_only |
| 1/4 | `IqToProbability` | [s04_readout_fidelity · 读取保真度测试](../agents/s04_readout_fidelity/card.md) | `ro_iq01` | 2 | contract_only；主方法 contract_only |
| 1/5 | `ReadoutParaSweepByIQProb` | [s05_readout_optimization · 读取参数优化](../agents/s05_readout_optimization/card.md) | `ro_optimize` | 1 | contract_only；主方法 contract_only |
| 1/6 | `T1Idle` | [s06_t1 · T1测试](../agents/s06_t1/card.md) | `sq_t1` | 2 | partial_simulated；主方法 limited_simulated |
| 1/7 | `T2CPMG` | [s07_t2 · T2测试](../agents/s07_t2/card.md) | `sq_cpmg` | 2 | partial_simulated；主方法 contract_only |
| 1/8 | `RB1QCal` | [s08_single_qubit_rb · 单比特RB测试](../agents/s08_single_qubit_rb/card.md) | `sq_rb` | 2 | contract_only；主方法 contract_only |
| 2/1 | `F01ZPulseDependency` | [d01_flux_spectroscopy · 二维比特能谱](../agents/d01_flux_spectroscopy/card.md) | `sq_flux_map` | 1 | contract_only；主方法 contract_only |
| 2/2 | `ZeroPointFinderByQQSwap` | [d02_idle_interaction · 两比特闲置工作点](../agents/d02_idle_interaction/card.md) | `tq_exchange_off` | 5 | source_suite_only；主方法 contract_only |
| 2/3 | `CZSwapSweepZPulse` | [d03_cz_width · CZ宽度粗扫](../agents/d03_cz_width/card.md) | `tq_cz_chevron` | 5 | source_suite_only；主方法 source_suite_only |
| 2/4 | `TTGAczDelicateG` | [d04_cz_amplitude · CZ幅度精调](../agents/d04_cz_amplitude/card.md) | `tq_cz_amplitude_fine` | 1 | contract_only；主方法 contract_only |
| 2/5 | `TTGAczDelicateDetune` | [d05_cz_detuning · CZ失谐与条件相位精调](../agents/d05_cz_detuning/card.md) | `tq_cz_detune_fine` | 4 | source_suite_only；主方法 contract_only |
| 2/6 | `CZXEBCal` | [d06_cz_xeb · CZ XEB测试](../agents/d06_cz_xeb/card.md) | `tq_xeb` | 6 | source_suite_only；主方法 contract_only |
| 2/7 | `CZParaOptimizer` | [d07_cz_optimizer · CZ参数优化](../agents/d07_cz_optimizer/card.md) | `tq_optimizer` | 1 | contract_only；主方法 contract_only |



第4框为 IqToProbability，第5框为 ReadoutParaSweepByIQProb；箭头上的复测编号不是新的功能方框。

## 主链与回测

s01→s02→s03→s04→s05；随后s06/s07/s08并行；d01同时依赖s06/s07/s08；d01→d02→d03→d04→d05→d06→d07。

s05→s04与d07→d06仅列为retest_routes，以fresh任务执行，不进入静态DAG。内部扩展可因先决条件延后，但不能以延后扩展或辅助通过冒充主方法完成。

## 44项内部方法唯一归属

| 内部procedure | 方法名称 | 唯一owner | 原主框是否以它为主方法 |
|---|---|---|---|
| `ro_resonator_survey` | 读出腔寻峰与偏置后定频 | `s01_readout_frequency` | 主方法 |
| `ro_power_shift` | 高低功率色散位移检查 | `s01_readout_frequency` | 辅助/诊断/扩展方法，非替代 |
| `ro_bias_map` | 量子比特偏置与腔频映射 | `s01_readout_frequency` | 辅助/诊断/扩展方法，非替代 |
| `sq_spectroscopy` | 单比特f01粗频谱 | `s02_qubit_frequency` | 主方法 |
| `sq_anharmonicity` | f02双光子谱与非谐性 | `s02_qubit_frequency` | 辅助/诊断/扩展方法，非替代 |
| `sq_frequency_fine` | 相位斜率精校频率 | `s02_qubit_frequency` | 辅助/诊断/扩展方法，非替代 |
| `sq_rabi_amplitude` | Rabi幅度粗校 | `s03_rabi_amplitude` | 主方法 |
| `sq_rabi_time` | Rabi时长扫描 | `s03_rabi_amplitude` | 辅助/诊断/扩展方法，非替代 |
| `sq_rabi12` | f12驱动幅度与|2>制备 | `s03_rabi_amplitude` | 辅助/诊断/扩展方法，非替代 |
| `sq_amplitude_fine` | 重复门幅度精校 | `s03_rabi_amplitude` | 辅助/诊断/扩展方法，非替代 |
| `sq_drag` | DRAG系数精校 | `s03_rabi_amplitude` | 辅助/诊断/扩展方法，非替代 |
| `sq_xy_z_timing` | XY–Z通道相对时延 | `s03_rabi_amplitude` | 辅助/诊断/扩展方法，非替代 |
| `ro_iq01` | 二态IQ分类训练与独立复测 | `s04_readout_fidelity` | 主方法 |
| `ro_iq012` | 三态IQ分类与泄漏可分辨性 | `s04_readout_fidelity` | 辅助/诊断/扩展方法，非替代 |
| `ro_optimize` | 读出频率与幅度二维优化 | `s05_readout_optimization` | 主方法 |
| `sq_t1` | T1能量弛豫 | `s06_t1` | 主方法 |
| `sq_t1_frequency` | T1随工作频率扫描 | `s06_t1` | 辅助/诊断/扩展方法，非替代 |
| `sq_cpmg` | CPMG/echo相干时间 | `s07_t2` | 主方法 |
| `sq_ramsey` | 双正交Ramsey与T2*估计 | `s07_t2` | 辅助/诊断/扩展方法，非替代 |
| `sq_rb` | 单比特随机基准测试 | `s08_single_qubit_rb` | 主方法 |
| `sq_xeb` | 单比特XEB | `s08_single_qubit_rb` | 辅助/诊断/扩展方法，非替代 |
| `sq_flux_map` | Z脉冲幅度—f01映射 | `d01_flux_spectroscopy` | 主方法 |
| `tq_exchange_off` | QQswap交换关断点 | `d02_idle_interaction` | 主方法 |
| `tq_residual_zz` | 残余ZZ独立测量 | `d02_idle_interaction` | 辅助/诊断/扩展方法，非替代 |
| `tq_dressed_zz` | 工作点有效dressed ZZ | `d02_idle_interaction` | 辅助/诊断/扩展方法，非替代 |
| `tq_coupler_resonator_map` | 耦合器偏置与谐振腔响应 | `d02_idle_interaction` | 辅助/诊断/扩展方法，非替代 |
| `tq_iswap` | QQ共振交换与iSWAP时长 | `d02_idle_interaction` | 辅助/诊断/扩展方法，非替代 |
| `tq_cz_setup` | CZ波形与工作点合同 | `d03_cz_width` | 辅助/诊断/扩展方法，非替代 |
| `tq_cz_chevron` | 11↔02避交叉二维粗扫 | `d03_cz_width` | 主方法 |
| `tq_cz_return` | 第一非零回归时长精修 | `d03_cz_width` | 辅助/诊断/扩展方法，非替代 |
| `tq_cz_rough` | CZ回归与条件相位联合粗定点 | `d03_cz_width` | 辅助/诊断/扩展方法，非替代 |
| `tq_qz_cz_timing` | 量子比特Z–耦合器Z时延 | `d03_cz_width` | 辅助/诊断/扩展方法，非替代 |
| `tq_cz_amplitude_fine` | 重复CZ耦合器幅度精校 | `d04_cz_amplitude` | 主方法 |
| `tq_cz_detune_fine` | 重复CZ失谐/条件相位精校 | `d05_cz_detuning` | 主方法 |
| `tq_gateoff_reference` | 四条件gate-off相位与对比度参考 | `d05_cz_detuning` | 辅助/诊断/扩展方法，非替代 |
| `tq_cz_phase_verify` | 冻结CZ的双target独立相位复核 | `d05_cz_detuning` | 辅助/诊断/扩展方法，非替代 |
| `tq_local_phase` | CZ局域动力学相位与virtual Z | `d05_cz_detuning` | 辅助/诊断/扩展方法，非替代 |
| `tq_xeb` | 双比特CZ XEB独立基准 | `d06_cz_xeb` | 主方法 |
| `tq_leakage` | CZ泄漏与重复门泄漏诊断 | `d06_cz_xeb` | 辅助/诊断/扩展方法，非替代 |
| `tq_truth_table` | CZ四输入九输出布居表 | `d06_cz_xeb` | 辅助/诊断/扩展方法，非替代 |
| `tq_state_tomography` | 九基双比特态层析 | `d06_cz_xeb` | 辅助/诊断/扩展方法，非替代 |
| `tq_process_tomography` | 16输入×9基过程层析 | `d06_cz_xeb` | 辅助/诊断/扩展方法，非替代 |
| `p0_bell` | P0半交换Bell关联验证 | `d06_cz_xeb` | 辅助/诊断/扩展方法，非替代 |
| `tq_optimizer` | 受约束CZ参数优化与独立复测 | `d07_cz_optimizer` | 主方法 |



## 必须保留的能力区别

- **s01_readout_frequency**：原框主方法是 ReadoutFreqDip。已有原子绑定仅线性单腔 S21 寻峰；高低功率、多腔归属和真实偏置依赖属于本角色内部合同，未接实机。

- **s02_qubit_frequency**：原框主方法是 F01Spectroscopy。已有 q0/q1 二能级能谱原子绑定；f12/非谐性与相位精校是有先决条件的内部方法，未因能谱可运行而全部实现。

- **s03_rabi_amplitude**：原框主方法是 XYGateAmpRabiImpl，即固定时宽扫描幅度。已绑定 sq_rabi_time 仅扫描时长并验证候选 π 脉冲布居，是辅助方法，不能完成或代替 Rabi 定幅。1→2、重复门精校、DRAG、XY–Z 时序仍需各自执行器。

- **s04_readout_fidelity**：原图第4框是 IqToProbability。真实 IQ 分类训练、留出复测与 0/1/2 混淆矩阵均未绑定；仿真中的已设读出误差不是训练分类器的实测通过证据。

- **s05_readout_optimization**：原图第5框是 ReadoutParaSweepByIQProb，扫描读出频率/幅度并比较读出区分能力。尚未绑定；优化后必须回到第4框以 fresh 数据复测，不可复用训练集宣称提升。

- **s06_t1**：T1Idle 的 q0/q1 单工作点衰减已有原子仿真绑定；频率依赖 T1 地图为内部扩展，未接真实调频/偏置执行器。

- **s07_t2**：原框主方法是 T2CPMG。已有 sq_ramsey 原子绑定只测自由演化 Ramsey 的 T2* 与带符号频偏，是诊断辅助；不能替代回聚焦的 CPMG，也不能据此宣称原框 T2CPMG 通过。

- **s08_single_qubit_rb**：原框主方法是 RB1QCal。RB 与单比特 XEB 都保留为各自有预算/序列/指标的内部方法，当前未绑定；T1/T2 或单脉冲布居不能代替 RB 门误差。

- **d01_flux_spectroscopy**：原框 F01ZPulseDependency 扫偏置×频率并建立 f01(Z)；当前单偏置能谱原子绑定不构成二维 flux mapping。真实偏置安全窗、迟滞与工作支路仍需设备适配。

- **d02_idle_interaction**：原框 ZeroPointFinderByQQSwap 的耦合器扫描未原子绑定。源程序仅有固定 coupling_scale 的独立 ZZ 测量和 P0 共振交换子流程；交换关断不等于 ZZ 为零，也不等于真实闲置工作点已找到。

- **d03_cz_width**：原框 CZSwapSweepZPulse 的粗扫在源 CZ 套件中有三能级矩形 pulse 的 chevron/第一非零回归对应；只有完整套件桥，尚无逐方法原子调用。真实 flux 波形、线路时序和波形整形未接通。

- **d04_cz_amplitude**：原框 TTGAczDelicateG 要扫描重复门数 N×耦合器幅度以放大回归/泄漏误差，当前未绑定。单门 chevron 或回归精修不能替代重复门幅度精调。

- **d05_cz_detuning**：原框 TTGAczDelicateDetune 的 N×量子比特幅度精调未绑定。源 CZ 套件包含 gate-off、双 target 相位复核和分析相位 VZ 子阶段；这些诊断不能冒充重复门失谐优化，也不能将所有 N 的累计相位直接对 π。

- **d06_cz_xeb**：原框主方法是 CZXEBCal，当前未绑定。源 CZ 套件只有单门泄漏、真值表、|++>九基态层析等；P0 Bell 是另一路两能级验证。层析、Bell、仿真 Favg 都不能代替 CZ XEB，过程层析本身也未完整实现。

- **d07_cz_optimizer**：原框 CZParaOptimizer 根据冻结目标与真实 XEB 反馈提出有界候选，当前未绑定。优化器不得用层析/Bell 分数替代 XEB；候选须经第6框独立 fresh XEB 复测，选择集和最终验证集分开。



## 公共模块与提示词

总控卡完整合并任务编排、能力路由、测量证据、拟合诊断、参数版本与独立验收职责。它们可以是普通代码模块或受限计算工具，不是额外活跃agent；领域调参判断留在各实验owner。

每个活跃角色各有card.md和prompt.md，共16套、32个文件。所有prompt使用相同七字段proposal合同；建议与实机动作/参数发布分离。

## 数据保留与接口语义

config/procedure_catalog.json使用procedures列表；每个对象仅将旧kind改为procedure并追加owner/provenance，原字段原值保留。旧card_path/prompt_path字面值相对resources/previous_edition解释，archived_card_path/archived_prompt_path是可直接从包根访问的归档路径。全部操作细节同时嵌入新owner卡，不依赖外链才能阅读。

agent_catalog参数读写并集代表该owner的范围与失效边界，不等于一次主方法调用的全部先决条件；实际执行检查选定procedure和精确binding。schema2 catalog是计划/职责合同，不等于runtime StepSpec。

## 原始来源

- `resources/new_attachments/双比特测试.pdf`：第1页8框、第2页7框。

- `config/workflow_ownership.json`：核对原图后的唯一归属映射。

- `resources/previous_edition/config/agent_catalog.json`：44项详细方法与6项公共职责的完整保留来源。

- 每张新卡逐方法列出原手册、报告与文本行号，来源材料不构成执行授权。
