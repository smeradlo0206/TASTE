# 第 12 批：真实 Find 形状兼容测试

## 给 Codex 的提示词

完整读取总提示词 C1、C17，确认第 11 批测试通过。本批只验证合同能承载真实 Find 的字段形状，不实现 Observer。

任务：

1. 只读核对已有成功和不完整 Find 产物；不得让测试直接依赖 `.runtime`。
2. 在测试代码中构造总提示词 C17 的成功和运行中最小字典。
3. 验证这些原始字段可以明确映射到 RunContext 或 ProgressSnapshot 所需信息。
4. 若必须增加 fixture，只能写入 `tests/fixtures/find_feedback/`，必须脱敏、缩减且稳定。
5. 增加故障注入样本：缺少结果、损坏 JSON、过期更新时间、`current > total` 中至少两类。
6. 测试不得启动 Find、访问网络、调用 Claude 或写 `.runtime`。

如果合同无法承载真实字段，只做最小合同修正并明确报告原因。兼容测试通过后停止。
