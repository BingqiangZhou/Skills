# Daily Digest

中文内容更新监控与每日摘要生成工具。包含三个独立的监控 skill，所有报告统一输出到 `daily-digests/YYYY-MM-DD/` 目录：

1. **RSS Monitor** — 统一监控微信公众号（~395）、科技博客（~26 RSS + Hacker News）、中文播客（~1000），生成 AI 摘要日报
2. **GitHub Monitor** — 统一监控 GitHub 动态：按仓库配置监控新合并 PR 和/或新 issue（默认 1c7/chinese-independent-developer 的 PR + ruanyf/weekly 社区开源自荐）
3. **Tool Update Monitor** — 监控 ~13 个开发工具（GitHub Releases / npm / changelog）的新版本

本仓库以 [Claude Code 插件市场](https://code.claude.com/docs/en/plugins-reference)（Plugin Marketplace）的形式分发，可通过 `/plugin` 命令一键安装。

---

## 安装

### 方式一：作为插件安装（推荐）

在 Claude Code / ZCode 中运行以下命令，将本仓库添加为插件市场并安装：

```text
# 1. 添加插件市场
/plugin marketplace add BingqiangZhou/Skills

# 2. 安装插件
/plugin install daily-digest@daily-digest
```

安装后，三个 skill 会自动注册：

- `rss-monitor` — 说出「公众号更新」「科技日报」「播客日报」「RSS监控」等即可触发
- `github-monitor` — 说出「GitHub 动态」「独立开发者项目更新」「阮一峰开源自荐」等即可触发
- `tool-update-monitor` — 说出「工具更新」「版本更新」「有没有新版本」等即可触发

> **插件与项目目录**：skill 的脚本和配置文件（`scripts/`、`references/`）随插件安装在插件缓存目录中，不可改动。运行时的中间文件（HTTP 缓存、AI 摘要批次）和最终日报输出会写到**你当前项目**（运行时的 cwd）下的 `workspaces/` 和 `daily-digests/` 目录。建议在专用项目里调用这些 skill，保持输出集中。

### 方式二：克隆仓库直接使用

如果不想通过插件市场安装，也可以直接克隆本仓库，将 `.claude/skills` 的路径替换为 `plugins/daily-digest/skills`：

```bash
git clone https://github.com/BingqiangZhou/Skills.git
cd Skills
```

然后在 Claude Code 中打开该仓库即可使用其中的 skill。

---

## 项目结构

```
.claude-plugin/
  marketplace.json                    # 插件市场清单（注册本仓库为一个 marketplace）

plugins/
  daily-digest/                       # 插件根目录
    .claude-plugin/
      plugin.json                     # 插件清单（名称、版本、作者、描述）
    skills/
      rss-monitor/                    # RSS 监控 skill（微信 + 科技 + 播客）
        SKILL.md
        scripts/  (_common, check_updates, merge_summaries, generate_report,
                   fetch_feed_list, fetch_articles, resolve_xiaoyuzhou_urls)
        references/  (feeds_wechat.json, feeds_tech.json, podcasts.json)
      github-monitor/                 # GitHub 动态监控 skill（PR 合并 + issue）
        SKILL.md
        scripts/  (_common, check_updates, merge_summaries, generate_report)
        references/  (repos.json)
      tool-update-monitor/            # 工具版本更新监控 skill
        SKILL.md
        scripts/  (check_updates, generate_report)
        references/  (tools.json)

daily-digests/                        # 统一日报输出目录（运行时生成，gitignored）
  YYYY-MM-DD/
    rss_HH-MM.md                      # RSS 日报
    github-monitor_HH-MM.md           # GitHub 动态日报
    tool-update_HH-MM.md              # 工具更新日报

workspaces/                           # 各 skill 运行时中间文件（gitignored）
  rss/
  github-monitor/
  tool-update-monitor/
podcast_rss_list.md                   # 原始播客排名数据
```

---

## 各 Skill 说明

### RSS Monitor（微信公众号 + 科技博客 + 播客）

监控多源 RSS 信息源，生成 AI 摘要的统一 Markdown 日报。

| 信息源 | 数量 | 内容 |
|--------|------|------|
| **WeChat**（微信公众号） | ~395 via Wechat2RSS | 安全、开发、其他、用户提交 |
| **Tech**（科技博客） | ~24 RSS + 2 Hacker News | AI/ML、芯片硬件、云计算、开源、网络安全、综合科技 |
| **Podcast**（播客） | ~1000（RSS + 小宇宙） | xyzrank.com 中文播客排名 |

**功能特性**：全量监控 · 并发抓取 · ETag 增量缓存 · 广告过滤 · AI 摘要（并行 sub-agent）· 跨账号去重 · 容错降级 · 零依赖（纯 Python 标准库）

**性能**：全量扫描（395 + 26 + 1000 源）~2-4 分钟；缓存扫描（ETag）~30-60s；AI 摘要（4 sub-agent）~60s

### GitHub Monitor

统一监控 GitHub 仓库动态。`references/repos.json` 驱动，每个仓库声明监控类型（`pulls`/`issues`）和可选的 issue 过滤规则。默认配置：

| 仓库 | monitor | 过滤 | 说明 |
|------|---------|------|------|
| 1c7/chinese-independent-developer | pulls | — | 中国独立开发者项目列表，监控新合并 PR（= 新收录项目） |
| ruanyf/weekly | issues | 开源自荐 | 阮一峰 科技爱好者周刊，保留社区开源自荐帖 |

**功能特性**：PR + issue 统一监控 · 通用多仓库 · 按仓库可选过滤 · 临时单仓库监控（`--owner`/`--repo`）· GitHub REST API + ETag 缓存 · AI 摘要 · 零依赖

**性能**：动态拉取（每仓库，单页，ETag 缓存）~1-2s；AI 摘要（4 sub-agent）~60s

### Tool Update Monitor

监控 13 个开发工具和操作系统的新版本发布，按"上次记录版本"增量检测。涵盖四类：AI 编码代理、系统工具、系统更新、网络代理。

**功能特性**：版本增量检测（首次建立 baseline，后续只报新版本）· GitHub Releases API / npm / HTML changelog 多源支持 · ETag 缓存 · AI 高亮总览 · 配置驱动（`tools.json` 加减工具无需改代码）· 零依赖

**性能**：首次扫描 ~5-10s；缓存扫描 ~3-6s；AI 高亮（1 sub-agent）~10-20s

---

## 依赖

- **Python 3**（标准库即可，无需额外安装包）

核心模块：`urllib.request`, `xml.etree.ElementTree`, `concurrent.futures`, `json`, `ssl`, `html.parser`, `argparse`, `re`, `datetime`

## License

MIT
