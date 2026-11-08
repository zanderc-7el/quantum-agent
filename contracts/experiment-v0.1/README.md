# 内部实验合同 v0.1

本目录冻结**确定性模拟器**的读出扫描与 F01 频谱请求结构。`readout-plan.schema.json` 和 `spectrum-plan.schema.json` 是结构合同；跨字段范围及已发布上游版本检查由服务端完成。它们不是 32 所真机接口或物理参数限值。

已实现端点：`GET /health`、`GET /api/v1/device-profile`、`GET /api/v1/skills/readout-s21`、`POST /api/v1/plans`、`GET /api/v1/plans/{id}`、`POST /api/v1/plans/{id}/runs`、`GET /api/v1/runs/{id}`、`GET /api/v1/runs/{id}/summary`、`GET /api/v1/runs/{id}/events?after=N`、`GET /api/v1/attempts/{id}/measurements`、`GET /api/v1/attempts/{id}/analysis`、`POST /api/v1/runs/{id}/stop`、`POST /api/v1/runs/{id}/follow-up-plan`。计划须带实验意图 `motivation`；写请求须有 `Idempotency-Key`。事件是可重放的 JSON 列表，尚未实现 SSE；测量产物带 SHA-256，仍保存在本机服务状态文件中。S21 Skill 决策可产生扩大扫描计划，由用户显式启动下一次模拟运行；不自动写回参数。

下一次合同修订应由实验与测控团队确认功率、平均次数、积分窗、设备拓扑、正式拟合算法、停止回执与原始数据格式后进行；不得把模拟 Profile 参数复制到真机。

模拟扩展端点：`GET /api/v1/skills/f01-spectrum`、`GET /api/v1/parameters/readout-frequency`、`GET .../readback`、`POST /api/v1/runs/{id}/parameter-candidate`、`POST /api/v1/parameter-candidates/{id}/approve|publish`、`POST /api/v1/parameters/readout-frequency/rollback`；以及 `GET /api/v1/topology`、`GET /api/v1/recipes`、`POST /api/v1/recipes/preview`、`GET /api/v1/agent/tools`、`POST /api/v1/agent/plan-preview`。F01 计划必须引用当前已发布且有 S21 测量哈希的读出参数版本；计划冻结上游版本、值、来源运行及哈希，启动时再次检查版本。F01 结果只给出模拟候选，不进入参数发布接口。参数发布与回读仅作用于本机模拟参数库；Agent 端点只预览，不执行测控命令。

`GET /api/v1/device-adapter` 返回模拟设备能力与故障场景；`/health` 同时声明 `hardwareConnected: false`。采样事件携带模拟设备回执。停止未确认保持资源占用；参数写入后回读不一致时拒绝发布新版本。此适配层用于假设真机接口已接通时的流程测试，不代表真机连接或验收。
