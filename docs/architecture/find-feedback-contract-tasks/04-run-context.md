# 第 04 批：实现 RunContext

## 给 Codex 的提示词

完整读取总提示词 C5，确认第 03 批测试通过。本批只实现 `RunContext`。

任务：

1. 严格实现 C5 列出的全部字段，不得自行删减或改名。
2. 固定 `schema_version=find.run_context.v1`、`stage=find`、`entrypoint=modules/finding/main.py`、`action=find`。
3. 实现 C5 的全部跨字段约束，包括启动前必填输入、停滞阈值、经验引用子集和脱敏要求；真实 run_id/run_dir、父运行关系和实际产物路径不属于 RunContext。
4. 映射和列表避免共享可变对象。
5. 支持内嵌 `ArtifactRef`、`ExperienceRef`、`ParameterChange` 的严格 JSON round-trip。
6. 更新公共导出。
7. 增加一个最小合法对象测试及 C16 中属于 RunContext 的非法情况测试。

只修改合同包和合同测试。不要实现 Observer 或读取真实配置。测试通过后停止。
