# 后端对接说明

当前应用默认仍是可操作的纯前端演示：所有任务、实验结果、阈值、曲线、审批、异常和运行时间均为本地模拟，没有连接量子硬件。v1.4 起增加了**可选远程模式**（`VITE_GATEWAY_MODE=remote`），可直接连接 `E:\work\QuantaMind` 的 QuantaMind 校准 API；未设置该变量时行为与之前完全一致。第 1–2 章描述的是前端模块与通用合同，仍然有效；远程模式的具体配置、鉴权和差异见文末「v1.4 远程模式」。

## 前端模块与替换入口

- `src/types.ts`：界面消费的任务、节点、策略和状态类型。
- `src/lib/simulator.ts`：无浏览器依赖的状态转换；初始化、创建、时钟推进、操作和本地持久化。
- `src/lib/gateway.ts`：`WorkflowGateway` 适配器接口；默认 `createGateway()` 返回 `mode: 'mock'`，传入适配器时返回该适配器。
- `createMockGateway({ load, save })` 可注入存储，`advance(seconds)` 显式推进演示，不创建隐藏后台定时器。`subscribe` 返回取消订阅函数。
- `src/lib/useWorkflowStore.ts`：界面数据入口，默认经Gateway加载、订阅和操作任务；可传入 `useWorkflowStore(remoteAdapter)`。返回 `state/create/act/policy/resetDemo/advance`，以及 `mode/error` 供界面展示加载错误和模式。
- `src/App.tsx`：已接入上述 Store，任务操作不再直接调用模拟器。应用入口可传入 `<App gateway={adapter} />`，其中 `adapter` 必须实现完整 `WorkflowGateway`；无需向页面组件逐一传递 HTTP 方法。
- `src/lib/measurements.ts`：蓝图指标、节点详情和报告共用的数据选择器。v1.5 起远程模式会叠加服务端测量（`remoteMeasurements` 缓存）；缓存为空时仍是模拟值。`ChatReport` 的规则助手也不属于真实模型服务。

界面目前统一调用 `useWorkflowStore`，后续新增操作也应保持这一入口。`resetDemo()` 只对mock适配器开放；真实模式下 `advance()` 不推进任务。真实后端模式初次加载时为空状态，不以演示种子伪装真实数据；列表或订阅错误通过 `error` 返回。较晚到达的初始列表不会覆盖较新的订阅快照。测量数据已接入 `useRemoteMeasurements`（见文末 v1.5）；模型服务仍未联调。

订阅快照是当前状态的权威来源。列表、操作和策略请求均捕获本地修订号；若请求期间已收到更新，迟到的响应不覆盖当前快照。创建请求若遇到这种情况且快照尚不包含新任务，会刷新列表取得权威结果；刷新失败说明“创建已成功、列表刷新失败”，不将成功创建误报为失败。实际服务端仍应提供任务版本和事件序号以支持跨客户端一致性。

注入方式示意（`adapter` 由后端集成代码创建，不是仓库中已实现的远程服务）：

```tsx
import App from './App';
import type { WorkflowGateway } from './lib/gateway';

function IntegratedApplication({ adapter }: { adapter: WorkflowGateway }) {
  return <App gateway={adapter} />;
}
```

适配器实例在同一次连接期间应保持稳定，不要在每次 React 渲染时重新构造。`subscribe` 必须返回有效的取消订阅函数；订阅回调当前消费完整 `AppState` 快照，若服务器发送增量事件，应由适配器归并并校验版本后再交给 UI。

## 本地入口与演示边界

构建后执行 `node scripts/start-preview.mjs`，服务地址固定为 `http://127.0.0.1:5286`，端口占用会报错而不自动换端口。

- 对话式 Agent：`http://127.0.0.1:5286/#chat`，规则指令生成计划、创建任务、解释模拟结果，导出 Markdown / HTML / CSV。
- 蓝图版工作流：`http://127.0.0.1:5286/#blueprint`，显示单比特流程或双比特的两个阶段，支持节点详情、实际缩放、执行、暂停、恢复、审批、重试及终止。
- 工作看台：`http://127.0.0.1:5286/#dashboard`，搜索、组合筛选、分页、创建和复制任务，导出完整任务 JSON。
- 接口验证台：`http://127.0.0.1:5286/#api`，在浏览器内运行离线进程内模拟，查看文档路径的请求封装、合成曲线、频率建议和真实接口合同缺口；它没有连接 32 所服务，也不会向真机下发参数。

三入口共享任务 Store，hash 只选择视图，不编码工作流 ID。模拟数据、预览结果与已执行模拟结果必须继续清晰区分。只有服务端提供相应证据后，才能将相关界面改为真实设备状态；不能通过删除模拟标签来完成联调。

## 拟定接口语义

| 前端方法 | 拟定服务端映射 | 语义 |
| --- | --- | --- |
| `list()` | `GET /api/workflows` | 返回当前用户有权查看的任务列表 |
| `get(id)` | `GET /api/workflows/:id` | 返回任务与节点的权威快照 |
| `create(input)` | `POST /api/workflows` | 仅创建草稿，所有节点待执行 |
| `act(id, action)` | `POST /api/workflows/:id/actions` | `start/pause/resume/approve/retry/abort`；服务端校验合法转换 |
| `policy(id, policy)` | `PATCH /api/workflows/:id/policy` | `strict/threshold/autopilot`，已有待决策项仍需显式处理 |
| `subscribe(listener)` | SSE 或 WebSocket，待双方确定 | 接收权威快照或具有版本号的增量事件 |

真实接口地址、鉴权方式、分页、错误格式、事件重连规则、任务版本、参数命名和实验字段尚未确定，应由前后端联调确定后实现。不得将前端的演示阈值当作真机参数。

## 关键操作合同

`approve` 明确接受当前结果并推进下一实验；`retry` 重新执行当前实验，增加该节点的 `attempt` 和任务 `iterations`，不会隐式批准。重复审批在当前模拟器中是幂等空操作；真实实现应将审批绑定到任务版本、节点和尝试编号，并支持请求幂等键，避免网络重试批准了后续节点。

接受低于阈值的结果不等于指标达标。此时节点记录明确保留“接受低指标”的说明，审批事件标记为警告；任务可以在完成后续节点后进入 `completed`，其语义是流程执行完成，不能据此声称全部指标通过。`iterations` 表示重试次数，不包含首次执行。

`pause` 冻结实验进度和演示运行时间，`resume` 从暂停位置恢复。`abort` 为终态，不能重新启动。模拟失败可通过 `retry` 恢复。严格策略在每个实验完成时等待审批；阈值策略仅在模拟异常时等待；自动策略遇到注入异常会调整参数并自动重试一次，第二次模拟通过，从而避免无限重试。

任务的 `elapsed` 是实际推进的**演示秒数**。每个模拟实验耗时约四秒，暂停、待审批、完成、终止期间不增长；一次大步推进也只累计任务实际运行的时长。真实耗时必须由服务端独立提供，不能沿用演示时间作为真机运行数据。

## PDF实验流程映射

当前仅支持 `experiment: 'single' | 'two'` 和一或两个量子比特；所选实验种类必须与 `qubits` 一致。创建接口为兼容现有表单允许省略 `experiment`，这时根据 `qubits` 推导。默认演示目标是 `Q12` 或 `Q12–Q13`。

单比特目标须匹配一个 `Q` 加数字的编号；双比特目标须提供两个不同编号，以 `-`、`–`、英文逗号、中文逗号或 `/` 分隔。接受大小写和分隔符两侧空格，统一保存为 `Q12` 或 `Q12–Q13` 形式。数字前导零归一化后仍须为两个不同编号，因此 `Q1–Q01` 会被拒绝；空目标使用默认值。

单比特共九个执行实例：`ReadoutFreqDip → F01Spectroscopy → XYGateAmpRabiImpl → IqToProbability → ReadoutParaSweepByIQProb → IqToProbability复测 → T1Idle / T2CPMG / RB1QCal`。PDF末尾三项是并列分支；蓝图保持并列关系，模拟引擎因共享测控资源将三项串行调度。完成进度按执行实例数量计算。

双比特先完成上述九个单比特前置实例，随后执行八个实例：`F01ZPulseDependency → ZeroPointFinderByQQSwap → CZSwapSweepZPulse → TTGAczDelicateG → TTGAczDelicateDetune → CZXEBCal → CZParaOptimizer → CZXEBCal复测`。`TTGAczDelicateG` 保留PDF指定的 `CZ_width=60ns`；此处仅演示，未发送真机参数。

复测使用独立节点ID `IqToProbabilityRetest` / `CZXEBCalRetest`，其 `name` 保持实验原名。异常场景只在单比特的 `ReadoutParaSweepByIQProb`，或双比特的首次 `CZXEBCal` 注入一次；用户的 `retry` 会重新执行该失败实例，流程中既定的后续复测仍会另外执行。

## 持久化与校验

本地存储键为 `qcal-workspace-demo-v1`，载荷包含 `version: 1`。读取时校验版本、任务和节点字段、状态、数值、事件、重复任务 ID、活动节点数量及大小限制；无存储权限、损坏 JSON 或不兼容数据均回退为演示任务。默认Gateway写入失败将拒绝本次变更，保持持久化数据与界面一致。导出/分享数据应持续标明模拟来源。

当前演示最多保存 `MAX_WORKFLOWS = 200` 条任务；界面经 Store 调用 `gateway.create`，在达到上限时返回明确错误且不修改既有任务。第200条可正常保存和恢复，不能允许创建后在刷新时因超过载荷限制而丢失数据。

## 联调验收

先固定服务端状态枚举和实验步骤，再验证：创建全待执行、暂停无进度漂移、审批与重试不同、重复请求幂等、取消后无复活、权限失败可解释、断线重连恢复正确版本、多个用户操作不互相覆盖。尤其要模拟慢列表、慢操作响应与新订阅快照交错的场景，确保旧结果不能覆盖新状态；在真实后端具备版本字段前，不要把网络到达顺序当作业务先后顺序。真实模式的指标和完成状态必须来自服务端证据；只有 mock 模式可本地造数和推进任务。

独立模拟器测试运行：`node --experimental-strip-types --test qa/simulator.test.ts`。项目整体测试运行：`npm test`。

## 工作区导入与导出

`exportWorkspace(state)` 返回兼容现有 `version: 1` 的JSON文本，包含 `dataSource: 'simulated'` 和导出时间。`previewWorkspaceImport(json, existingWorkflows)` 为只读预览，返回 `valid/errors/total/importable/skipped/runningToPause/single/two/statusCounts`；任何解析、版本、字段、状态机语义或容量错误均明确报告，导入从不回退成初始示例。

Gateway可选提供 `importWorkflows(json): Promise<WorkspaceImportResult>`。默认mock实现完整支持；未提供该能力的后端适配器会让hook的 `canImport` 为false，调用则明确拒绝。hook的同名方法返回 `total/imported/skipped/paused/importedIds`，并使用权威订阅或列表刷新同步界面。

合并导入保留原任务，为新增任务分配新ID，并将原始来源写入可选字段 `importSourceId`。同源任务重复导入会跳过；不会覆盖本地已有运行或历史。导入文件中的运行中任务一律转为暂停，须由用户显式继续。原队列任务仍保留排队状态。

文件、合并后的工作区和持久化候选状态统一限制为UTF-8编码后5×1024×1024字节、最多200任务。数据验收核对9/17节点序列、唯一ID、目标比特、节点状态位置、进度、累计演示时间和重试次数，并拒绝未知结构字段。容量超限或持久化依赖抛错时，Gateway在提交前拒绝，保留原状态；模拟时钟的失败由hook捕获并放入 `error`，不会每秒产生未捕获异常。浏览器拒绝localStorage时默认Gateway拒绝提交并显示可重试错误。

单任务保留最近10000条事件。超过该数量时移除最旧记录，防止当前会话生成下一次读取无法接受的历史长度；导入也使用同一保留规则。

## v1.1 设置、附件与导入接入点

`src/lib/preferences.ts` 管理 `qcal-preferences-v1` 本地设置。主题、界面名称、实验默认值、模拟时钟实际生效；provider/model/endpoint仅保存草稿，不发起网络请求，不收集或存储API密钥。正式连接应提供独立的连接状态、鉴权和错误反馈。

`src/lib/attachments.ts` 与 ChatReport 的附件链路保留浏览器 File 对象、文件元数据和安全文本片段。按任务持久化在本机IndexedDB；刷新恢复File及草稿。PDF仅作本地附件，不解析，不执行文件内容。后端需要另外实现上传、文档解析、模型上下文、取消与进度、权限及删除策略，不能将当前本地发送标记误作服务器接收。

`src/lib/workspaceTransfer.ts` 提供 `exportWorkspace(state)`、`previewWorkspaceImport(json,existingWorkflows)` 和 `mergeWorkspaceImport(state,json)`。Gateway新增可选 `importWorkflows(json)`；Store暴露 `canImport` 和 `importWorkflows`。缺少该能力的远程适配器禁用导入。Mock导入为原子合并：验证整个载荷后生成新任务ID，使用原来源ID进行重复识别，运行中任务暂停，既有任务保持原样。`importSourceId` 为可选来源标记。工作区JSON沿用version1，个人设置、附件和对话不混入工作流备份。


## v1.2 认证、数据与参数接入合同

本地模拟模式仍使用 `auth.ts` 的固定公开账号；远程模式的账号由 QuantaMind 后端目录校验，`loginWithBackend` 直接使用服务端返回的身份，不再要求账号出现在前端演示目录中。`roleGateway.ts` 的前端角色判断仅用于界面呈现，服务端仍独立校验每个请求，不可信任客户端 role/ownerId。使用 HttpOnly/Secure Cookie 代替 sessionStorage、并增加 CSRF 防护仍是生产化的前提。不要把公开演示密码用于真实服务。

`Workflow.ownerId?:string` 是稳定账号标识，`owner` 仅展示。旧version:1任务保留可读，没有ownerId时仅负责人可操作。创建时服务端根据会话赋ownerId；工作人员创建强制threshold，审批/attention状态重试/策略更改/导入/重置仅负责人。账号 CRUD 与密码重置由后端 `/api/v1/users` 提供，设置页「账号与权限」已接入（负责人管理账号，所有远程账号可修改自己的密码）。

本机chatPersistence采用IndexedDB `qcal-local-conversations-v1`，conversation/attachments/metadata分表，按workflowId关联；写入事务共用100MB原附件门限，reset epoch阻止旧操作回写。接口有loadChatSnapshot、saveChatSnapshot、clearChatStorage、readChatStorageStats及subscribeChatStorage。正式存储适配器需提供等价持久化语义、失败反馈、上传下载和按团队访问控制，数据迁移需明确用户选择，不能自动上传旧本地资料。

实时参数由nodeParameters和measurements的模拟选择器产生。真实接口需返回节点ID、单调revision、采集时间、参数key/value/unit、运行进度/attempt、结果阶段和数据来源；UI通过WorkflowGateway.subscribe更新，拒绝乱序快照。不要将当前预设拟合值当作真机结果。

## v1.3 消费界面与完整实验 CSV

- ParameterDrawer接收最新Workflow快照，selectDrawerNode跟随活动节点或保留固定选择；硬件结果接入后沿用明确的结果阶段和单位，不能将未执行预览填为最终结果。
- buildExperimentCsv(workflows, exportedAt)为无写入纯导出函数，须传全部可查看工作流；返回filename/content/mimeType及任务、节点、事件计数，含BOM、CRLF与电子表格公式前缀防护。
- 导出使用现存节点快照、配置、模拟结果和有界事件。真实后端须保留runId/attemptId、起止时间、输入快照、原始测量附件引用及完整事件；批量导出应分页穷尽服务端历史。当前接口未提供逐次测量历史。
- ChatReport.canEditConversation由任务归属决定：负责人可编辑，工作人员仅可编辑本人任务；只读可查看/下载报告附件，不能修改、上传、清空。服务端对会话和附件写入接口须独立检查同一权限。

## v1.4 远程模式

远程模式连接 QuantaMind 校准 API（前缀 `/api/v1/calibration`），由服务端负责状态推进、权限校验、幂等和事件流。默认关闭；不配置时仍使用本地模拟器。

### 配置

1. 启动后端（工作目录 `E:\work\QuantaMind`）。执行引擎默认关闭，需要显式开启，并且只应在单个实例上运行：

   ```powershell
   $env:QUANTAMIND_CALIBRATION_WORKER = "true"
   $env:QUANTAMIND_DATABASE_URL = "sqlite:///./data/quantamind.db"   # 可选：默认是进程内 SQLite，重启即清空
   python -m backend.quantamind_api.app
   ```

   默认监听 `0.0.0.0:18789`；可用 `QUANTAMIND_GATEWAY_PORT` 覆盖。任务按真实时间推进（`QUANTAMIND_CALIBRATION_TICK_SECONDS`，默认 1 秒）。后端默认允许的 CORS 来源包含 `http://localhost:5286` 与 `http://127.0.0.1:5286`（本前端预览端口），可用 `QUANTAMIND_CORS_ORIGINS` 覆盖（逗号分隔）。

2. 在前端目录创建 `.env.local` 并设置：

   ```text
   VITE_GATEWAY_MODE=remote
   VITE_API_BASE_URL=http://127.0.0.1:18789
   ```

3. `npm run dev`（或构建后用 `node scripts/start-preview.mjs`）访问 `http://127.0.0.1:5286`，使用后端账号目录中的账号登录（本地开发种子默认 `leader` / `Leader123!`，生产部署应已更换或删除）。登录会调用后端 `/api/v1/auth/login` 校验密码；登录成功后本地会话与后端 access/refresh token 分别保存在 sessionStorage（`qcal-demo-session-v1` 与 `qcal-remote-session-v1`），退出登录先调用服务端注销再清除两者。

### 模块

- `src/lib/apiClient.ts`：统一 `{success,data,error}` 解包、Bearer 注入、错误中文化与 401 处理；401 时用 refresh token 换取新令牌并重放一次，刷新失败才结束会话。`logoutRemote` 先调用服务端注销再清理本地会话；`loginWithBackend` 提交用户名，使用后端返回的身份，把 `calibration_leader`/`calibration_operator` 映射为界面角色，并明确拒绝暂不支持的角色。
- `src/lib/accountAdmin.ts`：远程账号目录的薄封装（列表、创建、改角色/显示名/启停、重置密码、修改本人密码）。修改本人密码后后端会吊销旧令牌，因此立即把新的 access/refresh token 写回会话。
- `src/lib/remoteGateway.ts`：实现完整 `WorkflowGateway`。每个写操作携带 `expectedRevision` 与服务端幂等键 `requestId`；`approve`/`retry` 绑定当前 `nodeId`/`attempt`。收到 409 时使用响应中的权威快照自动重试一次。
- `src/lib/roleGateway.ts`：未注入适配器时，根据 `VITE_GATEWAY_MODE` 选择远程或本地模拟。远程身份不再与本地演示目录比对，只校验结构并由服务端授权；远程适配器仍会把当前用户自己的 `ownerId` 映射为传入的 `localUserId`（远程模式即服务端用户 ID），保证 `canOperateWorkflow` 等纯前端判断可用。
- `src/App.tsx` / `src/components/LoginScreen.tsx`：登录改为异步；收到任意 API 401 时返回登录页；页脚、模式标签与侧栏文案在远程模式下显示“远程工作空间 / 服务器保存”。演示账号卡片与预填账号只在本地模拟模式出现，远程模式不再展示内置凭据。
- `src/lib/remoteInsights.ts`：只读 Skill 绑定与审计时间线，供设置页「Skill 与审计」与蓝图决策轨迹使用；不参与 `WorkflowGateway`，因此不会改变工作流载荷。
- `src/lib/decisionTrail.ts` / `src/lib/useDecisionTrail.ts`：把审计条目推导为节点徽标与决策路径（纯函数可单测），并在蓝图可见时按节点完成/重试事件重新拉取；重放按需触发。
- `src/components/DecisionTrail.tsx`：蓝图主视图中的只读决策轨迹面板，点击条目可定位到对应节点。

### 行为差异

| 能力 | 本地模拟（默认） | 远程模式 |
| --- | --- | --- |
| 任务推进 | 浏览器按 `advance()` 推进 | 后端执行引擎推进，经 SSE `workflow.updated` 推送 |
| 多端一致性 | 仅当前浏览器 | 服务端权威，`revision` 乐观锁 |
| 工作区导入 | `mergeWorkspaceImport` 本地原子合并 | `POST /workspace/import` 服务端合并并返回相同计数 |
| 工作区导出 | 浏览器内 `exportWorkspace`（仍可用） | 后端另提供 `GET /workspace/export` 与 `GET /export/csv` |
| 重置演示数据 | 可用 | 不可用（界面禁用） |
| 权限 | 前端角色网关演示 | 服务端按角色与归属独立校验 |

### Skill 与审计（远程模式）

设置 →「Skill 与审计」展示当前任务绑定的 Skill 版本、自主等级、每个节点的量化验收标准，以及测量/命令/事件合并的时间线。数据来自以下只读接口：

| 接口 | 用途 |
| --- | --- |
| `GET /api/v1/calibration/skills` | 版本化 Skill 库（单/双比特） |
| `GET /api/v1/calibration/skills/{skillId}?version=` | 单个 Skill 的步骤与验收标准 |
| `GET /api/v1/calibration/devices`、`/devices/{id}` | 设备档案：适配器、能力与按节点的阈值覆盖 |
| `GET /api/v1/calibration/workflows/{id}/skill` | 当前任务绑定的 Skill、设备档案、自主等级（L1 逐步确认 / L2 异常升级 / L3 自动运行）与设备生效的验收标准 |
| `GET /api/v1/calibration/workflows/{id}/audit?limit=` | 不可变时间线，含实测值、是否通过、决策与升级原因码 |
| `GET /api/v1/calibration/workflows/{id}/audit/replay` | 用当前 Skill 重放已记录测量（只读），返回 `changed` 漂移标记 |

审计记录在测量写入时冻结 Skill 版本、设备档案版本、阈值、实测值和决策；之后修改 Skill 或设备阈值不会改写历史。旧数据没有冻结记录时显示为 `evaluationSource: "derived"`，界面仍会标注。同一页的「重放校验」调用 replay 接口，比较存储判定与当前阈值：只有布尔结论不同才标记“标准漂移”，且不会写回任何记录。本地模拟模式没有服务端审计，该页会显示说明。

设备档案（M3/M5/M7/M8）可以覆盖单个节点的验收阈值，例如 M3 的 T1 阈值为 15 μs；表格中标注“设备覆盖”。任务使用的机器没有登记档案时，服务端回退 Skill 默认阈值并返回 `known: false`，界面会明确提示，不会把默认值伪装成设备策略。执行引擎只接受已注册的适配器（当前为 `simulator`），未知适配器会拒绝推进而不是回退仿真。

### 蓝图决策轨迹（远程模式）

蓝图画布下方新增只读「决策轨迹」，只在远程模式且已绑定服务端任务时出现；本地模拟没有服务端审计，不会渲染空面板。

- 每个节点卡片右上角显示最新一条测量判定：`验收通过`、`未达标`、`已升级`。只在审计返回布尔 `passed` 时标注；旧结果没有冻结判定（`evaluationSource: "derived"`）不会被显示成通过或失败。
- 面板按服务端顺序列出最近的测量与命令：测量行给出实测值、阈值、升级原因与第几次尝试；命令行给出操作者、动作与 `revision` 变化。点击条目会选中对应蓝图节点。
- 面板头部的「重放校验」按需调用 replay 接口。未发现漂移时明确显示“一致”；存在漂移时，节点徽标切换为 `标准漂移`，列表给出“记录 通过 → 重放 未通过”的对照。重放只读，不会改变审计中的冻结判定。
- 面板在节点完成、重试、工作流状态变化或事件数量变化时重新拉取审计；运行中的进度更新不会触发请求。服务端返回新的测量后，旧的重放结果会失效并被清除。

远程适配器通过 `EventSource` 订阅 `/api/v1/calibration/stream?token=…`（浏览器无法为 SSE 设置请求头，因此 token 放在查询参数中；生产部署应改用短期一次性流令牌或 HttpOnly Cookie）。断线后浏览器自动重连并携带 `Last-Event-ID`；超出服务端保留窗口时服务端会重新下发完整快照。

### 生产部署（QuantaMind P7）

- `docker compose up -d --build` 会启动 Postgres 16、执行 `alembic upgrade head`，再启动 API；后端容器健康检查使用 `/api/v1/ready`。
- 必填环境变量：`QUANTAMIND_JWT_SECRET`、`QUANTAMIND_DB_PASSWORD`。在 `QUANTAMIND_ENV=production` 下，缺少显式 secret、`QUANTAMIND_DEV_AUTH=true` 或未配置持久数据库时，服务拒绝启动。
- 每个响应带 `X-Request-ID`；`QUANTAMIND_LOG_JSON=true` 时日志为每行一个 JSON 对象，便于采集。
- 备份：`scripts/backup-quantamind-db.ps1 -DatabaseUrl ... -Keep 14`；压测：`python scripts/loadtest_calibration.py --base-url ... --path /api/v1/calibration/health`。
- 剩余部署项：TLS 反代、恢复演练、容量结论。SSE 仍使用查询参数 token，见下方安全边界。

### 安全边界

- 演示账号（`leader` / `Leader123!`、`operator` / `Operator123!`）与后端种子用户仅用于本地开发，部署前必须更换密码、轮换 `QUANTAMIND_JWT_SECRET`，并设置 `QUANTAMIND_DEV_AUTH=false` 关闭无密码角色直登。
- 访问令牌存放在 sessionStorage，任何 XSS 都能读取。生产接入建议改为 HttpOnly + Secure Cookie 并增加 CSRF 防护；当前实现只适合内网开发。
- SSE 的访问令牌放在查询参数中（EventSource 无法设置 Authorization 头），可能出现在反向代理访问日志里。生产环境应签发短期、一次性的流令牌。
- `docs/arclight/arclight/server/.env` 可能包含凭据，分发仓库前需要检查并移除。
- 当前测量结果仍由后端模拟生成（记录中带 `simulated: true`）；接入真实设备前不得把界面指标当作硬件性能依据。

## v1.5 服务端测量、令牌生命周期与账号管理

### 服务端测量

远程模式下 `useRemoteMeasurements(workflow, enabled)` 在任务可见或节点完成/重试时调用：

```text
GET /api/v1/calibration/workflows/{workflowId}/results
→ { items: [{ id, workflowId, nodeId, attempt, stage, metrics, rawRef, createdAt }] }
```

`metrics` 中可能包含：

| 字段 | 说明 |
| --- | --- |
| `fidelity` | 0–1 小数或百分比；≤1.5 视为小数并乘 100 |
| `parameters[qubit].t1Us` / `t2Us` / `f01Ghz` / `readoutGhz` | 参数侧栏与报告使用的比特参数 |
| `evaluation.passed` / `evaluation.reason` | 服务端冻结的验收判定与原因 |
| `simulated` / `adapter` | 是否模拟与适配器名称，用于界面标注 |

前端归一化规则（`src/lib/remoteMeasurements.ts`）：

- 每个节点只保留 `attempt` 最大的一条；节点当前 `attempt` 与结果不一致时视为过期，不显示，避免重试后误展示上一轮数值。
- 结果只在节点进入 `completed` 等已产生测量的状态后叠加；`preview` 阶段不使用服务端值。
- `evaluation.passed` 一旦存在，优先于前端演示阈值（读取节点 98%）；没有该字段时才回退到仿真阈值判断。
- 曲线仍由前端函数生成，只替换读数与判定；报告标注「服务端测量报告 / 阶段报告·服务端测量」，不会声称曲线是原始采集波形。
- 拉取失败保留上次快照；缓存最多保留 32 个任务，按任务 ID 淘汰。

服务端新增测量时，应先保证结果已持久化再推送 `workflow.updated`；前端不依赖事件顺序，按 `attempt` 与状态判断是否可见。

### 参数下发与回读

同一次同步还会请求每个节点的最新参数快照：

```text
GET /api/v1/calibration/workflows/{workflowId}/parameters
→ { items: [{ nodeId, attempt, stage, capturedAt, fidelity,
              parameters, publication, publicationVerified }] }
```

- `parameters[qubit]` 是服务端记录的参数值；参数侧栏的「服务端参数与指标」优先展示它们，未知数值字段以原始键名保留。
- `publication` 是适配器写入控制器后的回读校验：`{ adapter, controller, status, verified, requested, readback, mismatches, publishedAt }`。`publication` 为 `null` 表示该适配器不下发参数。
- `mismatches[].reason`：`value_mismatch`、`qubit_missing`、`readback_missing`、`readback_unavailable`。`publicationVerified === false` 时后端已把节点升级为 `attention`，前端在参数侧栏显示「回读不一致」及逐项差异，并注明即使质量指标达标也不视为已接受。
- 归一化规则与结果一致：节点当前 `attempt` 与快照不一致时不展示，避免重试后显示上一轮的旧回读；缓存随 `clearWorkflowResults()` 一并清理。
- 两个接口独立 settle：结果成功而参数接口失败时，指标卡保留，参数侧栏不展示回读信息，指标区标注「回读同步失败」。

### 令牌生命周期

| 接口 | 语义 |
| --- | --- |
| `POST /api/v1/auth/login` | 返回 `access_token`、`refresh_token`（可选）、用户与角色 |
| `POST /api/v1/auth/refresh` | 请求体 `{refreshToken}`；返回新的 access + refresh，旧 refresh 在账号/版本校验后失效 |
| `POST /api/v1/auth/logout` | 提升账号 `token_version`，所有已签发 access/refresh 立即作废；无需令牌黑名单表 |
| `POST /api/v1/auth/password` | 校验当前密码；改密后同样提升 `token_version` 并返回新令牌 |

access 与 refresh 都携带 `typ`、`tv`（token version）与 `jti`；`tv` 与账号当前版本不一致即拒绝。禁用账号、改角色、管理员重置密码都会提升版本，因此权限变更在下一次请求生效，而不是等令牌过期。刷新时重新读取账号，已禁用账号无法续期。前端只在收到 401 时刷新一次，不把其他错误当作令牌失效。

### 账号管理 API

仅持有 `calibration:manage_users` 的负责人可调用（前缀 `/api/v1/users`）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/users` | 账号列表 |
| POST | `/api/v1/users` | 创建：`username`、`password`、`displayName`、`role`（`calibration_leader` / `calibration_operator`） |
| PATCH | `/api/v1/users/{userId}` | 修改 `displayName`、`role`、`isActive` |
| POST | `/api/v1/users/{userId}/password` | 管理员重置密码 |

服务端拒绝负责人禁用或降级自己，并保证至少保留一个可用负责人。生产环境（`QUANTAMIND_ENV=production`）不写演示账号；`QUANTAMIND_SEED_DEMO_USERS=false` 可显式关闭，存在演示口令时生产模式拒绝启动。

### 真机控制语义

- `QUANTAMIND_DEVICE_CONTROL`（默认 `dry_run`）是部署级开关：真实适配器在 dry-run 下拒绝 `start`，界面显示拒绝原因。
- `live` 模式下，`POST .../actions` 的 `start` 仍需 `confirm: true`；缺少时返回可解释错误，不向硬件下发。
- 终止真实设备上的任务同样要求 `confirm: true`，表示操作者已确认硬件停止；设备返回的取消回执不能替代该确认。确认会写入动作日志（`metadata.confirmed`）。
- `start` 前校验设备能力：实验类型和比特数超出设备档案时拒绝启动，不回退仿真；未知适配器同样拒绝推进。
- 模拟适配器（`simulated: true`）不受上述确认限制，便于本地和 CI 使用。

### 测试与 CI

```powershell
npm test
# 可选：对运行中的后端执行跨仓 E2E
$env:QUANTAMIND_E2E_BASE_URL = 'http://127.0.0.1:18789'
node --experimental-strip-types --test qa/e2e-remote.test.ts
```

`qa/e2e-remote.test.ts` 覆盖登录 → `/auth/me` → 创建任务 → 启动 → 轮询结果 → 刷新令牌 → 读取权威 revision → 终止 → 注销 → 匿名访问，需要后端启用执行引擎（`QUANTAMIND_CALIBRATION_WORKER=true`）；未设置地址时自动跳过，`npm test` 不受影响。可选 `QUANTAMIND_E2E_USERNAME` / `QUANTAMIND_E2E_PASSWORD` 指定账号。

本仓库 `.github/workflows/ci.yml` 的 `backend-e2e` job 默认跳过，设置仓库变量 `QUANTAMIND_BACKEND_REPOSITORY`（及私有仓库所需的 `QUANTAMIND_BACKEND_TOKEN`）后自动启动后端并运行同一测试；后端仓库的 `frontend-contract` job 通过 `QUANTAMIND_FRONTEND_REPOSITORY` 反向检出前端并运行 `npm test`。

### 理想目标值（后端已下发）

前端 v1.6 在参数侧栏、节点指标卡和决策页同时展示「理想目标 / 验收下限 / 实测值」。理想目标与验收下限是两类独立数据，后端不复用 `threshold` 表示理想值：

- Skill v1.1.0 的 `SkillStep.target`：`{ metric, ideal, unit, direction, tolerance, description }`，例如 `fidelity 0.995 ratio higher`、`parameters.*.t1Us 60 μs higher`、`driveGhz 4.0 ±0.05 GHz band`。只有具备质量指标的步骤才有 `target`；频率扫描等控制步骤为 `null`。
- 设备档案 v1.1.0 的 `targetOverrides` / `fidelityTarget` 按与 `acceptanceOverrides` / `fidelityCriterion` 相同的优先级覆盖目标；`fidelityTarget` 只细化本身已有目标的步骤。
- `/workflows/{id}/skill` 的 `effectiveSteps[].effectiveTarget` / `targetSource` 返回当前设备实际采用的目标与来源，`targets` 提供扁平索引。
- 每条评估记录冻结 `evaluation.target` 与 `evaluation.targetSource`，与 `criterion` 并列；审计、重放与结果接口都可追溯当时的目标。

前端 `src/lib/remoteTargets.ts` 缓存该绑定，`src/lib/metricComparison.ts` 的解析顺序为：冻结目标 → 当前绑定目标 → `DEMO_METRIC_TARGETS` 演示兜底。验收下限同理：冻结 `criterion` → 绑定 `effectiveAcceptance` → 演示下限（仅模拟测量）。服务端结果若既无冻结标准、绑定也未加载，显示「未设验收下限」，不会把演示阈值冒充为设备标准。`targetSource` 会区分「设备设计目标」「Skill 设计目标」「演示参考目标」。

### 已知限制

- 远程登录已使用后端账号目录，但网页只映射 `calibration_leader` / `calibration_operator` 两种角色；其他后端角色（如 `project-manager`）会被明确拒绝，需要先扩展界面的角色模型。账号只能停用、不能删除；离开的成员应停用并重置密码。
- 角色调整与停用在服务端立即生效；网页展示的角色仍是登录时保存的值，需重新登录后才更新。越权操作仍会被服务端拒绝，因此这只影响界面提示，不影响权限边界。
- 浏览器环境无法安装 Playwright 时，跨仓验证使用上述 HTTP E2E；它不覆盖渲染层交互。
- 服务端测量仍由模拟适配器生成；图表曲线由前端合成，只有读数和判定来自服务端记录。
- 前端绑定的目标/验收标准在每次节点状态或 attempt 变化时刷新；Skill 或设备档案在服务端更新而工作流状态不变时，可点蓝图页的「刷新绑定」立即重新读取，否则在下一次状态变化后采用新值。历史结果仍以冻结记录为准。
