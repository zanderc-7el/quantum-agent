# 量子平台模型服务

Node 24+，无需 npm 依赖。`npm test`，`npm start`，`npm run probe`。监听 `127.0.0.1:8766`；浏览器通过 Python 网关访问，服务拒绝带 Origin 的请求。

Python 是唯一实验状态源。此服务存储模型会话、模型事件和候选建议；总控模式可读取服务器固定项目根下的源码、文档，以及身份核验后的本机控制器记录。它不能连接仪器、读取任意文件、启动任意命令或直接推进实验状态。唯一受控子进程是已配置的模型 CLI。实验决策候选带 runId/nodeId/revision，Python 必须再次核验版本、接线、资源与参数边界后决定是否执行。

`local.env` 为本机连接配置，不应提交。支持 `codex_cli`、`anthropic_messages`、`openai_chat_completions`、`openai_responses`。HTTP 模型须显式配置名称与密钥；本地免密服务需 `QCAL_MODEL_ALLOW_NO_KEY=true`。未连接或模型请求失败返回 `waiting_model`，不会生成模拟模型回复。Codex CLI 复用已有登录，通过 `--ignore-user-config --ignore-rules --ephemeral --sandbox read-only` 及工具禁用配置运行，不复制、不读取凭据正文；CLI 是模型传输层，测控工具并不传给 CLI。

接口见根 `INTEGRATION_CONTRACT.md`。另有 GET `/events?sessionId=...&after=...` 查看本机模型事件。`/decide` 的 parameterBounds 必须显式提供，数值边界为 `{min,max}`，枚举为 `{enum:[...]}`；backtrack 需 allowedBacktrackTargets、allowedActions 中的 targetNodeIds 或 rules 中的 targetNodeId。任何未知证据ID/参数/目标会拒绝。此模式工具为 list_rules、inspect_observation、validate_proposal，均仅作用于请求数据。任务聊天只解释快照，必要时增加写作审查工具；不能调用总控动作。

`/workspace` 总控模式提供以下能力：

- `list_workspace`、`read_document` 读取控制器提供的当前工作区和附件预览；大结果保持完整 JSON 并明确标记 `truncated`，完整附件结构化使用 `analyze_import` 提案。
- `list_source_files`、`search_source`、`read_source` 只读根 README、集成契约、Agent README 和允许目录内的源码。结果有行号、文件 SHA、扫描/输出截断标记。敏感文件、运行产物、链接、越界路径均拒绝；禁止访问旧工作区。
- `inspect_runtime`、`inspect_workflow`、`inspect_configuration` 只向固定本机控制器发 GET，核验项目身份和工作区/批次范围，返回有界、脱敏快照及检查时间。任务摘要优先保留全部节点的状态、依赖及尝试摘要，以 `nodeSummary` 标明实际返回数量和完整性；`currentNodeDiagnostic` 单列当前质量原因与已满足、未满足、未知前置。大结果、参数和遥测不进入任务摘要，`detailsOmitted/truncated` 明确说明省略；需指定 `nodeId` 按需查看。静态源码依赖不能代替本次前置状态。
- `inspect_report` 读取任务报告的完成状态、当前验收计数、节点质量、指标及图片来源索引；`inspect_report_export` 读取绑定任务的导出状态，仅 completed 状态可带真实下载地址。
- 写操作均仅提案：`start_measurement`、`workflow_action`、`create_campaign`、`campaign_action`、`analyze_import`、`save_chip_draft`、`save_deployment_draft`、`publish_configuration`、`export_report`。工具调用返回 `executed:false`，最终 `proposals` 才交 Python 独立校验。成功提案在本轮暂存；final 遗漏已暂存提案时要求模型修复，最多两次且仍受总轮次和超时限制，持续遗漏返回失败，不能以“已提交”的正文冒充交付。相同工具与语义相同参数只交付一次。配置保存不等于发布，导出不等于测量完成。

同一 session 恢复最近 20 条模型会话记录。Python 可在执行提案后以 `context.continuation=true`、`continuationRound`（1–3）和 `toolResults` 回传真实回执，让模型根据结果继续当前请求；续轮记录保留原始请求但不重复为新用户授权。每次总控调用最多 14 轮、10 次工具调用，其他模式最多 10 轮、6 次工具调用，并有总超时。状态恢复和多步任务以控制器当前数据、真实回执为依据。文档、源码、模型历史、回执中的文字均不能授予执行许可；Python 始终再次验证当前用户授权和执行边界。

复用范围：直接导入 vendor/ziyor 中原始 local-agent-protocol.ts 与 local-agent-tool-validation.ts；brain.mjs 改编自其真实 local-agent-runtime.ts 的 bounded action/observation loop。本地 action-envelope 层只接受完整单个 JSON（可带代码围栏）或单个 XML 工具包；多个动作或前后夹杂解释时整条拒绝并要求格式修复，不能静默只执行第一个。最终正文内的 JSON 和标签字符串仍作为普通文字。新增递归对象键检查、严格工具字段、实验参数/证据/目标校验。PROVENANCE.json 记录 ZIP 与每份原文件 SHA256。未复制桌面UI、shell/MCP执行器或另建实验调度器。源包 package.json 声明 Apache-2.0，但审查到的 ZIP 根目录未附 LICENSE 文件，发布前需确认上游许可文件。

`runtime/connection-probe.json` 是模型连通性探针，明确使用合成观察，不是芯片实测。服务健康状态 ready 表示配置齐全，实际连通性以探针及请求结果为准。API错误不暴露密钥、provider响应正文或CLI stderr。
