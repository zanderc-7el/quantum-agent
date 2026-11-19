# 本地真实服务前端

默认入口连接同源 `/api`，使用后端会话、真实任务状态和 SSE。旧演示通过 `?demo=1` 显式启用，不能作为真机或历史实测证据。

## 启动

生产：`npm ci`、`npm run build`，由根项目 Python 服务托管 `frontend/dist`。

开发：先启动根项目 API（127.0.0.1:8765），再运行 `npm run dev`（127.0.0.1:5286）。Vite 将 `/api` 代理至后端；开发 Origin 需被后端允许。正式运行建议使用同源 Python 地址。

## 实际路径

- `src/main.tsx`：默认 `RemoteApp`，演示入口独立。
- `src/RemoteApp.tsx`：沿用原壳和主题，工作看台、任务创建、蓝图、连接状态与后端会话。
- `src/lib/remoteGateway.ts`：REST/SSE、会话 token、operationId、expectedRevision、事件游标与防旧响应覆盖。
- `src/components/RemotePanels.tsx`：动态蓝图、实测产物和数值曲线、attempt 历史、规则和决策、后端聊天与报告。
- `src/components/remote.css`：在原导航/蓝图/节点/聊天视觉结构上补充的样式。

真实路径不使用 `simulator.ts`、`measurements.ts`、`nodeParameters.ts` 的模拟结果。数据不存在时显示等待数据，模型未配置时显示后端 `waiting_model`，历史记录标注 `historical`，不宣称新采集。资源 ID 原样传递，不将 Q01 改为 Q1。

## 行为

创建任务默认历史回放。真实任务选择后仍由后端执行器预检、参数边界和设备状态决定是否能够运行。点击开始才发送 start。

当前步骤与选中查看步骤分开：节点操作使用当前执行节点、attempt 和 revision；回退指定目标节点，后端计算依赖失效并保留旧尝试。接受结果不修改质量结论。

所有图片通过已登记 artifact ID 获取；文件路径只作为来源文字展示。图文报告使用后端 HTML 报告。聊天由 `/api/chat` 返回消息及模型状态；浏览器只缓存当前页面会话显示，长期聊天记录由后端管理。

会员入口位于左下角。`MembershipAccount.tsx` 使用 `/api/account/status`、`models`、`send-code`、`login`、`connect`、`logout`、`test`。登录默认 `useForAgent=true`，按钮为“登录并使用 Ziyor 额度”；独立模型连接只在高级设置中显式选择。会员登录状态与实际模型 provider 分别展示，未知积分显示“暂未获取”，真实零积分保留为 0。退出不自动切回独立模型。

打开会员面板只读取状态与模型目录；验证码、登录、退出和模型验证分别由用户按钮触发。验证按钮明确会发送一次短请求。账号写请求继承本地 token 与 operationId，401 刷新重试不生成新操作 ID。手机号与验证码不写入浏览器存储，会员密钥留在后端。

账户状态每 15 秒同步，并在窗口重新获得焦点、页面可见或 SSE 恢复时读取；重叠请求合并，旧响应不能覆盖较新的账户操作。这个同步只调用状态 GET，不触发模型连接或推理。

设备状态卡将同一 PID 的网络连接与共享锁合并列出。连接表检查不会主动连接仪器，默认执行器开关也不等于当前任务的执行状态。任务采集提示依据匹配的 attempt/operation 回执，不能用相同 PID 推断设备归属。

实时原始图保留每个采集点的 I/Q、坐标、文件及 SHA，独立于正式拟合和验收。对已核验的 `arclight-private-f8-q11-v2`，Ramsey 的等待时间与相位、Echo 的两段等待时间与相位属于原生 zip 扫描；显示时按同一采集曲线分组，仍分开 job、capture 和其他扫描条件。Echo 横轴标注原生等待时长，不将它冒充总自由演化时间。其他执行器与方法继续按全部剩余扫描维度分组。

## 验证

`npm run build`；`npm test`。`qa/remote-gateway.test.ts` 覆盖 token/版本/幂等操作 ID、原样资源 ID、SSE 顺序与重连游标、旧列表响应、失败不冒充成功、未连接模型状态和产物 URL 编码。401 只刷新会话并重试一次，同一个 operationId 和请求体不变。

`node qa/ui-replay-smoke.cjs` 使用官方运行时捆绑 Playwright 和本机 Chrome，以隔离 headless 会话测试 8765。可用 `QCAL_PLAYWRIGHT_ROOT` 和 `QCAL_BROWSER_PATH` 覆盖工具路径。仅创建名称含“界面验收”的 Q11/Q18 历史回放任务，不创建或启动 real 任务；截图及控制台/网络验收记录写入 `qa-artifacts/`。

`node qa/ui-account-fakehttp.cjs` 在临时 loopback 端口托管已构建前端，完整拦截所有 API，以显式 fakeHTTP 账户/模型/额度测试交互，无需运行生产服务，不发送真实短信、不登录真实会员、不消耗真实会员额度，也不创建任务。其截图文件名含 `fakehttp`，不能用作真实会员连接证据。`qa/account.test.ts` 校验积分未知/零值、纯读取不会触发推理、账户写操作沿用相同 operationId 重试，以及目录默认模型选择。
