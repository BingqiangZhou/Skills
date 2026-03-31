# OpenClaw Digest

中文内容更新监控与每日摘要生成工具。包含两个独立的监控 skill：

1. **Podcast Digest** — 追踪 Top 1000 中文播客的最新动态
2. **WeChat Digest** — 监控 ~300 个微信公众号的文章更新（via Wechat2RSS）

## 功能特性

- **全量监控** — 追踪 xyzrank 排名 Top 1000 中文播客（872 个 RSS 源 + 128 个小宇宙链接）
- **并发抓取** — 按域名分组的线程池并发请求，支持 ETag 缓存和增量更新
- **广告过滤** — 双层关键词过滤系统（高置信度 + 低置信度短文本），自动清理赞助和推广内容
- **AI 摘要** — 并行生成每集一句话中文摘要（30-50 字）
- **容错设计** — SSL 错误自动降级、429 限流重试、AI 摘要缺失时自动回退到截断摘要
- **零依赖** — 纯 Python 标准库实现，无需 pip install

## 项目结构

```
scripts/
├── check_updates.py              # RSS 更新检查器（并发抓取 + 广告过滤）
├── generate_report.py            # Markdown 日报生成器
└── convert_to_json.py            # 一次性工具：Markdown 表格转 JSON
references/
├── podcasts.json                 # 1000 播客元数据（排名、RSS 地址、分类等）
├── podcast_rss_list.md           # 原始 Markdown 排名表
└── ad_keywords.txt               # 广告过滤关键词列表
podcast-workspace/                # 运行时中间文件
podcast-digests/                  # 生成的日报输出目录
```

## 工作流程

```
Step 1: check_updates.py          Step 2: AI Summarization        Step 3: generate_report.py
━━━━━━━━━━━━━━━━━━━━━            ━━━━━━━━━━━━━━━━━━━━━━          ━━━━━━━━━━━━━━━━━━━━━━━━━━
加载 1000 播客数据                拆分为每批 10 集                  读取更新数据 + AI 摘要
     │                                │                               │
并发抓取 RSS / 小宇宙页面          AI 并行生成摘要                  合并摘要（AI 优先 / 截断回退）
     │                           （最多 4 个并行）                       │
ETag 缓存 + 增量过滤                  │                           广告二次过滤
     │                           合并为 ai_summaries.json              │
输出 latest_updates.json               │                          生成 Markdown 日报
                                       │                          → podcast-digests/
```

## 使用方式

### 检查播客更新

```bash
python scripts/check_updates.py --count 1000 --hours 24 --workers 30
```

参数说明：
- `--count` — 监控播客数量（默认 1000）
- `--hours` — 回溯时间窗口，单位小时（默认 24）
- `--workers` — 并发工作线程数（默认 30）

### 生成日报

```bash
python scripts/generate_report.py
```

读取 `podcast-workspace/latest_updates.json` 和可选的 `podcast-workspace/ai_summaries.json`，输出 Markdown 日报到 `podcast-digests/` 目录。

### 数据准备（一次性）

```bash
python scripts/convert_to_json.py
```

将 `references/podcast_rss_list.md` 转换为结构化的 `references/podcasts.json`。

## 依赖

- **Python 3**（标准库即可，无需额外安装包）

核心模块：`urllib.request`, `xml.etree.ElementTree`, `concurrent.futures`, `json`, `ssl`, `re`, `argparse`

## 性能

| 指标 | 数值 |
|------|------|
| 全量扫描 1000 播客 | ~2 分钟 |
| 缓存扫描（ETag） | ~30 秒 |
| 24 小时更新量 | ~120 集 |
| 日报文件大小 | ~50 KB |

## 示例输出

生成的日报格式如下（位于 `podcast-digests/` 目录）：

```markdown
# 📻 中文播客日报 2026-03-31

> 共监控 1000 个播客，发现 125 集更新（过去 24 小时）

---

## 1. 播客名 ⭐ 排名 #5
**最新剧集：** 剧集标题
**AI 摘要：** 一句话中文摘要
**发布时间：** 2026-03-31 08:00
```

## License

MIT

---

# WeChat Official Account Digest

微信公众号文章更新监控与每日摘要生成工具。通过 [Wechat2RSS](https://wechat2rss.xlab.app/list/all) 服务监控 ~300 个公众号的 RSS 订阅源，生成 AI 驱动的 Markdown 每日日报。

## 功能特性

- **全量监控** — 追踪 Wechat2RSS 上约 300 个微信公众号（安全 230+、开发 12、其他 8、用户提交 48）
- **自动获取** — 从 GitHub 自动获取并解析 Feed 列表，每周刷新
- **并发抓取** — 单域名并发策略，支持 ETag/If-Modified-Since 缓存
- **AI 摘要** — 并行生成每篇文章一句话中文摘要
- **分类输出** — 按分类（安全/开发/其他/用户提交）分组生成报告
- **零依赖** — 纯 Python 标准库实现，无需 pip install

## 项目结构

```
.claude/skills/wechat-rss-monitor/
  SKILL.md                        # Skill 定义
  scripts/
    fetch_feed_list.py            # 从 GitHub 获取并解析 Feed 列表
    check_updates.py              # 检查 RSS Feed 的文章更新
    generate_report.py            # 生成 Markdown 日报
  references/
    feeds.json                    # 缓存的 Feed 列表（每周刷新）
wechat-workspace/                  # 运行时中间文件
wechat-digests/                    # 生成的日报输出目录
```

## 工作流程

```
Step 0: fetch_feed_list.py      Step 1: check_updates.py       Step 2: AI Summary        Step 3: generate_report.py
━━━━━━━━━━━━━━━━━━━━━━━━━      ━━━━━━━━━━━━━━━━━━━━━━━━       ━━━━━━━━━━━━━━━━━━━       ━━━━━━━━━━━━━━━━━━━━━━━━━━━
从 GitHub 获取 Markdown         加载 feeds.json                  拆分为每批 10 篇           读取更新数据 + AI 摘要
     │                          并发检查 300 个 RSS Feed         AI 并行生成摘要            合并摘要（AI 优先 / 原文回退）
解析分类和 Feed URL                   │                        （最多 4 个并行）                 │
     │                          ETag 缓存 + 时间窗口过滤              │                     按分类分组
输出 feeds.json                       │                        合并为 ai_summaries.json          │
（7 天缓存）                    输出 latest_updates.json                  │                   生成 Markdown 日报
                                                                        │                   → wechat-digests/
```

## 使用方式

通过 Claude Code 调用 skill，或手动执行：

```bash
# 获取 Feed 列表
cd .claude/skills/wechat-rss-monitor
python scripts/fetch_feed_list.py --output references/feeds.json --cache ../../wechat-workspace/.feed_list_cache.json

# 检查更新
python scripts/check_updates.py --hours 24 --workers 20 --output ../../wechat-workspace/latest_updates.json --cache ../../wechat-workspace/.http_cache.json

# 生成报告
python scripts/generate_report.py -i ../../wechat-workspace/latest_updates.json -o ../../wechat-digests/report.md
```

## 性能

| 指标 | 数值 |
|------|------|
| Feed 列表获取 | ~1s（每周缓存） |
| 全量扫描 300 Feed | ~60s |
| 缓存扫描（ETag） | ~20s |
| AI 摘要（4 子代理） | ~60s |
