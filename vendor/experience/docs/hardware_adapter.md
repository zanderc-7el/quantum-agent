# 硬件适配协议与事务边界

下面的操作是本工程的抽象协议建议，**不是32所或任何现有设备的真实 SDK/API**。材料中的 CorrectF01ByPhase、DragAlphaCal、RB1QCal 等只用于定位实验能力。具体模块、签名、授权、单位和自动写参行为均须绑定后验证。

## 0. owner、方法与执行器

注册对象是15个原步骤与1个总控；Backend绑定的是具体procedure，不是所有owner能力的总开关。请求应关联`agent_id`、`procedure_id`、`primary_owner_id`和实际`runtime_step_id`；跨owner借用由宿主保留caller来源。现有低层ExecutionRequest字段以[运行时接口](runtime_contract.md)为准，未加入字段不得直接塞入该dataclass。

`run-agent`默认选择原步骤主方法，能力不足必须拒绝。显式选择s03的`sq_rabi_time`只运行时长辅助，s07的`sq_ramsey`只运行Ramsey辅助。CZ源套件是整体诊断桥，不是d06的XEB适配器。能读到某方法旧数据，不能因此把owner标为HARDWARE_VERIFIED或original_step_complete。

## 0A. 当前本地Backend接口

当前Python Protocol包含`capabilities(step)`、`measure(request)`、`apply_trial(request,candidate)`、`verify(request,candidate)`、`rollback(request,snapshot)`、`reconcile(operation_id)`。这六项是本包真实代码接口；后文细分的编译、查询、设备commit等属于待扩展协议。

capabilities返回schema_version=1、data_kind、operations、readback/rollback/reconcile布尔能力以及`acquisition_counts={measure:N,verify:M}`。两阶段采集成本分别是N×request.shots和M×request.shots。扫描证据要包含acquisition_count、total_shots与预声明一致；多点原始计数保存在后端扫描文件，顶层counts如提供则表示其声明的单个shots分母，不能塞入全扫描聚合计数后误用分母。

measure/verify公共证据字段为evidence_id、acquisition_id、data_kind、parameter_versions、shots、metrics。measure返回精确匹配StepSpec.writes的candidate；verify返回布尔passed及匹配请求的candidate_hash，并可返回stderr。apply_trial回执是`status=applied,actual_values=candidate`；rollback是`status=restored,actual_values=snapshot`。reconcile返回completed及原result、not_started或unknown。当前控制器可验证回执内容，但硬件真实性仍需设备适配验收。

ExecutionRequest.deadline是Unix秒截止时刻。Runtime在调用前后检查，物理模拟器逐扫描点协作检查；这属于软截止，无法抢占阻塞的原子SDK调用。真实适配需要超时取消、查询与输出排空能力，不能用Python函数超时代表设备已停止。

parameter_versions表示任务启动时冻结的注册表版本；候选试用通过candidate_hash及apply回执关联，提交成功才递增注册表revision。它不是独立的实机actuation revision，接入真实设备前必须增加实际生效版本和读回证明。当前运行时拒绝所有real后端，不提供解除该限制的使用步骤。

## 1. 能力登记

后端登记 backend_id、transport、device/chip/firmware/compiler_version、支持的实验与结果 schema、通道拓扑、范围/分辨率、时基/触发、复位条件、共享资源、查询和停止能力。另行声明：候选试用是否持久、读取当前配置是否可信、幂等键保留窗口、操作历史保留期、提交是否原子、回退是否可验证。

每项能力状态为 UNBOUND、DOCUMENTED、CODE_INSPECTED、OFFLINE_TESTED、HARDWARE_VERIFIED；不得由接口名或文档自动提升。设备范围与权限来自部署策略，模型不能补齐。含有自动写配置副作用的旧实验，要么包装成可恢复事务，要么先关闭写入并验证；不可当作纯采集函数调用。

## 2. 建议逻辑操作

| 操作 | 关键请求 | 成功证据及失败语义 |
| --- | --- | --- |
| describe_capabilities | backend_id、版本要求 | 机器可读能力及资源拓扑，不代表设备已就绪 |
| read_state | 对象、freshness_limit | 当前 revision、读回值、设备状态、时刻；缓存应标明 |
| validate_compile | 合同、模板哈希、控制/范围 | 实际量化序列、单位、资源、总时长/shot成本估计；不能执行 |
| submit_acquisition | request_id、request_hash、expected_revision、fencing_token | 作业 ID 与 accepted；完成需另查 |
| query/fetch | job_id/request_id、内容签名 | 原结果或当前状态；重复 fetch 不产生新 shots |
| cancel_and_drain | job_id、资源、停止原因 | 取消接收与输出排空分别确认；未知时隔离 |
| apply_candidate | operation_id、绝对目标、expected_revision、租约 | actual_values、new_revision、设备持久化回执 |
| commit_candidate | transaction_id、验证ID、候选revision、父依赖 | 一次提交的版本回执；不能将新结果套给旧版本 |
| rollback | transaction_id、原绝对快照、expected_revision | 读回一致及恢复 revision；冲突时不能覆盖他人新值 |
| query_operation | operation_id、原内容哈希 | 未执行/已执行/失败/未知，支持超时恢复 |

提交参数时区分 requested_value 与 quantized_value。拟合和复测必须使用 actual/quantized 值；若量化使候选超出科学容差，拒绝或重设计。Hz、s、rad 是逻辑公共单位，DAC code、电压和采样点只在经验证的映射处转换。

## 3. 回执丢失处理

先把 intent 和原快照持久化，再执行。超时后禁止再次发送累加修正。执行器先查询同一 operation_id；确认已执行且读回一致则继续；确认未执行才允许同 ID 同内容重试；结果矛盾或查询能力不足则隔离。事务发生中租约丢失时，旧执行者不能继续变更，新的执行者先做状态核对。

不能用文件系统原子 rename 推导真实设备“恰好执行一次”。真实测量触发、设备持久化、客户端日志通常不是同一事务。没有设备侧去重/查询证据时，重复 acquisition 可能产生额外 shots，必须记录 unknown_physical_cost 或上界；恢复后重新采集应使用新 ID 并保留原因。

COMMITTING 崩溃后可能是“设备已提交、控制器未记完成”；恢复应读取操作回执并完成登记，不能重复增加 calibration_version。ROLLING_BACK 崩溃同理。若实际状态与候选、基准都不一致，禁止强行覆盖，报告 CONFIG_CONFLICT/UNKNOWN_ISOLATED。

## 4. 适配验收清单

1. 在测试后端验证所有单位、符号、量化边界、非有限值、越界、错误对象与版本拒绝。
2. 验证 submit→query→fetch、重复读回不重采、重复ID异内容拒绝、部分shots、丢包和取消语义。
3. 在写参前后、提交前后、回退中断处注入故障；重启后只接受读回和操作历史支撑的状态。
4. 两个任务竞争同一 readout/AWG/coupler，确保不可并发；租约过期旧请求被围栏拒绝，旧波形停止后才能转交。
5. 检查 raw IQ/counts、分类器/积分权重版本、实际控制、时间戳和成本均可关联；不能只有截图或拟合图。
6. 仪器操作验收须由具备权限的设备责任团队绑定授权范围；本包离线/虚拟验收不替代此步骤。

未来可先实现只读扩展：读取历史数据、拟合与提出候选，并用例如 `execution_mode=offline` 标记，禁用 apply/commit/rollback。该字段与模式尚未接入当前Runtime；当前Backend仍须声明apply_trial、rollback、readback等完整能力，不能直接据此构造只读后端。“未调用硬件”不能被包装成硬件运行成功。

## 5. 待绑定记录模板

每项保存：材料名称/节号、目标软件版本、真实入口（待确认时为 null）、输入单位、输出schema、自动写参副作用、资源、查询/取消、幂等语义、验证命令、证据位置、责任人、绑定状态。真实入口未确认时明确写 UNBOUND；不要编造 import 路径或 SDK 方法以补齐表格。

来源：[手册](../resources/source_materials/06_工作脚本与数据/cal_manual_d0.md)、[最新架构](../resources/latest_architecture/)、[既有持久闭环与恢复经验](../resources/prior_knowledge/)。
