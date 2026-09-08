# 第 08 批：实现 RecoveryDecision

## 给 Codex 的提示词

完整读取总提示词 C9，确认第 07 批测试通过。本批只实现 `RecoveryDecision`。

任务：

1. 严格实现 C9 全部字段。
2. 实现重试、新 run_id、高风险审批、预算计算、stop/no_action 的全部不变量。
3. 明确 decision 只是决定，不代表恢复已经执行或成功。
4. 命令预览必须使用脱敏字段，不处理 API Key。
5. 支持 ParameterChange、ArtifactRef、ExperienceRef、EvidenceRef 和枚举的 JSON round-trip。
6. 更新公共导出。
7. 增加低风险合法决定、高风险待审批、预算不足、重试缺少 run_id 等测试。

不得执行任何恢复动作。测试通过后停止。
