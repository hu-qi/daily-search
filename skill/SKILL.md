---
name: daily-search-skill
description: |
  「每日一搜」：对订阅的中文话题跑一次增量搜索，只返回比上次新增/变化的内容，每条带原始链接。
  Use when：需要每日/按需追踪中文话题新增、竞品动态、政策更新，或要把「只推新增」能力接入其它 Agent 时。
tools:
  - bash
  - python
---

# 每日一搜 Skill（复用版）

> 本 Skill 是仓库 `hu-qi/daily-search` 的对外瘦身版：不做定时调度、不建 Issue，只负责「跑一次 → 拿增量」。
> 调度与发布交给仓库里的 GitHub Actions（`daily-search.yml`）。

## 一、前置

```bash
pip install -e ".[dev]"           # 仓库根目录执行
pip install -e ".[browser]" && python -m playwright install --with-deps chromium   # 需要真抓 so.qq.com 时
```

凭据（可选，全部走环境变量，**不要写进文件或对话**）：

| 变量 | 作用 | 缺失时 |
| --- | --- | --- |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI 兼容摘要 | 降级为「仅新增链接列表」 |
| `FEISHU_WEBHOOK_URL` / `WECOM_WEBHOOK_URL` | IM 推送 | 跳过渠道 |

## 二、标准步骤

1. **准备话题**：`topics.yaml` 至少一个话题，字段 `topic / keywords (+exclude) / subscribers / channels`。
2. **先探测**（首次或换 runner 时）：
   ```bash
   python -m daily_search spike --json
   ```
   `reachable=false` 或 `blocked=true` → 直接走兜底（不要绕过验证码）。
3. **跑增量**（只读，不落盘，适合嵌进别的 Agent 流程）：
   ```bash
   python -m daily_search run --dry-run --no-issue --topic "昇腾 CANN" --json
   ```
   返回 JSON 数组，每项含 `summary / points[]{title,url,note,confidence} / degraded / stats`。
4. **要落盘归档**（写 `reports/`、`data/`、`feed.xml`）：
   ```bash
   python -m daily_search run --date 2026-11-11 --no-issue
   ```
   同一天重复执行不会重复推送（快照生效）。
5. **交给 IM**（可选）：
   ```bash
   python -m daily_search notify --file reports/2026-11-11/index.md --channels feishu,wecom
   ```

## 三、输出契约

```json
[
  {
    "topic": "昇腾 CANN",
    "slug": "昇腾-cann",
    "date": "2026-11-11",
    "summary": "今日新增 3 条：版本发布、算子示例、文档更新。",
    "points": [
      {"title": "...", "url": "https://...", "note": "一句话说明", "confidence": "high", "worth_following": true}
    ],
    "degraded": false,
    "degraded_reason": "",
    "provider": "soqq",
    "fallback_reason": "",
    "stats": {"total": 3, "accepted": 3, "duplicates": 0, "repeat_rate": 0.0}
  }
]
```

下游使用规则：

- `degraded=true` → 只有链接，不要让模型补充事实；
- `points[].url` 一定来自搜索结果，可直接渲染为可点击链接；
- `stats.repeat_rate` 用于观测去重健康度（持续偏高说明阈值偏低或快照未提交）。

## 四、坑点

| 坑 | 处理 |
| --- | --- |
| so.qq.com 是 SPA，`requests` 抓不到结果 | 必须 Playwright；`provider_order` 保留 `fallback_rss` 兜底 |
| 海外 runner 被验证码拦截 | 连续 2 次风控会自动全量切兜底；长期用 self-hosted runner |
| 忘提交 `data/` 快照 | 下一次会重复推送，务必把 `data/` 纳入提交 |
| 用 `--dry-run` 却期望写报告 | dry-run 是只读语义，落盘请去掉该参数 |
| 把 key 打进日志 | 走 `setup_logging()`，`RedactionFilter` 会脱敏；切勿 `printenv` |

## 五、脚本

`scripts/run_daily_search.sh` 包装了「探测 → 干跑 → 落盘」三步，可直接在其它 Agent 的 shell 步骤里调用：

```bash
bash skill/scripts/run_daily_search.sh --topic "昇腾 CANN" --date 2026-11-11
```

脚本只读取环境变量中的凭据，不写任何临时凭据文件。