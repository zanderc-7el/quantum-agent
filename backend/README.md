# 实验服务：确定性模拟切片

`npm start` 在 `127.0.0.1:5290` 启动零依赖的内部实验 API。服务运行 Q12 读出谐振频率扫描与 F01 频谱的确定性模拟，**不连接 32 所接口或真机**。运行状态与逐次采样事件保存在 `data/state.json`；服务重启时未完成运行标记为失败，避免将中断伪装为完成。

`node --test test/*.test.mjs` 运行合同、互斥、幂等和停止语义测试。前端的 `#api` 页面直接访问本机 5290 端口；启动顺序为先在 `backend` 目录运行 `node src/server.mjs`，再按前端 README 启动页面。生产部署尚未配置。

示例流程：`GET /api/v1/device-profile` → `POST /api/v1/plans` → `POST /api/v1/plans/{id}/runs` → `GET /api/v1/runs/{id}` 和 `/events?after=0` → 可选 `POST /stop`。写请求要求 `Idempotency-Key`。参数仅包含 `centerHz`、`spanHz`、`points`，用于模拟，暂不包含功率、采集次数和积分窗；这些字段需经现场设备合同确认后引入。

当前服务仅绑定本机，没有正式身份认证、设备控制、数据库、SSE 或真机参数发布能力。读出 S21 Skill 的质量门和决策仅用于模拟；正常结果只生成候选，边界结果可建议扩大扫描。`POST /api/v1/runs/{id}/follow-up-plan` 从完整测量证据创建有来源关联的复扫计划，最多四层；用户再显式启动该计划。它是下一阶段合同联调的可运行基础，不可部署为真实测控服务。

P1/P2 模拟原型还包含：候选参数的模拟审批、版本发布/回读/回滚；Q12 与 Q13 的 26 节点 CZ 规划图及共享资源检查；无模型的目标到配方预览。以上均不连接仪器，审批人未经服务端认证，完整单/双比特流程尚未可执行。详见 `docs/超导量子测控智能体实施方案.md` 第 19 节。

F01 模拟演练顺序：先完成正常 S21 扫描，生成读出候选、模拟审批并发布；再创建 `f01_spectroscopy` 计划，附上当前 `sourceReadoutRevision`。计划记录已发布读出值、来源运行和测量 SHA-256；版本改变后旧计划不能启动。频谱采用离散峰值示例，不验证真实 0→1 跃迁，结果不能发布为频率参数。

假设测控接口已接通时，服务经 `simulated-controller-v1` 适配器执行采样块、停止确认、参数写入和回读。`GET /api/v1/device-adapter` 明确返回 `hardwareConnected: false`；采样事件保存模拟控制器回执。可在启动前设置 `QCAL_SIM_FAULT=acquisition_interrupt`、`stop_unconfirmed` 或 `readback_mismatch` 分别演练采集中断、停止未确认和回读不一致。停止未确认时资源保持占用，需用新的幂等键重试停止；回读不一致时不发布新参数版本。这些故障仅由模拟适配器产生，不表示已验证真实驱动行为。
