# 第 00 批：仓库与真实 Find 产物只读审计

## 给 Codex 的提示词

完整读取 `docs/architecture/find-feedback-data-contracts-codex-prompt.md`，本批只执行其中 C1 和开发前安全检查。

任务：

1. 执行 `git status --short --branch`、`git branch --show-current`、`git worktree list`。
2. 记录当前未提交修改，确认后续不得覆盖这些修改。
3. 只读检查以下运行目录：
   - `modules/finding/.runtime/runs/find_20260810_062018_982678`
   - `modules/finding/.runtime/runs/find_20260822_051206_346734`
4. 确认 `find_progress.json`、`find_results.json`、`source_status.md`、`manifest.json` 的实际位置、存在情况和顶层字段。
5. 确认成功、运行中或不完整运行的字段差异。
6. 检查仓库是否已有 `feedback` 包、同名合同或测试约定。

本批禁止修改任何源码、测试和运行产物；禁止启动 Find、Read、网页服务或 Claude。

输出一份简短审计报告，必须包含：

- 当前分支和 worktree；
- 需要保护的现有修改；
- 两个运行目录的产物表；
- 已确认的真实字段；
- 第 01 批可使用的准确开发路径；
- 发现的阻塞项。

报告完成后停止，不进入第 01 批。
