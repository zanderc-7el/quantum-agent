# 来源与许可记录

源码由用户提供的前端、Ziyor 源码、15 步经验工程包和隔离的 F8/ArcLight 测量副本组合。发布保留来源和哈希，不改变原材料署名或将其统一重新许可。

- `vendor/ziyor/PROVENANCE.json` 记录直接使用的协议解析、工具校验和改写的受限循环。上游 package 元数据声明 Apache-2.0；提供的源码包未包含根 LICENSE 文本，该事实保留在来源记录中。
- `agent/vendor/ziyor-account/PROVENANCE.json` 记录登录、模型目录与会员协议参考；snapshot 用于溯源，不作为账户凭据导入。
- `vendor/experience/MANIFEST.json` 和根 `source_manifest.json` 记录经验工程包和原前端来源。
- ArcLight/F8 runtime 的 manifest 记录原生代码及 SHA；本次导出不改写冻结原生文件。
- `desktop/assets/node-LICENSE.txt` 保留 Node 相关许可。React、Vite、TypeScript、Electron 等按锁定版本安装，其许可随依赖分发。
- 红色报告模板和标识保留原使用背景，不单独授予再许可。

个人登录态、API 密钥、真实原始采集和本机数据库不在本次源码快照中。
