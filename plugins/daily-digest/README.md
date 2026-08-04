# daily-digest

中文内容更新监控与每日摘要生成插件。采用**采集与日报分离**的两层架构：

- **采集层**（3 个 skill）：只采集数据并保存，不做总结、不出报告。
- **日报层**（1 个 skill）：`daily-digest` 编排三个采集任务、做 AI 总结、生成统一 Markdown 日报。

- **rss-monitor** — 采集微信公众号（~395）+ 科技博客（~26 RSS + Hacker News）+ 中文播客（~1000）
- **github-monitor** — 采集 GitHub 仓库动态（新合并 PR + 新 issue），多仓库配置驱动
- **tool-update-monitor** — 采集 13 个开发工具的新版本增量（GitHub Releases / npm / changelog）
- **daily-digest** — 编排上述三个采集 skill + AI 总结 + 生成统一日报

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

三个采集 skill 单独触发时只保存数据到 `workspaces/<source>/latest_updates.json`；想生成日报请用 daily-digest，它输出到当前项目（cwd）的 `daily-digests/YYYY-MM-DD/daily-digest_HH-MM.md`。

## License

MIT
