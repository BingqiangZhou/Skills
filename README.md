# OpenClaw Digest

中文内容更新监控与每日摘要生成工具。包含五个独立的监控 skill，所有报告统一输出到 `daily-digests/YYYY-MM-DD/` 目录：

1. **Podcast Digest** — 追踪 Top 1000 中文播客的最新动态
2. **WeChat Digest** — 监控 ~300 个微信公众号的文章更新（via Wechat2RSS）
3. **Tech Daily** — 监控中英文科技媒体 RSS + Hacker News，生成 AI 科技日报
4. **GitHub Monitor** — 统一监控 GitHub 动态：按仓库配置监控新合并 PR 和/或新 issue（默认 1c7/chinese-independent-developer 的 PR + ruanyf/weekly 社区开源自荐）
5. **Tool Update Monitor** — 监控 ~11 个开发工具（GitHub Releases / npm / changelog）的新版本

## 项目结构

```
.claude/skills/
  podcast-rss-monitor/             # 播客监控 skill
    SKILL.md
    scripts/  (check_updates.py, resolve_xiaoyuzhou_urls.py, generate_report.py)
    references/  (podcasts.json)
  wechat-rss-monitor/              # 微信公众号监控 skill
    SKILL.md
    scripts/  (fetch_feed_list.py, check_updates.py, fetch_articles.py, generate_report.py)
    references/  (feeds.json)
  tech-daily/                      # AI 科技日报 skill
    SKILL.md
    scripts/  (check_updates.py, generate_report.py)
    references/  (feeds.json)
  github-monitor/                  # GitHub 动态监控 skill（PR 合并 + issue，按仓库可选过滤）
    SKILL.md
    scripts/  (_common.py, check_updates.py, merge_summaries.py, generate_report.py)
    references/  (repos.json)
  tool-update-monitor/             # 工具版本更新监控 skill
    SKILL.md
    scripts/  (check_updates.py, generate_report.py)
    references/  (tools.json)

daily-digests/                     # 统一日报输出目录
  YYYY-MM-DD/
    podcast_HH-MM.md               # 播客日报
    wechat_HH-MM.md                # 微信日报
    tech-daily_HH-MM.md            # 科技日报
    github-monitor_HH-MM.md        # GitHub 动态日报（PR 合并 + issue）

workspaces/                         # 各 skill 的运行时中间文件（gitignored）
  podcast/                          # 每个 skill 一个子目录
  wechat/
  tech-daily/
  tool-update-monitor/
  github-monitor/
podcast_rss_list.md                 # 原始播客排名数据
```

---

# Podcast RSS Monitor

追踪 xyzrank 排名 Top 1000 中文播客（872 个 RSS 源 + 128 个小宇宙链接），生成 AI 摘要日报。

## 功能特性

- **全量监控** — 1000 播客，按域名分组并发抓取
- **并发抓取** — 线程池并发，ETag 缓存和增量更新
- **广告过滤** — 双层关键词过滤系统，自动清理赞助和推广内容
- **AI 摘要** — 并行 sub-agent 生成一句话中文摘要（30-50 字）
- **容错设计** — SSL 错误自动降级、429 限流重试、AI 摘要缺失时自动回退到截断摘要
- **零依赖** — 纯 Python 标准库

## 使用方式

通过 Claude Code 调用 skill（说"播客日报"或"检查播客更新"即可触发），或手动执行：

```bash
cd .claude/skills/podcast-rss-monitor
python scripts/check_updates.py --count 1000 --hours 24 --workers 30 \
  --output ../../workspaces/podcast/latest_updates.json \
  --cache ../../workspaces/podcast/.http_cache.json

python scripts/generate_report.py \
  -i ../../workspaces/podcast/latest_updates.json \
  -s ../../workspaces/podcast/ai_summaries.json \
  -o ../../daily-digests/YYYY-MM-DD/podcast_HH-MM.md
```

## 性能

| 指标 | 数值 |
|------|------|
| 全量扫描 1000 播客 | ~2 分钟 |
| 缓存扫描（ETag） | ~30 秒 |
| 24 小时更新量 | ~120 集 |

---

# WeChat Official Account Digest

通过 [Wechat2RSS](https://wechat2rss.xlab.app/list/all) 监控 ~300 个微信公众号的 RSS 订阅源，生成 AI 驱动的每日日报。

## 功能特性

- **全量监控** — ~300 个公众号（安全 230+、开发 12、其他 8、用户提交 48）
- **自动获取** — 从 GitHub 自动获取 Feed 列表，每周缓存刷新
- **并发抓取** — 单域名并发策略，ETag/If-Modified-Since 缓存
- **AI 摘要** — 并行 sub-agent 生成一句话中文摘要
- **分类输出** — 按分类（安全/开发/其他/用户提交）分组生成报告
- **零依赖** — 纯 Python 标准库

## 使用方式

通过 Claude Code 调用 skill（说"微信日报"或"公众号更新"即可触发），或手动执行：

```bash
cd .claude/skills/wechat-rss-monitor

# 获取 Feed 列表（每周一次）
python scripts/fetch_feed_list.py --output references/feeds.json --cache ../../workspaces/wechat/.feed_list_cache.json

# 检查更新
python scripts/check_updates.py --hours 24 --workers 10 \
  --output ../../workspaces/wechat/latest_updates.json \
  --cache ../../workspaces/wechat/.http_cache.json

# 生成报告
python scripts/generate_report.py \
  -i ../../workspaces/wechat/latest_updates.json \
  -s ../../workspaces/wechat/ai_summaries.json \
  -o ../../daily-digests/YYYY-MM-DD/wechat_HH-MM.md
```

## 性能

| 指标 | 数值 |
|------|------|
| Feed 列表获取 | ~1s（每周缓存） |
| 全量扫描 300 Feed | ~60s |
| 缓存扫描（ETag） | ~20s |
| AI 摘要（4 sub-agent） | ~60s |

---

# Tech Daily Report

监控 ~25 个中英文科技媒体 RSS 源 + Hacker News（via hnrss.org），生成包含"趋势洞察"版块的中文 AI 科技日报。

## 信息源

| 分类 | 英文源 | 中文源 |
|------|--------|--------|
| AI/ML | OpenAI Blog, Google AI Blog, DeepMind Blog, Hugging Face Blog, Anthropic News | 机器之心, 量子位 |
| 芯片硬件 | Tom's Hardware, AnandTech | — |
| 云计算 | AWS Blog, Google Cloud Blog, Azure Blog | — |
| 开源 | GitHub Blog, The New Stack | — |
| 网络安全 | Krebs on Security, The Hacker News, Dark Reading | — |
| 综合科技 | TechCrunch, The Verge, Ars Technica, Wired, VentureBeat, MIT Tech Review | 36kr |
| Hacker News | hnrss.org Frontpage (points >= 100) + AI Topics (points >= 30) | — |

## 功能特性

- **双语源监控** — 同时追踪英文和中文科技媒体
- **Hacker News 聚合** — 按 points 过滤热门内容，显示热度数据
- **智能去重** — URL 规范化 + 标题相似度（Jaccard）跨源去重
- **趋势洞察** — 额外 sub-agent 分析所有摘要，生成 3-5 条趋势洞察
- **AI 摘要** — 英文内容自动翻译为中文一句话摘要
- **分类报告** — 按 AI/ML、芯片硬件、云计算、开源、网络安全、综合科技、HN 热门分组
- **零依赖** — 纯 Python 标准库

## 使用方式

通过 Claude Code 调用 skill（说"科技日报"或"AI日报"即可触发），或手动执行：

```bash
cd .claude/skills/tech-daily

# 检查更新
python scripts/check_updates.py --hours 24 --workers 20 \
  --output ../../workspaces/tech-daily/latest_updates.json \
  --cache ../../workspaces/tech-daily/.http_cache.json

# 生成报告
python scripts/generate_report.py \
  -i ../../workspaces/tech-daily/latest_updates.json \
  -s ../../workspaces/tech-daily/ai_summaries.json \
  --insight ../../workspaces/tech-daily/trend_insight.json \
  -o ../../daily-digests/YYYY-MM-DD/tech-daily_HH-MM.md
```

## 性能

| 指标 | 数值 |
|------|------|
| 全量扫描 27 源（20 workers） | ~30-60s |
| 缓存扫描（ETag） | ~10-15s |
| AI 摘要（4 sub-agent） | ~60s |
| 趋势洞察（1 sub-agent） | ~30s |
| 24 小时更新量 | ~100-200 条 |

## 报告示例

```markdown
# AI 科技日报 - 2026-04-01

> 共检查 26 个信息源，时间范围 24 小时，发现 141 条更新

---

## 今日趋势洞察

1. OpenAI 完成 1220 亿美元融资，估值突破 8500 亿...
2. Claude Code 源码泄露引发安全社区广泛讨论...

---

## AI/ML (11 条)

### 1. 智谱上市后首份财报：超7.24亿元！
**来源**: 量子位 | **发布时间**: 2026-03-31 12:08
**链接**: https://...
**AI 摘要**: 提出新概念：Token架构力

---

## Hacker News 热门 (13 条)

### 129. The Claude Code Source Leak (points: 1205, comments: 489)
**链接**: https://...
**AI 摘要**: ...
```

---

---

# GitHub Monitor

统一监控 GitHub 动态：对 `references/repos.json` 里配置的一个或多个仓库，按每个仓库的 `monitor` 设置抓取**新合并的 PR** 和/或**新 issue**，可选按仓库过滤 issue，生成按仓库分组的 AI 摘要日报。PR 与 issue 出现在同一份报告里，按时间倒序排列，各自渲染适合的字段（PR：合并时间/分支/diff；issue：发布时间/标签）。

默认配置监控两个仓库：
- [`1c7/chinese-independent-developer`](https://github.com/1c7/chinese-independent-developer)（中国独立开发者项目列表）— 监控**新合并 PR**；该仓库通过合并社区提交的 PR 来收录新项目，因此「新合并的 PR」即对应「新收录的项目」。
- [`ruanyf/weekly`](https://github.com/ruanyf/weekly)（阮一峰 科技爱好者周刊）— 监控**新 issue**，只保留社区**开源自荐**帖（标题含 `【开源自荐】`/`【自荐】`/`【开源项目】` 等）。

可在 `repos.json` 增加任意仓库，各自配置 `monitor` 与可选 `filter`；也可用 `--owner`/`--repo` 临时监控单个仓库（PR + issue 都抓，不过滤）。

## 信息源（references/repos.json）

| 仓库 | monitor | 过滤 | 说明 |
|------|---------|------|------|
| 1c7/chinese-independent-developer | pulls | — | 中国独立开发者项目列表，监控新合并 PR（= 新收录项目） |
| ruanyf/weekly | issues | 开源自荐 | 阮一峰 科技爱好者周刊，保留社区开源自荐帖 |

可自行添加，例如 `{ "owner": "octocat", "repo": "Hello-World", "monitor": ["pulls","issues"] }`（无 `monitor` 默认 `["issues"]`，无 `filter` 即报全部 issue）。

## 每仓库配置

| 字段 | 类型 | 默认 | 含义 |
|------|------|------|------|
| `monitor` | string[] | `["issues"]` | 监控的活动类型：`"pulls"`、`"issues"`，或两者都填 |
| `name` | string | `{owner}/{repo}` | 报告中的显示名 |
| `filter` | object | — | 仅对 issue 生效（见下表）；PR 用 `merged_at` 过滤，忽略此字段 |

可选的 `filter` 对象（仅 issue）字段均可选：

| 字段 | 类型 | 默认 | 含义 |
|------|------|------|------|
| `drop_pull_requests` | bool | `true` | 丢弃 PR（issues 接口也会返回 PR） |
| `drop_owner` | bool | `false` | 丢弃仓库 owner 本人发的 issue |
| `title_allow` | string[] | `[]` | 正则数组；标题至少匹配其一才保留 |
| `title_block` | string[] | `[]` | 正则数组；命中即丢弃（在 `title_allow` 之后生效） |

## 过滤规则

`check_updates.py` 对每种活动类型分别判定：

**PR** — 满足全部条件才保留：
1. 通过 `state=closed` 拉取（同时包含已合并和未合并的已关闭 PR）
2. `merged_at` 非空——即确实被**合并**了，而非仅被关闭未合并
3. `merged_at` 在 `--hours` 时间窗内（GitHub API 的 `since` 按 `updated_at` 过滤，且 `state=closed` 含未合并 PR，客户端必须再用 `merged_at` 复筛）

**Issue** — 满足全部条件才保留：
1. `created_at` 在 `--hours` 时间窗内（GitHub API 的 `since` 按 `updated_at` 过滤，客户端必须再用 `created_at` 复滤）
2. 通过该仓库的 `filter`（无 `filter` 则保留时间窗内全部 issue）
3. 与本批次内已见条目去重（按 `html_url`/`number`，跨 PR/issue 类型）

## 功能特性

- **PR + issue 统一监控** — 一次运行按每个仓库配置同时抓取 PR 和/或 issue，一份日报覆盖全部
- **通用多仓库** — `repos.json` 驱动，单次扫描多个仓库，按仓库分组出报告
- **按仓库可选过滤** — 每个仓库自带 `drop_pull_requests` / `drop_owner` / `title_allow` / `title_block`（仅 issue）
- **临时监控** — `--owner`/`--repo` 不改文件即可监控任意单个仓库（PR + issue 都抓）
- **GitHub REST API** — `/repos/{owner}/{repo}/pulls` + `/repos/{owner}/{repo}/issues`，自动翻页
- **可选 Token** — 设置 `GITHUB_ACCESS_TOKEN` 将限额从 60/小时提升到 5000/小时
- **增量请求** — ETag 缓存，未变更的页面返回 304 直接复用
- **AI 摘要** — 并行 sub-agent 生成一句话中文摘要（每条 < 100 字）
- **容错设计** — 限流退避、5xx 重试、SSL 降级、AI 摘要缺失时回退到截断简介
- **零依赖** — 纯 Python 标准库

## 使用方式

通过 Claude Code 调用 skill（说「GitHub 动态更新」「独立开发者项目更新」「阮一峰开源自荐」「GitHub PR 合并」即可触发），或手动执行：

```bash
cd .claude/skills/github-monitor

# 可选：设置 GitHub token 提升速率限制
export GITHUB_ACCESS_TOKEN="ghp_xxx"

# 检查更新（按 repos.json 配置的全部仓库）
python scripts/check_updates.py --hours 24 \
  --output ../../workspaces/github-monitor/latest_updates.json \
  --cache  ../../workspaces/github-monitor/.http_cache.json

# 或临时监控单个仓库（PR + issue 都抓，覆盖 repos.json）
# python scripts/check_updates.py --hours 24 --owner OWNER --repo REPO ...

# 生成报告
python scripts/generate_report.py \
  -i ../../workspaces/github-monitor/latest_updates.json \
  -s ../../workspaces/github-monitor/ai_summaries.json \
  -o ../../daily-digests/YYYY-MM-DD/github-monitor_HH-MM.md
```

## 性能

| 指标 | 数值 |
|------|------|
| 动态拉取（每仓库，单页，ETag 缓存） | ~1-2s |
| AI 摘要（4 sub-agent） | ~60s |
| 24 小时动态量 | 视仓库而定（1c7 ~0-3 PR / ruanyf 少量 issue） |

## 报告示例

```markdown
# GitHub 动态更新 - 2026-07-31 09:00 (CST)

> 仓库: 1c7/chinese-independent-developer / ruanyf/weekly | 时间窗: 24 小时 | 检查 100 条 | 过滤 58 条 | 保留 42 条（PR 2 / issue 40）

---

## 中国独立开发者项目列表（`1c7/chinese-independent-developer`）（2 条）

---

### 1. 新增：CrossGen (#1228)

**合并**: 2026-07-30 14:20 | **作者**: [@Bliveren](https://github.com/Bliveren) | **分支**: `master` | **改动**: +3 / -0 / 1 files
**PR**: https://github.com/1c7/chinese-independent-developer/pull/1228
**AI 摘要**: ...

---

## 阮一峰 科技爱好者周刊（`ruanyf/weekly`）（40 条）

---

### 3. 【开源自荐】Panerelay：让 AI Agent 通过 agent-browser 操作你正在使用的 Chrome
**发布**: 2026-07-30 20:25 | **作者**: [@F-loat](https://github.com/F-loat) | 👍 12
**Issue**: https://github.com/ruanyf/weekly/issues/10958
**AI 摘要**: ...
```

---

## 依赖

- **Python 3**（标准库即可，无需额外安装包）

核心模块：`urllib.request`, `xml.etree.ElementTree`, `concurrent.futures`, `json`, `ssl`, `html.parser`, `argparse`, `re`, `datetime`

## License

MIT
