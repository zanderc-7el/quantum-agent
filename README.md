# 量子测控智能体平台 · Windows 桌面源码

这是 2026-11-24 的完整平台源码快照，包含 React 前端、Electron 桌面端、Python 实验控制器、Ziyor 模型服务、ArcLight/F8 适配器及 15 步经验方法库。两个仓库同步相同源码；`SOURCE_EXPORT.json` 记录文件校验值和导出范围。

Python 控制器是唯一实验状态源。会员模型提出受约束的方案，控制器检查前置条件、参数、预算、资源占用和质量规则后执行。已接入单比特读出、定频、幅度/时间 Rabi、IQ、读出优化、T1、AllXY、Ramsey、Echo、CPMG 和 RB。实时实验状态与现场验收以运行任务的本地数据库及独立复测记录为准。

源码不含本机 EXE、登录态、原始采集、数据库、日志或阶段 PPT。启动软件不会自动下发测量。

## 本次源码更新

- 新增多比特工作区、实验会话、芯片与设备部署版本、人工测量工作台和任务级操作。
- 新增文档表格与 OCR 导入、显式 ArcLight 资源绑定，以及报告结果页和停止回执的展示改进。
- 可用 `requirements-documents*.txt` 安装文档处理依赖。少部分只针对现场交接或历史采集记录的测试，在缺少对应本地夹具时会明确跳过。

- 蓝图提供当前实验浮动聊天，带入任务及所选节点；支持最小化、新对话和刷新后恢复。
- 本地归档按任务保留采集、尝试和报告记录，通过登记编号及 SHA256 关联文件；备份时需同时保留数据库与相关数据目录。
- 桌面可从当前任务快照导出 PPT，重复请求沿用同一导出任务，完成后下载并登记到本地归档。导出使用下述本地运行库配置。
- 增加受条件约束的 RB 诊断入口，以及候选采集和独立复测的联合 shots 预算预检；保留原生分析、冻结门槛和独立验证流程。

## 环境与启动

需要 Windows、Node.js 24+、npm 和 Python 3.11+。API 使用标准库及随仓库提供的经验 Runtime；真机测量另外需要与设备匹配的 NumPy/SciPy/matplotlib、VISA、驱动和测量 Python 环境。

在仓库根目录执行：

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix desktop ci
$env:QCAL_PYTHON = (Get-Command python).Source
npm --prefix desktop start
```

首次 Electron 安装有下载问题时，可执行 `npm --prefix desktop run setup:electron`。桌面通过本机服务显示界面，默认 API 为 8765、模型服务为 8766，均监听 loopback。

也可在两个终端分别运行 `node agent/src/server.mjs` 和 `python -m backend.server --agent-port 8766`，前端仍需先构建。`scripts/start.ps1` 保留实验电脑的 Python 路径，移机请核对或使用上述显式命令。

## 配置与会员

将 `config/runtime.example.json` 复制为 `config/runtime.local.json` 后按现场修改。核对适配器配置中的 Python、VISA、仪器地址、通道映射和原生来源清单。适配器默认全局关闭；实际任务还需设备交接确认。示例不能证明另一台设备已就绪。

在桌面会员入口自行登录 Ziyor；登录文件使用 Windows DPAPI 保存到本机运行目录，不在仓库中。独立模型连接参考 `agent/local.env.example`，实际 `agent/local.env` 和密钥不得提交。会员模型请求可能消耗账户额度。

`adapters/arclight_runtime/` 和 `adapters/f8_runtime/` 是隔离的原生副本。原生代码与其 SHA 清单需成套管理，不应在冻结任务中静默改动代码或门槛。移机需重新核对并独立复测；当前平台尚未接入 qoskit、网分和双比特真机流程。

模型输入过大时，仅在模型视图中对逐点摘要抽样并标明数量/索引，原始数据和验收不变。供应商可能返回超时、空正文或输出上限错误，这些状态不能当作执行了下一步实验。

## 打包桌面

```powershell
npm --prefix desktop run pack
.\start-platform.cmd
```

`pack` 生成 `desktop/dist/win-unpacked/`，需保留完整目录。构建会生成 `desktop/resources/` 内 Node 运行时和本机项目/Python 配置；移机后应重新配置/构建。

源码不带实验电脑的 `desktop/release.json`；缺省启动器使用 `desktop/dist/win-unpacked/QuantumControl.exe`。需要固定已验证包时，生成 schema 1 的 release 文件，填写相对 EXE 路径、版本和 SHA256。`scripts/start-desktop.ps1 -CheckOnly` 只检查入口，不启动设备。

## 离线检查

```powershell
npm --prefix frontend test
npm --prefix agent test
npm --prefix desktop test
node --test tests/report_pptx_model.test.mjs
python -m unittest discover -s tests -p "test_*.py"
```

完整 Python 测试中有依赖现场数学环境、历史文件或驱动的项目；缺少环境时按实际结果记录，不得将软件测试视为真机验收。

## 数据和报告

运行状态与采集结果写入本地 `data/`。刷新或重新打开界面仍使用同一任务。暂停/终止通过任务控制执行，关闭窗口不会结束后台实验；`stop-platform.cmd` 只请求 API 在无活动采集/决策/待核查回执时关闭。

桌面 PPT 导出使用 `backend/report_exports.py`、`scripts/export_reader_pptx.mjs` 和 `scripts/validate_reader_pptx.py`；红色模板已包含。将 `config/reporting.example.json` 复制为 `config/reporting.local.json`，按本机安装位置填写 `runtimeNodeModules` 和 `nodeExecutable`。其中模块目录必须包含 `@oai/artifact-tool/dist/artifact_tool.mjs`；也可用 `RUNTIME_NODE_MODULES` 提供模块目录。Artifact Tool 外部运行库未随源码打包，需先在本机安装可用版本。生成依赖现有任务快照与已登记图像，导出记录保存在 `data/report_exports/`。

旧离线 PPT 工具仍位于 `scripts/export_report_snapshot.py`、`scripts/export_report_pptx.mjs` 和 `scripts/finalize_report_pptx.mjs`，用法见 `scripts/report_assets/EXPORT_USAGE.txt`。界面同时提供 HTML/JSON 报告。

## 目录与来源

| 目录 | 内容 |
| --- | --- |
| frontend、desktop | 界面与 Windows 桌面壳 |
| backend | 实验控制、参数/证据状态、事件和接口 |
| agent | 模型循环、Ziyor 会员服务与受限工具 |
| adapters | ArcLight/F8 执行与原生分析副本 |
| knowledge、vendor/experience | 15 步方法、依赖和现场案例 |
| vendor/ziyor | 上游协议与工具校验源码 |
| tests、scripts | 离线测试、构建与报告工具 |

来源见 `THIRD_PARTY.md`、`source_manifest.json` 及各副本 provenance/manifest。本仓库不为所有来源材料重新授予统一许可证。项目不访问 `D:\qoskit-workspace`，原 F8/ArcLight 工作区不作为修改目标。
