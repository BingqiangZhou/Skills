# Daily Digest

中文内容更新监控与每日摘要生成工具。采用**采集与日报分离**的两层架构：

- **采集层**（3 个 skill）：各自只负责抓取数据并保存为 `latest_updates.json`，不做总结、不出报告。
- **日报层**（1 个 skill）：`daily-digest` 编排三个采集任务、做 AI 总结、生成一份**统一 Markdown 日报**。

所有日报统一输出到 `workspaces/daily-digests/reports/YYYY-MM-DD/` 目录。

## Skills 一览

| Skill | 层级 | 职责 | 触发词示例 |
|-------|------|------|-----------|
| **daily-digest** | 日报层 | 编排三个采集 skill + AI 总结 + 生成统一日报 | 「每日摘要」「生成日报」「信息汇总」 |
| **rss-monitor** | 采集层 | 采集微信公众号 / 科技博客 / 播客更新并保存 | 「检查公众号更新」「抓取 RSS」 |
| **github-monitor** | 采集层 | 采集 GitHub 新合并 PR / 新 issue 并保存 | 「检查 GitHub 动态」「1c7 仓库更新」 |
| **tool-update-monitor** | 采集层 | 采集开发工具 / 系统新版本并保存 | 「检查工具更新」「有没有新版本」 |

> 三个采集 skill 单独触发时**只采集数据**；想生成日报请用 `daily-digest`。

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

安装后，四个 skill 会自动注册：

- `daily-digest` — 说出「每日摘要」「生成日报」「信息汇总」「今天有什么更新」即可触发全套流程
- `rss-monitor` — 说出「检查公众号更新」「抓取 RSS」「检查信息源」即可触发采集
- `github-monitor` — 说出「检查 GitHub 动态」「独立开发者项目更新」「阮一峰开源自荐」即可触发采集
- `tool-update-monitor` — 说出「检查工具更新」「版本检查」「有没有新版本」即可触发采集

> **插件与项目目录**：skill 的脚本和配置文件（`scripts/`、`references/`）随插件安装在插件缓存目录中，不可改动。运行时的中间文件（HTTP 缓存、AI 摘要批次）和最终日报输出会写到**你当前项目**（运行时的 cwd）下的 `workspaces/` 目录（日报在 `workspaces/daily-digests/`，agent 手记在 `workspaces/journals/`）。建议在专用项目里调用这些 skill，保持输出集中。

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
      daily-digest/                   # ★ 日报层：编排 + AI 总结 + 统一日报
        SKILL.md
        scripts/  (prepare_batches, generate_unified_report)
      rss-monitor/                    # 采集层：RSS（微信 + 科技 + 播客）
        SKILL.md
        scripts/  (_common, check_updates, fetch_feed_list, fetch_articles,
                   resolve_xiaoyuzhou_urls, merge_summaries*, generate_report*)
        references/  (feeds_wechat.json, feeds_tech.json, podcasts.json)
      github-monitor/                 # 采集层：GitHub 动态（PR 合并 + issue）
        SKILL.md
        scripts/  (_common, check_updates, merge_summaries*, generate_report*)
        references/  (repos.json)
      tool-update-monitor/            # 采集层：工具版本更新
        SKILL.md
        scripts/  (check_updates, generate_report*)
        references/  (tools.json)

workspaces/                           # 运行时产物（gitignored，只保留 .gitkeep）
  daily-digests/                      # 整条 daily-digest 管线：数据 + 报告
    data/                             # 采集中间件 + 编排中间件
      daily-digest/                   # 日报层批次/摘要中间文件
      rss/                            # RSS 采集产物 latest_updates.json
      github-monitor/                 # GitHub 采集产物 latest_updates.json
      tool-update-monitor/            # 工具采集产物 latest_updates.json
    reports/                          # 统一日报输出
      YYYY-MM-DD/
        daily-digest_HH-MM.md         # ★ 统一日报（RSS + GitHub + 工具）
  journals/                           # agent-journal 产出
    YYYY-MM-DD/
      journal_HH-MM.md               # ★ 每日手记
```

> 带 `*` 的脚本（`merge_summaries.py` / `generate_report.py`）保留在采集 skill 目录中以供 daily-digest 复用及向后兼容，但采集 skill 的 SKILL.md 不再驱动它们。

---

## 数据流

```
daily-digest（日报层 · 编排）
  │
  ├─ 触发采集 → rss-monitor/check_updates.py      → workspaces/daily-digests/data/rss/latest_updates.json
  ├─ 触发采集 → github-monitor/check_updates.py   → workspaces/daily-digests/data/github-monitor/latest_updates.json
  └─ 触发采集 → tool-update-monitor/check_updates.py → workspaces/daily-digests/data/tool-update-monitor/latest_updates.json
        │
        ▼
  AI 总结（daily-digest 编排 sub-agents + 复用兄弟 merge 脚本）
        │
        ▼
  generate_unified_report.py  →  workspaces/daily-digests/reports/YYYY-MM-DD/daily-digest_HH-MM.md
```

---

## 各 Skill 说明

### daily-digest（日报层 · 编排 + 统一日报）

编排三个采集 skill，把分散采集结果汇总成一份统一 AI 摘要日报。它会：

1. 依次触发三个采集 skill 的 `check_updates.py`（数据写到各自 workspace）
2. 切批 → 并行 sub-agent 做一句话中文摘要 → 复用兄弟 `merge_summaries.py` 合并
3. 生成 `daily-digest_HH-MM.md`，含三大板块：📰 RSS 信息源 / 🔧 GitHub 动态 / 🛠 工具更新，并可选「今日要点」跨源高亮

**功能特性**：统一编排 · AI 总结（并行 sub-agent）· 跨源高亮 · 复用兄弟脚本（DRY）· 零依赖

**性能**：冷启动（含采集）~4-6 分钟；热启动（复用当日已采集数据）~1-2 分钟

---

### RSS Monitor（采集层 · 微信公众号 + 科技博客 + 播客）

采集多源 RSS 信息源，保存为 `latest_updates.json`。**只采集，不总结/不出报告。**

| 信息源 | 数量 | 内容 |
|--------|------|------|
| **WeChat**（微信公众号） | ~395 via Wechat2RSS | 安全、开发、其他、用户提交 |
| **Tech**（科技博客） | ~24 RSS + 2 Hacker News | AI/ML、芯片硬件、云计算、开源、网络安全、综合科技 |
| **Podcast**（播客） | ~1000（RSS + 小宇宙） | xyzrank.com 中文播客排名 |

**功能特性**：全量采集 · 并发抓取 · ETag 增量缓存 · 广告过滤 · 跨账号去重 · 容错降级 · 零依赖（纯 Python 标准库）

**性能**：全量扫描（395 + 26 + 1000 源）~2-4 分钟；缓存扫描（ETag）~30-60s

### GitHub Monitor（采集层 · PR 合并 + issue）

采集 GitHub 仓库动态并保存。`references/repos.json` 驱动，每个仓库声明监控类型（`pulls`/`issues`）和可选的 issue 过滤规则。**只采集，不出报告。**

| 仓库 | monitor | 过滤 | 说明 |
|------|---------|------|------|
| 1c7/chinese-independent-developer | pulls | — | 中国独立开发者项目列表，监控新合并 PR（= 新收录项目） |
| ruanyf/weekly | issues | 开源自荐 | 阮一峰 科技爱好者周刊，保留社区开源自荐帖 |

**功能特性**：PR + issue 统一采集 · 通用多仓库 · 按仓库可选过滤 · 临时单仓库监控（`--owner`/`--repo`）· GitHub REST API + ETag 缓存 · 零依赖

**性能**：动态拉取（每仓库，单页，ETag 缓存）~1-2s

### Tool Update Monitor（采集层 · 工具版本）

采集 13 个开发工具和操作系统的新版本发布并保存，按"上次记录版本"增量检测。涵盖四类：AI 编码代理、系统工具、系统更新、网络代理。**只采集，不出报告。**

**功能特性**：版本增量检测（首次建立 baseline，后续只报新版本）· GitHub Releases API / npm / HTML changelog 多源支持 · ETag 缓存 · 配置驱动（`tools.json` 加减工具无需改代码）· 零依赖

**性能**：首次扫描 ~5-10s；缓存扫描 ~3-6s

---

## 依赖

- **Python 3**（标准库即可，无需额外安装包）

核心模块：`urllib.request`, `xml.etree.ElementTree`, `concurrent.futures`, `json`, `ssl`, `html.parser`, `argparse`, `re`, `datetime`

## License

MIT
