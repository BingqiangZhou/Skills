# Changelog

All notable changes to this project will be documented in this file.

## v1.4.1 (2026-08-05)
**[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.4.0...v1.4.1)**
> 三个维护性修复：补 release skill 的 git-cliff 命令（Step 3 必须 `--unreleased`、校验避开 Git Bash 下被 ugrep 劫持的 `wc -l`）、修复全 skill 审计发现的文档与事实矛盾（tool-update 的 11→13、两个 collector 漏列的 `merge_summaries.py`、`--workers` 漏文档等），并把 release skill 从单插件扩展为三层版本模型覆盖多插件（daily-digest 主插件恒等于 tag、agent-journal 等独立按改动 bump），根治 agent-journal 版本漂移。同期修正了上一轮误把零改动的 agent-journal 从 1.0.1 改成 1.0.2 的违规 bump。
>
> 共 3 commits，其中 🐛 Fixes 2 | 📝 Docs 1
>
> **[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.4.0...v1.4.1)**

### 🐛 Bug Fixes

- **release**: Git-cliff Step 3 命令补 --unreleased，校验避开 wc -l([2d0d0ec](https://github.com/BingqiangZhou/Skills/commit/2d0d0ec27adc1b19b913c6fafe49493ca5472b75))
- **release**: 扩展为多插件覆盖；修正 agent-journal 版本误改([fe1d868](https://github.com/BingqiangZhou/Skills/commit/fe1d868a122706ae838d5d37238d4ba7136c3138))

### 📝 Documentation

- 修复全 skill 审计发现的文档与事实矛盾([d1d5372](https://github.com/BingqiangZhou/Skills/commit/d1d5372a2cc9f8b715a66bd7e10409412ceecce1))
## v1.4.0 (2026-08-05)
**[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.3.3...v1.4.0)**
> 补上 rss-monitor 缺失的 `fetch_podcast_list.py`,让被 `.gitignore` 排除的 `podcasts.json` 能像 wechat 的 `feeds_wechat.json` 一样自动重建与周更。新脚本直连 xyzrank.com 的 JSON API 抓 Top-1000 播客(7 天 TTL、`--force` 强刷、抓取失败回退旧文件),输出 schema 与旧文件逐键一致、采集层零改动;同步修正 `.gitignore` 里把该文件错误归因给 `resolve_xiaoyuzhou_urls.py` 的注释,并在 SKILL.md 新增 Step 1b 让 fresh install 不再静默无播客数据。
>
> 共 1 commit,其中 🚀 Features 1
>
> **[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.3.3...v1.4.0)**

### 🚀 Features

- **rss-monitor**: 补 fetch_podcast_list.py，podcasts.json 可自动重建与周更([bf31fae](https://github.com/BingqiangZhou/Skills/commit/bf31fae29bbb40ea73acce90f4fbe61654047550))
## v1.3.3 (2026-08-05)
**[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.3.2...v1.3.3)**
> 修复 tool-update-monitor 的 `.last_seen.json` 跨运行状态在 daily-digest 子 agent 误解析 `{project_root}` 时被指向不存在的路径,导致每次工具更新检查都被静默判定为「基线运行」、真实版本变化被吞掉的 bug。修复未动检测逻辑,而是给 `check_updates.py` 增加 state 路径可见性护栏(启动打印绝对路径与运行前已跟踪工具数、输出 `state_path`/`state_prior_count` 字段),并在 daily-digest Step 1c 加 sanity check:若 `baseline_run=true` 但 `state_prior_count=0` 且规范路径 `.last_seen.json` 非空,则停止而非继续渲染基线报告。
>
> 共 1 commit,其中 🐛 Fixes 1
>
> **[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.3.2...v1.3.3)**

### 🐛 Bug Fixes

- **daily-digest**: 修复工具更新永远判定为基线运行([7625168](https://github.com/BingqiangZhou/Skills/commit/7625168f8d6e278ca285ee52eb5a1cdd233f5b99))

## v1.3.2 (2026-08-04)
**[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.3.1...v1.3.2)**
> 修复 daily-digest 报告在播客主编步骤失败或产出为空时**静默丢失整个「🎧 播客精选」章节**的问题:报告生成器现在会渲染明确的章节占位与 ⚠️ 警告并打印 stderr,绝不静默丢弃;同时把脆弱的内联 heredoc 拆分/合并逻辑提炼为两个独立 CLI 脚本(`split_rss_summaries.py` / `merge_narratives.py`),后者采用非破坏性按字段合并以消除 `dict.update` 覆盖风险。
>
> 共 1 commit,其中 🐛 Fixes 1
>
> **[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.3.1...v1.3.2)**

### 🐛 Bug Fixes

- **daily-digest**: 报告不再静默丢失播客章节([f454377](https://github.com/BingqiangZhou/Skills/commit/f4543772662953898c3dd79f7dba05bbd87f3d18))
## v1.3.1 (2026-08-04)
**[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.3.0...v1.3.1)**
> 把 `workspaces/daily-digests/` 内的产物按用途分层:报告输出收进 `reports/`、采集中间件(rss / github-monitor / tool-update-monitor / daily-digest)收进 `data/`,两者对称;同时修复上轮重构引入的 `daily-digests/daily-digests` 双层路径误伤。
>
> 共 2 commits,其中 🔨 Refactor 2
>
> **[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.3.0...v1.3.1)**

### 🔨 Refactor

- Daily-digests 报告输出收进 reports/ 子目录([31cdd3c](https://github.com/BingqiangZhou/Skills/commit/31cdd3c25a11c742cc0db76c2f5d6eee0b9965ca))
- 采集中间件收进 workspaces/daily-digests/data/([9945080](https://github.com/BingqiangZhou/Skills/commit/99450805095065c4f26b6c5f35d1e2e55d578c86))
## v1.3.0 (2026-08-04)
**[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.2.0...v1.3.0)**
> 新增独立 plugin `agent-journal`——agent 每日自我回顾 skill,中文回顾配中英双语诗意收尾,无采集脚本(数据源交给 agent 自主判断);同时把顶层 `daily-digests/` 与 `journals/` 及采集中间件统一收纳到 `workspaces/` 下,形成单一运行时产物根目录。
>
> 共 2 commits,其中 🚀 Features 1 | 🔨 Refactor 1
>
> **[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.2.0...v1.3.0)**

### 🚀 Features

- **agent-journal**: Add agent daily self-reflection plugin([66f8ad3](https://github.com/BingqiangZhou/Skills/commit/66f8ad3c32b9e0748ad7a509f7278765c76af54c))

### 🔨 Refactor

- 统一运行时产物到 workspaces/ 下([3795bac](https://github.com/BingqiangZhou/Skills/commit/3795bac9a086323a02b73ef9d19c5d149b21575f))
## v1.2.0 (2026-08-04)
**[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.1.0...v1.2.0)**
> 本次发布将日报重构为以内容为核心的叙事综述(文章/播客双叙事轨 + GitHub/工具紧凑列表),并按 progressive disclosure 拆分 SKILL.md;同时清理三个 per-source generate_report.py 死代码,修复 CHANGELOG 历史覆盖风险,并为 tool-update-monitor 接入 GITHUB_ACCESS_TOKEN。
>
> 共 10 commits,其中 🚀 Features 1 | 🐛 Fixes 3 | 🔨 Refactor 3 | 📝 Docs 1 | ⚙️ Misc 2
>
> **[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.1.0...v1.2.0)**

### 🚀 Features

- **daily-digest**: 日报改为内容为核心的叙事综述([707668c](https://github.com/BingqiangZhou/Skills/commit/707668c7a65bcb4861826ea0ac224cf4e5330de5))

### 🐛 Bug Fixes

- **changelog**: 恢复 v1.0.0 历史 + 补全 v1.1.0 摘要([f585b43](https://github.com/BingqiangZhou/Skills/commit/f585b43e3dc241ee62565c2f994bfe145fb80a22))
- **release**: 防止 CHANGELOG 历史版本被覆盖丢失([fa506c9](https://github.com/BingqiangZhou/Skills/commit/fa506c91816f5e8d3dc1fcb7a1d57ce7df6d38f8))
- **tool-monitor**: GitHub 采集接入 GITHUB_ACCESS_TOKEN([9351ca6](https://github.com/BingqiangZhou/Skills/commit/9351ca6b86b3476ad5a5f6a88f2c3f02d5ed5f4d))

### 🔨 Refactor

- **daily-digest**: 按 progressive disclosure 拆分 SKILL.md([35d14c0](https://github.com/BingqiangZhou/Skills/commit/35d14c0d3554b97098f07b25e82c1f7d2cfd28a4))
- 删除三个 per-source generate_report.py 死代码([a3ad9a4](https://github.com/BingqiangZhou/Skills/commit/a3ad9a494cb7879d6c76fc20e13db8fa51ba15f8))
- **daily-digest**: 删除 generate_unified_report.py 中的死代码([c37008e](https://github.com/BingqiangZhou/Skills/commit/c37008e0ea57a69c4f36b5a458d0112357af5a41))

### 📝 Documentation

- **daily-digest**: README 更新为混合渲染说明([e54e509](https://github.com/BingqiangZhou/Skills/commit/e54e5090e45fa823a78b8f2f94cf33b03030c3aa))

### ⚙️ Miscellaneous

- 忽略 AGENTS.md + bump 改动 skill 版本号([4359f8c](https://github.com/BingqiangZhou/Skills/commit/4359f8caba7779292e01ea69220cb00db5fa4d45))
- 补 a3ad9a4 漏升的 skill 版本号([d1954b2](https://github.com/BingqiangZhou/Skills/commit/d1954b235964e8c73702095858cc0ad5191c55d0))
## v1.1.0 (2026-08-04)
**[Full diff](https://github.com/BingqiangZhou/Skills/compare/v1.0.0...v1.1.0)**
本次发布重构为**采集层/日报层两层架构**：新增 daily-digest 编排 skill 统一三源采集与 AI 总结；rss-monitor 抓取并发优化（播客域内并发 + 三源并行），冷缓存提速约 2.2x；release 工具链引入两层版本模型（插件全局版 + skill 独立版，改谁 bump 谁）。各 skill 新增 version 字段独立演进。

### 🚀 Features

- **daily-digest**: 新增日报编排 skill([5342d70](https://github.com/BingqiangZhou/Skills/commit/5342d70371f179d9e76b2658ddf31ca3c847b22a))
- **rss-monitor**: 抓取并发优化 + 报告格式改进([b620942](https://github.com/BingqiangZhou/Skills/commit/b6209425d6422710d354071060f70f2e07146a11))
- **release**: 两层版本模型 — 插件全局版 + skill 独立版([5131aa2](https://github.com/BingqiangZhou/Skills/commit/5131aa2579012fa783746d118cd8f17faae1d5cb))

### 🔨 Refactor

- **github-monitor, tool-update-monitor**: 重写为采集层定位 + 加 version([9e411ef](https://github.com/BingqiangZhou/Skills/commit/9e411efb2c79b08028071152880d88a9beac9b4b))

### 📝 Documentation

- 更新两层架构文档 + 插件元数据([65dea44](https://github.com/BingqiangZhou/Skills/commit/65dea44329bd35077bf6148b1c68185211552482))
- Release v1.1.0([2cb0576](https://github.com/BingqiangZhou/Skills/commit/2cb057687da381ceec45e7810994e1672d9904f8))

### ⚙️ Miscellaneous

- Ignore local docs/ directory([34829f8](https://github.com/BingqiangZhou/Skills/commit/34829f8334708a6056c446f85a4bc8192490834b))
## v1.0.0 (2026-08-04)

首个正式版本。本项目以 Claude Code 插件市场形式分发，包含三大监控 skill：统一 RSS 监控（微信公众号 / 科技博客 / 播客）、GitHub 动态监控（PR + issue）、开发工具版本更新监控，均生成 AI 摘要日报。本版本将早期分散的 `.claude/skills/` 布局迁移为标准插件市场结构（`plugins/daily-digest/`），可通过 `/plugin install` 一键安装；同时引入 git-cliff + release skill 的发布工具链。

### 🚀 Features

- 迁移为 Claude Code 插件市场布局([4966366](https://github.com/BingqiangZhou/Skills/commit/4966366d5403a7da247137416cf6b029b13f98f4))
- **rss-monitor**: 新增统一 RSS 监控 skill([f5dcbe4](https://github.com/BingqiangZhou/Skills/commit/f5dcbe4dbf2e863e24a8083bad827cc4a5065ac6))

### 📦 Other Changes

- Refactor code structure for improved readability and maintainability([d921c42](https://github.com/BingqiangZhou/Skills/commit/d921c4280666dcd0f903a6dae024580f5f92633a))
- Update podcasts.json with corrected RSS and Xiaoyuzhou links; add README.md for project documentation([94f0490](https://github.com/BingqiangZhou/Skills/commit/94f04900674fa64f8cb2cd198e6c7ca81da9ff8f))
- Implement structural updates and optimizations across multiple modules([2ed1796](https://github.com/BingqiangZhou/Skills/commit/2ed1796523415ec7b0dc414013499393b62574ad))
- Refactor code structure for improved readability and maintainability([c0a9327](https://github.com/BingqiangZhou/Skills/commit/c0a9327054cba980b5e5577b0ae38ba6416ff0fd))
- Add Wechat RSS monitoring scripts and related functionality([a18c7aa](https://github.com/BingqiangZhou/Skills/commit/a18c7aac3adb883e21bcd8bbb7b540a6ce37df02))
- Refactor summary map to use article URL as key; update .gitignore and remove .gitkeep file([ff15b6a](https://github.com/BingqiangZhou/Skills/commit/ff15b6a81f501379855361617e0c498bfaffdb7f))
- Update SKILL.md with critical JSON encoding rules and merging instructions([a24e9b4](https://github.com/BingqiangZhou/Skills/commit/a24e9b4a3ca2cc3185cccb1d44cd0298ea631a62))
- Add tech daily skill for monitoring tech news and generating reports([43f8384](https://github.com/BingqiangZhou/Skills/commit/43f838484df3e1968850cc7c43ca3cc79790ba28))
- Update README.md to include Tech Daily skill and enhance project structure details([58d382e](https://github.com/BingqiangZhou/Skills/commit/58d382ee4d14c35b95fc9e9d80beb11418a0e1ce))
- Fix wechat-rss-monitor skill issues (cache, dates, report, entities)([2e953b4](https://github.com/BingqiangZhou/Skills/commit/2e953b4ac5ec09fe0b197b5865a75108d65baf36))
- Refactor podcast-rss-monitor: extract shared helpers, fix cache/error handling([1540cd6](https://github.com/BingqiangZhou/Skills/commit/1540cd6b4afa4c51ca3d301554b39ab5447b5ef0))
- Fix wechat-rss-monitor sub-agent failures: batch sizing + truncation([2db6e64](https://github.com/BingqiangZhou/Skills/commit/2db6e64c6708b3f66aa08287d2fada5411aabead))
- Add github-monitor & tool-update-monitor skills; unify workspace layout([d3c9b74](https://github.com/BingqiangZhou/Skills/commit/d3c9b743bd1be89b20ff0b3c3cfbd107c4cf1718))

### 📝 Documentation

- Update CHANGELOG for v1.0.0([a642d78](https://github.com/BingqiangZhou/Skills/commit/a642d783d97fca17234b175bed091909a6ba79e4))

### ⚙️ Miscellaneous

- 引入发布工具链并清理派生数据([cad1c1f](https://github.com/BingqiangZhou/Skills/commit/cad1c1f2373856e893a09c7e918fe850b09c24ac))
<!-- generated by git-cliff -->
