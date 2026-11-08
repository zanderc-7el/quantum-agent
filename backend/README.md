# 实验服务：确定性模拟切片

`npm start` 在 `127.0.0.1:5290` 启动零依赖的内部实验 API。服务运行 Q12 读出谐振频率扫描与 F01 频谱的确定性模拟，**不连接 32 所接口或真机**。运行状态与逐次采样事件保存在 `data/state.json`；服务重启时未完成运行标记为失败，避免将中断伪装为完成。

`npm test` 运行合同、互斥、幂等和停止语义测试。前端的 `#api` 页面直接访问本机 5290 端口；启动顺序为先在 `backend` 目录运行 `node --experimental-strip-types src/server.mjs`，再按前端 README 启动页面。生产部署尚未配置。

示例流程：`GET /api/v1/device-profile` → `POST /api/v1/plans` → `POST /api/v1/plans/{id}/runs` → `GET /api/v1/runs/{id}` 和 `/events?after=0` → 可选 `POST /stop`。写请求要求 `Idempotency-Key`。参数仅包含 `centerHz`、`spanHz`、`points`，用于模拟，暂不包含功率、采集次数和积分窗；这些字段需经现场设备合同确认后引入。

当前服务仅绑定本机，没有正式身份认证、设备控制、数据库、SSE 或真机参数发布能力。读出 S21 Skill 的质量门和决策仅用于模拟；正常结果只生成候选，边界结果可建议扩大扫描。`POST /api/v1/runs/{id}/follow-up-plan` 从完整测量证据创建有来源关联的复扫计划，最多四层；用户再显式启动该计划。它是下一阶段合同联调的可运行基础，不可部署为真实测控服务。

P1/P2 模拟原型还包含：候选参数的模拟审批、版本发布/回读/回滚；Q12 与 Q13 的 26 节点 CZ 规划图及共享资源检查；无模型的目标到配方预览。以上均不连接仪器，审批人未经服务端认证，完整单/双比特流程尚未可执行。详见 `docs/超导量子测控智能体实施方案.md` 第 19 节。

F01 模拟演练顺序：先完成正常 S21 扫描，生成读出候选、模拟审批并发布；再创建 `f01_spectroscopy` 计划，附上当前 `sourceReadoutRevision`。计划记录已发布读出值、来源运行和测量 SHA-256；版本改变后旧计划不能启动。频谱采用离散峰值示例，不验证真实 0→1 跃迁，结果不能发布为频率参数。

假设测控接口已接通时，服务经 `simulated-controller-v1` 适配器执行采样块、停止确认、参数写入和回读。`GET /api/v1/device-adapter` 明确返回 `hardwareConnected: false`；采样事件保存模拟控制器回执。可在启动前设置 `QCAL_SIM_FAULT=acquisition_interrupt`、`stop_unconfirmed` 或 `readback_mismatch` 分别演练采集中断、停止未确认和回读不一致。停止未确认时资源保持占用，需用新的幂等键重试停止；回读不一致时不发布新参数版本。这些故障仅由模拟适配器产生，不表示已验证真实驱动行为。

## Docker 共享模拟工作流

仓库根目录 `docker compose up --build` 同时运行网页和此 Node 服务。仅 nginx 的 `127.0.0.1:5286` 对宿主机开放；服务位于 Docker 内部网络。`frontend/nginx.conf` 将 `/api/` 和 `/health` 代理到服务，`demo-data` 卷保存实验与工作流状态。全部是模拟，无身份认证、设备或正式权限隔离。不要将端口开放到公网，也不要放入真实实验数据。

共享工作流 API 直接复用 `frontend/src/lib/simulator.ts`，不是另一份状态机，不依赖独立的 QuantaMind 仓库。运行需 Node 22.12+ 及 `--experimental-strip-types`，已在 `npm start` 中配置。每秒由服务端推进一次；浏览器只轮询，不推进时钟。服务重启后，运行中的工作流暂停，需明确恢复。数据写入失败不确认变更；无效持久化数据会阻止启动，而不是清空覆盖。

- `GET /api/demo/workflows`：`{ workflows: Workflow[], dataSource: "simulated", hardwareConnected: false, authority: "demo-server" }`。
- `POST /api/demo/workflows`：输入 `CreateWorkflowInput`，可附 `ownerId`、`owner` 作为未经认证的演示标注；返回 201 单工作流响应。
- `GET /api/demo/workflows/:id`：单工作流响应。
- `POST /api/demo/workflows/:id/actions`：`{ action: "start" | "pause" | "resume" | "approve" | "retry" | "abort" }`。
- `POST /api/demo/workflows/:id/policy`：`{ policy: "strict" | "threshold" | "autopilot" }`。

单工作流响应为 `{ workflow: Workflow, dataSource: "simulated", hardwareConnected: false, authority: "demo-server" }`。错误采用现有 `{ error: { code, message, retryable } }` 结构，无效输入 400、未找到 404、容量超限 409。有效但不适用于当前状态的操作沿用原模拟器的幂等空操作行为。列表是共享工作区，不按传入的演示所有者提供安全隔离。创建请求不支持幂等键，遇到网络响应不确定应先刷新列表，不要自动重试创建。

`QCAL_HOST` 默认为 `127.0.0.1`，Docker 内为 `0.0.0.0`；`QCAL_PORT` 默认 5290。`QCAL_DATA_FILE` 保存原实验状态，`QCAL_DEMO_DATA_FILE` 保存工作流，后者默认与前者同目录的 `workflows.json`。原 `/api/v1/` 实验接口保留，独立 QuantaMind 的远程 API 并未打包或模拟冒充。

第一次启动的全新工作区会创建单比特、双比特两个未启动的演示草稿。已有持久化状态（包括空列表）不会被重置或补入示例。

宿主端口可通过 `DEMO_PORT=5287 docker compose up --build` 修改。Compose 同时设置精确的 `QCAL_ALLOWED_ORIGINS`（逗号分隔），允许该端口上的 `127.0.0.1` 和 `localhost` 页面访问，不使用通配来源，也不移除浏览器 Origin。
