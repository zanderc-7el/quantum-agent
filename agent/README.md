# 量子平台模型服务

Node 24+，无需 npm 依赖。`npm test`，`npm start`，`npm run probe`。监听 `127.0.0.1:8766`；浏览器通过 Python 网关访问，服务拒绝带 Origin 的请求。

Python 是唯一实验状态源。此服务仅存储模型会话、模型事件和候选建议。它不能连接仪器、读取任意文件、启动任意命令或推进实验状态。唯一受控子进程是已配置的模型 CLI。候选带 runId/nodeId/revision，Python 必须再次核验版本、接线、资源与参数边界后决定是否执行。

`local.env` 为本机连接配置，不应提交。支持 `codex_cli`、`anthropic_messages`、`openai_chat_completions`、`openai_responses`。HTTP 模型须显式配置名称与密钥；本地免密服务需 `QCAL_MODEL_ALLOW_NO_KEY=true`。未连接或模型请求失败返回 `waiting_model`，不会生成模拟模型回复。Codex CLI 复用已有登录，通过 `--ignore-user-config --ignore-rules --ephemeral --sandbox read-only` 及工具禁用配置运行，不复制、不读取凭据正文；CLI 是模型传输层，测控工具并不传给 CLI。

接口见根 `INTEGRATION_CONTRACT.md`。另有 GET `/events?sessionId=...&after=...` 查看本机模型事件。`/decide` 的 parameterBounds 必须显式提供，数值边界为 `{min,max}`，枚举为 `{enum:[...]}`；backtrack 需 allowedBacktrackTargets、allowedActions 中的 targetNodeIds 或 rules 中的 targetNodeId。任何未知证据ID/参数/目标会拒绝。工具只有 list_rules、inspect_observation、validate_proposal，均仅作用于请求数据。上下文内指令不改变工具权限。

复用范围：直接导入 vendor/ziyor 中原始 local-agent-protocol.ts 与 local-agent-tool-validation.ts；brain.mjs 改编自其真实 local-agent-runtime.ts 的 bounded action/observation loop。新增递归对象键检查、严格工具字段、实验参数/证据/目标校验。PROVENANCE.json 记录 ZIP 与每份原文件 SHA256。未复制桌面UI、shell/MCP执行器或另建实验调度器。源包 package.json 声明 Apache-2.0，但审查到的 ZIP 根目录未附 LICENSE 文件，发布前需确认上游许可文件。

`runtime/connection-probe.json` 是模型连通性探针，明确使用合成观察，不是芯片实测。服务健康状态 ready 表示配置齐全，实际连通性以探针及请求结果为准。API错误不暴露密钥、provider响应正文或CLI stderr。
