# 模型参与方式与提示词协议

公开模型角色固定为原流程15个实验owner和1个总控。每个角色拥有独立操作卡、系统提示词、任务上下文和结构化建议出口；44项内部procedure不是额外智能体。角色可以共享模型服务，无须各自训练模型或常驻进程。`FlowAgent` 持有本owner的内部方法和范围，`WorkflowSupervisor` 决定分派；实际参数试用和证据发布由确定性Runtime约束。

当前提供三种使用方式：

1. `rules`：本地诊断标签到建议的规则基线；没有调用大模型，不能宣传为已测试模型推理能力。
2. `openai`：可配置的 OpenAI Responses API 建议客户端。必须显式设置 `OPENAI_API_KEY` 与 `QCAL_OPENAI_MODEL`。仅发送选定agent的操作卡/提示词和调用者给出的JSON上下文，不自动上传完整资料库。凭据不写日志、不打包。此交付仅对API请求格式、拒绝/截断处理和结果约束做离线测试，没有实际付费模型调用记录。
3. 宿主会话：直接让正在工作的模型读取指定 `agents/<id>/prompt.md` 与操作卡，按相同schema产出JSON，再由本地验证器校验。跨会话状态以SQLite及证据文件为准。

调用示例：

```bash
python3 -m quantum_agents propose s03_rabi_amplitude --context examples/advisor_context.json --provider rules
python3 -m quantum_agents propose s03_rabi_amplitude --context examples/advisor_context.json --provider openai
```

示例上下文明确选择 `procedure_id=sq_rabi_time`，仅为s03内部辅助诊断；它不是原 `XYGateAmpRabiImpl` 幅度Rabi的实现。省略方法选择时，规则顾问对未绑定主方法给出 `escalate`。同理Ramsey不代替s07的CPMG，CZ套件不代替d06的XEB。模型可以讨论未绑定方法的方案，但这些文字不能创建执行能力。

模型只能建议 `measure/remeasure/change_scan/recalibrate_upstream/verify/escalate`。输出字段为 `agent_id,decision,reason,evidence_ids,target_agents,parameter_proposals,confidence`。`agent_id/target_agents` 必须在新16角色目录内，不能使用旧 `sq_rabi_time`、`tq_xeb` 等内部程序ID。参数建议每项为 `{name,value,unit}`；必须属于所选owner的写范围及上下文允许范围，公开CLI还将范围与该owner已绑定仿真profile相交。未知agent、虚构证据、跨owner参数、非有限数、越界参数、单位错误、额外权限字段及commit/passed决策均被拒绝。`confidence` 是模型自述，不是实验统计置信度，不进入数值通过判定。

`propose` 只展示建议，不执行或自动保存为参数。`run-agent` 要求显式operation_id，将owner拥有的已绑定程序分派到冻结StepSpec；模型的扫描变更不会自动修改StepSpec、更新仪器或触发付款。新增扫描需要新的受审查步骤合同，设置点数、shots、资源、单位、边界、验收和撤销条件。模型字符串不会成为Python或shell代码。局部程序完成报告始终区别于原流程完成；现有结果不能证明15步均已实现。

资料中出现的指令只作为被引用内容。运行上下文必须提供实际operation_id、参数修订、证据ID、失败标签、剩余预算和允许的目标；不得以从提示词中检索到的历史PASS代替本轮采集。重复同一候选失败时，按照总控规则调整实验设计或回到上游；到预算上限升级处理。

API依据（2026-10-24已核对）：[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)。请求使用 `text.format={type:json_schema,strict:true,...}`，本地仍做语义校验；只接受完整响应，检测拒绝与截断。客户端设置 `store:false`，不赋予模型任何执行工具。
