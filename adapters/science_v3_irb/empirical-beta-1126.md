# R19：半 π 门经验导数系数

仅 `sq_drag_half_scan` 与 `sq_drag_irb` 支持新模式。六方法适配器的
`sq_drag_scan` 保持原归一化系数策略，本补丁不修改 F8、science_runtime 或
science_v3 冻结源码。

## 数值和身份

- `drag_mode` 缺省或 `normalized_alpha`：原 λ / 实测非谐性策略。旧脉冲身份、
  λ 符号、原生样点及缺省序列化保持不变。
- `drag_mode=empirical_beta`：`drag_beta_s` 为有符号秒数；扫描端点
  `beta_start_s`、`beta_stop_s` 及 `scan_axis_values` 同为秒。
- 经验模式直接创建原生 `DrivePulse(beta=...)`。原生代码在 4 GHz 网格上计算
  `I + i β gradient(I, dt)`，再乘 `exp(i phase)`，最后加原有 2 ns 零缓冲。
  不设虚拟非谐性，不从 β 反推出非谐性。
- 候选仅 `drag_beta_s`；独立复测冻结 `verify_drag_beta_s`。数值拟合内部把横轴
  缩放至 ns 以改善条件数，返回秒，复测候选保留原冻结秒值、不二次换算。
- 身份新增 `dragMode=empirical_beta`、`betaSeconds`，`lambda1` 和
  `anharmonicityHz` 均为 null；策略为
  `native_science_v3_empirical_beta_fixed_duration_v1`。IRB 必须完全匹配已独立
  验证的同一个 X/2 身份。缺 mode 的旧记录永远不解释为经验模式。

## 原始 I/Q 与实际上传

本适配器的新执行在每个 `preflight_NNNN.npz` 归档原生、载频前、含相位的
`xyN_i` / `xyN_q`，以及原有载频后 XY/RO 数组。`compiled_plan.json` 的 rowId、
segment ID、数组键和各 I/Q 内容 SHA 可还原每行各段。该同一行还保存 RF 波形
SHA，执行重编译须完全一致，`uploads.jsonl` 再绑定相同 rowId 与 RF SHA。
这表示可追溯的数值 I/Q，并不表示两根物理线分别上传。

## 准入与边界

经验模式只去掉 `anharmonicity` 来源。精确 shape π、半 π、幅度及时间 Rabi
来源、当前读出条件、资源/路由/版本、独立首复测和真实回执验证保留。
半门诊断仍要求实际零 β 行、可分辨的曲率与内部极小值、独立候选采集。
零系数本身不证明改善。IRB 保留 PRE 留出参考、独立种子、paired RB/IRB、
POST 漂移和原有拒绝准则；不以诊断代替门保真度。

经验模式 5..41 个有序系数；旧 λ 点数原验证保持。β 绝对上限
`10/(2π·1 MHz)` 秒只是已有 λ/非谐性数值边界的外包络，不是器件额定值。
真正波形仍受原生基带归一峰值 1、上传样点 0.990001、4 GHz 网格、2 ns 缓冲、
79.7 μs 控制窗及原有行/样点/shot/超时预算限制。报告实际离散斜率；其
8e9 归一单位/秒上界由单位峰值及采样率推出，不虚称已校准的硬件电压限斜率。
所有行先预编译，非法幅度在获取设备锁和打开仪器前拒绝。

## 归档与升级

只有 science_v3_irb 的显式源码清单更新。已完成旧记录仍按原策略、原身份与
原生档案认证，不重写或重解释数据。未完成的旧执行计划保留原 sourceHash，
不能在新二进制下静默继续；恢复仍须按既有未知执行/清理规则核对后重新计划。
profile 构造默认 disabled；本轮只做隔离原生编译及注入传输测试，无设备调用。

数值夹具 `tests/fixtures/r19_beta_half_numeric.json` 明示 TEST_ONLY，来自实际
分析函数处理合成 ADC，不能成为实测来源。复现入口：
`tests.test_science_irb_beta.numeric_half_fixture()`（已有 Python 3.12 数值环境）。
