# Windows 桌面源码

本目录保存 Electron 桌面壳，不随 Git 发布实验电脑的打包 EXE 或 release.json。启动与配置详见仓库根 README。

先构建 frontend，设置可用的 QCAL_PYTHON，然后在仓库根目录执行：

```powershell
npm --prefix desktop ci
npm --prefix desktop start
npm --prefix desktop run pack
```

pack 会重建 Node 运行时与项目位置配置，并生成 desktop/dist/win-unpacked。整个目录需要一起保留。缺少 release.json 时，根 start-platform.cmd 使用这一默认包；指定其他经过验证的包时，release.json 使用 schema 1、相对 executable 路径、version 和 sha256。

API 默认监听本机8765；模型默认8766，可在 config/runtime.local.json 选8767。启动应用不自动下发仪器实验。关闭桌面窗口不终止后台任务，暂停和终止使用任务操作。

会员状态、Chromium数据、日志、数据库和测控原始数据均保留在本机运行目录，不包含在源码发布中。desktop/resources由prepare:runtime重建，移机时核对项目和Python路径。
