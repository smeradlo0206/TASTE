# 第 10 批：实现 SupervisorState

## 给 Codex 的提示词

完整读取总提示词 C11，确认第 09 批测试通过。本批只实现 `SupervisorState`。

任务：

1. 严格实现 C11 全部字段。
2. 只引用其他合同 ID，不把其他顶层合同完整嵌入状态对象。
3. 实现预算、Gate、recovering、awaiting approval、terminal/process_alive 等不变量。
4. 校验 recent_events 的 sequence 升序且不重复。
5. 支持 SupervisorEvent、枚举和 datetime JSON round-trip。
6. 更新公共导出。
7. 增加准备、运行、等待审批、恢复、完成等代表性合法状态，以及 C16 非法状态测试。

不得实现 Supervisor 循环、进程管理或 Framework Gate。测试通过后停止。
