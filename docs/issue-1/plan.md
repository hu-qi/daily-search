# Plan · Issue #1 实现方案

## 1. 总体结构

单一 Python 包，唯一入口 `python -m daily_search <command>`；workflow 只做 checkout → run → 提交/发 Issue 的薄封装。

```
cli → config → providers → diff → agent → publish
```

依赖单向无环；`runner` 负责编排，`publish` 不反向依赖 provider。

## 2. 关键设计决策

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| so.qq.com 抓取 | Playwright headless chromium，45s 超时，重试 1 次 | 站点是 SPA，`requests` 拿不到结果 |
| 风控处理 | 连续 2 次 `ProviderBlocked` → 本次运行全量切兜底 | 与 Spec 一致，避免对风控页反复撞击 |
| 兜底源 | `fallback_rss`（可配置 RSS/Atom 列表） | 无凭据、可离线单测、不引入付费依赖 |
| 去重 | URL 规范化指纹 + 标题/摘要 3-gram Jaccard（默认 0.85） | 中文无需分词；两层互补 |
| 快照 | `data/<slug>/latest.json`，保留 14 天 | 单文件、易 review、冲突面小 |
| LLM | OpenAI 兼容 `/v1/chat/completions`，30s 超时 / 重试 2 / 8k 预算 | 与需求给定模型兼容；失败可降级 |
| LLM 输出校验 | 只接受与输入条目 URL 完全一致的要点 | 防止模型编造/改写链接 |
| 通知顺序 | Issue（必）→ feed（必）→ IM（可选，缺 Secret 跳过） | 与验收标准一致 |
| 原子写 | `.tmp` + `os.replace` | Actions 中断不留下半成品文件 |
| dry-run | 只读：不写报告/快照/feed，也不建 Issue | 便于本地与 CI 验证降级路径 |
| workflow 落地 | 暂存 `.github/workflows.pending/` | 推送凭据缺 `workflow` scope，见该目录 README |

## 3. 模块设计

| 模块 | 关键类型/函数 | 说明 |
| --- | --- | --- |
| `config.py` | `AppConfig` / `TopicConfig` / `Channels` / `load_config` | pydantic 校验；slug 由话题名派生；重复 slug 直接报错 |
| `providers/base.py` | `SearchResult` / `ProviderError` / `ProviderBlocked` / `SearchProvider` | 结果必须 http(s) 绝对地址 |
| `providers/soqq.py` | `SoqqProvider` / `parse_results` / `build_queries` | HTMLParser 抽外链；站内跳转过滤；验证码识别 |
| `providers/fallback_rss.py` | `FallbackRssProvider` / `parse_feed` | RSS 2.0 + Atom；关键词/排除词过滤 |
| `providers/registry.py` | `ProviderRegistry` | 顺序尝试、连续风控熔断、全失败返回空 + 原因 |
| `diff/normalize.py` | `normalize_url` / `url_hash` / `shingles` / `jaccard` | 追踪参数、host、scheme、尾斜杠、fragment 全归一 |
| `diff/snapshot.py` | `SnapshotStore` / `SnapshotEntry` | 读写 + 保留期裁剪；损坏文件降级为空 |
| `diff/dedupe.py` | `Deduplicator.split/commit` | 双层判重 + 统计 `repeat_rate` |
| `agent/llm.py` | `LLMSettings` / `LLMClient` | 预算估算、超时重试、4xx 不重试、异常不带凭据 |
| `agent/summarize.py` | `Summarizer` / `TopicDigest` | JSON 解析容错；失败/无链接降级为链接列表 |
| `publish/markdown.py` | `render_topic_report` / `render_daily_index` | 每条结论 `[标题](url)`；头部 `degraded` 与统计 |
| `publish/feed.py` | `render_feed` / `merge_items` | guid = `daily-search:<slug>:<date>`，保留 60 条 |
| `publish/issue.py` | `render_issue_body` / `IssuePublisher` | 按话题分组；同日 Issue 存在则更新 |
| `publish/im.py` | `send_notifications` | 缺 webhook 跳过并 warn；失败不影响主流程 |
| `publish/pages.py` | `build_site` | 复制报告 + feed + 生成索引页 |
| `runner.py` | `DailyRunner.run` | topic 维度编排；dry-run 只读 |
| `comment_agent.py` | `CommentAgent` | `/deep` `/mute` `/why` + 权限校验 |
| `subscribe.py` | `parse_request` / `apply_request` | Issue 表单 → topics.yaml 变更 + PR 描述 |
| `spike.py` | `probe` | 可达性/风控/耗时探测 |

## 4. 数据流（一次运行）

1. 读 `topics.yaml`（校验失败即退出）。
2. 逐话题：Provider 搜索（含降级）→ 与快照比对（URL + 语义）→ LLM 摘要（失败降级）。
3. 写 `reports/<date>/<slug>.md` + `index.md`，提交快照，更新 `feed.xml`。
4. 汇总渲染 Issue body → 创建/更新当日 Issue（有 `GITHUB_TOKEN` 时）。
5. 可选：IM 推送（缺 webhook 跳过）。

## 5. 错误处理矩阵

| 故障 | 行为 |
| --- | --- |
| 单 Provider 超时/无结果 | 记录 `fallback_reason`，切下一个 Provider |
| 连续 2 次风控 | 本次运行禁用该 Provider，全量切兜底 |
| 全部 Provider 失败 | 产出 `degraded: true` 的空报告 + 原因（当天仍有产出） |
| LLM 未配置/超时/超预算/JSON 非法/编造链接 | 降级为仅新增链接列表，`degraded: true` |
| 快照损坏 | 按无历史处理并 warn，不阻断 |
| Issue 发布失败 | 报告与 feed 已落盘；告警由 workflow 步骤失败暴露 |
| IM 推送失败 | 记 `failed`，不影响其它渠道与主流程 |

## 6. 测试策略

- 单元：规范化、去重、Provider 解析/熔断、LLM 重试预算、摘要降级、脱敏、发布渲染、订阅解析、评论指令。
- 集成：`DailyRunner` 端到端（临时目录，fixture Provider），断言 dry-run 零落盘、次日零新增。
- 配置守卫：6 个 workflow + 2 个模板结构校验、仓库无明文凭据。
- 全部测试不触网、不依赖真实时钟（日期显式传入）。