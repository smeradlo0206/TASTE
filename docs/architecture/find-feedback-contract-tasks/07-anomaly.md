# 第 07 批：实现 Anomaly

## 给 Codex 的提示词

完整读取总提示词 C8，确认第 06 批测试通过。本批只实现 `Anomaly`。

任务：

1. 严格实现 C8 全部字段和稳定 kind 代码。
2. 至少要求一条症状、一条证据和一个检测来源。
3. confidence 限制为 0 到 1。
4. confirmed 根因必须同时具有非空 root_cause 和直接证据。
5. 合同中不得出现恢复 action、命令或执行结果。
6. 支持 EvidenceRef 和嵌套映射 JSON round-trip。
7. 更新公共导出，并增加合法、缺少症状、缺少证据、错误 confirmed 根因等测试。

不得实现 Anomaly Builder 分类逻辑。测试通过后停止。
