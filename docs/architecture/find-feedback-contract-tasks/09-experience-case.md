# 第 09 批：实现 ExperienceCase

## 给 Codex 的提示词

完整读取总提示词 C10，确认第 08 批测试通过。本批只实现 `ExperienceCase`。

任务：

1. 严格实现 C10 全部字段。
2. 区分 normal、technical、preference 三类案例的条件字段。
3. 实现 recovered、验证结果、ready_for_read_after 之间的不变量。
4. 实现 matched/applied/successful 统计顺序约束。
5. 未验证案例不得被表达为可自动应用的成功依据。
6. 支持所有内嵌结构、枚举和 datetime JSON round-trip。
7. 更新公共导出，增加三种案例的最小合法测试和关键非法测试。

不得实现 Experience Store 的文件保存、搜索或匹配。测试通过后停止。
