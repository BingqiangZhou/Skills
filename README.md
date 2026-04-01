# OpenClaw Digest

中文内容更新监控与每日摘要生成工具。包含三个独立的监控 skill，所有报告统一输出到 `daily-digests/YYYY-MM-DD/` 目录：

1. **Podcast Digest** — 追踪 Top 1000 中文播客的最新动态
2. **WeChat Digest** — 监控 ~300 个微信公众号的文章更新（via Wechat2RSS）
3. **Tech Daily** — 监控中英文科技媒体 RSS + Hacker News，生成 AI 科技日报

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

daily-digests/                     # 统一日报输出目录
  YYYY-MM-DD/
    podcast_HH-MM.md               # 播客日报
    wechat_HH-MM.md                # 微信日报
    tech-daily_HH-MM.md            # 科技日报

{skill}-workspace/                  # 各 skill 的运行时中间文件（gitignored）
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
  --output ../../podcast-workspace/latest_updates.json \
  --cache ../../podcast-workspace/.http_cache.json

python scripts/generate_report.py \
  -i ../../podcast-workspace/latest_updates.json \
  -s ../../podcast-workspace/ai_summaries.json \
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
python scripts/fetch_feed_list.py --output references/feeds.json --cache ../../wechat-workspace/.feed_list_cache.json

# 检查更新
python scripts/check_updates.py --hours 24 --workers 10 \
  --output ../../wechat-workspace/latest_updates.json \
  --cache ../../wechat-workspace/.http_cache.json

# 生成报告
python scripts/generate_report.py \
  -i ../../wechat-workspace/latest_updates.json \
  -s ../../wechat-workspace/ai_summaries.json \
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
  --output ../../tech-daily-workspace/latest_updates.json \
  --cache ../../tech-daily-workspace/.http_cache.json

# 生成报告
python scripts/generate_report.py \
  -i ../../tech-daily-workspace/latest_updates.json \
  -s ../../tech-daily-workspace/ai_summaries.json \
  --insight ../../tech-daily-workspace/trend_insight.json \
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

## 依赖

- **Python 3**（标准库即可，无需额外安装包）

核心模块：`urllib.request`, `xml.etree.ElementTree`, `concurrent.futures`, `json`, `ssl`, `html.parser`, `argparse`, `re`, `datetime`

## License

MIT
