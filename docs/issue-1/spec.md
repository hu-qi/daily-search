# Spec · Issue #1「每日一搜」

> 来源：GitHub Issue #1 需求全文 + Issue Agent Spec（comment 6789261311）。

## 1. 目标

为订阅的中文话题每日执行一次搜索，**只推送比昨日新增/变化的内容**，每条结论都附原始链接，发布到：

- `reports/YYYY-MM-DD/<topic>.md`（一搜一页）
- `feed.xml`（RSS）
- 当日 GitHub Issue `🔍 每日一搜 YYYY-MM-DD`
- 可选：飞书 / 企业微信 webhook

## 2. 用户在什么场景下用它

| 用户 | 场景 | 现有替代品的问题 |
| --- | --- | --- |
| 产品/竞品分析 | 追踪「昇腾 CANN」「鸿蒙 OHPM」版本与生态变化 | Google Alerts 噪音大、不覆盖中文生态 |
| 舆情/政策跟踪 | 追踪具身智能、人形机器人政策落地 | Brand24/Octolens 中文源几乎空白 |
| 开源维护者 | 追踪自家项目被提及、依赖升级 | 手工开十几个标签页 |

## 3. 范围

### In Scope

1. Python 包 `src/daily_search`：`config` / `providers` / `diff` / `agent` / `publish` / `runner` / `cli`。
2. 6 个 GitHub Actions workflow（均含 `workflow_dispatch`）：`daily-search` / `subscribe-from-issue` / `issue-comment-agent` / `notify` / `pages` / `ci`。
3. 2 个 Issue 模板：订阅话题、反馈误报。
4. `topics.yaml`（3 个示例话题）+ 首份报告 + `feed.xml` + 去重快照。
5. `skill/SKILL.md`（+ 脚本）与 README（架构图 / Secrets / 本地运行 / spike 结论）。
6. `tests/`（pytest）+ CI。

### Out of Scope

- 首次生产定时运行（需维护者配置 Secrets 后手动触发）。
- 飞书/企微真实 webhook 联调（实现 + mock 单测，不联真实租户）。
- 多语言、全文转载、付费数据源、检索质量调优实验。

## 4. 功能需求

| ID | 需求 | 验收方式 |
| --- | --- | --- |
| F1 | `topics.yaml` 四级 schema 校验，缺字段 fail fast | `tests/test_config.py` |
| F2 | Provider 抽象：`soqq` 主、`fallback_rss` 兜底，风控自动切换 | `tests/test_providers.py` |
| F3 | 双层去重（URL 规范化 + 3-gram Jaccard ≥ 0.85），快照保留 14 天 | `tests/test_dedupe.py` |
| F4 | LLM 生成一搜一页；不可用时降级为仅链接列表 | `tests/test_summarize.py` |
| F5 | 报告 / feed / Issue 均带可点击原始链接 | `tests/test_publish.py` |
| F6 | 日志与仓库无明文凭据 | `tests/test_redaction.py`、`tests/test_workflows.py` |
| F7 | `--dry-run` 只读（不写快照/feed/报告） | `tests/test_runner.py` |
| F8 | 订阅 Issue → `topics.yaml` PR；评论指令 `/deep` `/mute` `/why` | `tests/test_subscribe.py`、`tests/test_comment_agent.py` |
| F9 | so.qq.com 可行性探测（`spike`），结论入 README | `src/daily_search/spike.py`、README |

## 5. 非功能需求

- **安全**：凭据只从 env/Secrets 读；`LLM_API_KEY`/`*_TOKEN`/`*_WEBHOOK_URL` 与常见凭据形态在日志、异常栈、落盘文本中脱敏；评论指令限 `OWNER`/`MEMBER`/`COLLABORATOR`。
- **鲁棒**：Provider 失败、LLM 失败、快照损坏都不得让当天零产出；报告头标注 `degraded: true` 并写明原因。
- **可测试**：所有外部依赖（HTTP、LLM、时钟、抓取器）可注入，单测不触网。
- **合规**：每话题 ≤ 3 次/天、只存摘要与链接、不全文转载、不绕过验证码。

## 6. 验收标准

- [ ] 手动触发 `daily-search.yml` 无人工干预产出当日报告与 Issue（待 workflow 启用 + Secrets 配置后验证）
- [x] 连续两天运行第二天只出现新增，重复率 < 10%（双日 fixture 断言）
- [x] 每条结论带可点击原始链接
- [x] 仓库与 Actions 日志无明文 key
- [x] `pytest` 全绿