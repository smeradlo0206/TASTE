# 第 03 批：实现可复用内嵌数据结构

## 给 Codex 的提示词

先完整读取总提示词，确认前两批测试通过。本批只实现 C3 和相应 C14 测试。

实现以下结构，字段、类型、必填性和约束严格采用总提示词 C3：

```text
ArtifactRef
EvidenceRef
ExperienceRef
ParameterChange
ValidationCheck
SupervisorEvent
```

要求：

1. 它们是合同内部结构，不是可执行外围组件。
2. 可变字段使用安全的默认工厂，映射进入对象时浅复制。
3. 实现确定性字段校验。
4. 接入第 02 批的 JSON round-trip 能力。
5. 在 `__init__.py` 导出这六个结构。
6. 测试非负大小、证据行号、相似度范围、时区、枚举和嵌套 EvidenceRef 恢复。

只允许修改合同包和 `tests/feedback/test_contracts.py`。不得实现七个顶层合同。测试通过后停止。
