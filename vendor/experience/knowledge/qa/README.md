# 本版文档验收索引

> **2026-11-16增补**：本页以下611项检查及冻结哈希是10月24日历史快照。经验更新后的校验另存 `experience_update_20261116.json` 与 `experience_update_20261116_docs.json`，不改写原验收为本次运行结果。

此目录只记录原流程 15 步版的文档验收。正文冻结范围为 18 份 Markdown，共 60,451 字符；不把旧版 QA、测试数量或运行成功结论继承为本版结果。

- `document_acceptance.json`：611 项静态检查，覆盖相对链接、表格、15+1 归属、44 个内部方法、原科学公式与案例数值保留、当前能力边界。
- `clean_repeat_acceptance.json`：两个新的临时文档副本和同一副本重复检查均通过。资源与配置作为只读引用；这不是全包运行冷验收。
- `document_freeze_manifest.json`：18 份正文的 SHA256；明确排除本清单自身及 QA 记录。
- `catalog_domain_review.json`：独立领域检查。d03 主方法曾误指设置步骤，作者修为 `tq_cz_chevron` 后重新核对；其余已检查角色边界无阻断项。
- `style_acceptance.json`、`style_review.json`：技术报告措辞检查通过；唯一新闻式时序规则命中不适用于以 15 步结构开篇的技术规范，已记录理由。
- `validate_docs.py`：可重复执行的静态检查器；`report.md` 是本次 18 份正文合并文本，`baseline_previous_edition.md` 是用于保留核对的旧版参照。

静态校验曾发现参数依赖表格列分隔缺失，已修正并重新通过。完整原流程执行、硬件绑定、联网模型调用和浏览器视觉检查不属于此文档验收，也未据此宣称完成。

原 QA 已归档到 `../../resources/previous_edition/reorganization_archive/knowledge_qa/`；全包运行以 `../../output/integration_acceptance.json` 和包旁本版归档验收报告为准。

## 2026-11-16 152511 续补校验

当前续补检查见 `experience_update_20261116_152511.json` 与同前缀文档校验报告。前述 `experience_update_20261116.json` 及其哈希是本日较早版本的历史快照；最新文件清单以根目录 MANIFEST.json 为准。本轮仅做文档、提示词内容、链接和保存完整性检查，未重新运行实机或全流程验收。


## 2026-11-17 实测经验归档校验

`experience_update_20261117.json` 记录本轮知识、证据索引、SHA256、历史保留、执行文件不变和门户检查。`experience_update_20261117_docs.json` 与 `experience_update_20261117_catalog.json` 分别是实际重跑的文档和角色结构检查；原 10 月与 11 月 16 日报告不覆盖或改写。本轮不连接仪器、不跑实验，未重新验收整包运行或硬件绑定。

## 2026-11-17 Q11 最终补充校验

当前校验为 `experience_update_20261117_q11_final.json`，文档与目录报告分别为 `experience_update_20261117_q11_final_docs.json`、`experience_update_20261117_q11_final_catalog.json`。上面的初始 11 月 17 日报告保留不改；新报告检查最终短版 RB、相干和 Rabi 的归档哈希、正文入口、历史保留、代码不变及门户。科学审查 2181/2181 和 128/128 是来源中的独立审计，未冒称本轮文档更新重新执行该审计。静态门户不等于浏览器视觉验收或实机全流程验收。

## 2026-11-17 Q18/Q19 最终补充校验

最新报告为 `experience_update_20261117_q18_q19_final.json`，对应 `_docs.json` 和 `_catalog.json`。检查新增对象案例、四阶段摄入清单、最终 RB 更正来源、历史 Q11 证据保留、门户和 15+1/44 结构。独立 Q18 RB 科学审查2721项与其更正增补是归档来源，未由本轮文档操作重新执行。1751个保护文件仅指工程包；原F8已有哈希差异、作者未知、恢复动作不改原件等不同范围分别记录。此前 QA 不回写为新版本通过结果。
