# 第 06 批：实现 ValidationResult

## 给 Codex 的提示词

完整读取总提示词 C7，确认第 05 批测试通过。本批只实现 `ValidationResult`。

任务：

1. 严格实现 C7 全部字段和第一版检查代码常量或稳定表示。
2. 实现 required check、顶层 status、ready_for_read 和检查计数之间的不变量。
3. 校验 candidate_ids 不重复，推荐数量和 shortfall 非负。
4. 保持结果确定性：合同层不得读取文件或执行 reading bridge。
5. 支持 `ValidationCheck`、`EvidenceRef`、`ArtifactRef` 的 JSON round-trip。
6. 更新公共导出。
7. 增加 pass、warning、block 和 C16 冲突情况测试。

不得实现 Result Validator 的真实检查逻辑。测试通过后停止。
