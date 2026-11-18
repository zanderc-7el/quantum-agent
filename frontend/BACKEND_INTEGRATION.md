# 后端对接说明

当前应用是可操作的纯前端演示。所有任务、实验结果、阈值、曲线、审批、异常和运行时间均为本地模拟，没有连接量子硬件或真实后端。以下接口是为双方讨论准备的**拟定合同**，不是已经存在、已验证可调用的 API。

## 前端模块与替换入口

- `src/types.ts`：界面消费的任务、节点、策略和状态类型。
- `src/lib/simulator.ts`：无浏览器依赖的状态转换；初始化、创建、时钟推进、操作和本地持久化。
- `src/lib/gateway.ts`：`WorkflowGateway` 适配器接口；默认 `createGateway()` 返回 `mode: 'mock'`，传入适配器时返回该适配器。
- `createMockGateway({ load, save })` 可注入存储，`advance(seconds)` 显式推进演示，不创建隐藏后台定时器。`subscribe` 返回取消订阅函数。
- `src/lib/useWorkflowStore.ts`：界面数据入口，默认经Gateway加载、订阅和操作任务；可传入 `useWorkflowStore(remoteAdapter)`。返回 `state/create/act/policy/resetDemo/advance`，以及 `mode/error` 供界面展示加载错误和模式。
- `src/App.tsx`：已接入上述 Store，任务操作不再直接调用模拟器。应用入口可传入 `<App gateway={adapter} />`，其中 `adapter` 必须实现完整 `WorkflowGateway`；无需向页面组件逐一传递 HTTP 方法。
- `src/lib/measurements.ts`：蓝图指标、节点详情和报告共用的模拟数据选择器，仍需在正式联调时接入服务端测量结果。`ChatReport` 的规则助手也不属于真实模型服务。

界面目前统一调用 `useWorkflowStore`，后续新增操作也应保持这一入口。`resetDemo()` 只对mock适配器开放；真实模式下 `advance()` 不推进任务。真实后端模式初次加载时为空状态，不以演示种子伪装真实数据；列表或订阅错误通过 `error` 返回。较晚到达的初始列表不会覆盖较新的订阅快照。仅新增 HTTP 文件或注入任务适配器不代表测量数据、报告和模型服务已完成联调。

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

本轮仅提供前端演示，未新增真实HTTP端点。`auth.ts` 使用固定公开账号，`roleGateway.ts` 演示权限；正式接入须替换账号目录和sessionStorage会话为服务端会话。建议 `AuthGateway` 提供 `login(username,password)→user`、`currentUser()→user|null`、`logout()→void`；账号角色由服务端返回，使用HttpOnly/Secure Cookie并由服务端校验每次读写，不可信任客户端role/ownerId。不要把公开演示密码用于真实服务。

`Workflow.ownerId?:string` 是稳定账号标识，`owner` 仅展示。旧version:1任务保留可读，没有ownerId时仅负责人可操作。创建时服务端根据会话赋ownerId；工作人员创建强制threshold，审批/attention状态重试/策略更改/导入/重置仅负责人。人员分配、真实账号CRUD与密码重置由后端提供，当前界面仅列示演示成员。

本机chatPersistence采用IndexedDB `qcal-local-conversations-v1`，conversation/attachments/metadata分表，按workflowId关联；写入事务共用100MB原附件门限，reset epoch阻止旧操作回写。接口有loadChatSnapshot、saveChatSnapshot、clearChatStorage、readChatStorageStats及subscribeChatStorage。正式存储适配器需提供等价持久化语义、失败反馈、上传下载和按团队访问控制，数据迁移需明确用户选择，不能自动上传旧本地资料。

实时参数由nodeParameters和measurements的模拟选择器产生。真实接口需返回节点ID、单调revision、采集时间、参数key/value/unit、运行进度/attempt、结果阶段和数据来源；UI通过WorkflowGateway.subscribe更新，拒绝乱序快照。不要将当前预设拟合值当作真机结果。

## v1.3 消费界面与完整实验 CSV

- ParameterDrawer接收最新Workflow快照，selectDrawerNode跟随活动节点或保留固定选择；硬件结果接入后沿用明确的结果阶段和单位，不能将未执行预览填为最终结果。
- buildExperimentCsv(workflows, exportedAt)为无写入纯导出函数，须传全部可查看工作流；返回filename/content/mimeType及任务、节点、事件计数，含BOM、CRLF与电子表格公式前缀防护。
- 导出使用现存节点快照、配置、模拟结果和有界事件。真实后端须保留runId/attemptId、起止时间、输入快照、原始测量附件引用及完整事件；批量导出应分页穷尽服务端历史。当前接口未提供逐次测量历史。
- ChatReport.canEditConversation由任务归属决定：负责人可编辑，工作人员仅可编辑本人任务；只读可查看/下载报告附件，不能修改、上传、清空。服务端对会话和附件写入接口须独立检查同一权限。
