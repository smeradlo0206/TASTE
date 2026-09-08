# Find Feedback 数据合同分批开发任务

本目录把总提示词拆成可以逐批交给 Codex 的小任务。

总提示词及全部字段定义以以下文件为准：

```text
docs/architecture/find-feedback-data-contracts-codex-prompt.md
```

## 执行规则

1. 严格按照编号顺序执行，一次只执行一批。
2. 每批开始前必须完整读取总提示词和本批提示词。
3. 上一批测试未通过时，不进入下一批。
4. 每批只修改提示词列出的文件。
5. 不修改原 Find、Read 和 Framework 流程。
6. 不提交、不推送、不操作远端仓库。
7. 每批结束后报告修改文件、测试命令、测试结果和未完成内容，然后停止。

## 批次清单

- [x] [第 00 批：仓库与真实产物只读审计](./00-audit.md)（结果见 [00-audit-result.md](./00-audit-result.md)）
- [x] [第 01 批：建立合同包和枚举](./01-package-and-enums.md)
- [x] [第 02 批：建立 JSON 转换基础](./02-serialization-core.md)
- [x] [第 03 批：实现可复用内嵌数据结构](./03-embedded-structures.md)
- [x] [第 04 批：实现 RunContext](./04-run-context.md)
- [x] [第 05 批：实现 ProgressSnapshot](./05-progress-snapshot.md)
- [x] [第 06 批：实现 ValidationResult](./06-validation-result.md)
- [x] [第 07 批：实现 Anomaly](./07-anomaly.md)
- [x] [第 08 批：实现 RecoveryDecision](./08-recovery-decision.md)
- [x] [第 09 批：实现 ExperienceCase](./09-experience-case.md)
- [x] [第 10 批：实现 SupervisorState](./10-supervisor-state.md)
- [x] [第 11 批：补齐合同单元测试](./11-contract-test-hardening.md)
- [x] [第 12 批：真实 Find 形状兼容测试](./12-find-shape-compatibility.md)
- [x] [第 13 批：最终回归与边界检查](./13-final-regression.md)

## 开发文件

所有批次的源码修改限制在：

```text
framework/scripts/feedback/__init__.py
framework/scripts/feedback/contracts.py
tests/feedback/test_contracts.py
```

合同任务完成后，纯状态机另增加：

```text
framework/scripts/feedback/state_machine.py
tests/feedback/test_state_machine.py
```

这仅完成 39 条纯转换规则和 `advance_state()`；Adapter、Executor、Observer、Validator、Anomaly Builder、Recovery Controller、Experience Store、Supervisor 主循环和 Framework Gate 实际接入仍未开发。

## 当前正式定义

以 `framework/scripts/feedback/contracts.py` 和
`framework/scripts/feedback/state_machine.py` 为唯一代码准则：

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

`ANOMALY_DETECTED` 是事件而不是状态。恢复预算只在进入 `RECOVERING` 时扣除一次；自动重试和用户批准后的重试使用同一规则，预算为 0 时拒绝进入该状态。

第 12 批只有确有必要时才增加脱敏 fixture：

```text
tests/fixtures/find_feedback/
```
