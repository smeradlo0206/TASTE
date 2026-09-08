# 第 05 批：实现 ProgressSnapshot

## 给 Codex 的提示词

完整读取总提示词 C6，确认第 04 批测试通过。本批只实现 `ProgressSnapshot`。

任务：

1. 严格实现 C6 的全部字段、默认值和类型。
2. 实现 current/total、percent、counts、PID、耗时和产物大小等数值约束。
3. 保留“观察完成不等于验证通过”的语义，不增加 `ready_for_read`。
4. Observer 自身错误只能进入 `observation_errors`，不得改变为虚假的 Find 根因。
5. 支持 `ArtifactRef`、`EvidenceRef`、Enum、datetime 的 JSON round-trip。
6. 更新公共导出。
7. 增加最小合法、运行中、完成观察和关键非法值测试。

不得读取 `find_progress.json`，不得实现 Observer。测试通过后停止。
