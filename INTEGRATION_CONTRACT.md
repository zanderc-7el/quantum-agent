# 本地平台集成约定 v1

项目根目录 `D:/豆包/量子/quantum-control-platform`。禁止访问 `D:/qoskit-workspace`。原档案、原F8目录不修改。真实执行只能通过控制器和独占worker，不允许子代理自行连接硬件。

## 默认桌面入口

根 `start-platform.cmd` 通过隐藏辅助 PowerShell 启动 `scripts/start-desktop.ps1`，打开 `desktop/dist/win-unpacked/QuantumControl.exe` 的独立 Windows 窗口。桌面负责检查服务归属并启动或复用本项目 8765/8766 服务，不启动测量。关闭窗口仅退出桌面，后台实验继续；不按进程名称强杀已有服务。`scripts/start.ps1 -OpenBrowser` 保留为浏览器调试入口。

桌面包内包含 Electron 与 Node，仍依赖本机 Python 和完整平台工程；数据、模型配置、F8 运行环境留在项目/现场原有位置。portable EXE 不表示可脱离工程、Python 或现场依赖迁移。

## 目录所有权
- root: `backend/`, `config/`, 启动/集成脚本、根文档及系统验收。
- frontend agent: `frontend/`，保留原外观，将remote模式全部接实际API。
- brain agent: `agent/`, `vendor/ziyor/`，模型服务、工具决策及独立测试。
- adapter agent: `adapters/`, `data/imports/`, `knowledge/`, `tests/test_adapters*`, 实测资料导入及F8执行协议。不连接仪器。

## Python HTTP API（127.0.0.1:8765）
统一JSON，错误`{error:{code,message}}`，所有写请求含`X-Qcal-Token`（GET /api/session获取本地会话），拒绝跨Origin写。只监听loopback。前端生产静态文件由同源Python服务；开发Vite代理`/api`至8765。
- GET /api/session → `{token,actor:{id:'local-operator',displayName:'现场操作员',role:'leader'}}`
- GET /api/health → `{status,agent,hardware,version,projectId}`；桌面据服务标识检查端口归属。`hardware.ownership={verified,owners:[{pid,localPort}],endpoint,reason}` 为只读连接占用信息，verified 表示已完成检查，不表示 owners 为空。
- GET /api/workflows → `Workflow[]`；GET /api/workflows/:id → `Workflow`
- POST /api/workflows 输入兼容原`CreateWorkflowInput`，增加`mode:'replay'|'real'`, `resourceIds?:string[]`，返回Workflow。
- POST /api/workflows/:id/actions → `{action,operationId,expectedRevision,nodeId?,attemptId?,targetNodeId?}`；动作原6个+`backtrack`,`confirm_hardware`,`reconcile`。返回Workflow。可选targetNodeId用于明确回退。approve不能把失败指标改成合格。reconcile 只核查现有回执，不重新下发。confirm_hardware 通过后为本次任务临时启用执行器，不修改全局 profile。
- PATCH /api/workflows/:id/policy → `{policy,operationId,expectedRevision}` → Workflow。
- GET /api/events?after=N → SSE `id:<seq>`, `event:snapshot`, `data:{version:1,workflows:[...],seq:N}`；心跳comment；断线以lastEventId/after恢复。
- GET /api/workflows/:id/nodes/:nodeId → `{node,attempts:Attempt[],decisions:Decision[],rules:Rule[]}`。
- GET /api/artifacts/:artifactId → 原始数据/图片，只允许登记过的产物，不接受任意路径。
- GET /api/workflows/:id/report → 自包含JSON实测/回放报告；`?format=html`为图文报告。
- GET /api/catalog → `{blueprint:{version,nodes,edges},resources,rules,capabilities,stages}`；stages 分别说明网分预扫描、接线确认、F8 脉冲测量的可用状态与原因。保留流程结构不等同于方法已全部接入。
- POST /api/chat → `{message,workflowId?,sessionId?}` → `{sessionId,message,decisions?,status,model?}`；模型未连接明确返回waiting_model而非规则聊天冒充。

## Ziyor 会员账户接口

所有账户写操作使用同一 `X-Qcal-Token` 会话和 `operationId`。前端若收到 401，刷新本地会话后只重试一次，沿用原请求体和 operationId。会员密钥留在服务端，前端不写入手机号/验证码/密钥存储；status 返回脱敏账户。

- GET `/api/account/status` → `{provider:'ziyor',configured,authenticated,user?:{name,phoneMasked},balance?:{remainingCalls,label:'积分'},modelConnection:{provider,model,active,verified},capabilities:{sendCode,login,connect,test},error?}`。仅读取本地脱敏状态，不发模型推理。会员 authenticated 与 modelConnection 独立，不能因 Codex 已连接而显示会员已登录。
- GET `/api/account/models` → `{models:[{id,label,provider?,...}]}`；可获取官方模型 ID，不发推理。包含 `{id:'__existing_provider__',provider:'existing',label:'原有独立模型连接'}` 供显式切回原独立连接。
- POST `/api/account/send-code` ← `{phone,operationId}` → `{status:'sent',retryAfter:60}`；仅用户点击获取验证码后发送，倒计时采用回执。
- POST `/api/account/login` ← `{phone,code,operationId,useForAgent:boolean}`；主路径默认 true，按钮为“登录并使用 Ziyor 额度”。false 保留已有独立模型连接。真实账号登录由本人输入短信验证码。
- POST `/api/account/connect` ← `{model,operationId}`；只有显式选择 `__existing_provider__` 才切回独立 Codex/API。
- POST `/api/account/logout` ← `{operationId}`；退出时若实际模型处于会员模式，后续任务等待 `waiting_model`，不得静默切回其他 provider。
- POST `/api/account/test` ← `{operationId}`；用户明确点击“验证连接（发送一次短请求）”才执行，可能消耗会员额度。打开面板、登录或切换模型不自动触发测试。

账户写操作完成后前端重新 GET status，不推断登录或连接已成功。积分只显示会员服务返回的有限数值；0 为真实零值，字段缺失显示“暂未获取”。独立连接选项位于高级模型设置，侧栏模型名称始终展示实际 provider。

账户 UI 与后端协议的软件验收使用 fakeHTTP；不发送真实短信、不登录用户账号、不消耗真实会员额度。测试截图与日志必须标注 fakeHTTP，不能代表真实会员连接验证。

## 前端数据增量（原类型字段继续提供）
Workflow增加 `mode:'replay'|'real'`, `revision:number`, `engineState:string`, `blueprintVersion:string`, `edges:{from,to,kind?}[]`, `resourceIds:string[]`, `currentNodeId?:string`, `resultSummary?`。
Node增加 `ownerId:string`, `procedureId:string`, `capability:'available'|'unavailable'|'historical_only'`, `engineState:string`, `parameters:Record<string,unknown>`, `parameterVersions:Record<string,number>`, `result?:Result`, `latestAttemptId?:string`, `rules:Rule[]`, `dependsOn:string[]`。UI颜色使用原status，同时展示engineState以区分stale/waiting_hardware等。
Node 可含 `telemetry={rowNumber,completedRows,totalRows,jobIndex,jobCount,jobProgress,progress,progressBasis,message,prob_hz,drive_hz,...}`；jobIndex 从 1 开始，数值缺失时不造进度。新 attempt 开始清除当前 result，旧图只在执行历史中保留；完成采集后展示本次原生图。
Result = `{dataKind:'historical'|'real',sourceRunId?,acquiredAt?,metrics:Record<string,number|null>,units?:Record<string,string>,quality:{status:'passed'|'failed'|'unverified',reasons:string[]},artifacts:Artifact[],series?:{name,x:number[],y:number[],xUnit?,yUnit?}[],parameters?,provenance?,methodLabel?}`。
Artifact = `{id,name,kind:'image'|'raw'|'json'|'text',url,sha256,sourcePath?}`。
Attempt = `{id,nodeId,number,state,startedAt,endedAt?,parameters,parameterVersions,result?,error?,operationId,mode,procedureId?}`。
Decision = `{id,nodeId,attemptId?,createdAt,source:'rule'|'model'|'operator',action,reason,evidenceIds:string[],ruleId?,parameterChanges?,targetNodeId?,invalidatedNodes?:string[],status?:string}`。
Rule = `{id,version,title,scope,trigger,action,verification,evidence:string[]}`。

## Node智能体（127.0.0.1:8766，仅内部服务）
- GET /health → `{status,configured,provider,model,source}`，不输出密钥。
- POST /decide 输入 `{runId,nodeId,revision,mode,goal,observations,allowedActions,parameterBounds,rules,history}`；返回 `{status:'ok'|'waiting_model'|'error',decision?:{action:'continue'|'retry'|'backtrack'|'wait'|'stop',reason,evidenceIds,parameterChanges?,targetNodeId?,ruleId?},model?,error?}`。
- POST /chat 输入 `{message,sessionId?,context?}` → `{sessionId,message,status,model?}`。
Node只提出方案，不修改Python实验状态、不接仪器、不持有任意shell工具。使用模型提供商配置/环境变量；离线时不生成假模型结果。Python负责再次校验候选方案。实际决策及聊天进入自己的持久记录；实验日志由Python统一持久。

## 数据与F8适配协议
adapter agent提供 `adapters/history.py`，入口 `import_history(project_root:Path) -> dict`，生成 `data/imports/history.json`，结构`{version,resources:[{id,label,engineKey,...}],records:[{id,resourceId,ownerId,procedureId,parameters,result:Result,sourceFiles:[...]}]}`。图片/JSON可以复制到data/imports/artifacts，原始大文件保留明确路径+SHA，只读登记。历史真实数据的mode依旧replay/dataKind historical，不能作为新的真机采集证据。
adapter agent提供 `adapters/f8.py`：`F8Adapter(project_root,profile)`；`capabilities()->dict`；`preflight()->dict`只检查静态路径/版本/参数，不连接仪器；`execute(request,emit,cancel)->Result`通过独立进程；`reconcile(operation_id)->dict`。request含runId,nodeId,attemptId,operationId,resourceId,procedureId,parameters,parameterVersions,deadline,outputDir。emit接受`{event,...}`；cancel为callable检查暂停/终止请求。真实连接需profile明确可执行且参数范围验证通过。提供profile模板、真实runner代码映射；不能只移除经验包real拒绝。启动即连接设备的旧模块不得在API进程import。没有可信执行回执则返回unknown并保留资源隔离。
