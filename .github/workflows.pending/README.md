# 为什么 workflow 在 `workflows.pending/`

GitHub 只认 `.github/workflows/*.yml`，但**写入该目录需要一个带 `workflow` scope 的凭据**。本次实现使用的推送凭据只有 `repo` 权限，GitHub 明确拒绝：

```
! [remote rejected] feat/issue-1 -> feat/issue-1
  (refusing to allow a Personal Access Token to create or update workflow
   `.github/workflows/ci.yml` without `workflow` scope)
```

为了让 PR 能包含完整实现（代码、测试、报告、模板），6 个 workflow 先放在本目录，内容与最终版本一致。

## 启用步骤（维护者执行一次）

```bash
git mv .github/workflows.pending/*.yml .github/workflows/
git commit -m "ci: 启用 GitHub Actions workflows"
git push
```

或者给机器人凭据补上 `workflow` scope 后，由 coding agent 重跑一次（把目录改名并推送）。

## 启用前自检

```bash
pytest -q tests/test_workflows.py -v
```

`tests/test_workflows.py` 会校验：6 个文件存在、YAML 合法、均含 `workflow_dispatch`、声明了最小权限、未使用 `pull_request_target`、敏感变量均来自 `secrets` 表达式。测试会自动识别 `.github/workflows/` 或 `.github/workflows.pending/`，改名后无需修改测试。

`pages.yml` 监听 `daily-search` 的 `workflow_run` 完成事件：由 `GITHUB_TOKEN` 推送的报告不会触发 `push` 工作流，必须靠该事件保证 Pages 随每日报告更新。另需在 **Settings → Actions → General → Workflow permissions** 勾选 **Allow GitHub Actions to create and approve pull requests**，订阅 PR 才能自动创建。