# 已知限制与待修任务

本表保留原始twin历史审查结果，不把角色重组当算法修复。NUM-01主要影响s06/T1；NUM-02影响s07/Ramsey辅助及借用它的s02；FIT-01影响s03/Rabi和s06/T1；VAL-01由d03/d05/d06相关CZ诊断消费者关注。`resources/twin_source`保留原包，不在文档编写时修复。运行适配器若增加外部门禁，应单独说明其覆盖；不能声称原源码已修复。此前审查使用NumPy1.26.4/SciPy1.13.1的小探针，不等同本工程完整验收。

| ID | 已确认事实与触发 | 影响/待修任务 | 当前状态 |
| --- | --- | --- | --- |
| NUM-01 | analysis.py:115；t=linspace(0,100μs,121)，P=exp(−t/200μs)，min(P)=0.60653作为offset初值超过上界0.5，报x0 infeasible | 可行真参数仍被拒绝；给可行内部初值/重参数化，并验证短扫描可识别性。内存夹紧初值后原函数恢复T1≈200μs，RMSE≈7.4e−12 | 原源码未修 |
| NUM-02 | analysis.py:130–135；169点0–42μs、真实1.956MHz<1.960MHz约束上界，FFT初值1.964497MHz越界 | 初值映射应在可行域内；加入正负频率边界fixture。内存夹紧后恢复1.956MHz、T2=25μs、RMSE≈1.1e−14 | 原源码未修 |
| FIT-01 | T1+80kHz振荡仍success，RMSE≈0.104949；等强5/8MHz合成信号拟合为单频仍success，RMSE≈0.105085 | 优化成功与模型适配分开；追加噪声/残差门禁和独立验证，保留反例 | 原源码未修；调用方不可只看success |
| VAL-01 | two_qubit_cli.py:63–72只遍历现有项。完整17项通过；相位=[]后15项仍通过；truth_table={}后9项仍通过；两者为空仅7项仍通过 | 强制2个相位、4个基态及全检查集合；计数/对象/schema校验。完整键被删除会KeyError，问题是内容为空/少项 | 原评分API未修；工程包cz_suite桥接新增完整性拒绝门禁；正常源runner固定生成全项，不据此宣称默认运行已漏测 |

## 模型范围，不能冒充软件bug

- 三能级截断历史对照：强驱动例P1(N3)=0.475077、P1(N8)=0.301638，差0.173439；默认两弱π单脉冲差仅9.59e−9和3.17e−9。生产三级与独立三级一致，说明是适用范围问题。未完成完整含噪CZ高能级收敛验证。
- 矩形Segment、有效直接coupling_scale和频移不含实际耦合器/磁通电路映射；T1/T2噪声为独立Markov模型，高能级耗散按a/n算符推广。
- Ramsey均匀采样存在别名；不是优化器算错，须用先验/扫描设计限制分支。
- IQ三中心预设、独立高斯，线性层析含SPAM；负特征值如实输出，不能据此改成后选择保真度。
- 历史模型门Favg≈0.997841、相干Favg≈0.999330、图态估计≈0.988838是不同定义；不是实机门指标。
- 本次审查未发现2π、√2、虚Z符号、保留泄漏的非保迹Favg公式硬错；这是限定范围的审查结论，不是全域正确性证明。

## 修复验收要求

每个修复绑定新源码哈希、独立反例、正常数据回归及缺项/边界fixture。NUM修复后仍应拒绝不可识别数据；FIT门禁不应通过任意调高阈值消除失败；VAL必须对缺项fail-closed。原材料中的通过数量、旧日志和READY标记不作为新修复验收依据。

证据：[analysis.py](../resources/twin_source/quantum_twin/analysis.py)、[two_qubit_cli.py](../resources/twin_source/quantum_twin/two_qubit_cli.py)、[历史推断反例](../resources/twin_source/outputs/audit/inference_repro.json)、[历史截断对照](../resources/twin_source/outputs/audit/truncation_repro.json)、[本项目审查证据](../resources/prior_knowledge/review/)。具体定位以冻结源码和证据哈希为准。

## 原步骤与绑定的当前缺口

s03原幅度Rabi、s07原T2CPMG、d06原XEB尚未由现有时长Rabi、Ramsey和CZ源套件实现。该缺口属于执行覆盖不足，不是量子公式修复问题；分组报告不能据辅助通过写original_step_complete=true。其余未绑定方法同样按实际capability拒绝。44方法主归属集中到15个Agent后，原模型假设、数据条件、反例和真值评分边界继续有效。
