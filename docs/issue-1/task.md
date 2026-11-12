# Task · Issue #1 任务拆解与状态

## 交付清单

| # | 任务 | 产物 | 状态 |
| --- | --- | --- | --- |
| 1 | 项目骨架与打包 | `pyproject.toml`、`src/daily_search/__init__.py`、`py.typed` | ✅ |
| 2 | 配置校验（四级 schema） | `src/daily_search/config.py`、`topics.yaml` | ✅ |
| 3 | 日志脱敏 | `src/daily_search/logging_utils.py` | ✅ |
| 4 | Provider 抽象与注册/熔断 | `providers/base.py`、`providers/registry.py` | ✅ |
| 5 | so.qq.com Provider（Playwright） | `providers/soqq.py` | ✅ |
| 6 | RSS 兜底 Provider | `providers/fallback_rss.py` | ✅ |
| 7 | 离线 fixture Provider | `providers/fixture.py`、`fixtures/search-2026-11-11.json` | ✅ |
| 8 | URL 规范化 + 语义相似度 | `diff/normalize.py` | ✅ |
| 9 | 快照与双层去重 | `diff/snapshot.py`、`diff/dedupe.py` | ✅ |
| 10 | LLM 客户端（超时/重试/预算） | `agent/llm.py` | ✅ |
| 11 | 一搜一页摘要与降级 | `agent/summarize.py` | ✅ |
| 12 | Markdown / RSS 渲染 | `publish/markdown.py`、`publish/feed.py` | ✅ |
| 13 | 当日 Issue 与 IM 推送 | `publish/issue.py`、`publish/im.py` | ✅ |
| 14 | Pages 归档与原子写 | `publish/pages.py`、`publish/atomic.py` | ✅ |
| 15 | 运行编排 | `runner.py` | ✅ |
| 16 | CLI（run/spike/subscribe/comment/notify/pages） | `cli.py`、`__main__.py` | ✅ |
| 17 | 评论指令 Agent | `comment_agent.py` | ✅ |
| 18 | 订阅 Issue → topics.yaml | `subscribe.py` | ✅ |
| 19 | so.qq.com spike | `spike.py` + README 结论 | ✅ |
| 20 | 6 个 workflow | `.github/workflows.pending/*.yml`（启用步骤见该目录 README） | ✅ 内容就绪，**待移入 `.github/workflows/`** |
| 21 | 2 个 Issue 模板 | `.github/ISSUE_TEMPLATE/subscribe-topic.yml`、`report-false-positive.yml` | ✅ |
| 22 | 首份报告 + feed + 快照 | `reports/2026-11-11/`、`feed.xml`、`data/<slug>/latest.json` | ✅ |
| 23 | Skill 导出 | `skill/SKILL.md`、`skill/scripts/run_daily_search.sh` | ✅ |
| 24 | 文档 | `README.md`、`docs/issue-1/{spec,plan,task}.md` | ✅ |
| 25 | 测试与 CI | `tests/`（140 项）、`.github/workflows.pending/ci.yml` | ✅ |

## 验证命令

```bash
pytest -q                                              # 140 passed
python -m daily_search run --dry-run --no-issue         # 无 Secret 降级路径
python -m daily_search run --date 2026-11-11 --fixture fixtures/search-2026-11-11.json --no-issue
python -m daily_search spike --json                     # HTTP 200 / 未命中风控
```

## 遗留事项（需维护者）

1. 把 `.github/workflows.pending/*.yml` 移入 `.github/workflows/`（当前推送凭据缺 `workflow` scope）。
2. 配置 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`（可选：飞书/企微 webhook）。
3. 手动跑一次 `daily-search.yml`（可勾 `spike=true`），把海外 runner 的探测结论补进 README。
4. 启用 GitHub Pages（`pages.yml` 依赖仓库 Pages 设置）。