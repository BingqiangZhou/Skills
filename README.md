# OpenClaw Podcast Digest

中文播客更新监控与每日摘要生成工具。自动追踪 Top 1000 中文播客的最新动态，生成 AI 驱动的 Markdown 每日播客日报。

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
