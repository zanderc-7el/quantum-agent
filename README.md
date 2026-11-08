# 超导量子自进化测控平台

本仓库保存网页前端源码、软件建设方案和直接相关的校准及设计参考资料。前端源码版本为 **v1.3.1**，整理日期为 **2026-10-26**。

## 仓库目录

| 目录 | 内容 |
| --- | --- |
| [frontend/](frontend/) | React + TypeScript + Vite 前端完整源码、锁定依赖、测试与后端接入说明 |
| [docs/proposal/](docs/proposal/) | 《超导量子自进化测控平台建设方案》Word 版，包含详细设计章节 |
| [docs/超导量子测控智能体实施方案.md](docs/超导量子测控智能体实施方案.md) | 面向真实测控接入、DAG 校准和智能体闭环的分阶段实施与验收方案 |
| [backend/](backend/) | Q12 S21/F01 确定性服务端模拟切片；版本化蓝图约束、运行与事件持久化、模拟参数发布，不连接真机 |
| [contracts/experiment-v0.1/](contracts/experiment-v0.1/) | 首个模拟实验的内部请求合同 |
| [references/calibration/](references/calibration/) | 单比特、双比特实验流程及校准手册 |
| [references/design/](references/design/) | 建设方案格式、测控分系统与软件详细设计参考 |
| [references/feedback/](references/feedback/) | 功能与界面反馈 |
| [references/README.md](references/README.md) | 资料清单及用途 |
| [SHA256SUMS](SHA256SUMS) | 入库文件完整性校验值 |

## 本地运行

使用 Node.js 22.12 或更高版本：

```sh
cd frontend
npm ci
npm test
npm run build
npm start
```

打开 http://127.0.0.1:5286 。三个视图分别为 `#chat`（对话式 Agent）、`#blueprint`（蓝图版工作流）和 `#dashboard`（工作看台）。

当前支持单比特和双比特模拟流程、明暗主题、实时参数代码侧栏、团队负责人/工作人员演示身份、本浏览器数据持久化，以及报告和实验 CSV 导出。公开演示账号见 [前端使用说明](frontend/README.md)。

## 当前边界与后端接入

这是可完整试用的前端演示，任务、测量结果、曲线和运行时间均为模拟数据。未连接真实量子设备、正式认证、共享数据库或模型服务。数据保存在当前浏览器；演示身份不构成服务端安全隔离。

本机实验服务可独立运行 Q12 S21 和 F01 模拟，并在非远程模式的 `#api` 页展示结果。先在 `backend` 运行 `node src/server.mjs`，再运行前端；服务监听 `127.0.0.1:5290`。它只用于实验合同和故障演练，具备模拟 DAG 元数据、蓝图版本校验、候选审批与模拟参数版本，但不连接真机，也没有正式认证或物理拟合。远程模式下 `#api` 不再直连该服务；工作流由 QuantaMind 校准 API 管理。

架构升级与剩余真机门槛见 [架构实施记录](docs/架构实施记录.md)。

后端同事可按 [BACKEND_INTEGRATION.md](frontend/BACKEND_INTEGRATION.md) 实现 `WorkflowGateway` 并接入权威测量数据。接口地址、服务端权限、事件顺序和正式实验参数需要联调确认。建设方案中待建能力与参考资料中的技术选型，不等同于当前软件已实现能力。

本次归档不包含依赖目录、构建输出、渲染缓存、服务器凭据或本机运行日志。参考资料保留原作者和原用途，仓库不另行授予其再分发许可。
