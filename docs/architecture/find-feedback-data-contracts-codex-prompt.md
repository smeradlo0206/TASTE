# Find Feedback 数据合同开发执行提示词（Codex 版）

## 0. 任务目标

在 TASTE 本地仓库中完成 Find Feedback 第一阶段：定义并实现外围组件之间的数据合同。

本阶段只实现：

- 6 个枚举：`ProgressStatus`、`ValidationStatus`、`RecoveryAction`、`SupervisorStatus`、`SupervisorEventType`、`RiskLevel`；
- 7 个顶层数据合同：`RunContext`、`ProgressSnapshot`、`ValidationResult`、`Anomaly`、`RecoveryDecision`、`ExperienceCase`、`SupervisorState`；
- 为顶层合同服务的少量内嵌结构；
- 严格的字段校验、JSON 序列化和单元测试。

当前已完成数据合同、字段约束、纯状态转换表、`advance_state()` 和单元测试；尚未实现 Observer、Validator、Anomaly Builder、Recovery Controller、Framework Gate 的实际接入、Experience Store、Adapter、Executor 或 Supervisor 主循环，也未把这些组件接入现有 Find。

## 1. 本地开发约束

1. 所有工作只保存在本地。
2. 不执行 `git push`，不创建 Pull Request，不操作远端分支。
3. 除非用户明确要求，不创建本地 commit。
4. 开始前执行只读检查：

   ```text
   git status --short --branch
   git branch --show-current
   git worktree list
   ```

5. 保护现有未提交修改，不覆盖、不回退、不格式化无关文件。
6. 优先在独立的 `feature/find-feedback` 本地 worktree 中开发；如果当前目录不是该分支，只报告，不擅自迁移用户修改。
7. 不修改以下原有流程：

   ```text
   modules/finding/main.py
   modules/finding/scripts/flow/pipeline.py
   modules/reading/
   framework/scripts/orchestration/run_frontend.py
   framework/scripts/bridges/reading_bridge.py
   ```

8. 不读取、复制或写入 API Key。配置示例必须脱敏。

## 2. 开发路径和文件范围

在确认仓库约束后，使用以下最小文件范围：

```text
framework/scripts/feedback/
├── __init__.py
└── contracts.py

tests/
└── feedback/
    └── test_contracts.py
```

如仓库已有更明确的测试目录约定，沿用现有约定，但不要新增不必要的包层级。

这里使用公共名称 `test_contracts.py`，因为这些合同后续会同时服务 Find、Read 和其他阶段。当前文件只测试合同自身；Observer、Validator、Recovery Controller 和完整链路的测试以后分别放入对应测试文件，不混入合同单元测试。

`contracts.py` 只放：

- 枚举；
- 内嵌小结构；
- 7 个顶层数据合同；
- 确定性字段校验；
- JSON 兼容转换。

不得在 `contracts.py` 中启动进程、读取运行目录、调用 Find、执行恢复、访问网络或写 Experience Store。

## 3. 实现方法

### C1. 先检查真实 Find 产物

只读检查至少两个现有运行目录：

```text
modules/finding/.runtime/runs/find_20260810_062018_982678
modules/finding/.runtime/runs/find_20260822_051206_346734
```

确认当前 Find 的正式产物路径：

```text
logs/find_progress.json
final/find_results.json
reports/source_status.md
manifest.json
```

确认成功进度至少包含：

```text
run_id
updated_at
phase
selection
source_status
counts
```

确认运行中进度可能包含：

```text
live_progress.phase
live_progress.current
live_progress.total
live_progress.percent
live_progress.message
```

不要让单元测试直接依赖 `.runtime`；真实目录只用于确认字段语义。

### C2. 使用标准库实现

优先使用：

```python
dataclasses
enum
datetime
json
typing
```

不要为了合同层新增 Pydantic 等依赖。

要求：

- 枚举继承 `str, Enum`，JSON 中保存小写稳定值；
- 时间在 Python 中使用带时区 `datetime`，序列化为 ISO 8601；
- 路径字段使用字符串，以兼容 Windows 和 WSL；
- 集合序列化为 JSON 数组；
- 未知字段在 V1 解析时应报出明确错误；
- 缺少必填字段时给出包含字段名的错误；
- 不使用无约束的 `Any` 承载核心状态；
- 可扩展参数、原始计数和来源选择允许使用 JSON-compatible mapping；
- 所有映射进入合同时做浅复制，避免调用方随后修改原对象。

### C3. 定义合同内部可复用的数据结构

实现以下内嵌结构。它们不是新的外围组件，也不会独立运行；它们只是七个顶层合同内部重复使用的字段组合。例如多个合同都需要引用产物文件时，统一使用 `ArtifactRef`，避免各自定义不同的文件字段。

#### `ArtifactRef`

字段：

```text
role: str                         必填，产物角色
path: str                         必填，精确路径
required: bool                    必填
exists: bool | None               可选，尚未观察时为 None
size_bytes: int | None            可选，必须 >= 0
modified_at: datetime | None      可选
sha256: str                       可选
parse_status: str                 可选，例如 unknown/ok/invalid
```

#### `EvidenceRef`

字段：

```text
kind: str                         必填，例如 log/artifact/snapshot/validation
path: str                         可选
contract_id: str                  可选
line_start: int | None            可选，必须 > 0
line_end: int | None              可选，必须 >= line_start
summary: str                      必填，简短且不得包含密钥
sha256: str                       可选
```

#### `ExperienceRef`

字段：

```text
case_id: str                      必填
similarity: float | None          可选，范围 0 到 1
verified: bool                    必填
case_type: str                    必填，normal/technical/preference
summary: str                      可选
```

#### `ParameterChange`

字段：

```text
name: str                         必填
before: JSON-compatible value     可选
after: JSON-compatible value      必填
reason: str                       必填
source_case_id: str               可选
```

#### `ValidationCheck`

字段：

```text
code: str                         必填，稳定机器码
status: ValidationStatus          必填
required: bool                    必填
message: str                      必填
expected: JSON-compatible value   可选
actual: JSON-compatible value     可选
evidence_refs: list[EvidenceRef]   必填，默认空列表
```

#### `SupervisorEvent`

字段：

```text
sequence: int                     必填，>= 0
occurred_at: datetime             必填
event_type: SupervisorEventType   必填，只能使用声明的事件枚举
message: str                      必填
contract_id: str                  可选
```

### C4. 定义合同字段使用的枚举

枚举是数据合同字段的固定可选值，技术上属于枚举定义，同时也是合同开发的一部分。枚举成员与 JSON 值如下：

```text
ProgressStatus
  STARTING = "starting"
  RUNNING = "running"
  SUSPECTED_STALL = "suspected_stall"
  STALLED = "stalled"
  COMPLETED = "completed"
  FAILED = "failed"
  CANCELLED = "cancelled"
  UNKNOWN = "unknown"

ValidationStatus
  PASS = "pass"
  WARNING = "warning"
  BLOCK = "block"

RecoveryAction
  NO_ACTION = "no_action"
  RETRY_NEW_RUN = "retry_new_run"
  RETRY_WITH_PARAMETER_CHANGE = "retry_with_parameter_change"
  SKIP_OPTIONAL_SOURCE = "skip_optional_source"
  REQUEST_APPROVAL = "request_approval"
  STOP_AND_REPORT = "stop_and_report"

SupervisorStatus
  IDLE = "idle"
  PREPARING = "preparing"
  RUNNING = "running"
  VALIDATING = "validating"
  DIAGNOSING = "diagnosing"
  DECIDING = "deciding"
  AWAITING_APPROVAL = "awaiting_approval"
  RECOVERING = "recovering"
  GATING = "gating"
  COMPLETED = "completed"
  BLOCKED = "blocked"
  FAILED = "failed"
  CANCELLED = "cancelled"

SupervisorEventType
  START = "start"
  PREPARED = "prepared"
  PREPARE_FAILED = "prepare_failed"
  MONITOR_TICK = "monitor_tick"
  PROCESS_EXITED = "process_exited"
  STALL_DETECTED = "stall_detected"
  VALIDATION_PASSED = "validation_passed"
  VALIDATION_BLOCKED = "validation_blocked"
  ANOMALY_DETECTED = "anomaly_detected"
  ANOMALY_READY = "anomaly_ready"
  DIAGNOSIS_FAILED = "diagnosis_failed"
  RETRY_DECIDED = "retry_decided"
  APPROVAL_REQUIRED = "approval_required"
  STOP_DECIDED = "stop_decided"
  APPROVED = "approved"
  REJECTED = "rejected"
  NEW_RUN_STARTED = "new_run_started"
  RECOVERY_FAILED = "recovery_failed"
  GATE_ALLOWED = "gate_allowed"
  GATE_BLOCKED_RECOVERABLE = "gate_blocked_recoverable"
  GATE_BLOCKED_FINAL = "gate_blocked_final"
  CANCEL_REQUESTED = "cancel_requested"
  FATAL_ERROR = "fatal_error"

RiskLevel
  LOW = "low"
  MEDIUM = "medium"
  HIGH = "high"
```

`ANOMALY_DETECTED` 是 `SupervisorEventType` 事件，不是 `SupervisorStatus` 状态。

当前纯状态机定义 12 个 `SupervisorCommand`：

```text
CALL_ADAPTER
START_EXECUTOR
POLL_OBSERVER
CALL_VALIDATOR
CALL_GATE
CALL_ANOMALY_BUILDER
CALL_RECOVERY_CONTROLLER
WAIT_FOR_APPROVAL
EXECUTE_RECOVERY
RECORD_OUTCOME
CANCEL
FAIL
```

当前转换表共有 40 条规则。进入 `RECOVERING` 时统一消耗一次恢复预算：自动重试和用户批准后的重试均令 `recovery_attempts + 1`、`recovery_budget_remaining - 1`；剩余预算为 0 时不得进入 `RECOVERING`。

不要为展示文案增加中文枚举值；中文由 UI 映射。

## 4. 七个顶层合同

### C5. `RunContext`

用途：描述一次 Find 在启动前已经确定的方案、输入、参数、政策和经验应用情况。它不保存 Executor 启动后才产生的真实运行事实。

字段：

```text
schema_version: str                       固定 find.run_context.v1
context_id: str                           必填，唯一
attempt_index: int                        必填，>= 0
project_id: str | None                    必填字段，可为空；无项目的 CLI 调用使用 None
stage: str                                固定 find
request_source: str                       必填，web/cli/full_cycle
created_at: datetime                      必填
producer: str                             必填
producer_version: str                     必填

research_topic: str                       必填
researcher_profile_path: str              可选
researcher_profile_fingerprint: str       可选
selection_snapshot_path: str              必填
selection: mapping                        必填，JSON-compatible

entrypoint: str                           固定 modules/finding/main.py
action: str                               固定 find
command_redacted: list[str]               必填
working_directory: str                    必填
python_executable: str                    必填
conda_env: str                            可选
model_id: str                             可选
environment_fingerprint: str              可选

config_snapshot_path: str                 必填，必须是脱敏快照
requested_parameters: mapping             必填
effective_parameters: mapping             必填
parameter_changes: list[ParameterChange]  必填，默认空

expected_artifacts: list[ArtifactRef]      必填

startup_grace_seconds: int                必填，> 0
stall_suspect_seconds: int                必填，> startup_grace_seconds
stall_confirm_seconds: int                必填，> stall_suspect_seconds
recovery_budget: int                      必填，>= 0
allowed_recovery_actions: list[RecoveryAction] 必填
approval_risk_threshold: RiskLevel        必填
validation_policy_version: str            必填

experience_query: ExperienceQuery         必填，启动前经验查询合同
matched_experience_refs: list[ExperienceRef] 必填，默认空
applied_experience_refs: list[ExperienceRef] 必填，默认空
experience_parameter_changes: list[ParameterChange] 必填，默认空
```

约束：

- `effective_parameters` 是实际执行值，不能含密钥；
- `applied_experience_refs` 必须是 `matched_experience_refs` 的子集；
- RunContext 创建后不修改；重试会创建新的启动前方案和新的 `attempt_index`；
- 真实 `run_id`、`root_run_id`、`run_dir`、PID、退出码和实际产物位置由 Find 启动后产生，后续进入 ExecutionHandle、SupervisorState 和观察类合同。

### C6. `ProgressSnapshot`

用途：表示 Observer 在一个具体时刻看到的 Find 状态。

字段：

```text
schema_version: str                       固定 find.progress_snapshot.v1
snapshot_id: str                          必填
run_id: str                               必填
created_at: datetime                      必填，等同 observed_at 或明确区分
observed_at: datetime                     必填
sequence: int                             必填，>= 0
producer: str                             必填
producer_version: str                     必填

status: ProgressStatus                    必填
phase: str                                必填，标准化阶段
raw_phase: str                            可选
current: int | None                       可选，>= 0
total: int | None                         可选，>= 0
percent: int | None                       可选，0 到 100
message: str                              可选
counts: mapping[str, int]                 必填，值必须 >= 0

run_started_at: datetime | None           可选
progress_updated_at: datetime | None      可选
last_meaningful_change_at: datetime | None 可选
elapsed_seconds: float                    必填，>= 0
seconds_without_progress: float           必填，>= 0
phase_elapsed_seconds: float | None       可选，>= 0

process_alive: bool                       必填
pid: int | None                           可选，> 0
process_started_at: datetime | None       可选
exit_code: int | None                     可选
cancel_requested: bool                    必填
termination_signal: str                   可选

artifact_observations: list[ArtifactRef]  必填
progress_parse_ok: bool                   必填
result_exists: bool                       必填
result_size_bytes: int | None             可选，>= 0
source_status_exists: bool                必填

source_total: int                         必填，>= 0
source_ready: int                         必填，>= 0
source_limited: int                       必填，>= 0
source_failed: int                        必填，>= 0
source_signals: list[str]                 必填，默认空

signals: list[str]                        必填，默认空
status_reason: str                        必填
observation_errors: list[str]             必填，默认空
evidence_refs: list[EvidenceRef]           必填，默认空
```

约束：

- 同时有 `current` 和 `total` 时必须满足 `current <= total`；
- `percent` 必须在 0 到 100；
- `process_alive=true` 时通常不得带终态 `exit_code`；
- `status=completed` 只表示观察到完成信号，不代替结果验证；
- Observer 自己读取文件失败写入 `observation_errors`，不能直接伪装成 Find 失败。

### C7. `ValidationResult`

用途：表示 Validator 对精确 Find 运行目录和最终产物的确定性检查。

字段：

```text
schema_version: str                       固定 find.validation_result.v1
validation_id: str                        必填
run_id: str                               必填
created_at: datetime                      必填
validated_at: datetime                    必填
validated_run_dir: str                    必填
producer: str                             必填
producer_version: str                     必填
policy_version: str                       必填
duration_ms: int                          必填，>= 0

status: ValidationStatus                  必填
ready_for_read: bool                      必填
summary: str                              必填
checks: list[ValidationCheck]              必填，至少一项
passed_check_count: int                   必填，>= 0
warning_check_count: int                  必填，>= 0
blocked_check_count: int                  必填，>= 0

recommendation_target_count: int          必填，>= 0
recommendation_actual_count: int          必填，>= 0
recommendation_shortfall: int             必填，>= 0
strong_recommendation_count: int          必填，>= 0
recommendation_quality_status: str        必填
candidate_ids: list[str]                  必填
candidate_digest: str                     必填

downstream_stage: str                     固定 read
bridge_probe_status: ValidationStatus      必填
bridge_probe_errors: list[str]             必填，默认空
downstream_input_preview: mapping          可选，只允许轻量摘要

warnings: list[str]                       必填，默认空
blockers: list[str]                       必填，默认空
failure_codes: list[str]                  必填，默认空
evidence_refs: list[EvidenceRef]           必填，默认空
input_artifact_refs: list[ArtifactRef]     必填
```

第一版至少支持这些检查代码：

```text
run_dir_exists
manifest_exists
manifest_layout_supported
progress_exists
progress_parseable
progress_run_id_matches
progress_phase_complete
process_terminal
process_exit_code_ok
result_exists
result_parseable
result_run_id_matches
result_created_at_valid
strong_recommendations_present
recommendation_count_sufficient
recommendation_shortfall_acceptable
recommendation_quality_ok
real_abstracts_present
required_user_fields_present
source_status_present
source_integrity_not_blocking
reading_candidate_ids_unique
reading_bridge_probe_passed
```

约束：

- 任一必需检查为 `block` 时，顶层 `status=block` 且 `ready_for_read=false`；
- `ready_for_read=true` 时，顶层状态必须为 `pass`；
- `passed/warning/blocked_check_count` 必须与 checks 重新统计结果一致；
- `candidate_ids` 不得重复；
- 同一输入、同一 policy version 必须产生相同结果。

### C8. `Anomaly`

用途：把进度、进程、日志和验证结果整理成一个带证据的异常，不包含恢复决定。

字段：

```text
schema_version: str                       固定 find.anomaly.v1
anomaly_id: str                           必填
run_id: str                               必填
created_at: datetime                      必填
detected_at: datetime                     必填
updated_at: datetime                      必填
producer: str                             必填
producer_version: str                     必填

stage: str                                必填
kind: str                                 必填，稳定机器码
blocking: bool                            必填
confidence: float                         必填，0 到 1
detected_by: list[str]                    必填，至少一个来源
progress_snapshot_id: str                 可选
validation_id: str                        可选
supervisor_state_revision: int            必填，>= 0

symptoms: list[str]                       必填，至少一项
evidence_refs: list[EvidenceRef]           必填，至少一项
process_facts: mapping                    必填，默认空
artifact_facts: mapping                   必填，默认空
timing_facts: mapping                     必填，默认空

root_cause_status: str                    必填，unknown/suspected/confirmed
root_cause: str                           可选
hypotheses: list[mapping]                 必填，默认空
missing_evidence: list[str]               必填，默认空

affected_phase: str                       必填
affected_sources: list[str]               必填，默认空
affected_artifacts: list[str]             必填，默认空
downstream_impact: str                    必填
partial_results_usable: bool              必填

recovery_eligible: bool                   必填
retryable_signal: bool                    必填
fingerprint: str                          必填
duplicate_of: str                         可选
occurrence_count: int                     必填，>= 1
```

第一版 `kind` 允许的稳定代码至少包括：

```text
startup_failed
progress_missing
progress_unparseable
progress_stalled
process_exited_nonzero
completion_without_result
result_missing
result_unparseable
run_id_mismatch
empty_recommendations
recommendation_shortfall
source_integrity_blocked
reading_bridge_rejected
```

约束：

- 至少一条症状和一条证据；
- `root_cause_status=confirmed` 时必须有非空 root_cause 和直接证据；
- Anomaly 不得包含 `action` 或执行命令；
- `confidence` 不是恢复风险。

### C9. `RecoveryDecision`

用途：表示 Recovery Controller 针对一个 Anomaly 做出的受控决定，不表示动作已经成功。

字段：

```text
schema_version: str                       固定 find.recovery_decision.v1
decision_id: str                          必填
run_id: str                               必填，失败运行
anomaly_id: str                           必填
created_at: datetime                      必填
decided_at: datetime                      必填
producer: str                             必填
producer_version: str                     必填

action: RecoveryAction                    必填
reason: str                               必填
risk_level: RiskLevel                     必填
executable: bool                          必填
target_phase: str                         可选
target_sources: list[str]                 必填，默认空
new_run_required: bool                    必填

action_parameters: mapping                必填，默认空
parameter_changes: list[ParameterChange]  必填，默认空
preserve_artifacts: list[ArtifactRef]      必填，默认空
proposed_new_run_id: str                  可选
command_preview_redacted: list[str]       必填，默认空

matched_playbook_id: str                  可选
matched_experience_refs: list[ExperienceRef] 必填，默认空
evidence_of_previous_success: list[EvidenceRef] 必填，默认空
exploratory: bool                         必填

requires_approval: bool                   必填
approval_status: str                      必填，not_required/pending/approved/rejected
approval_reason: str                      可选
approved_by: str                          可选
approved_at: datetime | None              可选

attempt_index: int                        必填，>= 1
budget_before: int                        必填，>= 0
budget_cost: int                          必填，>= 0
budget_after: int                         必填，>= 0
max_same_action_attempts: int             必填，>= 0

preconditions: list[str]                  必填，默认空
verification_policy: str                  必填
required_post_checks: list[str]           必填
success_definition: str                   必填
stop_if_failed: bool                      必填
```

约束：

- `retry_new_run` 和 `retry_with_parameter_change` 必须 `new_run_required=true`；
- 重试动作可执行时必须有 `proposed_new_run_id`；
- `risk_level=high` 且未批准时必须 `executable=false`；
- `budget_after = budget_before - budget_cost`；
- 预算不足时不能生成可执行重试；
- `stop_and_report` 不得包含参数修改；
- `no_action` 不得声称新运行或消耗恢复预算。

### C10. `ExperienceCase`

用途：记录正常运行、技术恢复或用户偏好案例。它是 Experience Store 将来保存的基本记录，但本阶段不实现 Store。

字段：

```text
schema_version: str                       固定 find.experience_case.v1
case_id: str                              必填
case_type: str                            必填，normal/technical/preference
stage: str                                固定 find
created_at: datetime                      必填
updated_at: datetime                      必填
producer: str                             必填
producer_version: str                     必填
verified: bool                            必填
deprecated: bool                          必填

context_id: str                           必填
root_run_id: str                          必填
final_run_id: str                         必填
project_id: str                           可选
environment_fingerprint: str              可选
context_tags: list[str]                   必填，默认空

anomaly_id: str                           条件可选
anomaly_kind: str                         条件可选
anomaly_fingerprint: str                  条件可选
root_cause_status: str                    必填
confirmed_root_cause: str                 可选
evidence_refs: list[EvidenceRef]           必填

decision_id: str                          条件可选
recovery_action: RecoveryAction           条件可选
parameter_changes: list[ParameterChange]  必填，默认空
approval_record: mapping                  必填，默认空
attempt_count: int                        必填，>= 0
playbook_id: str                          可选

outcome: str                              必填，success/recovered/failed/partial/cancelled
execution_started_at: datetime | None     可选
execution_finished_at: datetime | None    可选
exit_code: int | None                     可选
duration_seconds: float | None             可选，>= 0
new_run_ids: list[str]                    必填，默认空
failure_reason: str                       可选

validation_before_id: str                 可选
validation_before_status: ValidationStatus 可选
validation_after_id: str                  必填
validation_after_status: ValidationStatus 必填
resolved_failure_codes: list[str]         必填，默认空
remaining_failure_codes: list[str]        必填，默认空
ready_for_read_after: bool                必填

applicability_conditions: list[str]       必填，默认空
non_applicable_conditions: list[str]      必填，默认空
risk_level: RiskLevel                     必填
recommended_action: RecoveryAction        可选
confidence: float                         必填，0 到 1

user_feedback: str                        可选
user_labels: list[str]                    必填，默认空
user_satisfied: bool | None               可选
preference_scope: list[str]               必填，默认空

matched_count: int                        必填，>= 0
applied_count: int                        必填，>= 0
successful_application_count: int         必填，>= 0
last_matched_at: datetime | None          可选
last_applied_at: datetime | None          可选
```

约束：

- `outcome=recovered` 必须 `verified=true`、`validation_after_status=pass` 且 `ready_for_read_after=true`；
- `case_type=technical` 必须有关联 anomaly；
- `case_type=preference` 必须有 user_feedback、user_labels 或 user_satisfied 中至少一项；
- `successful_application_count <= applied_count <= matched_count`；
- 用户取消不能自动标记为系统错误；
- 未验证案例不能成为自动恢复依据。

### C11. `SupervisorState`

用途：保存当前 Find Feedback 控制器的总状态。只保存其他合同 ID 引用，不重复嵌入所有完整对象。

字段：

```text
schema_version: str                       固定 find.supervisor_state.v1
supervisor_id: str                        必填
project_id: str | None                    必填字段，可为空；与 RunContext.project_id 对齐
root_run_id: str                          必填
active_run_id: str                        可选
status: SupervisorStatus                  必填
state_revision: int                       必填，>= 0
created_at: datetime                      必填
updated_at: datetime                      必填
heartbeat_at: datetime                    必填
producer: str                             必填
producer_version: str                     必填

run_context_id: str | None                可选；IDLE、PREPARING 和早期失败/取消可为空
latest_progress_snapshot_id: str          可选
latest_validation_id: str                 可选
active_anomaly_id: str                    可选
active_recovery_decision_id: str          可选
experience_case_id: str                   可选

active_pid: int | None                    可选，> 0
process_started_at: datetime | None       可选
process_alive: bool                       必填
exit_code: int | None                     可选
cancel_requested: bool                    必填
cancel_requested_at: datetime | None      可选

recovery_attempts: int                    必填，>= 0
recovery_budget_total: int                必填，>= 0
recovery_budget_remaining: int            必填，>= 0
last_recovery_action: RecoveryAction       可选
awaiting_approval: bool                   必填
pending_approval_decision_id: str         可选

gate_evaluated: bool                      必填
allow_read: bool                          必填
gate_reason: str                          必填
gate_validation_id: str                   可选

terminal: bool                            必填
terminal_reason: str                      可选
final_status: SupervisorStatus            可选
diagnostic_report_path: str               可选
last_error: str                           可选

event_sequence: int                       必填，>= 0
recent_events: list[SupervisorEvent]       必填，默认空
state_path: str                           必填
```

约束：

- `recovery_budget_remaining <= recovery_budget_total`；
- `recovery_attempts + recovery_budget_remaining` 不得超过可解释预算；
- 只有进入 `RECOVERING` 时才消耗一次恢复预算；`NEW_RUN_STARTED` 和 `RECOVERY_FAILED` 不重复扣除；
- `allow_read=true` 时必须已经 gate_evaluated，并引用一个通过的 ValidationResult；
- `status=recovering` 时必须有关联 anomaly 和 recovery decision；
- `status=awaiting_approval` 时必须有 pending approval decision；
- `terminal=true` 时进程不能存活；
- `state_revision` 只能递增；
- recent_events 按 sequence 升序且不重复。

## 5. 序列化要求

### C12. JSON 转换

所有顶层合同和内嵌结构提供一致接口，具体命名可以根据项目风格选择，但必须支持：

```text
to_dict()
from_dict()
to_json()
from_json()
```

要求：

- JSON 输出 UTF-8，`ensure_ascii=False`；
- 输出键顺序稳定；
- 枚举序列化为 `.value`；
- datetime 序列化为带时区 ISO 8601；
- 反序列化恢复为正确 Enum、datetime 和内嵌 dataclass；
- round-trip 后对象等价；
- 不在 JSON 中输出 Python `Path`、Enum repr 或 datetime repr；
- 错误信息指出合同名和字段路径，例如 `RunContext.recovery_budget must be >= 0`。

如实现公共基类或 helper，保持短小明确，不使用难以审计的元编程。

## 6. 测试要求

### C13. 枚举测试

验证：

- 所有枚举的字符串值完全符合本提示词；
- 枚举能正确 JSON 序列化；
- 非法枚举值反序列化失败。

### C14. 可复用内嵌数据结构测试

覆盖：

- ArtifactRef 非负大小；
- EvidenceRef 行号范围；
- ExperienceRef similarity 范围；
- ValidationCheck 的 Enum round-trip；
- datetime 必须带时区。

### C15. 七个合同 round-trip 测试

为每个合同构造一个最小合法对象：

```text
object -> dict -> JSON -> object
```

验证关键字段、枚举、时间和内嵌结构完全恢复。

### C16. 不变量测试

至少覆盖：

```text
RunContext
  启动前方案不包含 run_id、root_run_id 或 run_dir
  启动前必填字段为空
  停滞阈值顺序错误
  applied experience 不是 matched 子集

ProgressSnapshot
  percent < 0 或 > 100
  current > total
  counts 中出现负数

ValidationResult
  blockers 存在但 ready_for_read=true
  顶层计数与 checks 不一致
  candidate_ids 重复

Anomaly
  没有 symptoms
  没有 evidence
  confirmed root cause 没有根因或证据

RecoveryDecision
  retry 没有新 run_id
  高风险未批准却 executable=true
  预算计算错误
  stop 动作带参数修改

ExperienceCase
  recovered 但最终验证不是 pass
  technical 案例没有 anomaly
  使用统计顺序错误

SupervisorState
  allow_read=true 但没有验证引用
  recovering 但没有 anomaly/decision
  terminal=true 但 process_alive=true
  剩余预算大于总预算
```

### C17. 与真实 Find 结构的轻量兼容测试

在测试代码中手工构造与当前真实 `find_progress.json` 相同形状的最小字典，至少包含：

```json
{
  "run_id": "find_20260810_062018_982678",
  "updated_at": "2026-08-10T06:39:24.059402Z",
  "phase": "complete",
  "counts": {
    "raw_title_index_papers": 20,
    "llm_scored_candidates": 18
  }
}
```

以及运行中形状：

```json
{
  "run_id": "find_20260822_051206_346734",
  "updated_at": "2026-08-22T05:25:57.495977Z",
  "phase": "llm_title_filter",
  "live_progress": {
    "phase": "llm_title_filter",
    "current": 36,
    "total": 54,
    "percent": 67,
    "message": "ICML: scoring title batch 37/54"
  }
}
```

本阶段只验证合同能够承载这些信息，不实现从文件读取并转换成 ProgressSnapshot 的 Observer。

### 合同以后怎样验证能被组件正确使用

本阶段的 `tests/feedback/test_contracts.py` 只验证第一层：字段校验、枚举、JSON round-trip 和跨字段不变量。后续实现外围组件时按下面的层次继续补测试：

```text
第一层：合同自身测试
  tests/feedback/test_contracts.py

第二层：单个组件输入输出测试
  例如 Observer 读取固定 find_progress.json，输出合法 ProgressSnapshot
  例如 Validator 读取固定 find_results.json，输出合法 ValidationResult

第三层：组件连接测试
  验证 ProgressSnapshot -> Anomaly -> RecoveryDecision 可以直接传递，
  不出现字段缺失、字段类型不同或相同字段含义不一致。

第四层：Feedback 集成测试
  使用成功、停滞、缺失结果和损坏 JSON 等固定样本，
  验证最终 Gate 决定及恢复动作符合预期。
```

真实运行样本应复制、脱敏并缩减为固定 fixture，例如：

```text
tests/fixtures/find_feedback/
├── successful_run/
├── stalled_run/
├── missing_result/
└── invalid_result/
```

若现有 Find 没有系统性错误，通过故障注入构造样本：删除结果字段、截断 JSON、制造过期更新时间、令 `current > total`、模拟非零退出码或来源全部失败。测试只读取 fixture，不修改真实 `.runtime`，也不为了制造错误而启动真实 Find。

## 7. 执行顺序

严格按以下顺序工作：

```text
C1  检查真实产物和仓库状态
C2  建立标准库实现方式
C3  实现合同内部可复用的数据结构
C4  实现合同字段使用的枚举
C5  RunContext
C6  ProgressSnapshot
C7  ValidationResult
C8  Anomaly
C9  RecoveryDecision
C10 ExperienceCase
C11 SupervisorState
C12 JSON 序列化与反序列化
C13-C17 单元测试和兼容形状测试
```

每完成一个合同，立即补对应测试，不要最后一次性补测试。

## 8. 验证命令

先确认项目 Python，再执行最小测试：

```text
python -m pytest tests/feedback/test_contracts.py -q
```

然后执行现有相关合同测试，具体文件存在时再运行：

```text
python -m pytest tests/test_taste_contracts.py -q
python -m pytest tests/test_web_framework_bridge.py -q
```

最后执行：

```text
git diff --check
git status --short
```

测试不得启动真实 Find、访问网络、调用 Claude、写入 `.runtime` 或启动网页服务。

## 9. 验收标准

满足以下条件才算本阶段完成：

1. 6 个枚举和 7 个顶层合同可从稳定路径导入。
2. 所有合同能够严格 JSON round-trip。
3. 必填字段、枚举、时间、数值范围和跨字段不变量得到验证。
4. 测试覆盖正常和关键非法情况。
5. 没有修改 Find、Read 和 Framework 原流程。
6. 没有引入外部依赖。
7. 没有写入真实运行目录。
8. 没有远端 Git 操作，也没有自动 commit。
9. 最终报告列出：修改文件、设计取舍、测试命令、测试结果、仍未实现的后续组件。

## 10. 明确排除项

本次不要实现：

```text
Progress Observer 文件轮询
Result Validator 的真实产物检查逻辑
Anomaly Builder 分类逻辑
Recovery Controller 恢复策略
Framework Gate
Workflow Executor
Experience Store 存储或检索
Supervisor 运行循环
Web UI
Find/Read 源码改造
```

这些属于数据合同完成后的下一阶段。
