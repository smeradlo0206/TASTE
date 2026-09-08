# 第 02 批：建立 JSON 转换基础

## 给 Codex 的提示词

先完整读取总提示词，确认第 01 批测试通过。本批只实现 C2、C12 所需的最小公共转换基础。

允许修改：

```text
framework/scripts/feedback/contracts.py
tests/feedback/test_contracts.py
```

任务：

1. 在 `contracts.py` 中实现短小、可审计的私有转换与校验辅助函数。
2. 支持 Enum、带时区 datetime、列表、字典和后续内嵌 dataclass 的 JSON-compatible 转换。
3. 时间输出 ISO 8601；拒绝无时区 datetime。
4. 提供后续合同统一使用的 `to_dict/from_dict/to_json/from_json` 基础方式。
5. V1 解析拒绝未知字段；缺少字段时错误信息必须包含合同名和字段路径。
6. 不使用复杂元编程，不添加独立业务组件。
7. 测试枚举、时间、UTF-8、稳定键顺序、未知字段和缺失字段。

本批不实现 C3 的具体结构和 C5-C11 合同。测试通过后停止。
