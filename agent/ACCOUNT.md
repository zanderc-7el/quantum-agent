# Ziyor 官方账户

账户界面通过本地 Python 网关调用 Node 内部 `/account/*` 接口。短信、登录和连接测试只能由用户明确点击触发。开发验收使用假 HTTP 服务，没有发送真实短信、登录真实 Ziyor 账户或调用其收费模型。

手机验证码登录使用官方 `https://skill.zeelin.cn/api/zeelin/send_sms`、`/api/zeelin/login`。登录成功后使用 `app_key` 查询 `/api/zeelin/user_info`，界面仅获得脱敏手机、昵称、`remain_calls`（标签为“积分”）。这些字段不能证明会员期限、套餐范围或无限额度。

官方可选模型从 `/api/v3/models_v2` 的已启用 `zeelin/token` 通道读取；调用端点固定为 `/api/v3/anthropic/v1/messages`，使用 `x-api-key` 和 `anthropic-version`。没有远程目录时不编造可用模型。登录默认选择目录默认模型，但不自动产生推理请求。`POST /account/test` 是用户显式发起的一次 `max_tokens:1` 连接测试，可能使用官方积分。

凭据存于 `agent/runtime/account.sealed`，使用 Windows DPAPI `CurrentUser` 加密整个账户状态。固定保护子进程从 stdin 接收秘密，命令行没有秘密；不写明文 `.env`、SQLite 或日志，不向前端返回密钥。保护不可用时拒绝持久登录，不退化为明文文件。该文件只能在对应 Windows 用户环境解密。

账户状态、原有模型和 Ziyor 模型分开：`useForAgent:false` 只登录账户，保持原有独立连接；默认 true 切换到 Ziyor 官方 provider。会员模式退出后模型调用返回 `waiting_model`。只有显式选择模型项 `__existing_provider__` 才切回已有 Codex/API 连接。

登录、切换或退出开始时，立即取消本机旧模型请求：HTTP 请求发送取消信号，Codex 子进程终止，旧回复不持久化、不进入下一工具轮次。正在转换时新的模型请求等待；新凭据必须先经 DPAPI 原子保存才向运行层发布。已被远端接收的请求可能已产生额度消耗，本机取消不能撤销远端计费。实验采集和运行状态仍由 Python 控制器独立管理。

接口：GET `/account/status`、GET `/account/models`；POST `/account/send-code`、`/account/login`、`/account/connect`、`/account/logout`、`/account/test`。写请求都带唯一 `operationId`，重复请求不重复发短信或测试；发送短信有 60 秒冷却；已提交但未确认的操作返回 `account_outcome_unknown`。操作记录只保存请求指纹及脱敏回执，手机原文和验证码不保存。错误仅返回固定 code/message，不返回原始官方响应正文。

源协议快照及逐文件 SHA256：`agent/vendor/ziyor-account/PROVENANCE.json`。原源码的前端传递／SQLite 明文密钥设计没有照搬。
