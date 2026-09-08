# 第 13 批：最终回归与边界检查

## 给 Codex 的提示词

完整读取总提示词的验收标准和排除项，确认第 12 批通过。本批只做最终验证和必要的小修复。

任务：

1. 运行 `python -m pytest tests/feedback/test_contracts.py -q`。
2. 存在时运行 `tests/test_taste_contracts.py` 和 `tests/test_web_framework_bridge.py`。
3. 运行 `git diff --check` 和 `git status --short`。
4. 检查没有新增第三方依赖、没有写真实运行目录、没有启动进程或访问网络。
5. 检查没有修改 Find、Read、Framework 原流程和用户已有无关修改。
6. 检查 5 个枚举、6 个内嵌结构、7 个顶层合同均可从稳定路径导入。
7. 只修复本阶段测试暴露的问题，不实现任何排除项。

最终报告必须列出：

- 修改文件；
- 各批次完成情况；
- 测试命令和结果；
- 关键设计取舍；
- 尚未实现的 Observer、Validator、Anomaly Builder、Recovery Controller、Gate、Store 和 Supervisor 逻辑；
- 本地 Git 状态。

不得 commit、push 或创建 PR。报告后停止。
