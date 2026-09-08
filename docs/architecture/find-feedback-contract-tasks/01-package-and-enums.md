# 第 01 批：建立合同包和枚举

## 给 Codex 的提示词

先完整读取总提示词和第 00 批审计结果。本批只实现 C2 中的基本约束和 C4 的五个枚举。

允许修改：

```text
framework/scripts/feedback/__init__.py
framework/scripts/feedback/contracts.py
tests/feedback/test_contracts.py
```

任务：

1. 建立最小 `feedback` 包和测试文件。
2. 使用标准库定义 `ProgressStatus`、`ValidationStatus`、`RecoveryAction`、`SupervisorStatus`、`RiskLevel`。
3. 枚举必须继承 `str, Enum`，成员名和字符串值严格采用总提示词 C4。
4. 在 `__init__.py` 只导出本批已经实现的公共名称。
5. 测试全部枚举值、字符串行为和非法枚举值。

不得提前实现内嵌结构或七个顶层合同，不得新增第三方依赖。

执行本批定向测试并报告结果；测试通过后停止。
