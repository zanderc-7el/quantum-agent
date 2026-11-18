# 按原15步使用、诊断和复测

## 先选原步骤，再选内部方法

`python3 -m quantum_agents list`列出15实验与1总控；`show s03_rabi_amplitude`查看原Rabi定幅功能框的完整操作卡。每个实验Agent内部承担扫描选择、采集、拟合、判断偏差、受限调参和fresh重测。44个procedure是工具层，5项额外支持职责是公共模块；它们不要求额外模型角色。

```bash
python3 -m quantum_agents show s04_readout_fidelity
python3 -m quantum_agents show s05_readout_optimization
python3 -m quantum_agents plan --domain single_qubit
python3 -m quantum_agents plan --domain two_qubit
```

s04负责IQ分类与独立赋值检查，s05负责频率/功率优化；s05修改读出后必须回s04。双比特d07优化后回d06新随机线路XEB，必要时回d03–d05。原箭头复测是新任务版本，不增加智能体，也不把依赖图做成循环。

## 运行现有数值方法链

使用已安装`requirements-twin.txt`依赖的解释器，在新工作目录运行：

```bash
python3 -m quantum_agents twin --output /tmp/qcal-15-first --qubits 0,1
python3 -m quantum_agents status --db /tmp/qcal-15-first/runtime.sqlite
```

流程是公共单腔扫描，以及每个比特的粗频谱、时长Rabi种子、双正交Ramsey、新频率下时长Rabi复测、T1，共11次内部操作。每项独立采集开发与验证数据；Rabi验证实际施加0、π/2、π、2π候选时长。`agent_execution_report.json`把它们归到s01/s02/s03/s06/s07；所有原步骤完整完成状态仍为false。不要将11次执行称为11个Agent或原15步完整实现。

检查`twin_summary.json`的状态与valid_evidence，再读`backend/scans/`的实际采样及`backend/receipts/`回执，最后核对`agent_execution_report.json`的主目标缺口。独立oracle可对已保存数据重算观测、曲线、候选、参数修订与预算：

```bash
QCAL_TWIN_OUTPUT=/tmp/qcal-15-first python3 -m unittest discover -s tests -p test_independent.py -v
```

这是数值模拟证据，固定IQ代理不等于真实读出训练。T1/T2写入为表征记录，不能由写记录改变器件耗散。

## 主方法未绑定必须看得见

`run-agent s03_rabi_amplitude`默认幅度Rabi主方法，`run-agent s07_t2`默认T2CPMG，`run-agent d06_cz_xeb`默认XEB。三者当前主方法未绑定时必须拒绝；不能悄悄回退到时长Rabi、Ramsey或CZ源套件。

当目标就是运行辅助方法时显式指定，并提供已有有效前置的内部step：

```bash
python3 -m quantum_agents run-agent s03_rabi_amplitude   --procedure sq_rabi_time --step-id sq_rabi_time:q0:final   --workspace /tmp/qcal-15-first --operation-id q0-duration-fresh-002
python3 -m quantum_agents run-agent s06_t1   --procedure sq_t1 --step-id sq_t1:q0   --workspace /tmp/qcal-15-first --operation-id q0-t1-after-duration-002
```

第一条只复核候选脉宽；它产生新版本后T1旧证据会失效，所以第二条用新ID重新采集。运行器不自动跨owner补前置；前置过期就交总控安排。`run-step`仍是低层兼容入口，输出必须标为physical_procedure，不能将旧方法ID重新注册为独立Agent。

同目录同operation_id表示取回同一次结果，不新增shots；主动复测用新ID。backend seed、配置、源码或数值环境改变后新建目录，不删除冻结清单来绕过身份检查。

## 一个步骤怎样使用模型

先由可信宿主提供当前参数、版本、证据ID、现象、允许范围和剩余预算：

```bash
python3 -m quantum_agents propose s03_rabi_amplitude --context examples/advisor_context.json --provider rules
```

规则模式用于既定分支和协议检查，不代表模型推理。模型模式必须显式配置provider、模型和凭据；客户端输出仍只是结构化建议。模型可以建议本步骤内部方法及局部参数，不能自动发布、开放新SDK、任意改扫描或取得设备权限。精确字段以[模型协议](model_interface.md)为准。

跨owner借用仍遵循唯一归属：s02需要Ramsey诊断时，原方法属于s07，由总控形成方法调用/证据移交；当前CLI使用声明该方法的owner，而不是把`sq_ramsey`加入另一个活跃角色。方法通过后s02按频率目标作判断，s03承担旋转角复测，原s07的CPMG任务仍未完成。

## 偏差出现后怎么回测

例：s02得到带符号频差，先核符号、别名先验、模型残差和区间，再提出绝对频率。新频率通过fresh Ramsey诊断后，s03复核π/π2及相位，必要时改幅度/DRAG；s04复核依赖这些制备脉冲的分类证据；s08以新随机序列做规定RB。若幅度变化很小且有对照证据，总控可缩小重测集合，但不能直接跳过冻结必需项。详见[完整单比特案例](../knowledge/worked_cases.md)。

双比特中d03先找第一非零回归，d04调幅，d05验证条件相位并校正局域Z；任何改动都可能使另一项失效。d06保留泄漏、布居与层析诊断，并用真正XEB执行器完成原性能目标。d07只产生受约束候选；优化数据不能作为d06的新验收数据。未绑定XEB时应报告该主目标未实现，不以离线Favg填空。

## CZ桥与故障场景

```bash
python3 -m quantum_agents cz --output /tmp/qcal-15-cz-first
python3 -m quantum_agents demo --output /tmp/qcal-15-scenario-first
python3 scripts/verify_sources.py
```

CZ入口重新运行原套件，保存相位、泄漏、四基态表和九基态层析；17项具名检查及计数必须齐全。它是source_suite_only诊断桥，不是d06 XEB主方法，没有逐子步骤事务恢复。`demo`是明确标注scenario的故障注入演示，不能当作物理实验通过。

无效数据、错模型、漂移、设备状态未知和预算耗尽要分别处理：改扫描、追加新shots、回上游或升级均须有依据。候选验证失败要恢复绝对快照；回退读回不明时保留隔离，不能宣称恢复。详细决策见[总控策略](supervisor_policy.md)。

真实设备接口仍未绑定，当前运行时拒绝real。需要按照[硬件适配合同](hardware_adapter.md)验证通道、单位、时钟、读回、停止和恢复；没有只填IP就可启用的开关。
