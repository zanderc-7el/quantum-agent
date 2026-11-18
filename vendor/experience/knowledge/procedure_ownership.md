# 内部方法的唯一归属与跨步骤调用

原图定义15个实验Agent；下表保留44个procedure，每个只出现一次primary owner。它们是原步骤内部的方法，不是44个活跃Agent。完整科学内容已经并入各新步骤卡；旧方法卡仅作为技术追溯材料归档。参数读写权限由任务合同规定，不能由primary owner标签自动获得。

跨步骤借用采用`caller_agent_id → primary_owner_id/procedure_id → evidence_id`：记录谁请求、谁维护方法、实际执行何种扫描、结果给哪个原目标使用。一次采集只计一次成本；不同目标可引用同一证据，但独立验证不能与选点复用同一采集。当前CLI通过声明该procedure的owner运行；自动跨owner编排须由宿主显式实现。

| procedure_id | 方法名称 | primary owner / 原步骤 | 技术追溯 |
| --- | --- | --- | --- |
| `ro_resonator_survey` | 读出腔寻峰与偏置后定频 | [s01_readout_frequency：读取腔定频](../agents/s01_readout_frequency/card.md) | [归档方法细节](../resources/previous_edition/agents/ro_resonator_survey/card.md) |
| `ro_power_shift` | 高低功率色散位移检查 | [s01_readout_frequency：读取腔定频](../agents/s01_readout_frequency/card.md) | [归档方法细节](../resources/previous_edition/agents/ro_power_shift/card.md) |
| `ro_bias_map` | 量子比特偏置与腔频映射 | [s01_readout_frequency：读取腔定频](../agents/s01_readout_frequency/card.md) | [归档方法细节](../resources/previous_edition/agents/ro_bias_map/card.md) |
| `sq_spectroscopy` | 单比特f01粗频谱 | [s02_qubit_frequency：比特定频](../agents/s02_qubit_frequency/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_spectroscopy/card.md) |
| `sq_anharmonicity` | f02双光子谱与非谐性 | [s02_qubit_frequency：比特定频](../agents/s02_qubit_frequency/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_anharmonicity/card.md) |
| `sq_frequency_fine` | 相位斜率精校频率 | [s02_qubit_frequency：比特定频](../agents/s02_qubit_frequency/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_frequency_fine/card.md) |
| `sq_rabi_amplitude` | Rabi幅度粗校 | [s03_rabi_amplitude：Rabi定幅](../agents/s03_rabi_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_rabi_amplitude/card.md) |
| `sq_rabi_time` | Rabi时长扫描 | [s03_rabi_amplitude：Rabi定幅](../agents/s03_rabi_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_rabi_time/card.md) |
| `sq_rabi12` | f12驱动幅度与\|2>制备 | [s03_rabi_amplitude：Rabi定幅](../agents/s03_rabi_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_rabi12/card.md) |
| `sq_amplitude_fine` | 重复门幅度精校 | [s03_rabi_amplitude：Rabi定幅](../agents/s03_rabi_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_amplitude_fine/card.md) |
| `sq_drag` | DRAG系数精校 | [s03_rabi_amplitude：Rabi定幅](../agents/s03_rabi_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_drag/card.md) |
| `sq_xy_z_timing` | XY–Z通道相对时延 | [s03_rabi_amplitude：Rabi定幅](../agents/s03_rabi_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_xy_z_timing/card.md) |
| `ro_iq01` | 二态IQ分类训练与独立复测 | [s04_readout_fidelity：读取保真度测试](../agents/s04_readout_fidelity/card.md) | [归档方法细节](../resources/previous_edition/agents/ro_iq01/card.md) |
| `ro_iq012` | 三态IQ分类与泄漏可分辨性 | [s04_readout_fidelity：读取保真度测试](../agents/s04_readout_fidelity/card.md) | [归档方法细节](../resources/previous_edition/agents/ro_iq012/card.md) |
| `ro_optimize` | 读出频率与幅度二维优化 | [s05_readout_optimization：读取参数优化](../agents/s05_readout_optimization/card.md) | [归档方法细节](../resources/previous_edition/agents/ro_optimize/card.md) |
| `sq_t1` | T1能量弛豫 | [s06_t1：T1测试](../agents/s06_t1/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_t1/card.md) |
| `sq_t1_frequency` | T1随工作频率扫描 | [s06_t1：T1测试](../agents/s06_t1/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_t1_frequency/card.md) |
| `sq_cpmg` | CPMG/echo相干时间 | [s07_t2：T2测试](../agents/s07_t2/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_cpmg/card.md) |
| `sq_ramsey` | 双正交Ramsey与T2*估计 | [s07_t2：T2测试](../agents/s07_t2/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_ramsey/card.md) |
| `sq_rb` | 单比特随机基准测试 | [s08_single_qubit_rb：单比特RB测试](../agents/s08_single_qubit_rb/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_rb/card.md) |
| `sq_xeb` | 单比特XEB | [s08_single_qubit_rb：单比特RB测试](../agents/s08_single_qubit_rb/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_xeb/card.md) |
| `sq_flux_map` | Z脉冲幅度—f01映射 | [d01_flux_spectroscopy：二维比特能谱](../agents/d01_flux_spectroscopy/card.md) | [归档方法细节](../resources/previous_edition/agents/sq_flux_map/card.md) |
| `tq_exchange_off` | QQswap交换关断点 | [d02_idle_interaction：两比特闲置工作点](../agents/d02_idle_interaction/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_exchange_off/card.md) |
| `tq_residual_zz` | 残余ZZ独立测量 | [d02_idle_interaction：两比特闲置工作点](../agents/d02_idle_interaction/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_residual_zz/card.md) |
| `tq_dressed_zz` | 工作点有效dressed ZZ | [d02_idle_interaction：两比特闲置工作点](../agents/d02_idle_interaction/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_dressed_zz/card.md) |
| `tq_coupler_resonator_map` | 耦合器偏置与谐振腔响应 | [d02_idle_interaction：两比特闲置工作点](../agents/d02_idle_interaction/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_coupler_resonator_map/card.md) |
| `tq_iswap` | QQ共振交换与iSWAP时长 | [d02_idle_interaction：两比特闲置工作点](../agents/d02_idle_interaction/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_iswap/card.md) |
| `tq_cz_setup` | CZ波形与工作点合同 | [d03_cz_width：CZ宽度粗扫](../agents/d03_cz_width/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_setup/card.md) |
| `tq_cz_chevron` | 11↔02避交叉二维粗扫 | [d03_cz_width：CZ宽度粗扫](../agents/d03_cz_width/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_chevron/card.md) |
| `tq_cz_return` | 第一非零回归时长精修 | [d03_cz_width：CZ宽度粗扫](../agents/d03_cz_width/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_return/card.md) |
| `tq_cz_rough` | CZ回归与条件相位联合粗定点 | [d03_cz_width：CZ宽度粗扫](../agents/d03_cz_width/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_rough/card.md) |
| `tq_qz_cz_timing` | 量子比特Z–耦合器Z时延 | [d03_cz_width：CZ宽度粗扫](../agents/d03_cz_width/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_qz_cz_timing/card.md) |
| `tq_cz_amplitude_fine` | 重复CZ耦合器幅度精校 | [d04_cz_amplitude：CZ幅度精调](../agents/d04_cz_amplitude/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_amplitude_fine/card.md) |
| `tq_cz_detune_fine` | 重复CZ失谐/条件相位精校 | [d05_cz_detuning：CZ失谐与条件相位精调](../agents/d05_cz_detuning/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_detune_fine/card.md) |
| `tq_gateoff_reference` | 四条件gate-off相位与对比度参考 | [d05_cz_detuning：CZ失谐与条件相位精调](../agents/d05_cz_detuning/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_gateoff_reference/card.md) |
| `tq_cz_phase_verify` | 冻结CZ的双target独立相位复核 | [d05_cz_detuning：CZ失谐与条件相位精调](../agents/d05_cz_detuning/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_cz_phase_verify/card.md) |
| `tq_local_phase` | CZ局域动力学相位与virtual Z | [d05_cz_detuning：CZ失谐与条件相位精调](../agents/d05_cz_detuning/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_local_phase/card.md) |
| `tq_xeb` | 双比特CZ XEB独立基准 | [d06_cz_xeb：CZ XEB测试](../agents/d06_cz_xeb/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_xeb/card.md) |
| `tq_leakage` | CZ泄漏与重复门泄漏诊断 | [d06_cz_xeb：CZ XEB测试](../agents/d06_cz_xeb/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_leakage/card.md) |
| `tq_truth_table` | CZ四输入九输出布居表 | [d06_cz_xeb：CZ XEB测试](../agents/d06_cz_xeb/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_truth_table/card.md) |
| `tq_state_tomography` | 九基双比特态层析 | [d06_cz_xeb：CZ XEB测试](../agents/d06_cz_xeb/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_state_tomography/card.md) |
| `tq_process_tomography` | 16输入×9基过程层析 | [d06_cz_xeb：CZ XEB测试](../agents/d06_cz_xeb/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_process_tomography/card.md) |
| `p0_bell` | P0半交换Bell关联验证 | [d06_cz_xeb：CZ XEB测试](../agents/d06_cz_xeb/card.md) | [归档方法细节](../resources/previous_edition/agents/p0_bell/card.md) |
| `tq_optimizer` | 受约束CZ参数优化与独立复测 | [d07_cz_optimizer：CZ参数优化](../agents/d07_cz_optimizer/card.md) | [归档方法细节](../resources/previous_edition/agents/tq_optimizer/card.md) |

## 典型借用与完成边界

| 发起步骤 | 借用方法及primary owner | 返回的证据 | 原目标如何继续 |
| --- | --- | --- | --- |
| s02比特定频 | s07 / sq_ramsey | 符号已校验的残余频差、包络、别名/模型诊断 | s02判断频率候选；s03复核旋转角；s07的CPMG原目标不变 |
| s03 Rabi定幅 | s04 / ro_iq01或ro_iq012 | 有效分类与准备误差边界 | 分类失效先回s04，不能用幅度补读出偏差；初始可用明确raw IQ bootstrap |
| s04三态分类 | s03 / sq_rabi12 | \|2⟩制备与12旋转候选 | 交织制备/分类独立验证，避免标签与分类循环自证 |
| d03 CZ宽度 | d05 / tq_gateoff_reference、tq_cz_phase_verify | gate-off参考及两方向相位诊断 | 高回归必须同时具有非局域π相位；不因方法跨owner而新增角色 |
| d04或d05精调 | d06 / tq_leakage、tq_truth_table | 完整九标签计数与输入依赖 | 调幅/调频后重新检查泄漏和计算子空间错误；仍需原XEB任务 |
| d07优化 | d06 / tq_xeb及补充诊断 | 未参与选点的新随机线路与计数 | 训练分数不能自验；缺真正XEB执行器则原性能目标保持未完成 |

s02的非谐性/精频、s03的DRAG/时序等是条件触发的内部扩展，不要求第一次粗谱/Rabi启动前完成所有扩展，避免bootstrap死锁。跨步骤迭代通过新task/revision表达；初始依赖图保持无环。
