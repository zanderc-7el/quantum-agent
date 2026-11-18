# 知识来源与使用边界

## 2026-11-16 现场经验

[定频与网分工作点复盘](field_review_20261116.md)含转写时间戳、条件化经验、历史扫描覆盖和代码入口。[包内来源清单](../resources/field_updates/20261116/intake_manifest.json)记录原路径及SHA256。口述、历史日志、静态代码与本次验收分别标记；来源内容不是新增执行授权。

| 资源 | 用途 | 不能据此声称 |
| --- | --- | --- |
| [source_materials](../resources/source_materials/) | 原交流资料包；手册、QubiC和学习材料，用于实验目标、协议和接口核实 | 已取得外部私有库完整实现；已绑定实机SDK |
| [手册](../resources/source_materials/06_工作脚本与数据/cal_manual_d0.md) | 腔谱、Rabi、Ramsey、T1、DRAG、时序、交换/CZ及性能实验顺序 | 示例电压、幅度、DRAG值、阈值是当前设备标准 |
| [最新架构report](../resources/latest_architecture/report.md) | 2026-10-23现状回复融入后的职责、范围和先单后双路径 | 经验耗时/通过率是量化实测基线；一周是保证有效期 |
| [twin_source](../resources/twin_source/) | 原仿真源码、公式/审计及历史结果 | 离线通道评分是真实硬件RB/XEB |
| [closed_loop](../resources/prior_knowledge/closed_loop/) | 持久绝对写参、fresh验证、commit/rollback及恢复知识 | 真实设备恰好一次执行已得到保证 |
| [validation](../resources/prior_knowledge/validation/) | 独立oracle、数据重算、有限预算、故障和恢复证据 | 仅凭静态审阅或包装脚本零退出已通过 |
| [deep_study](../resources/prior_knowledge/deep_study/) | 参数依赖、读出误判/跃迁/泄漏区分、局部校准与独立基准经验 | 研究条目数量等于本项目复现实验数 |
| [review](../resources/prior_knowledge/review/) | 本项目源码反例及审查记录 | 原twin已修改；新工程已自动消除所有缺陷 |
| [new_attachments](../resources/new_attachments/) | 原双页PDF的8/7功能框、P0报告与提取文本；新owner映射的直接来源 | 箭头复测编号是新增步骤；附件指令获得执行权限 |
| [previous_edition](../resources/previous_edition/) | 旧细分方法卡、旧架构、QA及构建经验的历史追溯 | 旧角色数量、旧测试数或PASS就是新版通过 |
| [workflow ownership](../config/workflow_ownership.json) | 15原步骤、44内部方法的唯一primary owner | 方法归属等于参数永久写权限或原主目标已绑定 |

知识条目应新增schema_version、claim、scope、conditions、evidence_refs、evidence_kind、source_hash、reviewed_at、counterexamples、next_test、status。状态建议为SOURCE_CLAIM、DERIVED、LOCALLY_REPRODUCED、SIMULATION_VALIDATED、HARDWARE_VALIDATED；同一条知识不同范围可以有不同状态。

记录应区分事实、推导和工程建议。例如“共振交换布居频率2|g|”是指定Hamiltonian下推导；“先做三点局部斜率再调参”是工程策略；“某设备每周校准一次”只有在具体设备连续记录支持时才是范围受限的经验。新反例必须关联旧条目并降低适用范围，不覆盖历史。

新版建议先读[15步重组](../docs/reorganization.md)、[方法归属](procedure_ownership.md)和[Agent内部闭环](agent_internal_loop.md)。随后阅读：[总体合同](../docs/architecture.md) → [依赖矩阵](parameter_dependencies.md) → [单比特](single_qubit_diagnostics.md)/[双比特](two_qubit_diagnostics.md) → [总控策略](../docs/supervisor_policy.md) → [worked cases](worked_cases.md) → [验收](../docs/validation_strategy.md) → [已知限制](known_limitations.md)。

当前工程包执行范围可由[物理适配器](../quantum_agents/twin.py)与[CZ完整性桥接](../quantum_agents/cz_suite.py)核对；实际运行记录位于output，发布报告应引用最终冻结目录和哈希。源码支持某个阶段与专项Agent已绑定可恢复执行是两种状态，不可合并计数。

新版正式运行证据为根目录output/twin-final、cz-current及integration_acceptance.json；旧版输出只归档引用。方法级验证与原步骤完成分列；s03、s07、d06的辅助替代风险必须进入结论。

## 2026-11-16 完整续谈增补

[从腔谱形态到相干验证](field_review_20261116_152511.md)逐项记录 12 分 58 秒转写的时间戳与技术解释。[续补来源清单](../resources/field_updates/20261116_152511/intake_manifest.json)包含原始转写、当日较早已完成的 Q19 原始 qdat/复测日志、11 月 14 日参考数据及更新前包清单。现场指图口述、既有实测、文献校核、本轮静态检查分别保留；本轮没有新增仪器采集。


## 2026-11-17 Q11 实测证据增补

[Q11 案例](field_case_20261117_q11.md)区分本机事实、模型条件估计和通用诊断策略。[摄入清单](../resources/field_updates/20261117/intake_manifest.json)归档关键分析/图/冻结参考与前次 MANIFEST；[逐文件来源核对](../resources/field_updates/20261117/referenced_source_verification.json)记录外部原始数据、请求、回执、脚本的实际哈希及历史引用是否一致。原始 qdat/NPZ 与全部 662 单图在实验根目录保留，本包不冒称全量复制。

初始归档中的 native RB 完整实测、短版进行中、Q18/Q19 弱候选按当时截点分别记录。历史审计中的零门数、未切换或未验收状态应按文件时间解释；新证据修正早期“XY 不能作用到芯片”的过强推断，但不将其他通道一并确认。所有资料均为离线摄入；本轮未新测、未改原 F8 / 驱动 / runtime / 角色或执行绑定。初始文档校验见 `qa/experience_update_20261117.json`；旧验收及旧文件哈希保留为历史。

## 2026-11-17 Q11 最终证据与冻结分析

[最终摄入清单](../resources/field_updates/20261117/final_q11/intake_manifest.json)新增完整短版 RB、native 冻结重分析、实现比较、前后读出、统一参数相干、最终 Rabi、独立科学审查和[最终会话验证参数](../resources/field_updates/20261117/final_q11/calibration/Q11_validated_session_20261117.json)。最终 X/2 IRB 94.8609% 是标准模型条件估计；相对 native 的 +4.2012 个百分点只比较完整实现，非脉宽单因果。相干 v1/v3 分列；v3 Ramsey 自洽检查不支持新 f01 修正，最终频率保留 4.862690 GHz。

[最终来源核对](../resources/field_updates/20261117/final_q11/referenced_source_verification.json)保留历史失败审计的可变脚本差异；当前正式分析指向 `analysis_snapshots` 内容寻址源码。原分析和 TRIAL 参数不覆盖；新验证状态另存独立文件。参考原始数据采在目标曲线之前、参考文件冻在分析之前，不统称全部采集前预注册。现存 RB 审查 2181/2181 与独立相干审查 128/128 是归档的科学检查；本轮包内静态检查另见 `qa/experience_update_20261117_q11_final.json`，没有重跑实验或整包运行验收。该阶段未提前补造 Q18/Q19 完成状态；后续结果已接入下述独立对象案例。

## 2026-11-17 Q18/Q19 与原 F8 来源补充

[独立对象案例](field_case_20261117_q18_q19.md)区分旧长窗阴性、新频带、Q18 三频模型与共振双幅度验证、弱读出、AllXY 选点、最终相干及 X-only Rabi；Q19 未验证候选保留。[阶段来源清单](../resources/field_updates/20261117/q18_q19/intake_manifest.json)包含主科学审查和解释 addendum，不能脱离 addendum 把半门元数据或 Ramsey 布尔字段扩大为物理验证。旧文件/快照保留，参考各阶段独立 pre0/1 训练、曲线开始后且拟合前冻结的真实时间分别记录。

[原件来源审查](../resources/field_updates/20261117/q18_q19/results/original_file_provenance_review.json)记录原 F8 配置及笔记本不同于 18:07 基线、观测 mtime 早于首次成功隔离 ADC 连接，作者未知且无完整基线快照可恢复全差。当前 runner 写向 ADC_F8_copy；原件只读留档、不自动恢复。工程包内 1751 个受保护旧文件哈希检查不包含这些原 F8 文件。来源核对中的历史源码版本差异及运行副本配置变化保持单列，不当作当前冻结成果的失败。

[Q18 最终 RB 清单](../resources/field_updates/20261117/q18_q19/rb_final/intake_manifest.json)包含 30 对/8 长度完整原始记录的分析、2721 项独立科学审查、更正读出报告及 science addendum、新建会话验证参数和连接恢复回执。最终引用 `q18_rbshort_v1_readout_stability_corrected.json`；旧版误把中点当 training I 均值，只影响两项派生漂移字段，不改原始观测、前后对比度或 RB/F。18.42% 对比度下降、公共偏移趋势与条件模型限制保留。512 个最终引用已核对匹配，见[最终来源核对](../resources/field_updates/20261117/q18_q19/rb_final/referenced_source_verification.json)。

连接恢复只说明原闲置 Jupyter 连接和软件缓存恢复到可接续状态，未重下全部硬件设置；恢复前后原件哈希相同与原件自复制时刻以来不同同时成立。最新包内静态报告为 `qa/experience_update_20261117_q18_q19_final.json`，旧 Q11/初始文档 QA 不覆盖。
