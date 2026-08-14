# daily-digest

中文内容更新监控与每日摘要生成插件。采用**采集与日报分离**的两层架构：

- **采集层**（3 个 skill）：只采集数据并保存，不做总结、不出报告。
- **日报层**（1 个 skill）：`daily-digest` 编排三个采集任务、做 AI 总结、生成统一 Markdown 日报。

- **rss-monitor** — 采集微信公众号（~294，BestBlogs）+ 科技博客（~26 RSS + Hacker News）+ 中文播客（~1000）
- **github-monitor** — 采集 GitHub 仓库动态（新合并 PR + 新 issue），多仓库配置驱动
- **tool-update-monitor** — 采集 14 个开发工具的新版本增量（GitHub Releases / npm / changelog）
- **daily-digest** — 编排上述三个采集 skill + AI 总结 + 生成统一日报

日报按各源体量采用**混合渲染**：

- **RSS 文章**（量大、需要提炼）：一个"主编"子代理把一句话摘要按**内容主题**
  聚成 5-8 个话题，每个话题 = 一句导语 + 3-8 条独立要点（可扫读，不写成密集
  段落），顶部是今日概览、尾部是"其他动态"收拢零碎内容。
- **RSS 播客**：独立的逐集呈现——每集一句话说明这期讲什么，按节目分组；
  低信息量节目一行带过。
- **GitHub / 工具**（量小、本身是结构化的"谁更新了什么"）：直接渲染成紧凑列表，
  一条一行带链接和一句话要点。

完整结构化数据仍保留在 `workspaces/daily-digests/data/<source>/latest_updates.json`，需要某条 RSS
详情时让 agent 直接搜工作区数据即可。

## 安装

```text
/plugin marketplace add BingqiangZhou/Skills
/plugin install daily-digest@daily-digest
```

## 使用

安装后用自然语言触发即可，例如：

- 「每日摘要」「生成日报」「信息汇总」→ daily-digest（编排三源 + 统一日报）
- 「检查公众号更新」「抓取 RSS」「检查信息源」→ rss-monitor（只采集）
- 「检查 GitHub 动态」「独立开发者项目更新」「阮一峰开源自荐」→ github-monitor（只采集）
- 「检查工具更新」「版本检查」「有没有新版本」→ tool-update-monitor（只采集）

三个采集 skill 单独触发时只保存数据到 `workspaces/daily-digests/data/<source>/latest_updates.json`；想生成日报请用 daily-digest，它输出到当前项目（cwd）的 `workspaces/daily-digests/reports/YYYY-MM-DD/daily-digest_HH-MM.md`。

## License

MIT
