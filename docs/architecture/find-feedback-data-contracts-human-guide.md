# Find Feedback 数据合同开发说明（人读版）

## 1. 这一步到底要做什么

这一阶段已经完成数据合同和纯状态机；它还没有开始监控 Find 或自动修复 Find。

这一阶段只做一件事：先规定以后各个外围组件交换信息时使用什么格式。

可以把它理解为先设计七张统一表格：

```text
RunContext         本次任务的基本档案
ProgressSnapshot   某一时刻的进度照片
ValidationResult   最终结果检查报告
Anomaly            整理后的异常报告
RecoveryDecision   准备采取的恢复决定
ExperienceCase     最终保存的历史案例
SupervisorState    外围控制器当前总状态
```

对应的 Codex 执行文件是：

[Codex 数据合同开发提示词](./find-feedback-data-contracts-codex-prompt.md)

下面每一步都使用与 Codex 提示词相同的 `C1`、`C2` 编号，方便对照。

## 2. 最终会新增什么

当前已增加并验证以下合同与纯状态机文件：

```text
framework/scripts/feedback/__init__.py
framework/scripts/feedback/contracts.py
tests/feedback/test_contracts.py
framework/scripts/feedback/state_machine.py
tests/feedback/test_state_machine.py
```

已完成：数据合同、状态字段约束、纯状态转换表、`advance_state()` 和单元测试。

尚未完成：Adapter、Executor、Observer、Validator、Anomaly Builder、Recovery Controller、Experience Store、Supervisor 主循环，以及 Framework Gate 的实际接入。

其中：

- `contracts.py` 保存公共数据结构、枚举和七个主要合同；
- `test_contracts.py` 检查合同本身的字段、规则和 JSON 转换；
- 不修改原 Find 和 Read。

测试文件不再带 `find`，因为这些公共合同以后还会给 Read 和其他阶段使用。Observer、Validator 等组件实现后，各自建立对应测试文件；不会把所有测试一直堆在 `test_contracts.py` 中。

## 3. C1：先看真实 Find 现在产生什么

Codex 开发前先只读检查成功和未完成的 Find 目录。

这样做是为了避免设计一套与现有项目无关的字段。

当前真实 Find 已经会产生：

```text
logs/find_progress.json
final/find_results.json
reports/source_status.md
manifest.json
```

例如进度中已经存在：

```text
run_id
updated_at
phase
selection
counts
live_progress
```

新合同会承载这些已有信息，但这一阶段不会编写读取这些文件的 Observer。

## 4. C2：决定代码使用什么方式实现

合同使用 Python 自带功能实现，不增加新的第三方依赖。

主要使用：

```text
dataclass    表示固定格式的数据
Enum         表示只能选择固定值
datetime     表示时间
json         保存和恢复数据
```

每份合同都能：

```text
转换成字典
转换成 JSON
从字典恢复
从 JSON 恢复
检查字段是否合法
```

## 5. C3：定义合同内部可复用的数据结构

这里说的不是新的外围组件，而是七个主要合同内部会重复使用的一组字段。

例如 RunContext、ValidationResult 和 ExperienceCase 都可能引用产物文件。如果三处分别定义“路径、大小、是否存在”，以后很容易出现字段名不一样。因此统一定义 `ArtifactRef`，其他合同直接引用它。

七张大表中会反复出现一些小结构，因此先统一定义。

### `ArtifactRef`

表示一个产物文件：

```text
文件是什么
路径在哪里
是否必须存在
文件大小
修改时间
文件哈希
能否正常解析
```

### `EvidenceRef`

表示一条证据：

```text
证据来自日志还是产物
文件路径
对应行号
证据摘要
```

### `ExperienceRef`

表示一个历史案例的引用：

```text
案例编号
相似度
是否经过验证
案例类型
```

这里不复制完整案例，只保留引用。

### `ParameterChange`

表示参数发生了什么变化：

```text
参数名
修改前
修改后
为什么修改
来自哪个历史案例
```

### `ValidationCheck`

表示 Validator 的一项具体检查：

```text
检查代码
通过、警告还是阻塞
预期是什么
实际是什么
使用了哪些证据
```

### `SupervisorEvent`

表示 Supervisor 最近发生的一次状态变化。

其中 `event_type` 必须是 `SupervisorEventType`，不能是任意字符串；保存 JSON 时是稳定的小写字符串，读取后恢复为枚举。

## 6. C4：定义合同字段使用的枚举

枚举就是程序里的固定下拉框。

技术上这是“定义枚举”；它同时属于合同开发，因为合同里的状态、验证结果、恢复动作和风险等级都以这些枚举作为字段类型。

例如进度状态只能从下面选择：

```text
正在启动
正在运行
疑似停滞
确认停滞
已经完成
已经失败
用户取消
暂时未知
```

这样不同组件不会分别写出：

```text
失败
error
failed
run_failed
```

本次定义六组固定选项：

| 枚举 | 表示什么 |
|---|---|
| `ProgressStatus` | 原 Find 当前运行状态 |
| `ValidationStatus` | 结果检查是通过、警告还是阻塞 |
| `RecoveryAction` | 准备执行哪种恢复动作 |
| `SupervisorStatus` | 外围控制器当前在做什么 |
| `SupervisorEventType` | 驱动 Supervisor 状态变化的正式事件 |
| `RiskLevel` | 恢复动作的风险等级 |

当前 Supervisor 的精确定义如下。

```text
SupervisorStatus（13 项）
IDLE PREPARING RUNNING VALIDATING DIAGNOSING DECIDING
AWAITING_APPROVAL RECOVERING GATING COMPLETED BLOCKED FAILED CANCELLED

SupervisorEventType（23 项）
START PREPARED PREPARE_FAILED MONITOR_TICK PROCESS_EXITED STALL_DETECTED
VALIDATION_PASSED VALIDATION_BLOCKED ANOMALY_DETECTED ANOMALY_READY
DIAGNOSIS_FAILED RETRY_DECIDED APPROVAL_REQUIRED STOP_DECIDED APPROVED
REJECTED NEW_RUN_STARTED RECOVERY_FAILED GATE_ALLOWED
GATE_BLOCKED_RECOVERABLE GATE_BLOCKED_FINAL CANCEL_REQUESTED FATAL_ERROR

SupervisorCommand（12 项）
CALL_ADAPTER START_EXECUTOR POLL_OBSERVER CALL_VALIDATOR CALL_GATE
CALL_ANOMALY_BUILDER CALL_RECOVERY_CONTROLLER WAIT_FOR_APPROVAL
EXECUTE_RECOVERY RECORD_OUTCOME CANCEL FAIL
```

`ANOMALY_DETECTED` 是事件，不是状态。当前纯状态机有 40 条转换规则。

恢复预算只在进入 `RECOVERING` 时扣除一次；自动重试和用户批准后的重试使用同一规则，预算为 0 时不能进入 `RECOVERING`。

## 7. C5：RunContext——本次任务档案

`RunContext` 在 Find 启动前生成。

它记录：

### 启动前身份

```text
项目编号
当前是第几次尝试
从网页、命令行还是完整循环启动
```

### 研究输入

```text
研究主题
研究主题指纹
研究者画像路径
会议和年份选择
是否启用 arXiv、bioRxiv 等来源
```

### 执行信息

```text
使用哪个公开入口
工作目录
使用哪个 Python 和 Conda 环境
使用哪个模型
```

### 配置

```text
原始参数
实际参数
Feedback 修改了哪些参数
配置快照
```

### 预期产物类型

```text
哪些文件必须生成
```

### 控制规则

```text
启动保护时间
多久算疑似停滞
多久确认停滞
允许恢复几次
允许哪些恢复动作
哪种风险必须请用户批准
```

### 历史经验

```text
用什么条件查询 Experience Store
找到了哪些案例
实际采用了哪些案例
案例改变了哪些参数
```

RunContext 在 Find 启动后不再修改。发生重试时创建新的启动前方案和新的尝试序号，但不在这里生成假的运行编号。

真实 `run_id`、`root_run_id`、`run_dir`、PID、退出码和实际文件位置只能在 Executor 启动 Find 后获得；它们由后续的 ExecutionHandle、SupervisorState 和 Observer 相关合同保存。

## 8. C6：ProgressSnapshot——进度照片

Observer 每次检查都会生成一张 ProgressSnapshot。

它记录：

```text
观察时间
当前阶段
完成数量和总数
百分比
Find 原有 counts
进程是否还活着
进程退出码
多久没有有效进度
关键文件是否出现
来源正常、受限和失败数量
观察到的异常信号
```

这里要区分两件事：

```text
文件被重新写入
真正的进度发生变化
```

只有阶段、current、total 或 counts 发生变化，才算有效进度。

ProgressSnapshot 只描述现象，不决定怎样恢复。

## 9. C7：ValidationResult——结果检查报告

Find 结束后，Validator 生成 ValidationResult。

主要检查：

```text
运行目录是否存在
进度是否属于当前 run
Find 是否真正完成
进程退出码是否正常
find_results.json 是否存在
JSON 是否能解析
结果 run_id 是否一致
是否有强推荐论文
推荐数量和质量是否满足要求
论文摘要是否真实存在
论文 ID 是否重复
Reading bridge 是否能够接收
```

最终只有三种结果：

```text
pass       可以进入 Read
warning    可以使用，但保留警告
block      不能进入 Read
```

每项检查都要保存“预期、实际、原因和证据”，不能只保存一句“验证失败”。

## 10. C8：Anomaly——整理后的异常

Anomaly Builder 将以下信息合并：

```text
ProgressSnapshot
ValidationResult
进程状态
日志证据
产物状态
```

最后生成一份异常报告，说明：

```text
问题发生在哪个阶段
异常类型是什么
有哪些症状
有哪些证据
根因是未知、怀疑还是确认
影响哪些来源和产物
是否阻止 Read
是否具备恢复条件
```

Anomaly 不包含恢复命令。它只负责把问题说清楚。

## 11. C9：RecoveryDecision——恢复决定

Recovery Controller 根据 Anomaly 生成 RecoveryDecision。

它记录：

```text
准备执行什么动作
为什么执行
风险等级
是否可以立即执行
是否需要用户批准
是否必须生成新 run_id
准备修改哪些参数
使用了哪个 Playbook 或历史案例
当前还剩多少恢复预算
恢复完成后必须通过哪些检查
再次失败后是否停止
```

恢复决定不代表恢复成功。

恢复后必须再次生成 ValidationResult，只有重新通过才算成功。

## 12. C10：ExperienceCase——历史案例

ExperienceCase 记录完整的处理结果。

Experience Store V0 使用项目、案例类型、运行结果、验证状态、废弃状态和上下文标签筛选案例，不使用内容指纹、语义检索、Embedding 或 LLM 查询。

案例分三类：

```text
normal       正常运行案例
technical    技术异常和恢复案例
preference   用户反馈和偏好案例
```

它记录：

```text
当时是什么配置和来源选择
发生了什么异常
根因是否确认
执行了什么恢复动作
是否得到用户批准
恢复前检查结果
恢复后检查结果
最终是否允许进入 Read
这个案例在什么条件下可以复用
哪些情况下不能复用
以后被使用了多少次
```

只有恢复后通过 Validator 的案例，才能标记为已验证恢复经验。

## 13. C11：SupervisorState——总控制状态

SupervisorState 类似网页任务卡背后的完整状态。

它记录：

```text
Supervisor 编号
当前运行编号
当前处于准备、运行、验证还是恢复
最近一份 ProgressSnapshot
最近一份 ValidationResult
当前 Anomaly
当前 RecoveryDecision
子进程 PID 和退出码
已经恢复几次
还剩多少恢复预算
是否等待用户批准
Gate 是否允许进入 Read
是否已经结束
最近发生的状态事件
Supervisor 最后心跳时间
```

SupervisorState 主要保存其他合同的编号，不重复复制所有完整内容。

## 14. C12：JSON 保存和恢复

七份合同都必须能够保存成 JSON。

例如：

```text
ProgressSnapshot Python 对象
→ to_json()
→ 保存到状态文件
→ from_json()
→ 恢复成 ProgressSnapshot
```

保存再读取后，以下内容不能变化：

```text
枚举值
时间
run_id
内嵌结构
列表和映射
```

如果字段缺失或值非法，应直接指出具体字段，而不是报模糊错误。

## 15. C13—C17：怎样测试

### C13：枚举测试

检查固定选项不会被拼错，并能正确保存为 JSON。

### C14：可复用内嵌数据结构测试

例如：

```text
文件大小不能为负数
证据结束行不能小于开始行
相似度必须在 0 到 1
时间必须带时区
```

### C15：七个合同往返测试

每份合同都执行：

```text
创建对象
→ 转成字典
→ 转成 JSON
→ 从 JSON 恢复
→ 比较是否一致
```

### C16：规则冲突测试

主动构造不合法数据：

```text
ProgressSnapshot 百分比超过 100
ValidationResult 一边说阻塞、一边允许进入 Read
Anomaly 没有证据
高风险 RecoveryDecision 未批准却允许执行
ExperienceCase 声称恢复成功但最终验证失败
SupervisorState 已结束但子进程仍存活
```

程序必须拒绝这些自相矛盾的数据。

### C17：真实 Find 形状兼容测试

根据现有成功和未完成运行，手工构造最小进度字典，检查合同能承载真实字段。

测试不直接读取会变化的 `.runtime`，也不启动真实 Find。

### 以后怎样检查合同能不能真正接上组件

合同开发完成后，测试继续分三层增加：

```text
组件输入输出测试
  Observer：find_progress.json -> ProgressSnapshot
  Validator：find_results.json -> ValidationResult

组件连接测试
  ProgressSnapshot -> Anomaly -> RecoveryDecision
  检查字段名、类型和含义能否直接对应

完整链路测试
  使用固定的成功、停滞、缺失结果和损坏结果样本，
  检查 Framework Gate 和恢复决定是否正确
```

固定样本放在：

```text
tests/fixtures/find_feedback/
├── successful_run/
├── stalled_run/
├── missing_result/
└── invalid_result/
```

如果真实 Find 一直正常，就复制一份脱敏后的成功样本，再人为删除字段、截断 JSON、修改更新时间或制造错误计数。这样可以测试异常处理，又不会破坏真实运行目录。

## 16. 开发顺序

人可以按照下面顺序检查 Codex 是否正确执行：

```text
1. C1       查看现有产物，不修改
2. C2       确认只使用 Python 标准库
3. C3       实现合同内部可复用的数据结构
4. C4       实现合同字段使用的六个枚举
5. C5       实现 RunContext
6. C6       实现 ProgressSnapshot
7. C7       实现 ValidationResult
8. C8       实现 Anomaly
9. C9       实现 RecoveryDecision
10. C10     实现 ExperienceCase
11. C11     实现 SupervisorState
12. C12     实现 JSON 保存和恢复
13. C13-C17 每做一部分就补测试
```

## 17. 怎样判断这一阶段做完了

完成时应满足：

- 七份合同都能正常创建；
- 六个枚举值固定；
- 合同可以保存和恢复 JSON；
- 自相矛盾的数据会被拒绝；
- 测试全部通过；
- 没有启动真实 Find；
- 没有修改原 Find、Read 和 Framework 流程；
- 没有访问远端 GitHub；
- 没有 commit 或 push；
- 最终报告明确区分：合同与纯状态机已完成，Observer 等外围业务组件仍未实现。

## 18. 下一阶段是什么

数据合同完成后，下一阶段才开始实现：

```text
Progress Observer
Result Validator
Framework Gate 的影子模式
```

届时 Observer 负责把真实 `find_progress.json` 转成 `ProgressSnapshot`，Validator 负责把真实 Find 产物转成 `ValidationResult`。
